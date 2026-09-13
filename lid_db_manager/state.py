"""``state.json`` - the enabled mod list, modpacks and last-seen DB fingerprint.

The fingerprint (sha256 + mtime) is stamped on "Save Mod List". The watchdog
compares the live database against it to decide whether the mods on disk are
still the ones in the file.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from .sqlutil import sha256_file

STATE_VERSION = 1


@dataclass
class AppliedRecord:
    """What a mod did the last time it was applied successfully."""

    applied_at: str = ""
    rows_changed: int = 0
    tables: list[str] = field(default_factory=list)
    version: str = ""
    name: str = ""
    # The keys of the patches that actually ran. Switching a part off later has
    # to undo what it wrote, and that means knowing what was in the mod then.
    parts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> "AppliedRecord":
        return AppliedRecord(
            applied_at=data.get("applied_at", ""),
            rows_changed=int(data.get("rows_changed", 0) or 0),
            tables=list(data.get("tables", []) or []),
            version=data.get("version", ""),
            name=data.get("name", ""),
            parts=list(data.get("parts", []) or []),
        )


@dataclass
class Settings:
    auto_reapply: bool = True  # A3.3: re-apply automatically on a detected change
    watchdog_enabled: bool = True
    poll_seconds: int = 5
    dark_mode: bool = True
    keep_backups: int = 5
    # A folder of item artwork to show in the builder. Nothing ships with the
    # program - game artwork belongs to its owners - so this points at a copy
    # the player already has on their own machine, and stays empty otherwise.
    icon_folder: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> "Settings":
        defaults = Settings()
        return Settings(
            auto_reapply=bool(data.get("auto_reapply", defaults.auto_reapply)),
            watchdog_enabled=bool(data.get("watchdog_enabled", defaults.watchdog_enabled)),
            poll_seconds=max(1, int(data.get("poll_seconds", defaults.poll_seconds) or 5)),
            dark_mode=bool(data.get("dark_mode", defaults.dark_mode)),
            keep_backups=max(1, int(data.get("keep_backups", defaults.keep_backups) or 5)),
            icon_folder=str(data.get("icon_folder", defaults.icon_folder) or ""),
        )


@dataclass
class State:
    path: Path
    db_path: str = ""
    db_sha256_at_last_save: str = ""
    db_mtime_at_last_save: float = 0.0
    last_saved_at: str = ""
    # Explicit game folder (the one holding BrgGame), used for asset_file mods
    # when it cannot be derived from db_path - a loose masters.db, a test setup.
    game_root_override: str = ""
    enabled_mods: list[str] = field(default_factory=list)
    modpacks: dict[str, list[str]] = field(default_factory=dict)
    applied: dict[str, AppliedRecord] = field(default_factory=dict)
    # mod id -> the keys of that mod's patches the player has switched off.
    # Lets one imported rework stay a single mod whose parts toggle.
    disabled_patches: dict[str, list[str]] = field(default_factory=dict)
    settings: Settings = field(default_factory=Settings)

    # -- persistence -----------------------------------------------------

    @staticmethod
    def load(path: Path) -> "State":
        path = Path(path)
        state = State(path=path)
        if not path.is_file():
            return state
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # A corrupt state file is not worth crashing over: start clean and
            # keep the old one around so the user can look at it.
            try:
                path.replace(path.with_suffix(".json.corrupt"))
            except OSError:
                pass
            return state
        if not isinstance(data, dict):
            return state

        state.db_path = str(data.get("db_path", "") or "")
        state.db_sha256_at_last_save = str(data.get("db_sha256_at_last_save", "") or "")
        state.db_mtime_at_last_save = float(data.get("db_mtime_at_last_save", 0.0) or 0.0)
        state.last_saved_at = str(data.get("last_saved_at", "") or "")
        state.game_root_override = str(data.get("game_root_override", "") or "")
        state.enabled_mods = [str(m) for m in data.get("enabled_mods", []) or []]
        state.modpacks = {
            str(name): [str(m) for m in mods]
            for name, mods in (data.get("modpacks") or {}).items()
            if isinstance(mods, list)
        }
        state.applied = {
            str(mod_id): AppliedRecord.from_dict(record)
            for mod_id, record in (data.get("applied") or {}).items()
            if isinstance(record, dict)
        }
        state.disabled_patches = {
            str(mod_id): [str(k) for k in keys]
            for mod_id, keys in (data.get("disabled_patches") or {}).items()
            if isinstance(keys, list)
        }
        state.settings = Settings.from_dict(data.get("settings") or {})
        return state

    def save(self) -> None:
        payload = {
            "version": STATE_VERSION,
            "db_path": self.db_path,
            "db_sha256_at_last_save": self.db_sha256_at_last_save,
            "db_mtime_at_last_save": self.db_mtime_at_last_save,
            "last_saved_at": self.last_saved_at,
            "game_root_override": self.game_root_override,
            "enabled_mods": self.enabled_mods,
            "modpacks": self.modpacks,
            "applied": {mod_id: record.to_dict() for mod_id, record in self.applied.items()},
            "disabled_patches": {k: v for k, v in self.disabled_patches.items() if v},
            "settings": self.settings.to_dict(),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    # -- enabled list ----------------------------------------------------

    def is_enabled(self, mod_id: str) -> bool:
        return mod_id in self.enabled_mods

    def set_enabled(self, mod_id: str, enabled: bool) -> None:
        if enabled and mod_id not in self.enabled_mods:
            self.enabled_mods.append(mod_id)
        elif not enabled and mod_id in self.enabled_mods:
            self.enabled_mods.remove(mod_id)

    def is_part_enabled(self, mod_id: str, patch_key: str) -> bool:
        """Parts are on unless switched off, so an ordinary mod needs no entry."""
        return patch_key not in self.disabled_patches.get(mod_id, ())

    def set_part_enabled(self, mod_id: str, patch_key: str, enabled: bool) -> None:
        off = list(self.disabled_patches.get(mod_id, ()))
        if enabled and patch_key in off:
            off.remove(patch_key)
        elif not enabled and patch_key not in off:
            off.append(patch_key)
        if off:
            self.disabled_patches[mod_id] = off
        else:
            self.disabled_patches.pop(mod_id, None)

    def order_of(self, mod_id: str) -> int | None:
        """1-based position in the load order, or None when disabled."""
        if mod_id not in self.enabled_mods:
            return None
        return self.enabled_mods.index(mod_id) + 1

    def move(self, mod_id: str, delta: int) -> bool:
        """Shift a mod up (-1) or down (+1) the load order. False if it cannot."""
        if mod_id not in self.enabled_mods:
            return False
        old = self.enabled_mods.index(mod_id)
        new = old + delta
        if not 0 <= new < len(self.enabled_mods):
            return False
        self.enabled_mods.insert(new, self.enabled_mods.pop(old))
        return True

    def set_order(self, mod_ids: list[str]) -> None:
        """Replace the load order, keeping any enabled mod not mentioned."""
        wanted = [m for m in mod_ids if m in self.enabled_mods]
        self.enabled_mods = wanted + [m for m in self.enabled_mods if m not in wanted]

    def prune_missing(self, installed_ids: set[str]) -> list[str]:
        """Drop enabled ids whose folder is gone. Returns the dropped ids."""
        gone = [mod_id for mod_id in self.enabled_mods if mod_id not in installed_ids]
        for mod_id in gone:
            self.enabled_mods.remove(mod_id)
        return gone

    # -- DB fingerprint --------------------------------------------------

    def stamp_db(self, db_path: Path) -> None:
        """Record the current hash/mtime as 'this is what our mods are on'."""
        db_path = Path(db_path)
        self.db_path = str(db_path)
        self.db_sha256_at_last_save = sha256_file(db_path)
        self.db_mtime_at_last_save = os.path.getmtime(db_path)
        self.last_saved_at = datetime.now().isoformat(timespec="seconds")

    # -- applied records -------------------------------------------------

    def record_applied(self, mod_id: str, record: AppliedRecord) -> None:
        self.applied[mod_id] = record

    def forget_applied(self, mod_id: str) -> None:
        self.applied.pop(mod_id, None)

    # -- modpacks --------------------------------------------------------

    def save_modpack(self, name: str, mod_ids: list[str] | None = None) -> None:
        self.modpacks[name] = list(mod_ids if mod_ids is not None else self.enabled_mods)

    def load_modpack(self, name: str) -> list[str]:
        if name not in self.modpacks:
            raise KeyError(name)
        self.enabled_mods = list(self.modpacks[name])
        return self.enabled_mods

    def delete_modpack(self, name: str) -> None:
        self.modpacks.pop(name, None)
