"""The save library: every save a player keeps, each with a nickname and the mods it is played with.

The game has one save per Steam account, ``<game>/Savedata/<steam id>/brggame.sav``,
with its own previous copy ``brggame.bak`` beside it. The library keeps any
number of saves in the manager's folder (``saves/<entry>/``) and puts one of
them in the game's place when the player picks it:

- an entry is the save's own bytes, never re-encoded, its ``.bak`` when it had
  one, and ``entry.json``: the nickname (the library's name only - the file in
  the game is never renamed), whose Steam account it is, and the mod list it
  was last played with;
- ``live.json`` says which entry is in the game for each account, so the save
  there can be put back into its entry - with whatever the game added since -
  before another one takes its place.

Nothing in the game is written while it runs, a save in the game that is not
in the library is never replaced (it is added first, or the switch is
refused), and the save in the game is also copied to ``backups/saves`` before
anything replaces it.

Steam Cloud keeps whichever save is in the game. A save in the library can
also be backed up there, by the player's choice: one more copy beside the
game's save, ``brggame.sav.lidmm-<entry>``, with ``brggame.sav.lidmm-<entry>.json``
holding its nickname and mods. Steam carries files there named like the
game's own (it skips subfolders), and on another PC the manager finds them and
offers to bring them into the library. The game has 100 MB of Steam Cloud and
a full cloud could stop the game's own save syncing, so the copies are only
made while everything there stays under CLOUD_BUDGET.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from . import newsave, savegame, savelink

LIBRARY_DIR = "saves"
ENTRY_FILE = "entry.json"
LIVE_FILE = "live.json"
BAK_NAME = "brggame.bak"
MOD_KEYS = ("enabled_mods", "mod_settings", "disabled_patches", "enabled_patches",
            # how the list came to be known when it was not noted by Save Mod List:
            # "found" (by checking the save, savecheck.py) or "chosen" (by the player)
            "noted")
# The library has this many numbered slots, like a memory card: a clear limit
SLOTS = 30
CLOUD_PREFIX = "brggame.sav.lidmm-"
CLOUD_INFO = ".json"
MB = 1024 * 1024
CLOUD_QUOTA = 100 * MB          # what Steam gives LET IT DIE
CLOUD_BUDGET = 75 * MB          # the most the backups may take it to: room left for the game's own save


class LibraryError(Exception):
    """Nothing was changed, and this says why in words a player can act on."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_id(entry_id: str) -> bool:
    """An entry id is a folder and part of a file name: letters, digits, - and _ only."""
    return bool(re.fullmatch(r"[A-Za-z0-9_-]{1,64}", entry_id or ""))


def _slot_number(value) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return number if 1 <= number <= SLOTS else 0


def _now_text(moment: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(moment))


def check_save(path: Path) -> None:
    """Refuse a file that is not a readable LET IT DIE save."""
    try:
        savegame.read_container(Path(path).read_bytes())
    except OSError as exc:
        raise LibraryError(f"could not read {path}: {exc}") from None
    except savegame.SaveError as exc:
        raise LibraryError(f"{Path(path).name}: {exc}") from None


def same_save(a: Path, b: Path) -> bool:
    """Two save files the same save - one perhaps played further - by what never changes
    as a save is played (savelink.identity). False when either cannot be read."""
    try:
        return savelink.save_identity(a) == savelink.save_identity(b)
    except (OSError, savegame.SaveError):
        return False


def _copy_checked(source: Path, target: Path) -> None:
    """``source`` to ``target`` through a .new file swapped in only once it reads back the same."""
    temporary = target.with_name(target.name + ".new")
    try:
        shutil.copyfile(source, temporary)
        if sha256(temporary) != sha256(source):
            raise LibraryError(f"the copy of {source.name} did not come out the same - nothing was replaced")
        os.replace(temporary, target)
    except OSError as exc:
        raise LibraryError(f"could not write {target}: {exc}") from None
    finally:
        temporary.unlink(missing_ok=True)


@dataclass
class Entry:
    id: str
    folder: Path
    nickname: str = ""
    steam_id: str = ""
    added: str = ""
    updated: str = ""
    sha256: str = ""
    mods: dict = field(default_factory=dict)
    cloud: bool = False             # also kept beside the game's save, for Steam Cloud
    slot: int = 0                   # 1..SLOTS, its place in the library

    @property
    def save(self) -> Path:
        return self.folder / savegame.SAVE_NAME

    @property
    def bak(self) -> Path:
        return self.folder / BAK_NAME

    def to_dict(self) -> dict:
        return {"nickname": self.nickname, "steam_id": self.steam_id, "added": self.added,
                "updated": self.updated, "sha256": self.sha256, "mods": self.mods, "cloud": self.cloud,
                "slot": self.slot}

    @staticmethod
    def load(folder: Path) -> "Entry | None":
        try:
            data = json.loads((folder / ENTRY_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or not (folder / savegame.SAVE_NAME).is_file():
            return None
        mods = data.get("mods")
        return Entry(id=folder.name, folder=folder, nickname=str(data.get("nickname", "") or ""),
                     steam_id=str(data.get("steam_id", "") or ""), added=str(data.get("added", "") or ""),
                     updated=str(data.get("updated", "") or ""), sha256=str(data.get("sha256", "") or ""),
                     mods={k: mods[k] for k in MOD_KEYS if k in mods} if isinstance(mods, dict) else {},
                     cloud=bool(data.get("cloud", False)), slot=_slot_number(data.get("slot")))


@dataclass(frozen=True)
class CloudSave:
    """A save backed up to Steam Cloud by a manager (perhaps on another PC)."""

    id: str
    steam_id: str
    nickname: str
    updated: str
    save: Path
    info: dict


@dataclass
class GameFolderSync:
    """What sync_from_game found in the game's save folder."""

    restored: list = field(default_factory=list)    # entries brought back from their Steam Cloud copies
    relinked: list = field(default_factory=list)    # entries found to be the save in the game
    failed: list = field(default_factory=list)


@dataclass(frozen=True)
class LiveSave:
    """The save in the game for one Steam account, and which entry it is."""

    steam_id: str
    path: Path
    entry: Entry | None
    in_sync: bool       # the entry holds exactly what is in the game
    # The entry says it is in the game, but the save there is another save
    # altogether (put there by hand, say): never written over the entry.
    foreign: bool = False

    @property
    def known(self) -> bool:
        return self.entry is not None and not self.foreign


class Library:
    def __init__(self, manager_root: Path | str, lock_reason=None, clock=time.time, game_root=None) -> None:
        self.root = Path(manager_root) / LIBRARY_DIR
        self.backups = Path(manager_root) / "backups" / "saves"
        # The game folder, or something that gives it when asked: where the
        # Steam Cloud copies of a renamed or re-modded save are kept up to date.
        self._game_root = game_root if callable(game_root) else (lambda: game_root)
        if lock_reason is None:
            from . import asset_runner
            lock_reason = lambda: asset_runner.game_lock_reason()     # noqa: E731 - looked up per call
        self._lock_reason = lock_reason
        self._clock = clock

    # -- reading ---------------------------------------------------------

    def entries(self) -> list[Entry]:
        """Every save in the library, by slot. A save without a slot of its own
        (kept before there were slots, or sharing one) is given the first free one."""
        if not self.root.is_dir():
            return []
        found = [e for e in (Entry.load(p) for p in self.root.iterdir() if p.is_dir()) if e]
        taken: set[int] = set()
        unplaced = []
        for entry in sorted(found, key=lambda e: (e.added, e.id)):
            if entry.slot and entry.slot not in taken:
                taken.add(entry.slot)
            else:
                unplaced.append(entry)
        for entry in unplaced:
            free = [n for n in range(1, SLOTS + 1) if n not in taken]
            entry.slot = free[0] if free else 0
            if entry.slot:
                taken.add(entry.slot)
                self._write_entry(entry)
        return sorted(found, key=lambda e: (e.slot or SLOTS + 1, e.added))

    def by_slot(self) -> dict[int, Entry]:
        return {e.slot: e for e in self.entries() if e.slot}

    def first_free_slot(self) -> int | None:
        used = self.by_slot()
        return next((n for n in range(1, SLOTS + 1) if n not in used), None)

    def move_to_slot(self, entry_id: str, slot: int) -> Entry:
        """Put a save in another slot; a save already there takes this one's place."""
        if not 1 <= int(slot) <= SLOTS:
            raise LibraryError(f"there are only slots 1 to {SLOTS}")
        entry = self.get(entry_id)
        if not entry.slot:
            self.entries()
            entry = self.get(entry_id)
        other = self.by_slot().get(int(slot))
        if other is not None and other.id != entry.id:
            other.slot = entry.slot
            self._write_entry(other)
            self._refresh_cloud(other)
        entry.slot = int(slot)
        self._write_entry(entry)
        self._refresh_cloud(entry)
        return entry

    def get(self, entry_id: str) -> Entry:
        if not _safe_id(entry_id):
            raise LibraryError(f"there is no save {entry_id!r} in the library")
        entry = Entry.load(self.root / entry_id)
        if entry is None:
            raise LibraryError(f"there is no save {entry_id!r} in the library")
        return entry

    def _live_map(self) -> dict[str, str]:
        try:
            data = json.loads((self.root / LIVE_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}

    def _set_live(self, steam_id: str, entry_id: str | None) -> None:
        live = self._live_map()
        if entry_id:
            live[steam_id] = entry_id
        else:
            live.pop(steam_id, None)
        self._write_json(self.root / LIVE_FILE, live)

    def live(self, game_root: Path | str | None) -> list[LiveSave]:
        """Each account's save in the game, the most recently played first."""
        mapping = self._live_map()
        found = []
        for path in savegame.find_saves(game_root):
            steam_id = path.parent.name
            entry = None
            if steam_id in mapping:
                try:
                    entry = self.get(mapping[steam_id])
                except LibraryError:
                    entry = None
            in_sync = bool(entry) and entry.sha256 == sha256(path)
            # Played since is fine; a different save altogether is not that entry.
            foreign = bool(entry) and not in_sync and not same_save(path, entry.save)
            found.append(LiveSave(steam_id, path, entry, in_sync, foreign))
        return found

    def live_for(self, game_root, steam_id: str) -> LiveSave | None:
        return next((s for s in self.live(game_root) if s.steam_id == steam_id), None)

    # -- writing the library's own files --------------------------------

    def _write_json(self, path: Path, data) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".new")
        temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)

    def _write_entry(self, entry: Entry) -> None:
        self._write_json(entry.folder / ENTRY_FILE, entry.to_dict())

    def _new_entry(self, source: Path, steam_id: str, nickname: str, mods: dict | None,
                   entry_id: str | None = None, slot: int | None = None) -> Entry:
        check_save(source)
        used = self.by_slot()
        if slot and slot in used:
            slot = None
        slot = slot or self.first_free_slot()
        if slot is None:
            raise LibraryError(f"all {SLOTS} save slots are full - export or delete a save to make room")
        moment = self._clock()
        entry_id = entry_id or time.strftime("%Y%m%d-%H%M%S", time.localtime(moment)) + "-" + uuid.uuid4().hex[:6]
        folder = self.root / entry_id
        folder.mkdir(parents=True)
        entry = Entry(entry_id, folder, nickname.strip() or "Unnamed save", steam_id,
                      _now_text(moment), _now_text(moment), "", dict(mods or {}), slot=slot)
        try:
            _copy_checked(source, entry.save)
            # The game's brggame.bak is the previous copy of its brggame.sav only;
            # beside any other file (a backed-up copy, a stray backup) it belongs
            # to the save in the game, not to this one.
            bak = source.with_name(BAK_NAME)
            if source.name.lower() == savegame.SAVE_NAME and bak.is_file():
                _copy_checked(bak, entry.bak)
            entry.sha256 = sha256(entry.save)
            self._write_entry(entry)
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        return entry

    def rename(self, entry_id: str, nickname: str) -> Entry:
        entry = self.get(entry_id)
        entry.nickname = nickname.strip() or entry.nickname
        self._write_entry(entry)
        self._refresh_cloud(entry)
        return entry

    def saved_over(self, entry_id: str) -> Entry:
        """The entry's save file was written (edited): note its new contents, and its cloud copy follows."""
        entry = self.get(entry_id)
        check_save(entry.save)
        entry.sha256 = sha256(entry.save)
        entry.updated = _now_text(self._clock())
        self._write_entry(entry)
        self._refresh_cloud(entry)
        return entry

    def remember_mods(self, entry_id: str, mods: dict) -> Entry:
        """The mod list this save is played with, as saved in the manager."""
        entry = self.get(entry_id)
        entry.mods = {k: mods[k] for k in MOD_KEYS if k in mods}
        self._write_entry(entry)
        self._refresh_cloud(entry)
        return entry

    def import_file(self, source: Path | str, steam_id: str, nickname: str, mods: dict | None = None) -> Entry:
        """A .sav from anywhere, kept in the library (the game's save is not touched)."""
        return self._new_entry(Path(source), steam_id, nickname, mods)

    def export(self, entry_id: str, target: Path | str) -> Path:
        entry, target = self.get(entry_id), Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        _copy_checked(entry.save, target)
        return target

    def delete(self, entry_id: str) -> None:
        entry = self.get(entry_id)
        if entry_id in self._live_map().values():
            raise LibraryError(f"\"{entry.nickname}\" is the save in the game - switch to another one first")
        if entry.cloud:
            self.set_cloud(entry_id, False)
        shutil.rmtree(entry.folder)

    # -- Steam Cloud copies --------------------------------------------------

    def _cloud_paths(self, entry: Entry, game_root=None) -> tuple[Path, Path] | None:
        game_root = game_root or self._game_root()
        if not game_root or not entry.steam_id:
            return None
        folder = Path(game_root) / "Savedata" / entry.steam_id
        save = folder / f"{CLOUD_PREFIX}{entry.id}"
        return save, save.with_name(save.name + CLOUD_INFO)

    def cloud_usage(self, steam_id: str, game_root=None) -> int:
        """Bytes in the account's save folder - what Steam Cloud carries for the game."""
        game_root = game_root or self._game_root()
        folder = Path(game_root) / "Savedata" / steam_id if game_root else None
        if folder is None or not folder.is_dir():
            return 0
        return sum(p.stat().st_size for p in folder.iterdir() if p.is_file())

    def _write_cloud(self, entry: Entry, paths: tuple[Path, Path]) -> None:
        save, info = paths
        already = save.stat().st_size if save.is_file() else 0
        used = sum(p.stat().st_size for p in save.parent.iterdir() if p.is_file()) if save.parent.is_dir() else 0
        after = used - already + entry.save.stat().st_size
        if after > CLOUD_BUDGET:
            raise LibraryError(
                f"that would take the game's Steam Cloud to {after / MB:.0f} MB. The manager keeps its "
                f"backups under {CLOUD_BUDGET // MB} MB of the {CLOUD_QUOTA // MB} MB Steam gives the game, "
                "so your own save always fits - untick another save's Steam Cloud backup first")
        if not save.is_file() or sha256(save) != entry.sha256:
            _copy_checked(entry.save, save)
        data = dict(entry.to_dict(), id=entry.id)
        data.pop("cloud", None)
        self._write_json(info, data)

    def _refresh_cloud(self, entry: Entry) -> bool:
        """Bring an entry's Steam Cloud copy up to date. False when it could not be."""
        if not entry.cloud:
            return True
        paths = self._cloud_paths(entry)
        if paths is None:
            return False
        try:
            self._write_cloud(entry, paths)
        except (LibraryError, OSError):
            return False
        return True

    def set_cloud(self, entry_id: str, on: bool, game_root=None) -> Entry:
        """Back a save up to Steam Cloud, or stop: one more copy beside the game's save, or none."""
        self._refuse_while_running()
        entry = self.get(entry_id)
        paths = self._cloud_paths(entry, game_root)
        if paths is None:
            raise LibraryError("the game folder is not known, so there is nowhere to put a Steam Cloud copy")
        if on:
            paths[0].parent.mkdir(parents=True, exist_ok=True)
            self._write_cloud(entry, paths)
        else:
            for path in paths:
                try:
                    path.unlink(missing_ok=True)
                except OSError as exc:
                    raise LibraryError(f"could not remove {path.name}: {exc}") from None
        entry.cloud = on
        self._write_entry(entry)
        return entry

    def cloud_saves(self, game_root=None) -> list[CloudSave]:
        """Saves backed up to Steam Cloud that this library does not have - from another PC, say."""
        game_root = game_root or self._game_root()
        folder = Path(game_root) / "Savedata" if game_root else None
        if folder is None or not folder.is_dir():
            return []
        have = {e.id for e in self.entries()}
        found = []
        for info in sorted(folder.glob(f"*/{CLOUD_PREFIX}*{CLOUD_INFO}")):
            save = info.with_name(info.name[: -len(CLOUD_INFO)])
            entry_id = save.name[len(CLOUD_PREFIX):]
            if entry_id in have or not save.is_file() or not _safe_id(entry_id):
                continue
            try:
                data = json.loads(info.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(data, dict):
                found.append(CloudSave(entry_id, info.parent.name, str(data.get("nickname", "") or entry_id),
                                       str(data.get("updated", "") or ""), save, data))
        return found

    def restore_from_cloud(self, cloud: CloudSave) -> Entry:
        """A save backed up to Steam Cloud, brought into the library as it was there."""
        mods = cloud.info.get("mods")
        entry = self._new_entry(cloud.save, cloud.steam_id, cloud.nickname,
                                mods if isinstance(mods, dict) else None, entry_id=cloud.id,
                                slot=_slot_number(cloud.info.get("slot")) or None)
        entry.added = str(cloud.info.get("added", "") or entry.added)
        entry.updated = cloud.updated or entry.updated
        entry.cloud = True
        self._write_entry(entry)
        return entry

    # -- rebuilding from the game's save folder ------------------------------

    def sync_from_game(self, game_root=None) -> "GameFolderSync":
        """Make the library match what is in the game's save folder.

        The library's own folder can go - a fresh install, a rebuilt program -
        but the saves backed up to Steam Cloud are still beside the game's
        save. Each one this library lacks is brought back in (same slot,
        nickname and mods), and a save in the game that no slot is noted for
        is matched to the slot that holds the same save (same_save), so it is
        brought up to date there rather than kept twice. Only reads the game's
        folder; writes only the library's own files.
        """
        game_root = game_root or self._game_root()
        result = GameFolderSync()
        if not game_root:
            return result
        for cloud in self.cloud_saves(game_root):
            try:
                result.restored.append(self.restore_from_cloud(cloud))
            except (LibraryError, OSError) as exc:
                result.failed.append(f"{cloud.nickname}: {exc}")
        for live in self.live(game_root):
            if live.known:
                continue
            match = self._slot_for(live.path)
            if match is not None:
                self._set_live(live.steam_id, match.id)
                result.relinked.append(match)
        return result

    def _slot_for(self, save: Path) -> Entry | None:
        """The library's entry that is ``save``: the very same bytes, else the same save
        (played further) kept most recently."""
        digest = sha256(save)
        same = [e for e in self.entries() if e.sha256 == digest]
        if same:
            return same[0]
        same = [e for e in self.entries() if same_save(save, e.save)]
        return max(same, key=lambda e: (e.updated, e.added)) if same else None

    def stray_saves(self, game_root=None) -> list[Path]:
        """Other saves in the game's save folders - backups people or other tools left there
        (``brggame.sav.<anything>``, ``*.sav``) - that are not the game's own or the library's."""
        game_root = game_root or self._game_root()
        folder = Path(game_root) / "Savedata" if game_root else None
        if folder is None or not folder.is_dir():
            return []
        have = {e.sha256 for e in self.entries()}
        found = []
        for path in sorted(list(folder.glob("*/*")) + list(folder.glob("*.sav"))):
            name = path.name.lower()
            if (not path.is_file() or name in (savegame.SAVE_NAME, BAK_NAME) or name.startswith(CLOUD_PREFIX)
                    or name.endswith((".json", ".vdf", ".new"))):
                continue
            if not (name.startswith(savegame.SAVE_NAME + ".") or name.endswith(".sav")):
                continue
            try:
                check_save(path)
            except LibraryError:
                continue
            if sha256(path) not in have:
                found.append(path)
        return found

    # -- the save in the game ---------------------------------------------

    def _refuse_while_running(self) -> None:
        reason = self._lock_reason()
        if reason:
            raise LibraryError(f"{reason} Close the game first: it writes its save when it quits.")

    def _live_or_refuse(self, game_root, steam_id: str) -> LiveSave | None:
        live = self.live_for(game_root, steam_id)
        if live is not None and live.foreign:
            raise LibraryError(f"the save in the game is not \"{live.entry.nickname}\" any more - it is "
                               "another save (put there by hand, perhaps). Keep it as a save of its own "
                               "first, so neither is lost")
        if live is not None and not live.known:
            raise LibraryError("the save in the game is not in the library yet - add it first, "
                               "so it is not lost")
        return live

    def add_live(self, game_root, steam_id: str, nickname: str, mods: dict | None = None) -> Entry:
        """The save in the game, kept in the library as a new entry, which is now the one in the game."""
        self._refuse_while_running()
        live = self.live_for(game_root, steam_id)
        if live is None:
            raise LibraryError(f"there is no save in the game for account {steam_id}")
        entry = self._new_entry(live.path, steam_id, nickname, mods)
        self._set_live(steam_id, entry.id)
        return entry

    def store_live(self, game_root, steam_id: str) -> Entry | None:
        """What the game has added to its save since, copied into the save's entry."""
        self._refuse_while_running()
        live = self._live_or_refuse(game_root, steam_id)
        if live is None or live.in_sync:
            return live.entry if live else None
        check_save(live.path)
        entry = live.entry
        _copy_checked(live.path, entry.save)
        bak = live.path.with_name(BAK_NAME)
        if bak.is_file():
            _copy_checked(bak, entry.bak)
        entry.sha256 = sha256(entry.save)
        entry.updated = _now_text(self._clock())
        self._write_entry(entry)
        self._refresh_cloud(entry)
        return entry

    def _backup_live(self, live: LiveSave) -> Path:
        folder = self.backups / live.steam_id
        folder.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d-%H%M%S", time.localtime(self._clock()))
        backup, number = folder / f"brggame-{stamp}-before-switch.sav", 1
        while backup.exists():          # two switches in one second keep both
            number += 1
            backup = folder / f"brggame-{stamp}-before-switch-{number}.sav"
        shutil.copy2(live.path, backup)
        if sha256(backup) != sha256(live.path):
            raise LibraryError(f"the backup at {backup} did not come out the same - nothing was switched")
        return backup

    def switch_to(self, game_root, entry_id: str) -> Entry:
        """Put ``entry_id`` in the game, after the save there goes back into its own entry.

        The mods are not touched here: the caller shows the entry's mod list
        and applies it only once the player says yes.
        """
        self._refuse_while_running()
        entry = self.get(entry_id)
        if not entry.steam_id:
            raise LibraryError(f"\"{entry.nickname}\" has no Steam account recorded")
        folder = Path(game_root) / "Savedata" / entry.steam_id
        live = self._live_or_refuse(game_root, entry.steam_id)
        if live is not None:
            self.store_live(game_root, entry.steam_id)
            if live.entry.id == entry.id:
                return self.get(entry.id)
            self._backup_live(live)
        check_save(entry.save)
        folder.mkdir(parents=True, exist_ok=True)
        _copy_checked(entry.save, folder / savegame.SAVE_NAME)
        # the game's fallback copy has to belong to the same save
        _copy_checked(entry.bak if entry.bak.is_file() else entry.save, folder / BAK_NAME)
        self._set_live(entry.steam_id, entry.id)
        return entry

    def start_fresh(self, game_root, steam_id: str, profile_name: str, nickname: str,
                    mods: dict | None = None, start: str = newsave.TUTORIAL) -> Entry:
        """A brand-new game in place of the save in the game, which stays in its own entry.

        The save is not taken away - Steam Cloud would put it straight back -
        but replaced by a new game's save (newsave.py), which goes in the
        library as a new entry and is the save in the game from now on.
        """
        self._refuse_while_running()
        live = self._live_or_refuse(game_root, steam_id)
        data = newsave.new_save(steam_id, profile_name, start)   # checked before anything moves
        if live is not None:
            self.store_live(game_root, steam_id)
            self._backup_live(live)
        folder = Path(game_root) / "Savedata" / steam_id
        folder.mkdir(parents=True, exist_ok=True)
        staged = self.root / f".new-save-{uuid.uuid4().hex[:8]}.sav"
        staged.parent.mkdir(parents=True, exist_ok=True)
        try:
            staged.write_bytes(data)
            entry = self._new_entry(staged, steam_id, nickname or f"New game - {profile_name}", mods)
        finally:
            staged.unlink(missing_ok=True)
        _copy_checked(entry.save, folder / savegame.SAVE_NAME)
        _copy_checked(entry.save, folder / BAK_NAME)
        self._set_live(steam_id, entry.id)
        return entry
