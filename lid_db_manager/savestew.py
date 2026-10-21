"""The Mushroom Stew's decal list.

A normal stew does not roll a decal when it is bought: the game rolls a long list
ahead from the stew's odds (master_skillgacha_odds for SKLGACH_NORMAL_OFFLINE) and
keeps it in the save, soul.skl.gacha.normal.sklids, taking each pull from the end.
So a decal a mod adds to the stew does not come until that list runs out - often
hundreds of pulls. A new game keeps {} there and the game rolls a fresh list at
the first stew, from the odds as they are then; clearing the list here puts the
save back to that, so the next stew rolls a new list with the new decals in it.
(lid-stewpull.dll passes normal stews to the game's own draw, so it rolls the
same way.)
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from .saveedit import SaveDocument

PATH = ("soul", "skl", "gacha", "normal", "sklids")
NORMAL_STEW = "SKLGACH_NORMAL_OFFLINE"


@dataclass
class StewOdds:
    """The decals the normal stew can give now, by its odds in this save's game data."""
    odds: dict = field(default_factory=dict)        # sklid -> odds

    @classmethod
    def read(cls, db_path: Path | str | None) -> "StewOdds":
        found = cls()
        if not db_path or not Path(db_path).is_file():
            return found
        try:
            con = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
        except sqlite3.Error:
            return found
        try:
            row = con.execute("SELECT odds_id FROM master_skillgacha WHERE id = ?", (NORMAL_STEW,)).fetchone()
            if row is None:
                row = con.execute("SELECT odds_id FROM master_skillgacha WHERE type = 'NORMAL' "
                                  "ORDER BY menu_priority LIMIT 1").fetchone()
            if row is not None:
                found.odds = {str(s): int(o) for s, o in con.execute(
                    "SELECT sklid, odds FROM master_skillgacha_odds WHERE id = ? AND odds > 0", (row[0],))}
        except (sqlite3.Error, TypeError, ValueError):
            pass
        finally:
            con.close()
        return found


def queue(save: SaveDocument) -> list[str]:
    """The decals rolled ahead, in the order the save keeps them (the next pull is the last)."""
    rows = save.get(PATH)
    return [str(s) for s in rows] if isinstance(rows, list) else []


def coming(save: SaveDocument, count: int = 20) -> list[str]:
    """The next pulls, next first."""
    return list(reversed(queue(save)))[:count]


def missing(save: SaveDocument, odds: StewOdds) -> list[str]:
    """Decals the stew can give now that the rolled list never will."""
    have = set(queue(save))
    return [s for s in odds.odds if s not in have]


def clear(save: SaveDocument) -> bool:
    """Empty the list the way a new game has it ({}), so the next stew rolls a new one."""
    normal = save.get(PATH[:-1])
    rows = save.get(PATH)
    if not isinstance(normal, dict) or not isinstance(rows, list) or not rows:
        return False
    normal["sklids"] = {}
    save.note("Mushroom Stew: decals rolled ahead", f"{len(rows):,}", "none (the next stew rolls a new list)")
    return True
