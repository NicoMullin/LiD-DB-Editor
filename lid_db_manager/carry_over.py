"""Bring a player's mods and history over from an older copy of the manager.

Everything the manager needs to undo what it did lives in its own folder:

    state.json      which mods are applied, in what order, with what settings
    snapshots/      what each mod overwrote in masters.db, to put it back
    backups/        the stock copies of game files it changed, and dated
                    copies of the database
    cache/          the texture index, slow to rebuild

A new release unzipped somewhere else has none of it while the game is still
modded, which is why "copy your files over" kept going wrong: copy too little
and the new copy cannot undo anything; copy the whole folder and the old
program and the old shipped mods come with it. This copies exactly the data,
and leaves the new release's own program and shipped mods alone. Mods whose
version moved on are then swapped by the normal save, which already takes an
old version off before putting the new one on.

The old folder is only ever read.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .migrations import RETIRED_DIRNAME
from .paths import AppPaths

STATE_FILE = "state.json"
EXE_NAME = "LID DB Mod Manager.exe"
# Copied whole, in this order; state.json goes last of all, so a copy that
# fails halfway leaves this install looking as new as it was.
DATA_DIRS = ("snapshots", "backups", "cache")
VANILLA_DIR = "LiD Vanilla DB"
# Releases up to 0.10.1 carried their clean databases inside the program, here.
# They are still clean copies, so they come across too: a player updating from
# one of those never has to download a clean database at all.
BUNDLED_VANILLA_DIR = "_internal/" + VANILLA_DIR
_SKIPPED_MOD_FOLDERS = {RETIRED_DIRNAME, "__pycache__"}


class CarryOverError(Exception):
    """Nothing was copied, and this says why."""


@dataclass
class OldInstall:
    """An earlier copy of the manager, as far as its state file tells."""

    root: Path
    last_used: float = 0.0
    applied: list[str] = field(default_factory=list)
    enabled: list[str] = field(default_factory=list)
    db_path: str = ""

    @property
    def used(self) -> bool:
        """Did anybody ever point it at a game? A fresh unzip has not."""
        return bool(self.db_path or self.applied or self.enabled)


@dataclass
class CarryOverPlan:
    old: OldInstall
    new_root: Path
    data_dirs: list[str] = field(default_factory=list)
    own_mods: list[str] = field(default_factory=list)       # copied: the player's own
    shipped_mods: list[str] = field(default_factory=list)   # left: this release has its own
    # Paths relative to the old folder; each lands in this one's LiD Vanilla DB.
    vanilla_files: list[str] = field(default_factory=list)


@dataclass
class CarryOverReport:
    plan: CarryOverPlan
    files_copied: int = 0


# -- finding one ---------------------------------------------------------------

def read_install(root: Path) -> OldInstall | None:
    """The install at ``root``, or None when there is no readable state file."""
    root = Path(root)
    state_file = root / STATE_FILE
    if not state_file.is_file():
        return None
    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return OldInstall(
        root=root,
        last_used=state_file.stat().st_mtime,
        applied=sorted((data.get("applied") or {}).keys()),
        enabled=[str(m) for m in data.get("enabled_mods") or []],
        db_path=str(data.get("db_path") or ""),
    )


def find_install(path: Path) -> OldInstall | None:
    """What a dropped or picked path points at, if it is a copy of the manager.

    Accepts the folder itself, its .exe, its state.json, or the folder a zip
    was unpacked into when the manager sits one level inside it.
    """
    path = Path(path)
    if path.is_file():
        if path.name.lower() in (STATE_FILE, EXE_NAME.lower()):
            return read_install(path.parent)
        return None
    if not path.is_dir():
        return None
    found = read_install(path)
    if found is not None and _looks_like_manager(path):
        return found
    inside = [read_install(child) for child in _subfolders(path) if _looks_like_manager(child)]
    inside = [i for i in inside if i is not None]
    return inside[0] if len(inside) == 1 else None


def _looks_like_manager(folder: Path) -> bool:
    # A built copy has its .exe; a copy run from source has run.py. A state
    # file alone is not enough to call some random folder a manager.
    return (folder / STATE_FILE).is_file() and (
        (folder / EXE_NAME).is_file() or (folder / "run.py").is_file()
    )


def _subfolders(folder: Path) -> list[Path]:
    try:
        return sorted(p for p in Path(folder).iterdir() if p.is_dir())
    except OSError:
        return []


# -- planning and copying -------------------------------------------------------

def plan(old: OldInstall, paths: AppPaths, *, has_applied_here: bool) -> CarryOverPlan:
    """What would be copied. Raises CarryOverError when it should not happen."""
    new_root = Path(paths.root).resolve()
    old_root = Path(old.root).resolve()
    if old_root == new_root:
        raise CarryOverError("That is this copy of the manager, not an older one.")
    if not old.used:
        raise CarryOverError(
            f"The copy in {old.root} was never pointed at a game, so there is nothing "
            "to bring over from it."
        )
    if has_applied_here:
        raise CarryOverError(
            "This copy has already applied mods to your game, so bringing the old "
            "copy's history in now would not match what is in the game. Untick "
            "everything here and click Save Mod List first, then try again - or "
            "carry on with this copy as it is."
        )

    result = CarryOverPlan(old=old, new_root=new_root)
    result.data_dirs = [d for d in DATA_DIRS if (old_root / d).is_dir()]
    shipped_here = {p.name.lower() for p in _subfolders(paths.mods_dir)}
    for folder in _subfolders(old_root / "mods"):
        if folder.name in _SKIPPED_MOD_FOLDERS:
            continue
        if folder.name.lower() in shipped_here:
            result.shipped_mods.append(folder.name)
        else:
            result.own_mods.append(folder.name)
    vanilla_new = new_root / VANILLA_DIR
    taken: set[str] = set()
    for source in (VANILLA_DIR, BUNDLED_VANILLA_DIR):
        vanilla_old = old_root / source
        if not vanilla_old.is_dir():
            continue
        for item in sorted(vanilla_old.rglob("*")):
            relative = item.relative_to(vanilla_old).as_posix()
            if item.is_file() and relative not in taken and not (vanilla_new / relative).exists():
                taken.add(relative)
                result.vanilla_files.append(f"{source}/{relative}")
    return result


def carry_over(plan: CarryOverPlan) -> CarryOverReport:
    """Copy what ``plan`` lists into this install. The old folder is only read."""
    report = CarryOverReport(plan=plan)
    old_root, new_root = Path(plan.old.root), Path(plan.new_root)
    try:
        for name in plan.data_dirs:
            report.files_copied += _copy_tree(old_root / name, new_root / name)
        for name in plan.own_mods:
            report.files_copied += _copy_tree(old_root / "mods" / name, new_root / "mods" / name)
        for relative in plan.vanilla_files:
            source = old_root / relative
            target = new_root / VANILLA_DIR / relative.split(VANILLA_DIR + "/", 1)[1]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            report.files_copied += 1
        # Last, and in one step: until this lands the install still starts as new.
        temporary = new_root / (STATE_FILE + ".incoming")
        shutil.copy2(old_root / STATE_FILE, temporary)
        os.replace(temporary, new_root / STATE_FILE)
        report.files_copied += 1
    except OSError as exc:
        raise CarryOverError(f"Copying stopped partway: {exc}") from exc
    return report


def _copy_tree(source: Path, target: Path) -> int:
    count = 0
    for item in source.rglob("*"):
        if item.is_dir():
            continue
        destination = target / item.relative_to(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, destination)
        count += 1
    return count
