"""The headless controller.

Both the GUI and the CLI drive this object; neither of them talks to the runner,
the state file or the watchdog directly. Everything it does is synchronous and
returns a report, so callers decide how to present the outcome.
"""

from __future__ import annotations

from pathlib import Path

import sqlite3

from . import backup as backup_module
from . import dbdiff
from . import install as install_module
from .conflict import ConflictReport, analyze
from .mod import Mod
from .mod_loader import ScanResult, scan_mods
from .paths import AppPaths
from .patch import DiffPreview
from .runner import (
    ApplyReport,
    RevertResult,
    apply_mods,
    orphaned_snapshots,
    preview_mods,
    previews_from_delta,
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
        self._delta_cache: dict[str, tuple] = {}
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
        self.ensure_original_backup()

    def ensure_original_backup(self) -> Path | None:
        """Keep an untouched copy the moment a database is chosen.

        Waiting until the first save meant anything that happened in between -
        a game update, another tool, a hand edit - ended up baked into the
        "original". Taking it now captures the file in the state the user was
        just told to make sure it was in. Never overwrites an existing copy.
        """
        if not self.db_path or not self.db_path.is_file():
            return None
        original = backup_module.original_backup_path(self.db_path)
        if original.exists():
            return None
        try:
            backup_module.copy_database(self.db_path, original)
        except (OSError, RuntimeError) as exc:
            self.log.warn(
                f"Could not keep an untouched copy as {original.name} ({exc}). "
                "Check the folder is writable - without it there is no way back to stock."
            )
            return None
        self.log.info(
            f"Kept an untouched copy as {original.name} - this is your way back to "
            "stock and it will never be overwritten"
        )
        return original

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
        """Enabled mods in load order - first applied first, last one wins."""
        by_id = self.scan.by_id
        return [by_id[mod_id] for mod_id in self.state.enabled_mods if mod_id in by_id]

    def move_mod(self, mod_id: str, delta: int) -> bool:
        """Move a mod up or down the load order. Returns True if it moved."""
        moved = self.state.move(mod_id, delta)
        if moved:
            self._conflict_cache = None
            self.state.save()
        return moved

    def listed_mods(self) -> list[Mod]:
        """Every installed mod: enabled ones in load order, then the rest."""
        enabled = self.enabled_mods()
        enabled_ids = {mod.id for mod in enabled}
        return enabled + [mod for mod in self.scan.mods if mod.id not in enabled_ids]

    def set_enabled(self, mod_id: str, enabled: bool) -> None:
        self.state.set_enabled(mod_id, enabled)
        self._conflict_cache = None

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
        """What each mod would change, exactly where that can be worked out."""
        if not self.db_path or not self.db_path.is_file():
            return {}
        wanted = mods if mods is not None else self.enabled_mods()

        result: dict[str, list[DiffPreview]] = {}
        fallback: list[Mod] = []
        for mod in wanted:
            delta = self.mod_delta(mod)
            if delta is None:
                fallback.append(mod)
            else:
                result[mod.id] = previews_from_delta(delta)
        if fallback:
            result.update(preview_mods(self.db_path, fallback))
        return result

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

        deltas = {}
        for mod in mods:
            delta = self.mod_delta(mod)
            if delta is not None:
                deltas[mod.id] = delta

        # If the database is still the one these mods were applied to, their
        # existing snapshots are the only record of the pre-mod values - a
        # second apply must not overwrite them. When the database has been
        # replaced, a fresh snapshot is the right one.
        keep_snapshots = set()
        if self.db_status().state != STATUS_STALE:
            keep_snapshots = {mod.id for mod in mods if mod.id in self.state.applied}

        report = apply_mods(
            self.db_path,
            mods,
            snapshots_dir=self.paths.snapshots_dir,
            log=self.log,
            installed_ids=self.installed_ids(),
            deltas=deltas,
            keep_snapshots=keep_snapshots,
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

    # -- installing new mods ----------------------------------------------

    @property
    def vanilla_path(self) -> Path | None:
        """The untouched copy of the database, if one has been kept."""
        if not self.db_path:
            return None
        original = backup_module.original_backup_path(self.db_path)
        return original if original.is_file() else None

    def mod_delta(self, mod: Mod):
        """Exactly what this mod changes, measured against the vanilla copy.

        Costs a database copy plus a diff, so the answer is cached against the
        mod's files and the vanilla database - repeat calls are free until one
        of them changes. Returns None when there is no vanilla to compare with.
        """
        vanilla = self.vanilla_path
        if vanilla is None:
            return None

        stamp = max(
            (f.stat().st_mtime_ns for f in mod.folder.rglob("*") if f.is_file()), default=0
        )
        key = (stamp, vanilla.stat().st_size, vanilla.stat().st_mtime_ns)
        cached = self._delta_cache.get(mod.id)
        if cached is not None and cached[0] == key:
            return cached[1]

        try:
            delta = dbdiff.delta_for_mod(vanilla, mod)
        except Exception as exc:
            # Never let this break an apply - callers fall back to the old path.
            self.log.warn(f"{mod.id}: could not work out its exact changes ({exc})")
            return None
        self._delta_cache[mod.id] = (key, delta)
        return delta

    def inspect_install(self, source: Path) -> "install_module.InstallCandidate":
        """Work out what a dropped file is. Writes nothing."""
        return install_module.inspect(Path(source), self.vanilla_path)

    def install(
        self,
        candidate: "install_module.InstallCandidate",
        name: str,
        description: str = "",
        author: str = "",
        version: str = "1.0.0",
        overwrite: bool = False,
    ) -> Mod | None:
        """Write the mod folder and rescan. The new mod arrives disabled."""
        folder = install_module.install(
            candidate,
            self.paths.mods_dir,
            name,
            description=description,
            author=author,
            version=version,
            vanilla=self.vanilla_path,
            overwrite=overwrite,
        )
        self.log.info(f"Installed {folder.name} from {candidate.source.name}")
        self._conflict_cache = None
        self.rescan()
        return self.scan.get(folder.name)

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
