"""The headless controller.

Both the GUI and the CLI drive this object; neither of them talks to the runner,
the state file or the watchdog directly. Everything it does is synchronous and
returns a report, so callers decide how to present the outcome.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field, fields, replace
from pathlib import Path

import shutil
import sqlite3

from . import adopt
from . import asset_runner
from . import browse
from . import backup as backup_module
from . import db_record
from . import dbdiff
from . import exe_check_off
from . import validator
from . import vanilla_capture
from . import vanilla_library
from . import vetted
from . import install as install_module
from . import migrations
from . import modedit
from . import snapshot as snapshot_module
from .conflict import ConflictReport, analyze
from .errors import ModManagerError
from .mod import Mod
from .mod_loader import ScanResult, scan_mods
from .paths import AppPaths
from .patch import DiffPreview
from .progress import Progress, ensure as ensure_progress
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

DELTAS_KEPT_PER_MOD = 6


@dataclass(frozen=True)
class FileCheckStatus:
    """What the executable's file list says right now.

    ``reason`` is filled in when nothing could be read - no game folder, no
    executable, or a layout this does not know - and the rest is then empty.
    Nothing is guessed: a status that cannot be established says so.
    """

    exe: Path | None = None
    checked: list[str] = field(default_factory=list)
    switched_off: list[str] = field(default_factory=list)
    reason: str = ""

    @property
    def readable(self) -> bool:
        return not self.reason

    @property
    def all_off(self) -> bool:
        return self.readable and not self.checked and bool(self.switched_off)


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
        # Merged and renamed shipped mods: move the old folders aside before
        # the scan, then point the saved state at their replacements.
        migrations.retire_shipped_folders(self.paths.mods_dir, self.log)
        self.rescan()
        if migrations.migrate_state(
            self.state, self.installed_ids(), self.paths.snapshots_dir, self.log
        ):
            self.state.save()

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
        self.capture_clean_copy()

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

    # -- the game's own file check ----------------------------------------

    def file_check_status(self) -> "FileCheckStatus":
        """What the game currently verifies, and what has been switched off."""
        game_root = self.asset_game_root
        if game_root is None:
            return FileCheckStatus(reason="the game folder could not be found")
        exe = Path(game_root) / vetted.GAME_EXE
        if not exe.is_file():
            return FileCheckStatus(reason=f"{vetted.GAME_EXE} is not in the game folder")
        try:
            raw = exe.read_bytes()
            off = exe_check_off.switched_off_names(raw)
            checked = exe_check_off.checked_names(raw)
        except Exception as exc:
            return FileCheckStatus(reason=f"the file list could not be read ({exc})")
        return FileCheckStatus(exe=exe, checked=checked, switched_off=off)

    def blocked_packages(self) -> list[str]:
        """Files an enabled mod replaces that the game still checks.

        The list inside the executable is read once here. Letting every mod read
        it meant parsing 45 MB per enabled mod, which the Hash Patcher then paid
        again on each refresh.
        """
        game_root = self.asset_game_root
        listed = validator.listed_names(game_root)
        if listed is None:
            return []
        names: list[str] = []
        seen: set[str] = set()
        for mod in self.enabled_mods():
            for name in validator.still_checked(mod, game_root, listed):
                if name.lower() not in seen:
                    seen.add(name.lower())
                    names.append(name)
        return names

    def _write_file_check(self, change, what: str) -> list[str]:
        """Shared path for switching off and back on. Returns what changed."""
        status = self.file_check_status()
        if status.reason:
            raise RuntimeError(status.reason)
        locked = asset_runner.game_lock_reason(self.db_path)
        if locked:
            raise RuntimeError(locked)
        asset_runner.keep_stock_executable(Path(status.exe).parents[2], self.paths.backups_dir)
        raw = Path(status.exe).read_bytes()
        changed, done = change(raw)
        if not done:
            return []
        asset_runner.write_executable(Path(status.exe).parents[2], changed)
        self.log.info(f"{what}: {len(done)} file(s) - {', '.join(done[:6])}"
                      + (" ..." if len(done) > 6 else ""))
        return done

    def switch_file_check_off(self, packages) -> list[str]:
        """Stop the game verifying those packages. Returns the ones changed."""
        return self._write_file_check(
            lambda raw: exe_check_off.switch_off(raw, packages),
            "Switched the game's file check off",
        )

    def switch_file_check_on(self, packages=None) -> list[str]:
        """Put packages back under the game's check. None means all of them."""
        return self._write_file_check(
            lambda raw: exe_check_off.switch_on(raw, packages),
            "Switched the game's file check back on",
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

    def configured(self, mod: Mod) -> Mod:
        """``mod`` with the values the player chose filled in."""
        return mod.with_settings(self.state.mod_settings.get(mod.id))

    def configured_mod(self, mod_id: str) -> Mod | None:
        mod = self.scan.get(mod_id)
        return self.configured(mod) if mod is not None else None

    def set_mod_setting(self, mod_id: str, setting_id: str, value) -> int | float:
        """Choose one of a mod's values. Raises ValueError with a readable reason.

        Takes effect on the next save, like ticking a box. A value equal to the
        default is simply forgotten.
        """
        mod = self.scan.get(mod_id)
        if mod is None:
            raise ModManagerError(f"no such mod: {mod_id}")
        setting = mod.setting(setting_id)
        if setting is None:
            raise ModManagerError(f"{mod.name} has no setting called {setting_id!r}")
        number = setting.coerce(value)
        chosen = dict(self.state.mod_settings.get(mod_id, {}))
        if number == setting.default:
            chosen.pop(setting_id, None)
        else:
            chosen[setting_id] = number
        if chosen:
            self.state.mod_settings[mod_id] = chosen
        else:
            self.state.mod_settings.pop(mod_id, None)
        self.state.save()
        self._delta_cache.pop(mod_id, None)
        self._conflict_cache = None
        self.log.info(f"{mod.name}: {setting.label} set to {setting.display(number)}")
        return number

    def reset_mod_settings(self, mod_id: str) -> None:
        """Put every value of a mod back to its default."""
        if self.state.mod_settings.pop(mod_id, None) is not None:
            self.state.save()
            self._delta_cache.pop(mod_id, None)
            self._conflict_cache = None

    def active_mod(self, mod: Mod) -> Mod:
        """``mod`` with its chosen values, and without the parts switched off."""
        mod = self.configured(mod)
        kept = [
            patch
            for patch in mod.patches
            if self.state.is_part_on(mod.id, patch.key, patch.ships_on)
        ]
        if len(kept) == len(mod.patches):
            return mod
        return replace(mod, patches=kept)

    def set_part_enabled(self, mod_id: str, patch_key: str, enabled: bool) -> None:
        """Switch one part of a mod on or off, without touching the rest."""
        mod = self.scan.get(mod_id)
        ships_on = True
        if mod is not None:
            for patch in mod.patches:
                if patch.key == patch_key:
                    ships_on = patch.ships_on
                    break
        self.state.set_part_enabled(mod_id, patch_key, enabled, ships_on=ships_on)
        self._conflict_cache = None
        self._delta_cache.pop(mod_id, None)

    def move_mod(self, mod_id: str, delta: int) -> bool:
        """Move a mod up or down the load order. Returns True if it moved."""
        moved = self.state.move(mod_id, delta)
        if moved:
            self._conflict_cache = None
            self.state.save()
        return moved

    def set_mod_position(self, mod_id: str, position: int) -> bool:
        """Move a mod to a given place in the load order. True if it moved."""
        moved = self.state.set_position(mod_id, position)
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
        everything = ordered + [mod for mod in self.scan.mods if mod.id not in enabled_ids]
        return [self.configured(mod) for mod in everything]

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
        self.last_validation = validate(
            self.db_path, self.enabled_mods(), self.installed_ids(), self.asset_game_root
        )
        return self.last_validation

    def _apply(self, *, take_backup: bool, progress: Progress | None = None) -> ApplyReport:
        progress = ensure_progress(progress)
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

        # Where each step sits on the bar. Rebuilding game packages dwarfs
        # everything else when a mod has any, so it gets most of the bar.
        if asset_mods:
            marks = {"backup": (0, 8), "undo": (8, 14), "database": (14, 40), "files": (40, 100)}
        else:
            marks = {"backup": (0, 25), "undo": (25, 35), "database": (35, 100)}

        if take_backup:
            progress.stage("Backing up your database...", *marks["backup"])
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
            progress.stage("Undoing mods that were switched off...", *marks["undo"])
            self._undo_switched_off_mods()

        progress.stage("Applying mods to the database...", *marks["database"])

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
            record=True,
            progress=progress,
            game_root=self.asset_game_root,
        )
        self.last_apply = report
        self.last_validation = report.validation

        if report.ok and asset_mods:
            progress.stage("Writing game files...", *marks["files"])
            self._apply_asset_files(mods, report, pre_asset_db, progress)
        if pre_asset_db is not None:
            pre_asset_db.unlink(missing_ok=True)

        if report.ok:
            progress.stage("Finishing up...", 100, 100)
            record_apply(self.state, mods, report)
            self.state.stamp_db(self.db_path)
            self.watcher.stamp(
                self.state.db_sha256_at_last_save, self.state.db_mtime_at_last_save
            )
        self.state.save()
        return report

    def _apply_asset_files(
        self, mods, report: ApplyReport, db_rollback: Path | None,
        progress: Progress | None = None,
    ) -> None:
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
                mods, game_root, self.paths.backups_dir, log=self.log, progress=progress
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
            if mod is None:
                continue
            # A newer version of the mod may no longer write rows the old one
            # did; taking the old one off first means none of those are left.
            if record.version and mod.version and record.version != mod.version:
                stranded.append(mod_id)
                continue
            if not record.parts:
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

    def save_mod_list(self, progress: Progress | None = None) -> ApplyReport:
        """The main action: back up, validate, apply, stamp the DB fingerprint."""
        self.log.info("Save Mod List: backing up and applying")
        return self._apply(take_backup=True, progress=progress)

    def reapply_all(self, progress: Progress | None = None) -> ApplyReport:
        """Re-run the enabled list against a DB that changed. No new backup."""
        self.log.info("Re-apply All")
        return self._apply(take_backup=False, progress=progress)

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
        reverting = [self.scan.get(mod_id) for mod_id in mod_ids]
        reverting = [mod for mod in reverting if mod is not None]
        # A TFC Installer mod only knows every file it changed once it has seen
        # the game folder: its textures sit in packages it never names.
        if self.asset_game_root is not None:
            try:
                asset_runner.bind_tfc_patches_for_revert(
                    reverting, self.asset_game_root, self.paths.backups_dir)
            except (OSError, ValueError) as exc:
                self.log.warn(f"could not work out every game file to put back ({exc})")
        for mod in reverting:
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
            record_after=lambda reverted: db_record.from_applied(
                {k: v for k, v in self.state.applied.items() if k not in reverted}
            ),
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

    def delete_mod(self, mod_id: str) -> None:
        """Remove a mod's folder and everything remembered about it.

        The rows it changed are *not* put back - that is Revert's job, and
        whether to offer it first is the caller's decision. Deleting an applied
        mod without reverting leaves its values in the database with nothing
        left to undo them, so the window asks before it gets here.
        """
        mod = self.scan.get(mod_id)
        if mod is None:
            raise ModManagerError(f"no such mod: {mod_id}")
        shutil.rmtree(Path(mod.folder))
        self.state.set_enabled(mod_id, False)
        self.state.forget_applied(mod_id)
        self.state.disabled_patches.pop(mod_id, None)
        self.state.mod_settings.pop(mod_id, None)
        self.state.save()
        snapshot_module.discard(self.paths.snapshots_dir, mod_id)
        self._delta_cache.pop(mod_id, None)
        self._conflict_cache = None
        self.log.info(f"Deleted mod {mod_id}")
        self.rescan()

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
        """The clean database everything is measured against.

        A copy that ships with the manager wins over ``masters.db.original``,
        because it is known to be stock: the ".original" is only as clean as the
        file it was taken from, and someone who arrives with an already-modded
        database has a copy of *that* sitting there under the name "original".
        When no shipped copy matches their game build, the kept copy is still
        the best available answer.
        """
        build = self.chosen_vanilla()
        if build is not None:
            return build.path
        if not self.db_path:
            return None
        original = backup_module.original_backup_path(self.db_path)
        return original if original.is_file() else None

    # -- the clean databases that ship with the manager --------------------

    def vanilla_builds(self) -> list[vanilla_library.VanillaBuild]:
        """Every clean database available, shipped or dropped in by the user."""
        return vanilla_library.builds(self.paths.root)

    def chosen_vanilla(self) -> "vanilla_library.VanillaBuild | None":
        """The clean copy to use: the user's pick, else the one that matches."""
        choice = self.state.settings.vanilla_choice
        if choice:
            picked = vanilla_library.for_version(
                choice, self.paths.root
            ) or vanilla_library.for_label(choice, self.paths.root)
            if picked is not None:
                return picked
        return vanilla_library.best_for(self.db_path, self.paths.root)

    def set_vanilla_choice(self, choice: str) -> None:
        """Pin the reference copy, or pass "" to go back to matching by build."""
        self.state.settings.vanilla_choice = choice or ""
        self.state.save()
        self._delta_cache.clear()
        self.log.info(
            f"Comparing against {choice}" if choice
            else "Comparing against whichever clean copy matches the database"
        )

    # -- adopting an already-modded database -------------------------------

    def capture_clean_copy(self) -> vanilla_capture.CaptureResult:
        """Keep the game's own database as the clean copy for a new build.

        Runs only when no clean copy matches the game's build - normally the
        first time the manager is opened after a game update, when the file
        Steam has just written is exactly what is wanted. Every check has to
        agree (see vanilla_capture); otherwise nothing is kept.
        """
        result = vanilla_capture.CaptureResult()
        if not self.db_path or not self.db_path.is_file():
            result.reason = "no database has been chosen"
            return result
        if self.chosen_vanilla() is not None:
            result.reason = "there is already a clean copy of this build"
            return result
        version = vanilla_library.database_version(self.db_path)
        if not version:
            result.reason = "this database does not say which build it is"
            return result

        record = db_record.read(self.db_path)
        if record is not None and record.mods:
            result.reason = ("the manager's own note in it lists "
                             f"{len(record.mods)} mod(s), so it is not stock")
            return result
        game_root = self.asset_game_root
        if game_root is None:
            result.reason = "the game folder could not be found"
            return result
        if not vanilla_capture.left_as_steam_wrote_it(self.db_path, game_root):
            result.reason = ("it is not from the same write as the rest of the game's "
                             "files, so something has changed it since the update")
            return result

        reference = vanilla_library.newest(self.paths.root)
        if reference is not None:
            try:
                result.unexpected_tables = vanilla_capture.unexpected_tables(
                    reference.path, self.db_path
                )
            except Exception as exc:  # a diff that fails is not proof of anything
                result.reason = f"it could not be compared with {reference.name} ({exc})"
                return result
            if result.unexpected_tables:
                shown = ", ".join(result.unexpected_tables[:4])
                result.reason = (
                    f"it changes {len(result.unexpected_tables)} table(s) that a game "
                    f"update has never been seen to touch ({shown}) - that looks like "
                    "mods, not a patch"
                )
                return result

        label = vanilla_library.suggested_label(self.db_path) or version
        try:
            result.kept = vanilla_capture.keep(self.db_path, self.paths.vanilla_dir, label)
        except (OSError, FileExistsError) as exc:
            result.reason = f"it could not be copied in ({exc})"
            return result
        result.label = label
        self.log.info(
            f"Kept your game's masters.db as the clean copy for build {version} "
            f"(LiD Vanilla DB/{result.kept.parent.name}) - the game had just been "
            "updated and the file was still exactly as Steam wrote it"
        )
        return result

    def keep_as_clean_copy(self) -> vanilla_capture.CaptureResult:
        """Keep the chosen database as this build's clean copy, because the
        player says it is one. Their word, on the record in the log."""
        result = vanilla_capture.CaptureResult()
        if not self.db_path or not self.db_path.is_file():
            result.reason = "no database has been chosen"
            return result
        version = vanilla_library.database_version(self.db_path)
        label = vanilla_library.suggested_label(self.db_path) or version or "unknown"
        try:
            result.kept = vanilla_capture.keep(self.db_path, self.paths.vanilla_dir, label)
        except (OSError, FileExistsError) as exc:
            result.reason = f"it could not be copied in ({exc})"
            return result
        result.label = label
        self.log.warn(
            f"Kept {self.db_path.name} as the clean copy for build {version or label} "
            f"(LiD Vanilla DB/{result.kept.parent.name}) because you said it is an "
            "unmodded file for a new game version. Everything is measured against it "
            "from now on, so delete that folder if it turns out to have mods in it."
        )
        return result

    def last_known_record(self):
        """The mod list last saved into a database, and where it was read from.

        A game update wipes the note along with the mods, so afterwards the
        database itself no longer says what was in it. The backups still do:
        the rolling copy beside the game database first, then the dated ones,
        newest first.
        """
        candidates = []
        if self.db_path:
            candidates.append(self.db_path)
            candidates.append(backup_module.rolling_backup_path(self.db_path))
        candidates.extend(
            sorted(self.paths.backups_dir.glob("*.db"), key=lambda p: p.name, reverse=True)
        )
        for candidate in candidates:
            if not Path(candidate).is_file():
                continue
            record = db_record.read(Path(candidate))
            if record is not None and record.mods:
                return record, Path(candidate).name
        return None, ""

    def adopt_scan(self) -> "adopt.AdoptionReport":
        """Work out which mods are already in the chosen database.

        Reads only - the caller decides what to do with the answer.
        """
        if not self.db_path or not self.db_path.is_file():
            return adopt.AdoptionReport(error="No database has been chosen yet.")
        build = self.chosen_vanilla()
        if build is None:
            version = vanilla_library.database_version(self.db_path) or "unknown"
            return adopt.AdoptionReport(
                error=(
                    f"No clean copy of game build {version} ships with this manager, so "
                    "there is nothing safe to compare your database against. Comparing "
                    "against a different build would read the game's own patch as a mod."
                )
            )
        self.log.info(f"Scanning {self.db_path.name} against clean {build.name}")
        record = db_record.read(self.db_path)
        if record is not None and record.unreadable:
            self.log.warn(record.unreadable)
        elif record is not None:
            self.log.info(
                f"The database lists {len(record.mods)} mod(s) it was saved with; "
                "checking each against the actual values"
            )
        report = adopt.scan(
            build.path,
            self.db_path,
            self.mods,
            self.mod_delta,
            self.asset_game_root,
            record=migrations.migrate_record(record, self.installed_ids()),
            chosen=self.state.mod_settings,
        )
        self.log.info(f"Scan: {report.summary()}")
        return report

    def adoption_order(self, mod_ids: list[str], recorded: list[str] | None = None) -> list[str]:
        """Content packs first, then everything else, order otherwise kept.

        ``recorded`` is the load order the database's own note lists (db_record)
        - the order the player last saved with, which beats any guess. The mods
        it names keep that order exactly; only mods it does not name are placed
        by the rule below, packs above them and everything else below.

        Load order is "top applies first, bottom wins". A content pack rewrites
        and adds rows across a great many tables, so anything applied *before*
        it gets buried - a small mod retuning one of those tables would look as
        if it simply had not worked, and the player has no reason to suspect the
        order. Putting the packs at the top means the small tweaks land on top
        of them, which is what ticking both is meant to do.

        A pack is recognised by it shipping game files: that is what makes it a
        pack rather than a tweak, and it needs no list of names to maintain.
        """
        known = [mod_id for mod_id in mod_ids if self.scan.get(mod_id) is not None]
        packs = [
            mod_id for mod_id in known if self.scan.get(mod_id).asset_targets()
        ]
        # Among the packs, whichever rewrites more of the database goes first. A
        # pack that only swaps artwork - the button prompts replace one package
        # and touch no table at all - can bury nothing, while a content pack
        # writes sixteen tables and buries anything applied before it.
        packs.sort(key=lambda mod_id: (-len(self.scan.get(mod_id).tables()), known.index(mod_id)))
        if recorded:
            position = {mod_id: n for n, mod_id in enumerate(recorded)}
            kept = sorted((m for m in known if m in position), key=position.__getitem__)
            new_packs = [m for m in packs if m not in position]
            others = [m for m in known if m not in position and m not in set(packs)]
            return new_packs + kept + others
        return packs + [mod_id for mod_id in known if mod_id not in set(packs)]

    def adopt_rebuild(self, mod_ids: list[str], values: dict | None = None) -> ApplyReport:
        """Reset the database to vanilla and apply the chosen mods to it.

        This is what makes an adopted database honest: afterwards the file is
        genuinely vanilla-plus-these-mods, with a snapshot taken for each one as
        usual, so every one of them can be switched off again. Their own file is
        backed up first and their old snapshots are dropped, because those
        described a database that no longer exists.
        """
        if not self.db_path or not self.db_path.is_file():
            raise ModManagerError("No database has been chosen yet.")
        build = self.chosen_vanilla()
        if build is None:
            raise ModManagerError(
                "No clean copy matching this database's game build is available."
            )
        reason = asset_runner.game_lock_reason(self.db_path)
        if reason:
            raise ModManagerError(reason)

        # The order they last saved with. Read before the file is replaced -
        # and from the backups when the database itself no longer says, which
        # is what a game update leaves behind.
        record, where = self.last_known_record()
        recorded = [entry.mod_id for entry in record.mods] if record is not None else []
        if recorded and where != self.db_path.name:
            self.log.info(f"Load order taken from the mod list last saved, read from {where}")

        theirs = backup_module.take_backups(
            self.db_path, self.paths.backups_dir, self.state.settings.keep_backups
        )
        state_before = copy.deepcopy(self.state)
        # The snapshots are what switches each mod off again; they are replaced
        # below, so a copy is set aside in case the rebuild has to be undone.
        snapshots_aside = self.paths.backups_dir / ".pre_rebuild_snapshots"
        shutil.rmtree(snapshots_aside, ignore_errors=True)
        if self.paths.snapshots_dir.is_dir():
            shutil.copytree(self.paths.snapshots_dir, snapshots_aside)
        self.log.info(f"Rebuilding {self.db_path.name} from clean {build.name}")
        backup_module.copy_database(build.path, self.db_path)
        # The kept copy beside their database is now genuinely stock, which for
        # someone who arrived with a modded file it never was.
        backup_module.copy_database(
            build.path, backup_module.original_backup_path(self.db_path)
        )

        for mod in self.scan.mods:
            snapshot_module.discard(self.paths.snapshots_dir, mod.id)
        # Rebuilt at the values found in their database, not the defaults: an
        # x7 they already had stays x7.
        for mod_id, found in (values or {}).items():
            mod = self.scan.get(mod_id)
            if mod is None or not mod.settings:
                continue
            self.reset_mod_settings(mod_id)
            for setting_id, value in found.items():
                if mod.setting(setting_id) is not None:
                    try:
                        self.set_mod_setting(mod_id, setting_id, value)
                    except (ValueError, ModManagerError) as exc:
                        self.log.warn(f"{mod_id}: kept the default for {setting_id} ({exc})")
        self.state.applied.clear()
        self.state.enabled_mods = self.adoption_order(mod_ids, recorded)
        self.state.save()
        self._delta_cache.clear()
        report = self._apply(take_backup=False)
        if not report.ok:
            self._undo_failed_rebuild(theirs.dated, state_before, snapshots_aside)
        shutil.rmtree(snapshots_aside, ignore_errors=True)
        return report

    def _undo_failed_rebuild(
        self, their_copy: Path | None, state_before, snapshots_aside: Path
    ) -> None:
        """Put the player's own database and the manager's state back.

        A failed apply rolls back to how the file was when the apply started -
        which, in a rebuild, is the clean copy it had just been reset to. Left
        there, the player's mods would be gone from the database while their
        game files were still modded.
        """
        if their_copy is None or not their_copy.is_file():
            self.log.error(
                "The rebuild failed and the copy of your database from before it could "
                "not be found - use Tools > Restore a backup."
            )
            return
        try:
            backup_module.restore_backup(their_copy, self.db_path)
        except OSError as exc:
            self.log.error(
                f"The rebuild failed and your database could not be put back ({exc}) - "
                f"use Tools > Restore a backup and pick {their_copy.name}."
            )
            return
        for item in fields(self.state):
            setattr(self.state, item.name, getattr(state_before, item.name))
        self.state.save()
        if snapshots_aside.is_dir():
            shutil.rmtree(self.paths.snapshots_dir, ignore_errors=True)
            shutil.copytree(snapshots_aside, self.paths.snapshots_dir)
        self._delta_cache.clear()
        self.log.warn(
            f"The rebuild failed, so your own database was put back as it was "
            f"(from {their_copy.name}). Nothing else was changed."
        )

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
            # And the chosen values: x5 changes different numbers than x2.
            tuple(sorted(mod.values.items())),
        )
        # A few answers per mod: working out a value from a database asks for
        # the same mod at two or three values in a row, and one slot would
        # throw away the player's own each time.
        cached = self._delta_cache.setdefault(mod.id, {})
        if key in cached:
            return cached[key]

        try:
            delta = dbdiff.delta_for_mod(vanilla, mod)
        except Exception as exc:
            # Never let this break an apply - callers fall back to the old path.
            self.log.warn(f"{mod.id}: could not work out its exact changes ({exc})")
            return None
        if len(cached) >= DELTAS_KEPT_PER_MOD:
            cached.pop(next(iter(cached)))
        cached[key] = delta
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
        category: str | None = None,
        tags=None,
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
            category=category,
            tags=tags,
        )
        if new_id != mod_id:
            self.log.info(f"Renamed {mod_id} to {new_id}")
        else:
            self.log.info(f"Updated the details of {new_id}")
        self._conflict_cache = None
        self._delta_cache.pop(mod_id, None)
        self.rescan()
        return self.scan.get(new_id)

    # -- what kind of mod it is -------------------------------------------

    def set_mod_filing(
        self, mod_id: str, *, category: str | None = None, tags=None
    ) -> Mod | None:
        """Change a mod's category and/or tags, and nothing else about it.

        Goes through ``edit_mod`` with the mod's own name, so the folder is not
        renamed and a bare ``.sql`` mod gains a real mod.json the same way giving
        it a name does.
        """
        mod = self.scan.get(mod_id)
        if mod is None:
            raise modedit.ModEditError(f"no such mod: {mod_id}")
        return self.edit_mod(mod_id, mod.name, category=category, tags=tags)

    def categories(self) -> dict[str, int]:
        """Every category in use, with how many mods are in it."""
        return browse.category_counts(self.scan.mods)

    def tags(self) -> dict[str, int]:
        """Every tag in use, with how many mods carry it, commonest first."""
        return browse.tag_counts(self.scan.mods)

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
