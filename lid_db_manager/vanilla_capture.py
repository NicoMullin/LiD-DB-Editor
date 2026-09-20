"""Keeping the game's own ``masters.db`` as the clean copy for a new build.

A mod is "what this database has that a clean one does not", so every game
update needs a clean copy of the new build before anything can be compared
(see vanilla_library). Waiting for one to be shipped leaves a player stuck.

There is no need to download anything: right after an update, the file sitting
in the game folder *is* the new build's clean copy - Steam has just written it
over, mods and all. The only question is whether it is still as Steam left it,
and that is what the checks here answer:

    1. The manager's own note (db_record) is not in it. A save always writes
       that note, so its presence means mods.
    2. It carries the same timestamp as the rest of the game's files. Steam
       writes them all in one go, so anything changed afterwards - by this
       manager, by TFC Installer, by hand - is minutes or days later.
    3. What it changes against the newest clean copy already held is shaped
       like a patch, not like a mod: only tables that real updates have been
       seen to touch, plus any table an update adds.

All three have to agree. When they do not, nothing is kept and the reason is
returned - a wrong clean copy is worse than none at all, because then the
game's own patch reads as a mod.

Nothing here writes to the game. The only file written is the manager's own
copy under ``LiD Vanilla DB/<build>/masters.db``.
"""

from __future__ import annotations

import shutil
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from . import dbdiff

# Every table the shipped clean copies show a real game update changing:
# 1.86 -> 1.87 -> 1.88 -> 1.89 -> 5.0.4.2. Tables an update adds are allowed
# too (see _unexpected_tables); this list is only about ones it rewrites.
PATCH_TABLES = frozenset({
    "master_area_connect_escalator",
    "master_area_connect_node",
    "master_area_connect_node_repeat_straight",
    "master_area_escalator",
    "master_area_setting_unit",
    "master_automaticshop_lineup",
    "master_const_int",
    "master_const_str",
    "master_credit",
    "master_credit_steam",
    "master_ngword",
    "master_text",
    "master_tgtpnt",
    "monitoring",
    "sqlite_sequence",
})

COOKED = "BrgGame/CookedPCConsole"
# How many game packages to read timestamps from when working out when Steam
# last wrote the game. Mods replace a few; the time most of them share is the
# update's.
SAMPLE = 120
# Steam takes a while to write 44 GB, so "the same run" is generous. A mod
# applied later is minutes away at the very least, and normally days.
SAME_RUN_SECONDS = 3600


@dataclass
class CaptureResult:
    """What came of an attempt to keep the game's database as a clean copy."""

    kept: Path | None = None
    label: str = ""
    # Why it was not kept, in words fit to show someone. Empty when it was.
    reason: str = ""
    unexpected_tables: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.kept is not None


def steam_wrote_at(game_root: Path) -> float | None:
    """When Steam last wrote this game, from the time most of its files share.

    Taking the most common timestamp rather than any one file's means the few
    a mod has replaced - and the executable, which the file check can be
    switched off in - do not move the answer.
    """
    cooked = Path(game_root) / COOKED
    if not cooked.is_dir():
        return None
    stamps: Counter = Counter()
    for number, package in enumerate(sorted(cooked.glob("*.upk"))):
        if number >= SAMPLE:
            break
        try:
            stamps[round(package.stat().st_mtime)] += 1
        except OSError:
            continue
    if not stamps:
        return None
    when, seen = stamps.most_common(1)[0]
    return float(when) if seen > 1 else None


def left_as_steam_wrote_it(db_path: Path, game_root: Path) -> bool:
    """Is this database still from the same write as the rest of the game?"""
    written = steam_wrote_at(game_root)
    if written is None:
        return False
    try:
        return abs(Path(db_path).stat().st_mtime - written) <= SAME_RUN_SECONDS
    except OSError:
        return False


def unexpected_tables(reference: Path, candidate: Path) -> list[str]:
    """Tables the candidate changes that no game update has been seen to touch.

    A table the update *adds* is not unexpected: new content arrives that way,
    and a mod cannot add one without the manager knowing.
    """
    delta = dbdiff.compare(Path(reference), Path(candidate))
    return sorted(
        table.table
        for table in delta.tables
        if not table.empty
        and not table.is_new_table
        and table.table.lower() not in PATCH_TABLES
    )


def keep(db_path: Path, vanilla_dir: Path, label: str) -> Path:
    """Copy the database in as the clean copy for ``label``. Overwrites nothing."""
    folder = Path(vanilla_dir) / _folder_name(label)
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / "masters.db"
    if destination.exists():
        raise FileExistsError(f"{destination} is already there")
    shutil.copy2(Path(db_path), destination)
    return destination


def _folder_name(label: str) -> str:
    """A folder name from a build string. Only a label - the build is read
    back out of the file itself (vanilla_library)."""
    keep_chars = [c if (c.isalnum() or c in "._-") else "_" for c in label.strip()]
    name = "".join(keep_chars).strip("._-") or "unknown"
    return name[:60]
