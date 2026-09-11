"""The headless controller.

Both the GUI and the CLI drive this object; neither of them talks to the runner,
the state file or the watchdog directly. Everything it does is synchronous and
returns a report, so callers decide how to present the outcome.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import sqlite3

from . import asset_runner
from . import backup as backup_module
from . import dbdiff
from . import install as install_module
from . import modedit
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

    # -- game folder (for asset_file mods) ------------------------------

    @property
    def asset_game_root(self) -> Path | None:
        """The folder that holds BrgGame - explicit override, else derived from db_path."""
        override = self.state.game_root_override
        if override:
            candidate = Path(override)
            return candidate if candidate.is_dir() else None
        return asset_runner.game_root_for(self.db_path)

    def set_game_root_override(self, path: Path | str | None) -> None:
        self.state.game_root_override = str(path) if path else ""
        self.state.save()
        self.log.info(
            f"Game folder set to {path}" if path else "Game folder override cleared"
        )

    def asset_backups(self) -> list[asset_runner.AssetBackupEntry]:
        return asset_runner.list_asset_backups(self.paths.backups_dir)

    def restore_all_asset_backups(self) -> asset_runner.AssetApplyReport:
        game_root = self.asset_game_root
        if game_root is None:
            raise RuntimeError(
                "the game folder could not be found - set it with Tools > Set game folder"
            )
        return asset_runner.restore_all_originals(
            game_root, self.paths.backups_dir, log=self.log
        )

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
        """Enabled mods in load order, each trimmed to the parts still switched on.

        The trimming happens here, once, so everything downstream - validation,
        snapshots, conflicts, apply - sees a mod that simply does not contain
        the parts the player turned off, and needs to know nothing about them.
        """
        by_id = self.scan.by_id
        mods = [by_id[mod_id] for mod_id in self.state.enabled_mods if mod_id in by_id]
        return [self.active_mod(mod) for mod in mods]

    def active_mod(self, mod: Mod) -> Mod:
        """``mod`` without the patches the player has switched off."""
        off = self.state.disabled_patches.get(mod.id)
        if not off:
            return mod
        kept = [patch for patch in mod.patches if patch.key not in off]
        return replace(mod, patches=kept)

    def set_part_enabled(self, mod_id: str, patch_key: str, enabled: bool) -> None:
        """Switch one part of a mod on or off, without touching the rest."""
        self.state.set_part_enabled(mod_id, patch_key, enabled)
        self._conflict_cache = None
        self._delta_cache.pop(mod_id, None)

    def move_mod(self, mod_id: str, delta: int) -> bool:
        """Move a mod up or down the load order. Returns True if it moved."""
        moved = self.state.move(mod_id, delta)
        if moved:
            self._conflict_cache = None
            self.state.save()
        return moved

    def listed_mods(self) -> list[Mod]:
        """Every installed mod: enabled ones in load order, then the rest.

        These are the whole mods, parts and all - the list has to draw a switch
        for every part, including the ones currently off.
        """
        by_id = self.scan.by_id
        ordered = [by_id[mod_id] for mod_id in self.state.enabled_mods if mod_id in by_id]
        enabled_ids = {mod.id for mod in ordered}
        return ordered + [mod for mod in self.scan.mods if mod.id not in enabled_ids]

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
        asset_mods = [mod for mod in mods if mod.asset_targets()]

        # Nothing is written to disk while the game holds its files and its
        # database open - that means a locked .upk or a half-written masters.db.
        if asset_mods:
            reason = asset_runner.game_lock_reason(self.db_path)
            if reason:
                report = ApplyReport(error=reason)
                self.log.error(reason)
                self.last_apply = report
                return report

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

        if take_backup:
            self._undo_switched_off_mods()

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

        # A copy of the database to fall back to if the file copies fail after
        # the DB transaction has already committed. Save Mod List always takes a
        # backup; Re-apply does not, so keep our own either way.
        pre_asset_db: Path | None = None
        if asset_mods:
            pre_asset_db = self.paths.backups_dir / ".pre_asset_apply.db"
            try:
                backup_module.copy_database(self.db_path, pre_asset_db)
            except OSError as exc:
                report = ApplyReport(error=f"could not stage a rollback copy, nothing applied: {exc}")
                self.log.error(report.error)
                self.last_apply = report
                return report

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

        if report.ok and asset_mods:
            self._apply_asset_files(mods, report, pre_asset_db)
        if pre_asset_db is not None:
            pre_asset_db.unlink(missing_ok=True)

        if report.ok:
            record_apply(self.state, mods, report)
            self.state.stamp_db(self.db_path)
            self.watcher.stamp(
                self.state.db_sha256_at_last_save, self.state.db_mtime_at_last_save
            )
        self.state.save()
        return report

    def _apply_asset_files(self, mods, report: ApplyReport, db_rollback: Path | None) -> None:
        """Copy game files after the DB commit. On failure, roll the DB back too."""
        game_root = self.asset_game_root
        if game_root is None:
            report.ok = False
            report.error = (
                "the game folder could not be found - set it with Tools > Set game folder, "
                "or point the manager at the masters.db inside BrgGame/Content"
            )
            self.log.error(report.error)
        else:
            ares = asset_runner.apply_asset_patches(
                mods, game_root, self.paths.backups_dir, log=self.log
            )
            report.asset_report = ares
            if not ares.ok:
                report.ok = False
                report.error = ares.error or "game files could not be written"

        if not report.ok and db_rollback is not None:
            try:
                backup_module.restore_backup(db_rollback, self.db_path)
                self.log.warn(
                    "Rolled the database back - game files could not be applied, so the "
                    "whole save was undone."
                )
            except OSError as exc:
                self.log.error(f"could not roll the database back: {exc}")

    def _undo_switched_off_mods(self) -> None:
        """Put back what a mod wrote once it stops being ticked.

        Unticking used to mean only "do not apply this next time", so any value
        no other enabled mod rewrote stayed changed while the mod showed as off
        - which is not what switching something off looks like it should do.

        Only on Save Mod List, and only against the database these mods were
        actually applied to. When the game has replaced the file, the snapshots
        describe values that are no longer there and replaying them would write
        an older version's numbers over a newer one's.
        """
        stranded = []
        for mod_id, record in self.state.applied.items():
            if not self.state.is_enabled(mod_id):
                stranded.append(mod_id)
                continue
            # Still ticked, but a part of it has been switched off since it ran.
            # Undoing the whole mod puts everything it wrote back; the parts
            # still switched on are re-applied a moment later in the same save.
            mod = self.scan.get(mod_id)
            if mod is None or not record.parts:
                continue
            if [p.key for p in self.active_mod(mod).patches] != list(record.parts):
                stranded.append(mod_id)

        if not stranded:
            return
        if self.db_status().state == STATUS_STALE:
            self.log.warn(
                f"{len(stranded)} switched-off mod(s) are still applied, but the "
                "database has been replaced since - leaving them alone. Use "
                "Tools > Restore a backup if you need to get back to stock."
            )
            return

        # In the order they were applied: revert_mods unwinds the list it is
        # given back to front, so handing it newest-first would take the bottom
        # mod off before the one layered over it and restore the wrong values.
        order = list(self.state.applied)
        stranded.sort(key=order.index)
        self.log.info(
            "Undoing " + ", ".join(stranded) + " - what was applied no longer "
            "matches what is switched on"
        )
        for result in self.revert(stranded, disable=False):
            if not result.ok:
                self.log.warn(f"{result.mod_id}: could not be undone ({result.error})")

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

        asset_targets: set[str] = set()
        for mod_id in mod_ids:
            mod = self.scan.get(mod_id)
            if mod is not None:
                asset_targets.update(mod.asset_targets())
        if asset_targets:
            reason = asset_runner.game_lock_reason(self.db_path)
            if reason:
                self.log.error(reason)
                return [RevertResult(mod_id=m, error=reason) for m in mod_ids]

        results = revert_mods(
            self.db_path,
            mod_ids,
            snapshots_dir=self.paths.snapshots_dir,
            mods_by_id=self.scan.by_id,
            log=self.log,
        )
        reverted_ok = {result.mod_id for result in results if result.ok}
        for result in results:
            if not result.ok:
                continue
            self.state.forget_applied(result.mod_id)
            if disable:
                self.state.set_enabled(result.mod_id, False)

        # Put the reverted mods' game files back to stock (or delete the ones
        # they added), then re-apply whatever is still enabled so a file another
        # mod also claims comes straight back. Same trick the DB side uses for a
        # whole reworked masters.db - no per-mod incremental undo of an opaque blob.
        if asset_targets and reverted_ok:
            game_root = self.asset_game_root
            if game_root is None:
                self.log.warn(
                    "Reverted the database, but the game folder was not found - game files "
                    "for the reverted mod(s) were left in place. Set it with Tools > Set game folder."
                )
            else:
                asset_runner.restore_targets(
                    asset_targets, game_root, self.paths.backups_dir, log=self.log
                )
                still_enabled = [m for m in self.enabled_mods() if m.id not in reverted_ok]
                asset_runner.apply_asset_patches(
                    still_enabled, game_root, self.paths.backups_dir, log=self.log
                )

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
        # The patch list is part of the key: switching a part off changes what
        # the mod does without touching a single file on disk.
        key = (
            stamp,
            vanilla.stat().st_size,
            vanilla.stat().st_mtime_ns,
            tuple(patch.key for patch in mod.patches),
        )
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

    def edit_mod(
        self,
        mod_id: str,
        name: str,
        description: str | None = None,
        author: str | None = None,
        version: str | None = None,
        readme: str | None = None,
    ) -> Mod | None:
        """Rename and re-describe a mod in place. Returns it under its new id."""
        mod = self.scan.get(mod_id)
        if mod is None:
            raise modedit.ModEditError(f"no such mod: {mod_id}")
        new_id = modedit.edit_mod(
            mod,
            state=self.state,
            snapshots_dir=self.paths.snapshots_dir,
            name=name,
            description=description,
            author=author,
            version=version,
            readme=readme,
        )
        if new_id != mod_id:
            self.log.info(f"Renamed {mod_id} to {new_id}")
        else:
            self.log.info(f"Updated the details of {new_id}")
        self._conflict_cache = None
        self._delta_cache.pop(mod_id, None)
        self.rescan()
        return self.scan.get(new_id)

    def install_database(
        self,
        candidate: "install_module.InstallCandidate",
        name: str,
        description: str = "",
        author: str = "",
        version: str = "1.0.0",
        overwrite: bool = False,
        selection: dict | None = None,
        split_by_table: bool = False,
        requires: list[str] | None = None,
    ) -> list[Mod]:
        """Install a modded database as one mod per table. All arrive disabled."""
        folders = install_module.install_database(
            candidate,
            self.paths.mods_dir,
            name,
            description=description,
            author=author,
            version=version,
            vanilla=self.vanilla_path,
            overwrite=overwrite,
            selection=selection,
            split_by_table=split_by_table,
            requires=requires,
        )
        self.log.info(
            f"Installed {len(folders)} mod(s) from {candidate.source.name}: "
            + ", ".join(f.name for f in folders)
        )
        self._conflict_cache = None
        self.rescan()
        return [mod for mod in (self.scan.get(f.name) for f in folders) if mod]

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
