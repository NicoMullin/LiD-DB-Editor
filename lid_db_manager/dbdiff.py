"""Comparing two databases and turning the difference into a mod.

This is what lets someone hand the manager a `masters.db` that has already been
reworked and get a normal, toggleable mod out of it: compare it against vanilla,
keep only what differs, and write that out as SQL.

Rows are matched by primary key where a table has one - 197 of the 221 tables in
the real database do. The rest fall back to rowid, which is only meaningful when
the two files share a history, so those tables are reported as lower confidence.

Everything here is read-only with respect to both databases.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import ModManagerError
from .sqlutil import if_not_exists, quote_ident


class IncompatibleDatabase(ModManagerError):
    """The two databases are not the same shape, so a diff would be nonsense."""


class _AllRows:
    """Sentinel: keep every row of this table, without listing them."""

    def __repr__(self) -> str:  # pragma: no cover - debugging only
        return "ALL_ROWS"


ALL_ROWS = _AllRows()

# What a chooser hands back: table name -> ALL_ROWS, or the set of row keys to
# keep. A table missing from the mapping is dropped entirely.
Selection = dict


@dataclass
class RowUpdate:
    key: tuple                      # primary key (or rowid) values
    changes: dict[str, Any]         # column -> new value
    before: dict[str, Any]          # column -> old value, for the preview


@dataclass
class TableDelta:
    table: str
    key_columns: list[str]
    columns: list[str]
    updates: list[RowUpdate] = field(default_factory=list)
    inserts: list[tuple] = field(default_factory=list)
    deletes: list[tuple] = field(default_factory=list)
    keyed_by_rowid: bool = False
    # Set only for a table vanilla does not have: the CREATE statement that
    # brings it into being, plus any indexes defined on it. Every row of such a
    # table is an insert, and reverting it is simply a DROP.
    create_sql: str | None = None
    index_sql: list[str] = field(default_factory=list)

    @property
    def is_new_table(self) -> bool:
        return self.create_sql is not None

    @property
    def cell_count(self) -> int:
        return sum(len(u.changes) for u in self.updates)

    @property
    def empty(self) -> bool:
        # A new table is worth carrying across even when it has no rows yet.
        return not (self.updates or self.inserts or self.deletes or self.create_sql)


@dataclass
class DbDelta:
    tables: list[TableDelta] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def cell_count(self) -> int:
        return sum(t.cell_count for t in self.tables)

    @property
    def insert_count(self) -> int:
        return sum(len(t.inserts) for t in self.tables)

    @property
    def delete_count(self) -> int:
        return sum(len(t.deletes) for t in self.tables)

    @property
    def new_table_count(self) -> int:
        return sum(1 for t in self.tables if t.is_new_table)

    @property
    def empty(self) -> bool:
        return all(t.empty for t in self.tables)

    def summary(self) -> str:
        if self.empty:
            return "no differences - this database matches vanilla"
        parts = []
        if self.cell_count:
            parts.append(f"{self.cell_count:,} changed value(s)")
        if self.insert_count:
            parts.append(f"{self.insert_count:,} added row(s)")
        if self.delete_count:
            parts.append(f"{self.delete_count:,} removed row(s)")
        if self.new_table_count:
            parts.append(f"{self.new_table_count:,} new table(s)")
        return f"{', '.join(parts)} across {len(self.tables)} table(s)"

    @property
    def change_count(self) -> int:
        """Changed rows plus added and removed ones - what a chooser counts."""
        return sum(
            len(t.updates) + len(t.inserts) + len(t.deletes) for t in self.tables
        )

    def filtered(self, selection: "Selection") -> "DbDelta":
        """A copy carrying only what ``selection`` keeps.

        Lets someone import a reworked database and take part of it - the shop
        changes without the enemy tuning, say. Dropping pieces can produce a
        combination the rework's author never tested, which is the caller's
        business to warn about, not this function's.
        """
        kept = DbDelta(warnings=list(self.warnings))
        for table_delta in self.tables:
            wanted = selection.get(table_delta.table)
            if wanted is None:  # table not selected at all
                continue
            if wanted is ALL_ROWS:
                kept.tables.append(table_delta)
                continue
            trimmed = TableDelta(
                table_delta.table,
                list(table_delta.key_columns),
                list(table_delta.columns),
                keyed_by_rowid=table_delta.keyed_by_rowid,
                create_sql=table_delta.create_sql,
                index_sql=list(table_delta.index_sql),
                updates=[u for u in table_delta.updates if u.key in wanted],
                inserts=[
                    v for v in table_delta.inserts
                    if insert_key(table_delta, v) in wanted
                ],
                deletes=[k for k in table_delta.deletes if k in wanted],
            )
            # A new table keeps its CREATE even when every row is dropped:
            # the table itself is one of the things being imported.
            if not trimmed.empty:
                kept.tables.append(trimmed)
        return kept

    def split_by_table(self) -> list["DbDelta"]:
        """One single-table delta per table, so each can become its own mod.

        That is what makes an imported rework toggleable afterwards rather than
        only at import: each piece is an ordinary mod the player can switch off,
        reorder or revert on its own.
        """
        out = []
        for table_delta in self.tables:
            piece = DbDelta(warnings=list(self.warnings))
            piece.tables.append(table_delta)
            out.append(piece)
        return out


def insert_key(table_delta: TableDelta, values: tuple) -> tuple | None:
    """The primary-key tuple of a row about to be inserted.

    Inserts carry every column, so the key has to be read back out of them.
    Returns None for a table addressed by rowid, where an inserted row has no
    key of its own to select on.
    """
    try:
        return tuple(values[table_delta.columns.index(c)] for c in table_delta.key_columns)
    except ValueError:  # a key column that is not a real column, i.e. rowid
        return None


def _open(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _tables(con: sqlite3.Connection) -> list[str]:
    return [
        r[0]
        for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]


def _columns(con: sqlite3.Connection, table: str) -> tuple[list[str], list[str]]:
    info = list(con.execute(f"PRAGMA table_info({quote_ident(table)})"))
    names = [r["name"] for r in info]
    # pk is 1-based ordinal within a composite key, 0 when not part of one.
    key = [r["name"] for r in sorted((r for r in info if r["pk"]), key=lambda r: r["pk"])]
    return names, key


def _schema_sql(con: sqlite3.Connection, table: str) -> tuple[str, list[str]]:
    """The CREATE statement for a table, and the CREATE statements for its indexes.

    Indexes SQLite builds itself for a PRIMARY KEY or UNIQUE constraint have a
    NULL ``sql`` - they come back automatically with the table, so they are
    skipped here rather than being written out twice.
    """
    row = con.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    create = row[0] if row and row[0] else ""
    indexes = [
        r[0]
        for r in con.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? "
            "AND sql IS NOT NULL ORDER BY name",
            (table,),
        )
    ]
    return create, indexes


def check_compatible(vanilla: Path, other: Path) -> list[str]:
    """Refuse databases that are not the same shape as vanilla.

    A rework built on a different game version diffs as "undo everything the
    developers changed", which would apply silently and wrongly - so a mismatch
    is an error, not a warning.
    """
    van, mod = _open(vanilla), _open(other)
    try:
        van_tables, mod_tables = set(_tables(van)), set(_tables(mod))
        missing = van_tables - mod_tables
        if missing:
            raise IncompatibleDatabase(
                f"{len(missing)} table(s) from vanilla are missing, including "
                f"{', '.join(sorted(missing)[:3])}. This file does not look like "
                "the same game version as your vanilla database."
            )
        mismatched = []
        for table in sorted(van_tables):
            if _columns(van, table)[0] != _columns(mod, table)[0]:
                mismatched.append(table)
        if mismatched:
            raise IncompatibleDatabase(
                f"{len(mismatched)} table(s) have different columns, including "
                f"{', '.join(mismatched[:3])}. This file was almost certainly built "
                "on a different game version."
            )
        notes = []
        new_tables = mod_tables - van_tables
        if new_tables:
            notes.append(
                f"adds {len(new_tables)} table(s) vanilla does not have "
                f"({', '.join(sorted(new_tables)[:3])}); those are carried across too"
            )
        return notes
    finally:
        van.close()
        mod.close()


def compare(vanilla: Path, other: Path, only_tables: list[str] | None = None) -> DbDelta:
    """Everything ``other`` changes relative to ``vanilla``."""
    delta = DbDelta(warnings=list(check_compatible(vanilla, other)))
    van, mod = _open(vanilla), _open(other)
    try:
        wanted = set(only_tables) if only_tables else None
        for table in _tables(van):
            if wanted is not None and table not in wanted:
                continue
            columns, key = _columns(van, table)
            keyed_by_rowid = not key
            key = key or ["rowid"]

            selection = ", ".join(quote_ident(c) for c in columns)
            key_selection = ", ".join(
                quote_ident(c) if c != "rowid" else "rowid" for c in key
            )
            sql = f"SELECT {key_selection}, {selection} FROM {quote_ident(table)}"

            def rows(con):
                out = {}
                for row in con.execute(sql):
                    out[tuple(row[i] for i in range(len(key)))] = tuple(
                        row[len(key) + i] for i in range(len(columns))
                    )
                return out

            try:
                before, after = rows(van), rows(mod)
            except sqlite3.Error:
                delta.warnings.append(f"{table}: could not be read, skipped")
                continue

            table_delta = TableDelta(table, list(key), columns, keyed_by_rowid=keyed_by_rowid)
            for row_key, old in before.items():
                new = after.get(row_key)
                if new is None:
                    table_delta.deletes.append(row_key)
                    continue
                changed = {
                    columns[i]: new[i] for i in range(len(columns)) if old[i] != new[i]
                }
                if changed:
                    table_delta.updates.append(
                        RowUpdate(
                            row_key,
                            changed,
                            {name: old[columns.index(name)] for name in changed},
                        )
                    )
            for row_key, new in after.items():
                if row_key not in before:
                    table_delta.inserts.append(new)

            if not table_delta.empty:
                if keyed_by_rowid:
                    delta.warnings.append(
                        f"{table} has no primary key, so rows were matched by position - "
                        "check this table's changes before trusting them"
                    )
                delta.tables.append(table_delta)

        # Tables the modded database invents. There is nothing to compare, so
        # the whole table is the difference: its schema plus every row it holds.
        for table in sorted(set(_tables(mod)) - set(_tables(van))):
            if wanted is not None and table not in wanted:
                continue
            create, indexes = _schema_sql(mod, table)
            if not create:
                delta.warnings.append(f"{table}: no schema recorded, skipped")
                continue
            columns, key = _columns(mod, table)
            selection = ", ".join(quote_ident(c) for c in columns)
            try:
                rows = [
                    tuple(row[i] for i in range(len(columns)))
                    for row in mod.execute(f"SELECT {selection} FROM {quote_ident(table)}")
                ]
            except sqlite3.Error:
                delta.warnings.append(f"{table}: could not be read, skipped")
                continue
            delta.tables.append(
                TableDelta(
                    table,
                    key or ["rowid"],
                    columns,
                    inserts=rows,
                    keyed_by_rowid=not key,
                    create_sql=create,
                    index_sql=indexes,
                )
            )
    finally:
        van.close()
        mod.close()
    return delta


def delta_for_mod(vanilla: Path, mod, scratch_dir: Path | None = None) -> DbDelta:
    """What a mod actually changes, measured against vanilla.

    The mod's patches run on a throwaway copy of the vanilla database, never on
    the live one, and the copy is then compared with the original. That gives
    the exact set of cells the mod is responsible for - without parsing a line
    of its SQL, and regardless of how the SQL is written.
    """
    import shutil
    import tempfile

    scratch_root = Path(scratch_dir or tempfile.mkdtemp(prefix="lid-delta-"))
    scratch_root.mkdir(parents=True, exist_ok=True)
    scratch = scratch_root / "scratch.db"
    created_here = scratch_dir is None
    try:
        shutil.copy2(Path(vanilla), scratch)
        con = sqlite3.connect(str(scratch))
        con.row_factory = sqlite3.Row
        try:
            for patch in mod.patches:
                patch.apply(con, mod.id)
            con.commit()
        finally:
            con.close()
        # Only the tables the mod could have written need comparing.
        return compare(Path(vanilla), scratch, only_tables=sorted(mod.tables()) or None)
    finally:
        if created_here:
            shutil.rmtree(scratch_root, ignore_errors=True)
        elif scratch.exists():
            scratch.unlink()


def _literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "X'" + bytes(value).hex() + "'"
    return "'" + str(value).replace("'", "''") + "'"


def to_sql(delta: DbDelta, header: str = "") -> str:
    """Render a delta as a .sql file the existing engine can already apply."""
    lines: list[str] = []
    if header:
        lines += [f"-- {line}" for line in header.splitlines()]
        lines.append("")
    for table_delta in delta.tables:
        if table_delta.is_new_table:
            lines.append(
                f"-- {table_delta.table}: new table, "
                f"{len(table_delta.inserts)} row(s) - vanilla does not have this one"
            )
            lines.append(if_not_exists(table_delta.create_sql.strip().rstrip(";")) + ";")
            for index in table_delta.index_sql:
                lines.append(if_not_exists(index.strip().rstrip(";")) + ";")
        else:
            lines.append(
                f"-- {table_delta.table}: {len(table_delta.updates)} updated, "
                f"{len(table_delta.inserts)} added, {len(table_delta.deletes)} removed"
            )
        name = quote_ident(table_delta.table)
        for update in table_delta.updates:
            assignments = ", ".join(
                f"{quote_ident(c)} = {_literal(v)}" for c, v in update.changes.items()
            )
            where = " AND ".join(
                f"{quote_ident(c)} = {_literal(v)}"
                for c, v in zip(table_delta.key_columns, update.key)
            )
            lines.append(f"UPDATE {name} SET {assignments} WHERE {where};")
        for values in table_delta.inserts:
            cols = ", ".join(quote_ident(c) for c in table_delta.columns)
            vals = ", ".join(_literal(v) for v in values)
            if table_delta.keyed_by_rowid:
                # No primary key, so OR REPLACE has nothing to replace on and
                # every re-apply would append the row again. Insert it only if
                # an identical row is not already there. IS, not =, so a NULL
                # in the row matches the NULL already in the table.
                match = " AND ".join(
                    f"{quote_ident(c)} IS {_literal(v)}"
                    for c, v in zip(table_delta.columns, values)
                )
                lines.append(
                    f"INSERT INTO {name} ({cols}) SELECT {vals} "
                    f"WHERE NOT EXISTS (SELECT 1 FROM {name} WHERE {match});"
                )
            else:
                lines.append(f"INSERT OR REPLACE INTO {name} ({cols}) VALUES ({vals});")
        for row_key in table_delta.deletes:
            where = " AND ".join(
                f"{quote_ident(c)} = {_literal(v)}"
                for c, v in zip(table_delta.key_columns, row_key)
            )
            lines.append(f"DELETE FROM {name} WHERE {where};")
        lines.append("")
    return "\n".join(lines)
