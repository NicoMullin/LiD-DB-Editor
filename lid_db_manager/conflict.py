"""Conflict and dependency analysis across the enabled mod list.

    two update_set mods on the same (table, column)  -> warn, last-wins
    two text_replace mods on the same matched row    -> warn, last-wins
    update_set + text_replace                        -> no conflict
    raw SQL + anything, same table                   -> warn (table granularity)
    a mod's declared conflicts_with                  -> warn

A mod can suppress raw-SQL warnings for tables it knows its SQL leaves alone by
listing them in ``raw_sql_files_do_not_touch``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import sqlite3

from .mod import Mod
from .mod_loader import out_of_order_requirements
from .patch import RawSqlPatch, TextReplacePatch, UpdateSetPatch

KIND_COLUMN = "column"
KIND_TEXT = "text"
KIND_RAW_SQL = "raw_sql"
KIND_DECLARED = "declared"
KIND_ASSET = "asset"


@dataclass
class Conflict:
    """Two enabled mods that write the same thing. The later one wins."""

    kind: str
    first: str
    second: str
    detail: str

    def message(self) -> str:
        return f"{self.first} and {self.second} both write {self.detail} - {self.second} wins"


@dataclass
class MissingRequirement:
    mod_id: str
    required_id: str
    installed: bool

    def message(self) -> str:
        if self.installed:
            return f"{self.mod_id} requires {self.required_id}, which is installed but not enabled"
        return f"{self.mod_id} requires {self.required_id}, which is not installed"


@dataclass
class OrderProblem:
    """A mod sits above something it requires in the load order."""

    mod_id: str
    required_id: str

    def message(self) -> str:
        return (
            f"{self.mod_id} is applied before {self.required_id}, which it requires - "
            f"move it below {self.required_id} in the load order"
        )


@dataclass
class ConflictReport:
    conflicts: list[Conflict] = field(default_factory=list)
    missing_requirements: list[MissingRequirement] = field(default_factory=list)
    order_problems: list[OrderProblem] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.conflicts and not self.missing_requirements and not self.order_problems

    def for_mod(self, mod_id: str) -> list[str]:
        """Every message that mentions this mod, for the inline UI warning."""
        messages = [
            conflict.message()
            for conflict in self.conflicts
            if mod_id in (conflict.first, conflict.second)
        ]
        messages += [
            requirement.message()
            for requirement in self.missing_requirements
            if requirement.mod_id == mod_id
        ]
        messages += [
            problem.message() for problem in self.order_problems if problem.mod_id == mod_id
        ]
        return messages


@dataclass
class _ModFootprint:
    """What one mod writes, split by patch kind.

    ``rows`` maps a table to the rowids the mod actually writes there, when
    those can be worked out against the live database. None means "unknown -
    assume all of them", which is what an INSERT or an unparseable statement
    gives. Without a database connection every entry is None and analysis falls
    back to whole-table granularity.
    """

    columns: set[tuple[str, str]] = field(default_factory=set)
    texts: set[tuple] = field(default_factory=set)
    raw_tables: set[str] = field(default_factory=set)
    rows: dict[str, set[int] | None] = field(default_factory=dict)
    asset_targets: set[str] = field(default_factory=set)

    def all_tables(self) -> set[str]:
        return {table for table, _ in self.columns} | {key[0] for key in self.texts} | self.raw_tables

    def add_rows(self, table: str, rowids: set[int] | None) -> None:
        if table not in self.rows:
            self.rows[table] = rowids if rowids is None else set(rowids)
        elif self.rows[table] is None or rowids is None:
            self.rows[table] = None
        else:
            self.rows[table] |= rowids


def footprint(mod: Mod, con: sqlite3.Connection | None = None) -> _ModFootprint:
    excluded = set(mod.raw_sql_files_do_not_touch)
    result = _ModFootprint()
    for patch in mod.patches:
        if isinstance(patch, UpdateSetPatch):
            result.columns |= patch.targets()
        elif isinstance(patch, TextReplacePatch):
            result.texts |= patch.text_keys()
        elif isinstance(patch, RawSqlPatch):
            # Raw SQL can do anything, so it normally counts as writing whole
            # tables. When every statement is a plain UPDATE the columns are
            # readable, and then it is treated like any other column-level
            # patch - so two mods changing different columns of the same rows
            # stop being reported as fighting over them.
            resolved = patch.resolved_targets()
            if resolved is not None and not (
                {table for table, _ in resolved} & excluded
            ):
                result.columns |= resolved
            else:
                result.raw_tables |= patch.tables() - excluded

        result.asset_targets |= patch.asset_targets()

        if con is None:
            for table in patch.tables() - excluded:
                result.add_rows(table, None)
            continue
        try:
            touched = patch.touched_rows(con)
        except Exception:
            # Row detail is an optimisation; never let it break the mod list.
            touched = {table: None for table in patch.tables()}
        for table, rowids in touched.items():
            if table not in excluded:
                result.add_rows(table, rowids)
    return result


def _shared_rows(left: _ModFootprint, right: _ModFootprint, table: str) -> set[int] | None:
    """Rows both mods write in this table. None means "cannot tell - assume yes"."""
    a, b = left.rows.get(table), right.rows.get(table)
    if a is None or b is None:
        return None
    return a & b


def _row_detail(shared: set[int] | None) -> str:
    if shared is None:
        return ""
    return f" ({len(shared)} shared row(s))"


def _describe_text_key(key: tuple) -> str:
    table, column, match_items = key
    selector = ", ".join(f"{name}={value!r}" for name, value in match_items)
    return f"{table}.{column} [{selector}]"


def analyze(
    enabled: list[Mod],
    installed_ids: set[str] | None = None,
    con: sqlite3.Connection | None = None,
) -> ConflictReport:
    """Compare every enabled mod against every other, in apply order.

    Pass a read-only connection to get row-level precision: two mods that write
    the same table in rows that never overlap then produce no warning at all.
    """
    report = ConflictReport()
    installed_ids = installed_ids if installed_ids is not None else {mod.id for mod in enabled}
    enabled_ids = {mod.id for mod in enabled}

    for mod in enabled:
        for required_id in mod.requires:
            if required_id not in enabled_ids:
                report.missing_requirements.append(
                    MissingRequirement(mod.id, required_id, required_id in installed_ids)
                )

    report.order_problems = [
        OrderProblem(mod_id, required_id)
        for mod_id, required_id in out_of_order_requirements(enabled)
    ]

    footprints = {mod.id: footprint(mod, con) for mod in enabled}

    for index, first in enumerate(enabled):
        for second in enabled[index + 1 :]:
            left, right = footprints[first.id], footprints[second.id]

            if second.id in first.conflicts_with or first.id in second.conflicts_with:
                report.conflicts.append(
                    Conflict(
                        KIND_DECLARED,
                        first.id,
                        second.id,
                        "each other (declared incompatible by the mod author)",
                    )
                )

            for table, column in sorted(left.columns & right.columns):
                shared = _shared_rows(left, right, table)
                if shared is not None and not shared:
                    continue  # same column, but no row in common
                report.conflicts.append(
                    Conflict(
                        KIND_COLUMN,
                        first.id,
                        second.id,
                        f"{table}.{column}{_row_detail(shared)}",
                    )
                )

            for key in sorted(left.texts & right.texts, key=str):
                report.conflicts.append(
                    Conflict(KIND_TEXT, first.id, second.id, _describe_text_key(key))
                )

            # Raw SQL is opaque, so it conflicts at table granularity with
            # anything else that writes the same table.
            raw_overlap = (left.raw_tables & right.all_tables()) | (
                right.raw_tables & left.all_tables()
            )
            for table in sorted(raw_overlap):
                shared = _shared_rows(left, right, table)
                if shared is not None and not shared:
                    # Both write this table, but never the same row - which is
                    # the common case for two unrelated master_text mods.
                    continue
                detail = f"table {table} (raw SQL){_row_detail(shared)}"
                report.conflicts.append(Conflict(KIND_RAW_SQL, first.id, second.id, detail))

            # Two mods copying the same game file: last in load order wins,
            # same as a whole-table SQL dump.
            for target in sorted(left.asset_targets & right.asset_targets):
                report.conflicts.append(
                    Conflict(KIND_ASSET, first.id, second.id, f"game file {target}")
                )

    return report
