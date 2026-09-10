"""Applying and reverting mods.

Everything happens inside a single ``BEGIN IMMEDIATE`` transaction: either the
whole enabled mod list lands or none of it does. Before each mod's patches run,
the rows they are about to touch are snapshotted, so any mod can be unwound
later without restoring the whole database.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import snapshot as snapshot_module
from .errors import ApplyError, RevertError, ValidationError
from .mod import APPLY_DIFF, Mod
from .patch import PREVIEW_ROW_LIMIT, DiffPreview, DiffRow, PatchResult, SnapshotSpec
from .session_log import SessionLog
from .snapshot import Snapshot
from .sqlutil import (
    begin_immediate,
    connect,
    if_not_exists,
    quote_ident,
    sha256_file,
    split_statements,
)
from .state import AppliedRecord, State
from .validator import ValidationReport, validate


@dataclass
class ModApplyResult:
    mod_id: str
    ok: bool = False
    rows_changed: int = 0
    tables: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str = ""


@dataclass
class ApplyReport:
    ok: bool = False
    results: list[ModApplyResult] = field(default_factory=list)
    validation: ValidationReport | None = None
    error: str = ""
    failed_mod: str = ""
    duration_seconds: float = 0.0
    db_sha256_after: str = ""

    @property
    def rows_changed(self) -> int:
        return sum(result.rows_changed for result in self.results)

    def for_mod(self, mod_id: str) -> ModApplyResult | None:
        for result in self.results:
            if result.mod_id == mod_id:
                return result
        return None

    def summary_line(self) -> str:
        if not self.ok:
            where = f" ({self.failed_mod})" if self.failed_mod else ""
            return f"Apply failed{where}: {self.error}"
        return (
            f"Applied {len(self.results)} mod(s), {self.rows_changed} row(s) "
            f"in {self.duration_seconds:.2f}s"
        )


def snapshot_specs_from_delta(delta) -> list[SnapshotSpec] | None:
    """Turn a mod's delta into the smallest snapshot that can undo it.

    Returns None when the delta cannot be trusted to describe every row that
    will change - then the caller keeps the old, heavier behaviour.

    A table the mod inserts into or deletes from still needs a whole-table
    copy: restoring by rowid can put a changed value back, but it cannot bring
    back a deleted row or remove an inserted one.
    """
    if delta is None:
        return None
    specs: list[SnapshotSpec] = []
    for table_delta in delta.tables:
        if table_delta.is_new_table:
            # Nothing to copy: the table is not there yet, so undoing the mod
            # means dropping it again. This is checked before the rowid rule,
            # which is about addressing rows that already exist.
            specs.append(SnapshotSpec("absent", table_delta.table))
            continue
        if table_delta.keyed_by_rowid:
            return None  # no primary key: rowids are not a dependable address
        if table_delta.inserts or table_delta.deletes:
            specs.append(SnapshotSpec("table", table_delta.table))
            continue
        columns = sorted({c for u in table_delta.updates for c in u.changes})
        if not columns:
            continue
        specs.append(
            SnapshotSpec(
                "keys",
                table_delta.table,
                columns=columns,
                key_columns=list(table_delta.key_columns),
                keys=[u.key for u in table_delta.updates],
            )
        )
    return specs


def apply_delta(con: sqlite3.Connection, delta, mod_id: str) -> PatchResult:
    """Write only what a mod changes relative to vanilla.

    This is what makes a whole-table mod behave itself: it contributes the
    cells it actually alters and nothing else, so a mod applied earlier keeps
    everything it set in rows this one does not care about. It also makes a mod
    idempotent - re-applying writes the same cells to the same values, whatever
    its SQL does.
    """
    changed = 0
    for table_delta in delta.tables:
        name = quote_ident(table_delta.table)
        where = " AND ".join(f"{quote_ident(c)} = ?" for c in table_delta.key_columns)

        if table_delta.is_new_table:
            # Vanilla has no such table, so it has to exist before anything can
            # go into it. IF NOT EXISTS keeps a re-apply harmless.
            for statement in [table_delta.create_sql, *table_delta.index_sql]:
                try:
                    con.execute(if_not_exists(statement.strip().rstrip(";")))
                except sqlite3.Error as exc:
                    raise ApplyError(
                        mod_id, f"{table_delta.table}: could not create table: {exc}"
                    ) from exc

        for update in table_delta.updates:
            assignments = ", ".join(f"{quote_ident(c)} = ?" for c in update.changes)
            try:
                cursor = con.execute(
                    f"UPDATE {name} SET {assignments} WHERE {where}",
                    list(update.changes.values()) + list(update.key),
                )
            except sqlite3.Error as exc:
                raise ApplyError(mod_id, f"{table_delta.table}: {exc}") from exc
            changed += max(cursor.rowcount, 0)

        if table_delta.inserts:
            columns = ", ".join(quote_ident(c) for c in table_delta.columns)
            placeholders = ", ".join("?" for _ in table_delta.columns)
            try:
                con.executemany(
                    f"INSERT OR REPLACE INTO {name} ({columns}) VALUES ({placeholders})",
                    [list(v) for v in table_delta.inserts],
                )
            except sqlite3.Error as exc:
                raise ApplyError(mod_id, f"{table_delta.table}: {exc}") from exc
            changed += len(table_delta.inserts)

        for key in table_delta.deletes:
            try:
                cursor = con.execute(f"DELETE FROM {name} WHERE {where}", list(key))
            except sqlite3.Error as exc:
                raise ApplyError(mod_id, f"{table_delta.table}: {exc}") from exc
            changed += max(cursor.rowcount, 0)

    return PatchResult(rows_changed=changed)


def apply_mods(
    db_path: Path,
    mods: list[Mod],
    *,
    snapshots_dir: Path,
    log: SessionLog | None = None,
    skip_validation: bool = False,
    installed_ids: set[str] | None = None,
    deltas: dict[str, object] | None = None,
    keep_snapshots: set[str] | None = None,
) -> ApplyReport:
    """Validate, snapshot and apply every mod in ``mods`` as one transaction."""
    log = log or SessionLog()
    db_path = Path(db_path)
    report = ApplyReport()
    started = time.monotonic()

    ordered = list(mods)  # already in load order

    if not skip_validation:
        report.validation = validate(db_path, ordered, installed_ids)
        for warning in _validation_warnings(report.validation):
            log.warn(warning)
        if not report.validation.ok:
            report.error = report.validation.summary_line()
            failures = report.validation.failed
            if failures:
                report.failed_mod = failures[0].mod_id
                for failure in failures:
                    for message in failure.errors:
                        log.error(f"{failure.mod_id}: {message}")
            else:
                log.error(report.validation.fatal or report.error)
            report.duration_seconds = time.monotonic() - started
            return report
        log.info(report.validation.summary_line())

    sha_before = sha256_file(db_path) if db_path.is_file() else ""

    try:
        con = connect(db_path)
    except sqlite3.Error as exc:
        report.error = f"could not open the database: {exc}"
        log.error(report.error)
        report.duration_seconds = time.monotonic() - started
        return report

    pending_snapshots: list[Snapshot] = []
    try:
        try:
            begin_immediate(con)
        except sqlite3.OperationalError as exc:
            report.error = str(exc)
            log.error(report.error)
            report.duration_seconds = time.monotonic() - started
            return report

        try:
            for mod in ordered:
                result = ModApplyResult(mod_id=mod.id, tables=sorted(mod.tables()))

                # A delta says exactly which rows change, so raw SQL no
                # longer costs a copy of every table it writes.
                specs = snapshot_specs_from_delta((deltas or {}).get(mod.id))
                if specs is None:
                    specs = []
                    for patch in mod.patches:
                        specs.extend(patch.snapshot_specs(con))
                pending_snapshots.append(
                    snapshot_module.capture(
                        con,
                        mod.id,
                        specs,
                        db_path=str(db_path),
                        db_sha256_before=sha_before,
                    )
                )

                delta = (deltas or {}).get(mod.id)
                if mod.apply_mode == APPLY_DIFF and delta is not None:
                    patch_result = apply_delta(con, delta, mod.id)
                    result.rows_changed += patch_result.rows_changed
                    result.warnings.extend(patch_result.warnings)
                    log.info(f"{mod.id}: applied as a difference from vanilla")
                else:
                    if mod.apply_mode == APPLY_DIFF:
                        result.warnings.append(
                            "asked to apply as a difference from vanilla, but there is no "
                            "vanilla copy to compare against - ran its SQL directly instead"
                        )
                    for patch in mod.patches:
                        patch_result = patch.apply(con, mod.id)
                        result.rows_changed += patch_result.rows_changed
                        result.warnings.extend(patch_result.warnings)
                result.ok = True
                report.results.append(result)
                for warning in result.warnings:
                    log.warn(f"{mod.id}: {warning}")
                log.info(f"{mod.id}: {result.rows_changed} row(s) changed")

            con.execute("COMMIT")
        except (ApplyError, ValidationError, sqlite3.Error) as exc:
            con.execute("ROLLBACK")
            mod_id = getattr(exc, "mod_id", "")
            reason = getattr(exc, "reason", str(exc))
            report.failed_mod = mod_id
            report.error = reason
            report.results = [r for r in report.results if r.mod_id != mod_id]
            report.results.append(ModApplyResult(mod_id=mod_id or "(unknown)", ok=False, error=reason))
            log.error(f"rolled back - {mod_id or 'apply'}: {reason}")
            report.duration_seconds = time.monotonic() - started
            return report
    finally:
        con.close()

    # Only once the transaction is committed do the snapshots become the truth.
    for captured in pending_snapshots:
        # Re-applying a mod that is already on this database would capture the
        # state it produced, and reverting would then restore the modded values
        # rather than the originals. Keep the snapshot taken the first time.
        if captured.mod_id in (keep_snapshots or set()) and snapshot_module.snapshot_path(
            snapshots_dir, captured.mod_id
        ).is_file():
            continue
        try:
            snapshot_module.save(snapshots_dir, captured)
        except OSError as exc:
            log.warn(f"{captured.mod_id}: snapshot could not be written ({exc}); revert will "
                     "have to use the .db backup")

    report.ok = True
    report.db_sha256_after = sha256_file(db_path)
    report.duration_seconds = time.monotonic() - started
    log.info(report.summary_line())
    return report


def _validation_warnings(validation: ValidationReport) -> list[str]:
    messages = []
    for result in validation.results:
        messages.extend(f"{result.mod_id}: {warning}" for warning in result.warnings)
    messages.extend(conflict.message() for conflict in validation.conflicts.conflicts)
    messages.extend(
        requirement.message() for requirement in validation.conflicts.missing_requirements
    )
    messages.extend(problem.message() for problem in validation.conflicts.order_problems)
    return messages


@dataclass
class RevertResult:
    mod_id: str
    ok: bool = False
    rows_restored: int = 0
    method: str = ""
    warnings: list[str] = field(default_factory=list)
    error: str = ""


def revert_mods(
    db_path: Path,
    mod_ids: list[str],
    *,
    snapshots_dir: Path,
    mods_by_id: dict[str, Mod] | None = None,
    log: SessionLog | None = None,
) -> list[RevertResult]:
    """Undo mods, newest first, in one transaction.

    A mod that ships ``inverse.sql`` uses it; everything else replays its
    pre-apply snapshot. Either way the whole batch is atomic.
    """
    log = log or SessionLog()
    mods_by_id = mods_by_id or {}
    results = [RevertResult(mod_id=mod_id) for mod_id in mod_ids]

    plans: list[tuple[RevertResult, Mod | None, Snapshot | None]] = []
    for result in results:
        mod = mods_by_id.get(result.mod_id)
        try:
            stored = snapshot_module.load(snapshots_dir, result.mod_id)
        except RevertError as exc:
            stored = None
            result.warnings.append(str(exc))
        if mod is not None and mod.has_inverse_sql:
            result.method = "inverse.sql"
        elif stored is not None:
            result.method = "snapshot"
        else:
            result.error = (
                "no snapshot on file and no inverse.sql - restore a .db backup instead"
            )
            log.error(f"{result.mod_id}: {result.error}")
            continue
        plans.append((result, mod, stored))

    if not plans:
        return results

    try:
        con = connect(Path(db_path))
    except sqlite3.Error as exc:
        for result in results:
            if not result.error:
                result.error = f"could not open the database: {exc}"
        return results

    try:
        begin_immediate(con)
        # Reverse order: the most recently layered mod comes off first.
        for result, mod, stored in reversed(plans):
            if result.method == "inverse.sql" and mod is not None:
                sql = mod.inverse_sql.read_text(encoding="utf-8-sig")
                for statement in split_statements(sql):
                    cursor = con.execute(statement)
                    if cursor.rowcount and cursor.rowcount > 0:
                        result.rows_restored += cursor.rowcount
            else:
                restored, warnings = snapshot_module.restore(con, stored)
                result.rows_restored += restored
                result.warnings.extend(warnings)
            result.ok = True
        con.execute("COMMIT")
    except (sqlite3.Error, RevertError) as exc:
        con.execute("ROLLBACK")
        for result, _, _ in plans:
            result.ok = False
            result.error = str(exc)
        log.error(f"revert rolled back: {exc}")
        return results
    finally:
        con.close()

    for result in results:
        if result.ok:
            snapshot_module.discard(snapshots_dir, result.mod_id)
            log.info(
                f"{result.mod_id}: reverted via {result.method} "
                f"({result.rows_restored} row(s) restored)"
            )
            for warning in result.warnings:
                log.warn(f"{result.mod_id}: {warning}")
    return results


def previews_from_delta(delta) -> list[DiffPreview]:
    """Row-level preview built from a diff against vanilla.

    Raw SQL used to preview as "rewritten by raw SQL" because nothing could be
    told about it without running it. Having run it against a throwaway vanilla
    copy, we can show precisely which values change.
    """
    previews: list[DiffPreview] = []
    for table_delta in delta.tables:
        rows: list[DiffRow] = []
        for update in table_delta.updates[:PREVIEW_ROW_LIMIT]:
            key = ", ".join(str(k) for k in update.key)
            rows.append(
                DiffRow(
                    key=key,
                    before=", ".join(f"{c}={update.before.get(c)!r}" for c in update.changes),
                    after=", ".join(f"{c}={v!r}" for c, v in update.changes.items()),
                )
            )
        notes = []
        if len(table_delta.updates) > len(rows):
            notes.append(f"... and {len(table_delta.updates) - len(rows)} more changed row(s)")
        if table_delta.inserts:
            notes.append(f"adds {len(table_delta.inserts)} new row(s)")
        if table_delta.deletes:
            notes.append(f"removes {len(table_delta.deletes)} row(s)")
        previews.append(
            DiffPreview(
                patch_summary=f"{table_delta.table}: {table_delta.cell_count} value(s) changed",
                table=table_delta.table,
                total_rows=len(table_delta.updates),
                rows=rows,
                note="\n".join(notes),
            )
        )
    if not previews:
        previews.append(
            DiffPreview("This mod changes nothing on the current database", "", 0)
        )
    return previews


def preview_mods(db_path: Path, mods: list[Mod]) -> dict[str, list[DiffPreview]]:
    """Row-level before/after for the diff panel. Read-only."""
    previews: dict[str, list[DiffPreview]] = {}
    con = connect(Path(db_path), read_only=True)
    try:
        for mod in mods:
            entries: list[DiffPreview] = []
            for patch in mod.patches:
                try:
                    entries.append(patch.preview(con))
                except (sqlite3.Error, ValidationError) as exc:
                    entries.append(
                        DiffPreview(
                            patch_summary=f"(preview unavailable: {exc})",
                            table="",
                            total_rows=0,
                        )
                    )
            previews[mod.id] = entries
    finally:
        con.close()
    return previews


def record_apply(state: State, mods: list[Mod], report: ApplyReport) -> None:
    """Write the applied-mod bookkeeping into state after a successful apply."""
    by_id = {mod.id: mod for mod in mods}
    now = datetime.now().isoformat(timespec="seconds")
    for result in report.results:
        if not result.ok:
            continue
        mod = by_id.get(result.mod_id)
        state.record_applied(
            result.mod_id,
            AppliedRecord(
                applied_at=now,
                rows_changed=result.rows_changed,
                tables=result.tables,
                version=mod.version if mod else "",
                name=mod.name if mod else result.mod_id,
            ),
        )


def orphaned_snapshots(snapshots_dir: Path, installed_ids: set[str]) -> list[str]:
    """Mods with a snapshot on disk whose folder has been deleted."""
    return [
        mod_id
        for mod_id in snapshot_module.known_mod_ids(snapshots_dir)
        if mod_id not in installed_ids
    ]
