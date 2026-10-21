"""Decals (the decal stock, and the decals each fighter wears) and weapon mastery.

The stock is soul.skl.psskl: one entry per decal held, {sklid, cnt, updated,
is_checked}; a new game has {} there. The game holds no more than 99 of one
decal (BrgUIMenu_SkillPut: a premium decal cannot come off while 99 are held),
and its list drops a decal whose count reaches 0, so an entry set to 0 goes.

What a fighter wears is soul.skl.eqskl[uid]: {cid, sklid, slot} per decal, slot 0
to 8 - the game has 9 places (PutSticker refuses 9 and up), as many usable as
the fighter's decal slots - and never the same decal twice on one fighter
(MSHR_KAIKAN.TXT_MS_SAME); a decal and its premium version are different decals.
In the game a decal put on comes out of the stock, and one taken off goes back if
it is premium; here the stock is set on its own, so putting one on or taking one
off leaves the stock as it is.

Weapon mastery is soul.expert: {ptarmtp, abp, lvl, is_checked} per weapon type.
The game works the level out from the points (abp) against
master_expert_lvl_reward, so a level set here sets the points to that level's
least; abp -1 means the type was never used. Ported with the help of oberlinx's
LET IT DIE save editor (used with permission).
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

from .saveedit import EditError, SaveDocument

COUNT_MAX = 99
WORN_MAX = 9


def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# -- what the game has ----------------------------------------------------------------

@dataclass(frozen=True)
class Decal:
    ident: str
    name: str
    description: str
    premium: bool
    platform: int
    category: str

    @property
    def label(self) -> str:
        return self.name + (" (Premium)" if self.premium else "")


@dataclass
class DecalCatalog:
    decals: dict = field(default_factory=dict)          # sklid -> Decal
    categories: dict = field(default_factory=dict)      # category id -> its name
    weapon_types: dict = field(default_factory=dict)    # ptarmtp -> its name (only named ones: the game's own)
    thresholds: dict = field(default_factory=dict)      # ptarmtp -> {level: points it needs}

    @classmethod
    def read(cls, db_path: Path | str | None) -> "DecalCatalog":
        cat = cls()
        if not db_path or not Path(db_path).is_file():
            return cat
        try:
            con = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
        except sqlite3.Error:
            return cat
        try:
            try:
                text = {f"{s}.{i}": str(t) for s, i, t in con.execute(
                    "SELECT sct, id, txt FROM master_text WHERE lang = 'int'") if t}
            except sqlite3.Error:
                text = {}
            try:
                cat.categories = {str(i): text.get(str(n), str(i)) for i, n in con.execute(
                    "SELECT id, name FROM master_skill_category")}
            except sqlite3.Error:
                pass
            try:
                for row in con.execute("SELECT id, name, desc, premium, platform, category, val0, val1, val2, val3, "
                                       "val4, val5 FROM master_skill"):
                    ident, name, desc, premium, platform, category = row[:6]
                    shown = text.get(str(name))
                    if not shown:
                        continue
                    cat.decals[str(ident)] = Decal(str(ident), shown, _description(text.get(str(desc), ""), row[6:]),
                                                   bool(_int(premium)), _int(platform), str(category or ""))
            except sqlite3.Error:
                cat.decals.clear()
            try:
                for ident, name in con.execute("SELECT id, name FROM master_ptarm_type"):
                    if text.get(str(name)):
                        cat.weapon_types[str(ident)] = text[str(name)]
                for ident, level, points in con.execute("SELECT ptarmtp, lvl, abp FROM master_expert_lvl_reward"):
                    cat.thresholds.setdefault(str(ident), {})[_int(level)] = _int(points)
            except sqlite3.Error:
                cat.weapon_types.clear()
                cat.thresholds.clear()
        finally:
            con.close()
        return cat

    def label(self, sklid: str) -> str:
        decal = self.decals.get(sklid)
        return decal.label if decal else sklid

    def offered(self, include_ps: bool = False) -> list[Decal]:
        return sorted((d for d in self.decals.values() if include_ps or d.platform != 1),
                      key=lambda d: (d.name.lower(), d.premium, d.ident))

    # -- mastery --

    def top_level(self, ptarmtp: str) -> int:
        return max(self.thresholds.get(ptarmtp, {}), default=1)

    def level_of(self, ptarmtp: str, points: int) -> int:
        level = 1
        for lvl, needs in sorted(self.thresholds.get(ptarmtp, {}).items()):
            if points >= needs:
                level = lvl
        return level

    def points_for(self, ptarmtp: str, level: int) -> int:
        return 0 if level <= 1 else self.thresholds.get(ptarmtp, {}).get(level, 0)


def _description(text: str, values) -> str:
    """The game's decal text as one plain line: #0..#5 filled in, its line breaks and // joins
    turned to spaces."""
    for n, value in enumerate(values):
        text = text.replace(f"#{n}", str(_int(value)))
    return " ".join(text.replace("//", " ").split())


# -- the stock ------------------------------------------------------------------------

def _stock_list(save: SaveDocument, make: bool = False) -> list | None:
    skl = save.get(("soul", "skl"))
    if not isinstance(skl, dict):
        if not make:
            return None
        raise EditError("this save has no decal list")
    if not isinstance(skl.get("psskl"), list):
        if not make:
            return None
        skl["psskl"] = []
    return skl["psskl"]


def stock(save: SaveDocument) -> dict[str, int]:
    rows = _stock_list(save) or []
    return {str(r.get("sklid")): _int(r.get("cnt")) for r in rows if isinstance(r, dict)}


def set_stock(save: SaveDocument, catalog: DecalCatalog, counts: dict[str, int], now: int | None = None) -> None:
    """Set how many of each decal are held (0 to 99; 0 takes it off the list)."""
    held = stock(save)
    wanted = {k: max(0, min(COUNT_MAX, int(v))) for k, v in counts.items() if int(v) != held.get(k, 0)}
    if not wanted:
        return
    rows = _stock_list(save, make=True)
    now = int(now if now is not None else time.time())
    for sklid, count in wanted.items():
        row = next((r for r in rows if isinstance(r, dict) and r.get("sklid") == sklid), None)
        label = f"Decals: {catalog.label(sklid)}"
        if row is None:
            rows.append({"sklid": sklid, "cnt": count, "updated": now, "is_checked": 0})
            save.note(label, 0, count)
        elif count == 0:
            rows.remove(row)
            save.note(label, held.get(sklid, 0), 0)
        else:
            save.note(label, row.get("cnt"), count)
            row["cnt"] = count
            row["updated"] = now


# -- what fighters wear -------------------------------------------------------------------

def _worn_list(save: SaveDocument, make: bool = False) -> list | None:
    skl = save.get(("soul", "skl"))
    if not isinstance(skl, dict):
        if make:
            raise EditError("this save has no decal list")
        return None
    holder = skl.get("eqskl")
    if not isinstance(holder, dict):
        if not make:
            return None
        holder = skl["eqskl"] = {}
    if not isinstance(holder.get(save.uid), list):
        if not make:
            return None
        holder[save.uid] = []
    return holder[save.uid]


def worn(save: SaveDocument, cid: str) -> dict[int, str]:
    """slot -> sklid, for one fighter."""
    rows = _worn_list(save) or []
    return {_int(r.get("slot")): str(r.get("sklid")) for r in rows if isinstance(r, dict) and r.get("cid") == cid}


def check_worn(layout: dict[int, str], slots: int, catalog: DecalCatalog, name: str = "") -> str:
    """Why the game would not have this fighter wear these, or ""."""
    who = name or "This fighter"
    used = [s for s, d in layout.items() if d]
    if any(not 0 <= s < WORN_MAX for s in used):
        return f"{who} has a decal outside the game's {WORN_MAX} places"
    if len(used) > slots:
        return f"{who} wears {len(used)} decals but has {slots} decal slot(s)"
    beyond = [s for s in used if s >= slots]
    if beyond:
        return f"{who} has a decal in slot {beyond[0] + 1}, past their {slots} decal slot(s)"
    seen = set()
    for s in sorted(used):
        if layout[s] in seen:
            return f"{who} wears {catalog.label(layout[s])} twice - the game allows one of each"
        seen.add(layout[s])
    return ""


def set_worn(save: SaveDocument, catalog: DecalCatalog, cid: str, layout: dict[int, str], slots: int,
             name: str = "") -> None:
    """What one fighter wears, slot by slot. Their entries are changed in place, so the rest of
    the list keeps its order."""
    before = worn(save, cid)
    layout = {int(s): d for s, d in layout.items() if d}
    if layout == before:
        return
    why = check_worn(layout, slots, catalog, name)
    if why:
        raise EditError(why)
    rows = _worn_list(save, make=True)
    kept = []
    for row in rows:
        if isinstance(row, dict) and row.get("cid") == cid:
            slot = _int(row.get("slot"))
            if slot not in layout:
                continue
            if row.get("sklid") != layout[slot]:
                row["sklid"] = layout[slot]
        kept.append(row)
    there = {_int(r.get("slot")) for r in kept if isinstance(r, dict) and r.get("cid") == cid}
    for slot in sorted(layout):
        if slot not in there:
            kept.append({"cid": cid, "sklid": layout[slot], "slot": slot})
    rows[:] = kept
    who = f"{name or 'Fighter'}'s decals"
    for slot in sorted(set(before) | set(layout)):
        was, now = before.get(slot), layout.get(slot)
        if was != now:
            save.note(f"{who}: slot {slot + 1}", catalog.label(was) if was else "(empty)",
                      catalog.label(now) if now else "(empty)")


# -- weapon mastery ----------------------------------------------------------------------

def mastery(save: SaveDocument) -> dict[str, dict]:
    rows = save.get(("soul", "expert"))
    return {str(r.get("ptarmtp")): r for r in rows if isinstance(r, dict)} if isinstance(rows, list) else {}


def set_mastery(save: SaveDocument, catalog: DecalCatalog, levels: dict[str, int]) -> None:
    """Each weapon type to a level (held to the game's table), its points to match."""
    rows = save.get(("soul", "expert"))
    if not isinstance(rows, list):
        if not levels:
            return
        raise EditError("this save has no weapon mastery list")
    have = mastery(save)
    for ptarmtp, level in levels.items():
        level = max(1, min(int(level), catalog.top_level(ptarmtp)))
        row = have.get(ptarmtp)
        if row is None:
            row = {"ptarmtp": ptarmtp, "abp": -1, "lvl": 1, "is_checked": 1}
            if level == 1:
                continue
            rows.append(row)
        points = _int(row.get("abp"), -1)
        before = _int(row.get("lvl"), 1)
        if catalog.level_of(ptarmtp, max(points, 0)) != level or (level > 1 and points < 0):
            row["abp"] = points if level <= 1 and points < 0 else catalog.points_for(ptarmtp, level)
        if before != level:
            row["lvl"] = level
            save.note(f"Weapon Mastery: {catalog.weapon_types.get(ptarmtp, ptarmtp)}", before, level)
