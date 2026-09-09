"""The headless controller.

Both the GUI and the CLI drive this object; neither of them talks to the runner,
the state file or the watchdog directly. Everything it does is synchronous and
returns a report, so callers decide how to present the outcome.
"""

from __future__ import annotations

from pathlib import Path

import sqlite3

from . import backup as backup_module
from .conflict import ConflictReport, analyze
from .mod import Mod
from .mod_loader import ScanResult, resolve_order, scan_mods
from .paths import AppPaths
from .patch import DiffPreview
from .runner import (
    ApplyReport,
    RevertResult,
    apply_mods,
    orphaned_snapshots,
    preview_mods,
    record_apply,
    revert_mods,
)
from .session_log import SessionLog, rotate_logs
from .sqlutil import connect
from .state import State
from .validator import ValidationReport, validate
from .watchdog import STATUS_MISSING, STATUS_STALE, DbStatus, DbWatcher

# Per-mod status shown in the list.
MOD_APPLIED = "applied"  # green
MOD_PENDING = "pending"  # yellow - enabled but not on the current DB
MOD_FAILED = "failed"  # red
MOD_DISABLED = "disabled"


class Manager:
    def __init__(self, paths: AppPaths | None = None, *, echo_log: bool = False):
        self.paths = (paths or AppPaths.default()).ensure()
        self.log = SessionLog(self.paths.logs_dir, echo=echo_log)
        rotate_logs(self.paths.logs_dir)
        self.state = State.load(self.paths.state_file)
        self.scan = ScanResult()
        self.watcher = DbWatcher(
            self.state.db_path or None,
            self.state.db_sha256_at_last_save,
            self.state.db_mtime_at_last_save,
        )
        self.last_apply: ApplyReport | None = None
        self.last_validation: ValidationReport | None = None
        self._conflict_cache: tuple | None = None
        self.rescan()

    # -- database selection ----------------------------------------------

    @property
    def db_path(self) -> Path | None:
        return Path(self.state.db_path) if self.state.db_path else None

    def set_db_path(self, path: Path | str) -> None:
        path = Path(path)
        changed = str(path) != self.state.db_path
        self.state.db_path = str(path)
        if changed:
            # A different database has its own fingerprint and its own history.
            self.state.db_sha256_at_last_save = ""
            self.state.db_mtime_at_last_save = 0.0
        self.watcher.set_db(
            path, self.state.db_sha256_at_last_save, self.state.db_mtime_at_last_save
        )
        self.state.save()
        self.log.info(f"Database set to {path}")

    def db_status(self) -> DbStatus:
        return self.watcher.check()

    # -- mods -------------------------------------------------------------

    def rescan(self) -> ScanResult:
        self.scan = scan_mods(self.paths.mods_dir)
        for failure in self.scan.failures:
            self.log.warn(f"{failure.folder}: {failure.reason}")
        self.log.info(f"Loaded {len(self.scan.mods)} mod(s) from {self.paths.mods_dir}")
        return self.scan

    @property
    def mods(self) -> list[Mod]:
        return self.scan.mods

    def installed_ids(self) -> set[str]:
        return {mod.id for mod in self.scan.mods}

    def enabled_mods(self) -> list[Mod]:
        by_id = self.scan.by_id
        return resolve_order(
            [by_id[mod_id] for mod_id in self.state.enabled_mods if mod_id in by_id]
        )

    def set_enabled(self, mod_id: str, enabled: bool) -> None:
        self.state.set_enabled(mod_id, enabled)

    def mod_status(self, mod_id: str) -> str:
        if not self.state.is_enabled(mod_id):
            return MOD_DISABLED
        if self.last_apply is not None:
            result = self.last_apply.for_mod(mod_id)
            if result is not None and not result.ok:
                return MOD_FAILED
        if self.last_validation is not None:
            validation = self.last_validation.for_mod(mod_id)
            if validation is not None and not validation.ok:
                return MOD_FAILED
        if mod_id not in self.state.applied:
            return MOD_PENDING
        return MOD_APPLIED if self.db_status().state != STATUS_STALE else MOD_PENDING

    def conflicts(self) -> ConflictReport:
        """Conflicts among the enabled mods, row-accurate when the DB is there.

        Resolving raw SQL down to rowids means querying the database, which is
        too much to redo on every checkbox tick - so the answer is cached until
        the enabled list or the database itself changes.
        """
        enabled = self.enabled_mods()
        key = (tuple(mod.id for mod in enabled), self._db_fingerprint())
        if self._conflict_cache is not None and self._conflict_cache[0] == key:
            return self._conflict_cache[1]

        con = None
        if self.db_path and self.db_path.is_file():
            try:
                con = connect(self.db_path, read_only=True)
            except sqlite3.Error:
                con = None
        try:
            report = analyze(enabled, self.installed_ids(), con)
        finally:
            if con is not None:
                con.close()
        self._conflict_cache = (key, report)
        return report

    def _db_fingerprint(self) -> tuple:
        """Cheap stand-in for "has the database changed": path, size, mtime."""
        if not self.db_path:
            return ()
        try:
            stat = self.db_path.stat()
        except OSError:
            return (str(self.db_path),)
        return (str(self.db_path), stat.st_size, stat.st_mtime)

    def previews(self, mods: list[Mod] | None = None) -> dict[str, list[DiffPreview]]:
        if not self.db_path or not self.db_path.is_file():
            return {}
        return preview_mods(self.db_path, mods if mods is not None else self.enabled_mods())

    # -- validate / apply --------------------------------------------------

    def validate(self) -> ValidationReport:
        if not self.db_path:
            report = ValidationReport(fatal="no database selected")
            self.last_validation = report
            return report
        self.last_validation = validate(self.db_path, self.enabled_mods(), self.installed_ids())
        return self.last_validation

    def _apply(self, *, take_backup: bool) -> ApplyReport:
        if not self.db_path or not self.db_path.is_file():
            report = ApplyReport(error="no database selected - point the manager at masters.db")
            self.log.error(report.error)
            self.last_apply = report
            return report

        mods = self.enabled_mods()
        if take_backup:
            try:
                result = backup_module.take_backups(
                    self.db_path, self.paths.backups_dir, self.state.settings.keep_backups
                )
                if result.original is not None:
                    self.log.info(
                        f"Wrote {result.original.name} - this is the copy of the database "
                        "from before any mod was applied, and it will never be overwritten"
                    )
                self.log.info(f"Backed up to {result.rolling.name} and {result.dated.name}")
                for removed in result.removed:
                    self.log.info(f"Rotated out old backup {removed.name}")
            except OSError as exc:
                report = ApplyReport(error=f"backup failed, nothing applied: {exc}")
                self.log.error(report.error)
                self.last_apply = report
                return report

        report = apply_mods(
            self.db_path,
            mods,
            snapshots_dir=self.paths.snapshots_dir,
            log=self.log,
            installed_ids=self.installed_ids(),
        )
        self.last_apply = report
        self.last_validation = report.validation

        if report.ok:
            record_apply(self.state, mods, report)
            self.state.stamp_db(self.db_path)
            self.watcher.stamp(
                self.state.db_sha256_at_last_save, self.state.db_mtime_at_last_save
            )
        self.state.save()
        return report

    def save_mod_list(self) -> ApplyReport:
        """The main action: back up, validate, apply, stamp the DB fingerprint."""
        self.log.info("Save Mod List: backing up and applying")
        return self._apply(take_backup=True)

    def reapply_all(self) -> ApplyReport:
        """Re-run the enabled list against a DB that changed. No new backup."""
        self.log.info("Re-apply All")
        return self._apply(take_backup=False)

    def on_db_changed(self, status: DbStatus) -> ApplyReport | None:
        """Watchdog hook. Re-applies automatically when that setting is on."""
        if status.state == STATUS_MISSING:
            self.log.warn(status.label)
            return None
        self.log.warn(status.label)
        if not self.state.settings.auto_reapply:
            return None
        if not self.state.enabled_mods:
            return None
        return self.reapply_all()

    # -- revert ------------------------------------------------------------

    def revert(self, mod_ids: list[str], *, disable: bool = True) -> list[RevertResult]:
        if not self.db_path:
            return [RevertResult(mod_id=m, error="no database selected") for m in mod_ids]
        results = revert_mods(
            self.db_path,
            mod_ids,
            snapshots_dir=self.paths.snapshots_dir,
            mods_by_id=self.scan.by_id,
            log=self.log,
        )
        for result in results:
            if not result.ok:
                continue
            self.state.forget_applied(result.mod_id)
            if disable:
                self.state.set_enabled(result.mod_id, False)
        if any(result.ok for result in results):
            self.state.stamp_db(self.db_path)
            self.watcher.stamp(
                self.state.db_sha256_at_last_save, self.state.db_mtime_at_last_save
            )
        self.state.save()
        return results

    def deleted_mods(self) -> list[str]:
        """Mods that were applied but whose folder has since been removed."""
        installed = self.installed_ids()
        known = set(orphaned_snapshots(self.paths.snapshots_dir, installed))
        known |= {mod_id for mod_id in self.state.applied if mod_id not in installed}
        return sorted(known)

    def forget(self, mod_ids: list[str]) -> None:
        """Drop a deleted mod's bookkeeping without touching the database."""
        from . import snapshot as snapshot_module

        for mod_id in mod_ids:
            self.state.forget_applied(mod_id)
            self.state.set_enabled(mod_id, False)
            snapshot_module.discard(self.paths.snapshots_dir, mod_id)
        self.state.save()

    # -- modpacks ----------------------------------------------------------

    def save_modpack(self, name: str) -> None:
        self.state.save_modpack(name)
        self.state.save()
        self.log.info(f"Saved modpack {name!r} ({len(self.state.enabled_mods)} mod(s))")

    def load_modpack(self, name: str) -> list[str]:
        mod_ids = self.state.load_modpack(name)
        self.state.save()
        self.log.info(f"Loaded modpack {name!r}")
        return mod_ids

    def delete_modpack(self, name: str) -> None:
        self.state.delete_modpack(name)
        self.state.save()

    # -- backups -----------------------------------------------------------

    def backups(self) -> list[backup_module.BackupEntry]:
        """Every restorable copy, including the rolling one beside the database."""
        return backup_module.list_backups(self.paths.backups_dir, self.db_path)

    def dated_backups(self) -> list[backup_module.BackupEntry]:
        return [entry for entry in self.backups() if entry.kind == "dated"]

    def restore_backup(self, backup: "backup_module.BackupEntry | Path") -> None:
        if not self.db_path:
            raise RuntimeError("no database selected")
        backup_path = getattr(backup, "path", backup)
        backup_module.restore_backup(backup_path, self.db_path)
        self.state.applied.clear()
        self.state.stamp_db(self.db_path)
        self.watcher.stamp(self.state.db_sha256_at_last_save, self.state.db_mtime_at_last_save)
        self.state.save()
        self.log.info(f"Restored {Path(backup_path).name} over {self.db_path.name}")
