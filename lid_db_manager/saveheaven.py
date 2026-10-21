"""A Heaven run, part 2: moving to another boss floor, locking the boss floors ahead, "I'm
feeling lucky" and the Jackals' loot. (Part 1, ending a run and going back to its last boss
floor, is saverun.py.)

All four are oberlinx's, confirmed in game with their editor (docs/in-game-status.md), and only
work on a save quit in the middle of a Heaven run:
- Floor move: a fighter paused on a boss floor goes to another boss floor of the same route
  that uses the same map (so the saved position still fits). Every reference to the floor in
  root.floor is renamed, the boss waiting there gets the new floor's boss level, the planned
  boss floors (soul.hvntrinfo) shift with it, and the unopened rewards can be rolled again from
  the new floor's tables. A Screamer Pit that cannot appear on the new floor turns into its
  twin (RUSH1-5 below 100F, RUSH6-10 from 100F: same map and spawn points).
- Boss floor lock: the planned boss floors ahead become Dons, mid-bosses, Screamer Pits, or
  Dons and mid-bosses (or one particular boss), with the arenas' own odds
  (master_ref_boss_area_setting) and rewards from the floors' tables (master_floor_drop_gen).
  The game plans only the next 3; the lock can write every remaining one, which the game keeps.
- I'm feeling lucky: plans every boss floor left and hides a few blueprints the player does
  not have in their large boxes and treasure enemies. Nothing says where or how many.
- Jackal loot: what each Jackal on the floor drops (master_jackal's odds). The game rolls them
  all again whenever it builds a new floor, so it holds only on the floor the run is on.

Every random draw comes from a seed the page keeps, so what the page shows and what is written
at Save changes are the same.
Ported with the help of oberlinx's LET IT DIE save editor (used with permission).
"""

from __future__ import annotations

import copy
import json
import random
import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

from .saveedit import SaveDocument
from .saverun import HeavenFloors, _fighter
from .savestorage import Catalog

RARE_CHANCE = 0.55              # share of rare Legendary Chests in the game's own plans (oberlinx: 21 of 38)
GAME_PLAN_AHEAD = 3             # boss floors the game plans past the one the fighter is on
BOSS_NAMES = {"mboss": {1: "COEN", 2: "JIN-DIE", 3: "GOTO-9", 4: "U-10"},
              "boss": {1: "COEN Mk-2", 2: "JIN-DIE Mk-2", 3: "GOTO-9 Mk-2", 4: "U10 Mk-2"}}
# lock modes: the arena kinds that count, the kinds to fall back on, the label and the noun
LOCK_MODES = {
    "don": (("boss",), ("mboss",), "Don only (main bosses)", "Don"),
    "mboss": (("mboss",), (), "Mid-boss only", "mid-boss"),
    "pit": (("pit",), (), "Screamer Pit only", "Screamer Pit"),
    "boss": (("boss", "mboss"), (), "Don or mid-boss (no Screamer Pits)", "Don or mid-boss"),
}
# rewards on the floors a lock writes: all rolled now, only the Legendary Chest (the game rolls
# the rest), the same but never shown, or none (the game then makes no Legendary Chest)
LOCK_REWARDS = {
    "roll": "Roll all now: Legendary Chest, large boxes, boss drop",
    "chest": "Legendary Chest now, the rest rolled by the game",
    "hidden": "Spoiler free: like the one above, and the editor never shows the rewards",
    "none": "None: the game rolls the boxes and boss drop, no Legendary Chest",
}
DROP_TYPES = ("PTGENTP_TRBOX_L", "PTGENTP_TRBOX_SPXL_RARE", "PTGENTP_SPXL", "PTGENTP_TRZAKO")
# parts with blueprint items the game never hands out (oberlinx's lists)
NO_BLUEPRINT = {"PT_ARM_WP000_001", "PT_ARM_WP011_0B1", "PT_ARM_WP001_0B1", "PT_ARM_WP023_001",
                "PT_ARM_WP025_0A4", "PT_MIL_HEAD_1001", "PT_MIL_TOPS_1001", "PT_MIL_BTM_1001",
                "PT_MIL_HEAD_1002", "PT_MIL_TOPS_1002", "PT_MIL_BTM_1002"}
# I'm feeling lucky: share of the run's blueprint pool still unknown -> how many it may hide
LUCKY_TIERS = (0.5, 0.25, 0.1)
LUCKY_CEILING = {"main": (5, 3, 2, 1), "neo": (3, 2, 2, 1)}
LUCKY_COUNT_WEIGHTS = {"main": (30, 30, 22, 12, 6), "neo": (45, 35, 20)}
LUCKY_SHORT_DRAW = 0.12         # a press places just one this often
LUCKY_RARE_BOOST = 1.5          # the rarest quarter of the pool is picked a little more often
MASKS = tuple(f"ITMP_GAS_HEAD_0N{n}" for n in range(1, 9))


def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _list(value) -> list:
    return value if isinstance(value, list) else []


def _pick(rng: random.Random, rows: list, weight) -> object:
    """A weighted pick; the first row when every weight is 0, None for no rows."""
    if not rows:
        return None
    total = sum(weight(r) for r in rows)
    if total <= 0:
        return rows[0]
    x = rng.random() * total
    for row in rows:
        x -= weight(row)
        if x < 0:
            return row
    return rows[-1]


@dataclass
class Heaven(HeavenFloors):
    """Heaven's boss floors, their arenas and drop tables, and the Jackals."""
    levels: dict = field(default_factory=dict)      # flrid -> the boss's level there (mbslvlmin)
    arenas: dict = field(default_factory=dict)      # flrid -> [(refareaid, freq)]
    kis: dict = field(default_factory=dict)         # arena areaid -> its enemy set (KIS)
    drops: dict = field(default_factory=dict)       # (flrid, areaid, type) -> [(refareaid, grp, freq)]
    jackals: dict = field(default_factory=dict)     # type -> master_jackal row
    groups: dict = field(default_factory=dict)      # master_item grp -> [itemid]
    catalog: Catalog = field(default_factory=Catalog)
    _chances: dict = field(default_factory=dict, repr=False)

    @classmethod
    def read(cls, db_path: Path | str | None, catalog: Catalog | None = None) -> "Heaven":
        found = cls(catalog=catalog or Catalog.read(db_path))
        for itemid, row in found.catalog.items.items():
            found.groups.setdefault(_int(row.get("grp"), -1), []).append(itemid)
        if not db_path or not Path(db_path).is_file():
            return found
        try:
            con = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
        except sqlite3.Error:
            return found
        con.row_factory = sqlite3.Row
        queries = (
            ("SELECT id, no, areaid, mbsmax, mbslvlmin FROM master_floor WHERE stgid = 'S_HVN'", found._floor),
            ("SELECT flrid, refareaid, freq FROM master_ref_boss_area_setting",
             lambda r: found.arenas.setdefault(str(r["flrid"]), []).append((str(r["refareaid"]), _int(r["freq"])))),
            ("SELECT areaid, kis FROM master_area_setting_unit WHERE stgid = 'S_HVN' AND unit = 'HEAVEN_BOSS'",
             lambda r: found.kis.__setitem__(str(r["areaid"]), str(r["kis"] or ""))),
            ("SELECT d.flrid, d.areaid, d.refareaid, d.type, d.grp, d.freq FROM master_floor_drop_gen d "
             "JOIN master_floor f ON f.id = d.flrid AND f.areaid = d.areaid WHERE f.stgid = 'S_HVN' "
             "AND f.mbsmax > 0 AND (d.type IN ('PTGENTP_TRBOX_L', 'PTGENTP_TRBOX_SPXL_RARE', 'PTGENTP_SPXL', "
             "'PTGENTP_TRZAKO') OR d.type LIKE 'PTGENTP_MBOSS%')",
             lambda r: found.drops.setdefault((str(r["flrid"]), str(r["areaid"]), str(r["type"])), []).append(
                 (str(r["refareaid"] or "-"), _int(r["grp"], -1), _int(r["freq"])))),
            ("SELECT * FROM master_jackal", lambda r: found.jackals.__setitem__(str(r["type"]), dict(r))),
        )
        try:
            for sql, take in queries:
                try:
                    for row in con.execute(sql):
                        take(row)
                except sqlite3.Error:
                    pass
        finally:
            con.close()
        return found

    def _floor(self, row) -> None:
        self.floors[str(row["id"])] = (_int(row["no"]), str(row["areaid"] or ""), _int(row["mbsmax"]))
        self.levels[str(row["id"])] = _int(row["mbslvlmin"])

    # -- floors and routes --

    def no(self, flrid: str) -> int:
        return (self.floors.get(flrid) or (10 ** 9,))[0]

    def areaid(self, flrid: str) -> str:
        return (self.floors.get(flrid) or (0, ""))[1]

    @staticmethod
    def route(flrid: str) -> str:
        """'main' for regular Heaven, R00-R03 for the NEO routes."""
        found = re.match(r"^HVN_FLR_(R0\d)_", str(flrid or ""))
        return found.group(1) if found else "main"

    def route_name(self, flrid: str) -> str:
        key = self.route(flrid)
        if key == "main":
            return "Regular Heaven"
        name = self.catalog.text.get(f"MOVE_MESSAGE.TXT_MOVE_NEO_{key}", "")
        return re.sub(r"\s*area$", "", name, flags=re.I) if name else f"NEO {key}"

    def map_label(self, flrid: str) -> str:
        areaid = self.areaid(flrid)
        if re.match(r"^HVN_AREA_001_R0\d$", areaid):
            return "boss map A"
        if re.match(r"^HVN_AREA_002_R0\d$", areaid):
            return "boss map B"
        return "boss floor" if areaid == "HVN_AREA_017" else ""

    def route_boss(self, flrid: str) -> list[str]:
        """The boss floors of the floor's route, in floor order."""
        key = self.route(flrid)
        return sorted((f for f, row in self.floors.items() if row[2] > 0 and self.route(f) == key), key=self.no)

    def ahead(self, flrid: str) -> list[str]:
        here = self.no(flrid)
        return [f for f in self.route_boss(flrid) if self.no(f) > here]

    # -- arenas --

    def kind(self, refareaid: str) -> tuple[str, int]:
        """("mboss" | "boss" | "pit" | "fixed", the boss or pit number)."""
        found = re.search(r"_KIS_(MBOSS|BOSS|RUSH)(\d+)$", self.kis.get(str(refareaid or ""), ""))
        if not found:
            return "fixed", 0
        return {"MBOSS": "mboss", "BOSS": "boss", "RUSH": "pit"}[found.group(1)], int(found.group(2))

    def kind_label(self, refareaid: str) -> str:
        kind, n = self.kind(refareaid)
        if kind == "mboss":
            return f"Mid-boss {BOSS_NAMES['mboss'].get(n, n)}"
        if kind == "boss":
            return f"Don {BOSS_NAMES['boss'].get(n, n)}"
        return "Screamer Pit" if kind == "pit" else "Fixed arena"

    def allowed(self, flrid: str) -> set[str]:
        return {ref for ref, _ in self.arenas.get(flrid, [])}

    def pit_twin(self, refareaid: str) -> str:
        """A Screamer Pit's twin five numbers apart (1-5 <-> 6-10), or ""."""
        kind, n = self.kind(refareaid)
        if kind != "pit":
            return ""
        want = n + 5 if n <= 5 else n - 5
        return next((a for a, k in self.kis.items() if re.search(rf"_KIS_RUSH{want}$", k)), "")

    # -- drawing rewards the way the game does: a group by its weight, then an item in it --

    def draw(self, rng: random.Random, flrid: str, ref: str, gentype: str) -> str:
        rows = [(self.groups.get(grp, []), freq) for r, grp, freq in self.drops.get((flrid, self.areaid(flrid), gentype), [])
                if r in (ref, "-") and self.groups.get(grp)]
        total = sum(w for _, w in rows)
        if not total:
            return ""
        x = rng.random() * total
        for items, w in rows:
            x -= w
            if x < 0:
                return items[int(rng.random() * len(items))]
        return rows[-1][0][0]

    def draw_for(self, rng: random.Random, flrid: str, ref: str, gentype: str) -> str:
        """From the arena's own rows when it has them (with the floor's general rows), else the general ones."""
        own = any(r == ref for r, _, _ in self.drops.get((flrid, self.areaid(flrid), gentype), []))
        return self.draw(rng, flrid, ref if own else "-", gentype)

    def entry(self, rng: random.Random, flrid: str, ref: str, rare: bool, rewards: str = "roll") -> dict | None:
        """A planned boss floor as the game writes one in soul.hvntrinfo."""
        featured = "PTGENTP_TRBOX_SPXL_RARE" if rare else "PTGENTP_SPXL"
        if rewards in ("none", "chest", "hidden"):
            rwdid = self.draw_for(rng, flrid, ref, featured) if rewards != "none" else ""
            if rewards != "none" and not rwdid:
                return None
            return {"flrid": flrid, "areaid": self.areaid(flrid), "refareaid": ref, "rwdtype": "TBRWD_ITEM",
                    "rwdid": rwdid, "is_rare": int(rare), "created": 0, "rwds": []}
        kind, n = self.kind(ref)
        rwds = []
        if kind == "mboss":
            rwds.append(("PTGENTP_MBOSS" + str(n), "-"))
        if kind == "pit":
            rwds += [("PTGENTP_TRZAKO", "-")] * 3
        rwds += [("PTGENTP_TRBOX_L", ref)] * 3
        drawn = [{"gentype": g, "rwdtype": "TBRWD_ITEM", "rwdid": self.draw_for(rng, flrid, r, g)} for g, r in rwds]
        rwdid = self.draw_for(rng, flrid, ref, featured)
        if not rwdid or any(not w["rwdid"] for w in drawn):
            return None
        return {"flrid": flrid, "areaid": self.areaid(flrid), "refareaid": ref, "rwdtype": "TBRWD_ITEM",
                "rwdid": rwdid, "is_rare": int(rare), "created": 0, "rwds": drawn}

    def unplaced(self, entry: dict) -> set[int]:
        """Planned rewards that never become chests: a pit's large boxes, a mid-boss floor's third."""
        rwds = _list((entry or {}).get("rwds"))
        kind = self.kind((entry or {}).get("refareaid"))[0]
        pit = kind == "pit" or any(isinstance(w, dict) and w.get("gentype") == "PTGENTP_TRZAKO" for w in rwds)
        mboss = not pit and kind != "boss" and (kind == "mboss" or any(
            isinstance(w, dict) and str(w.get("gentype", "")).startswith("PTGENTP_MBOSS") for w in rwds))
        out, large = set(), 0
        for i, w in enumerate(rwds):
            if isinstance(w, dict) and w.get("gentype") == "PTGENTP_TRBOX_L":
                large += 1
                if pit or (mboss and large > 2):
                    out.add(i)
        return out

    def full_label(self, flrid: str) -> str:
        where = self.map_label(flrid)
        return f"{self.route_name(flrid)}, {self.label(flrid)}" + (f" ({where})" if where else "")


# -- the run as saved --------------------------------------------------------------------

def paused(save: SaveDocument) -> bool:
    """The fighter is in a Heaven run (quit mid-run, or closed without pausing)."""
    soul = save.doc["soul"]
    return bool(soul.get("pause")) and soul.get("stgid") == "S_HVN"


def plan(save: SaveDocument) -> list:
    """soul.hvntrinfo: the boss floors planned ahead ({} between runs)."""
    return _list(save.doc["soul"].get("hvntrinfo"))


def _plan_entry(entries: list, flrid: str) -> dict | None:
    return next((e for e in entries if isinstance(e, dict) and e.get("flrid") == flrid), None)


def current_arena(save: SaveDocument) -> str:
    soul = save.doc["soul"]
    entry = _plan_entry(plan(save), soul.get("flrid"))
    if entry and entry.get("refareaid"):
        return str(entry["refareaid"])
    rlg = (save.doc.get("floor") or {}).get("rlg") or {}
    built = next((a for a in _list(rlg.get("archive")) if isinstance(a, dict) and a.get("flrid") == soul.get("flrid")), None)
    return str((built or {}).get("ref_areaid") or "")


def _item_records(save: SaveDocument) -> list:
    rows = (save.doc.get("item") or {}).get("items")
    if isinstance(rows, list):
        return rows
    return [r for v in (rows or {}).values() if isinstance(v, list) for r in v] if isinstance(rows, dict) else []


# -- moving to another boss floor ---------------------------------------------------------

def move_targets(save: SaveDocument, heaven: Heaven) -> list[str]:
    """Boss floors of the same route and map the paused floor can move to (its built arena fits there)."""
    soul = save.doc["soul"]
    here = str(soul.get("flrid") or "")
    if not paused(save) or not heaven.boss(here):
        return []
    ref = current_arena(save)
    twin = heaven.pit_twin(ref)

    def fits(flrid: str) -> bool:
        ok = heaven.allowed(flrid)
        return not ref or not ok or ref in ok or bool(twin and twin in ok)

    return sorted((f for f in heaven.floors if f != here and heaven.areaid(f) == heaven.areaid(here)
                   and heaven.route(f) == heaven.route(here) and fits(f)), key=heaven.no)


def same_map_floors(save: SaveDocument, heaven: Heaven) -> int:
    here = str(save.doc["soul"].get("flrid") or "")
    return sum(1 for f in heaven.floors if f != here and heaven.areaid(f) == heaven.areaid(here)
               and heaven.route(f) == heaven.route(here))


def pit_swap(save: SaveDocument, heaven: Heaven, target: str) -> str:
    """The pit twin the paused floor turns into on ``target``, or "" when it keeps its arena."""
    ref, ok = current_arena(save), heaven.allowed(target)
    twin = heaven.pit_twin(ref)
    if not ref or not ok or ref in ok or not twin or twin not in ok:
        return ""
    return twin


@dataclass
class MoveRoll:
    """The rewards drawn again for a move (each a change of item id) and the planned floors re-picked."""
    boxes: list = field(default_factory=list)       # {eid, pntid, from, to, gentype}
    boss: dict | None = None                        # {eid, from, to, gentype}
    featured: dict | None = None                    # {from, to}: the Legendary Chest on the floor
    upcoming: list = field(default_factory=list)    # {flrid, featured, rwds: [{gentype, placeholder, from, to}], refix}
    refix: dict = field(default_factory=dict)       # new flrid -> {entry, from, to}

    @property
    def count(self) -> int:
        return len(self.boxes) + bool(self.boss) + bool(self.featured) + len(self.upcoming)


def _upcoming(save: SaveDocument, heaven: Heaven, target: str) -> list[tuple[dict, str]]:
    """The planned boss floors after the paused one and where a move to ``target`` shifts each."""
    here = str(save.doc["soul"].get("flrid") or "")
    route = heaven.route_boss(here)
    if here not in route or target not in route:
        return []
    start, shift = route.index(here), route.index(target) - route.index(here)
    out = []
    for entry in plan(save):
        if not isinstance(entry, dict) or entry.get("flrid") not in route:
            continue
        j = route.index(entry["flrid"])
        if j <= start or not 0 <= j + shift < len(route):
            continue
        out.append((entry, route[j + shift]))
    return sorted(out, key=lambda pair: heaven.no(pair[1]))


def roll_move(save: SaveDocument, heaven: Heaven, target: str, seed: int) -> MoveRoll:
    """What a move to ``target`` draws again (nothing is written)."""
    rng = random.Random(seed)
    soul = save.doc["soul"]
    here = str(soul.get("flrid") or "")
    out = MoveRoll()
    swap = pit_swap(save, heaven, target)
    ref = swap or current_arena(save) or "-"
    # planned floors whose arena can't appear on the floor they shift to are picked again for it
    for entry, new in _upcoming(save, heaven, target):
        ok, old = heaven.allowed(new), entry.get("refareaid")
        if not old or not ok or old in ok:
            continue
        rows = heaven.arenas.get(new, [])
        kind = heaven.kind(old)[0]
        pool = ([r for r in rows if heaven.kind(r[0])[0] == kind] or [r for r in rows if heaven.kind(r[0])[0] == "mboss"]
                or rows)
        row = _pick(rng, pool, lambda r: r[1])
        made = row and heaven.entry(rng, new, row[0], _int(entry.get("is_rare")) == 1)
        if made:
            out.refix[new] = {"entry": made, "from": old, "to": row[0]}
    mine = _plan_entry(plan(save), here)
    featured_type = "PTGENTP_TRBOX_SPXL_RARE" if mine and _int(mine.get("is_rare")) else "PTGENTP_SPXL"
    if mine and mine.get("rwdid"):
        to = heaven.draw(rng, target, swap or mine.get("refareaid") or ref, featured_type)
        if to:
            out.featured = {"from": mine["rwdid"], "to": to}
    for entry, new in _upcoming(save, heaven, target):
        if new in out.refix:
            made = out.refix[new]["entry"]
            out.upcoming.append({"flrid": new, "refix": out.refix[new], "rare": bool(made["is_rare"]),
                                 "featured": {"from": entry.get("rwdid"), "to": made["rwdid"]} if made["rwdid"] else None,
                                 "rwds": [{"gentype": w["gentype"], "placeholder": i in heaven.unplaced(made),
                                           "from": w["rwdid"], "to": w["rwdid"]} for i, w in enumerate(made["rwds"])]})
            continue
        own_ref, unplaced = entry.get("refareaid") or "-", heaven.unplaced(entry)
        rwds = []
        for i, w in enumerate(_list(entry.get("rwds"))):
            if not isinstance(w, dict):
                continue
            hold = i in unplaced
            rwds.append({"gentype": w.get("gentype"), "placeholder": hold, "from": w.get("rwdid"),
                         "to": w.get("rwdid") if hold else heaven.draw(rng, new, own_ref, str(w.get("gentype"))) or w.get("rwdid")})
        kind = "PTGENTP_TRBOX_SPXL_RARE" if _int(entry.get("is_rare")) else "PTGENTP_SPXL"
        featured = {"from": entry["rwdid"], "to": heaven.draw(rng, new, own_ref, kind) or entry["rwdid"]} \
            if entry.get("rwdid") else None
        out.upcoming.append({"flrid": new, "refix": None, "rare": bool(_int(entry.get("is_rare"))),
                             "featured": featured, "rwds": rwds})
    # the boxes on the floor: the extra-large one holds the Legendary Chest, large ones the TRBOX_L table
    floor = save.doc.get("floor") or {}
    items = {r.get("eid"): r for r in _item_records(save) if isinstance(r, dict)}
    spawn_type = {}
    pop = (floor.get("pop") or {}).get("trbox")
    if isinstance(pop, dict):
        for key, rows in pop.items():
            if str(key).startswith(here + "-"):
                for e in _list(rows):
                    if isinstance(e, dict) and e.get("pntid"):
                        spawn_type[e["pntid"]] = e.get("type")
    for box in _list(floor.get("trbox")):
        if not isinstance(box, dict) or box.get("rwdtype") != "TBRWD_ITEM" or box.get("contentid") not in items:
            continue
        held = items[box["contentid"]].get("itemid")
        if box.get("type") in ("TBTP_EXLARGE", "TBTP_SPXL") or spawn_type.get(box.get("pntid")) == "TBTP_SPXL":
            if not out.featured:
                to = heaven.draw(rng, target, swap or (mine or {}).get("refareaid") or ref, featured_type)
                if to:
                    out.featured = {"from": (mine or {}).get("rwdid") or held, "to": to}
            if out.featured:
                out.boxes.append({"eid": box["contentid"], "pntid": box.get("pntid"), "from": held,
                                  "to": out.featured["to"], "gentype": "featured"})
        elif box.get("type") == "TBTP_LARGE":
            to = heaven.draw(rng, target, ref, "PTGENTP_TRBOX_L")
            if to:
                out.boxes.append({"eid": box["contentid"], "pntid": box.get("pntid"), "from": held, "to": to,
                                  "gentype": "PTGENTP_TRBOX_L"})
    for box in _list((floor.get("zako") or {}).get("trbox")):
        if not isinstance(box, dict) or box.get("rwdtype") != "TBRWD_ITEM" or box.get("contentid") not in items:
            continue
        to = heaven.draw(rng, target, "-", "PTGENTP_TRZAKO")
        if to:
            out.boxes.append({"eid": box["contentid"], "pntid": "treasure enemy",
                              "from": items[box["contentid"]].get("itemid"), "to": to, "gentype": "PTGENTP_TRZAKO"})
    for boss in _list((floor.get("mboss") or {}).get("mbss")):
        if not isinstance(boss, dict) or boss.get("eitemid") not in items:
            continue
        to = heaven.draw(rng, target, "-", "PTGENTP_" + str(boss.get("type")))
        if to:
            out.boss = {"eid": boss["eitemid"], "from": items[boss["eitemid"]].get("itemid"), "to": to,
                        "gentype": "PTGENTP_" + str(boss.get("type"))}
    return out


def moved_plan(save: SaveDocument, heaven: Heaven, target: str, roll: MoveRoll, reroll: bool) -> list:
    """soul.hvntrinfo as a move to ``target`` leaves it (a copy; the save is not touched)."""
    here = str(save.doc["soul"].get("flrid") or "")
    entries = copy.deepcopy(plan(save))
    route = heaven.route_boss(here)
    if here not in route or target not in route:
        for entry in entries:
            if isinstance(entry, dict) and entry.get("flrid") == here:
                entry["flrid"] = target
    else:
        shift = route.index(target) - route.index(here)
        for i in range(len(entries) - 1, -1, -1):
            entry = entries[i]
            if not isinstance(entry, dict) or entry.get("flrid") not in route:
                continue
            j = route.index(entry["flrid"]) + shift
            if not 0 <= j < len(route):
                del entries[i]                      # pushed off the end of the route
                continue
            entry["flrid"], entry["areaid"] = route[j], heaven.areaid(route[j])
            if route[j] in roll.refix:
                entry.update(copy.deepcopy(roll.refix[route[j]]["entry"]))
    swap = pit_swap(save, heaven, target)
    if swap:
        mine = _plan_entry(entries, target)
        if mine and mine.get("refareaid") == current_arena(save):
            mine["refareaid"] = swap
    if not reroll:
        return entries
    mine = _plan_entry(entries, target)
    if mine and roll.featured and mine.get("rwdid") == roll.featured["from"]:
        mine["rwdid"] = roll.featured["to"]
    for up in roll.upcoming:
        entry = _plan_entry(entries, up["flrid"])
        if not entry or up.get("refix"):
            continue
        if up["featured"] and entry.get("rwdid") == up["featured"]["from"]:
            entry["rwdid"] = up["featured"]["to"]
        for w, new in zip(_list(entry.get("rwds")), up["rwds"]):
            if isinstance(w, dict) and w.get("rwdid") == new["from"] and w.get("gentype") == new["gentype"]:
                w["rwdid"] = new["to"]
    if mine:
        used = set()
        for swapped in roll.boxes + ([roll.boss] if roll.boss else []):
            if swapped["gentype"] == "featured":
                continue
            for i, w in enumerate(_list(mine.get("rwds"))):
                if i not in used and isinstance(w, dict) and w.get("gentype") == swapped["gentype"] \
                        and w.get("rwdid") == swapped["from"]:
                    used.add(i)
                    w["rwdid"] = swapped["to"]
                    break
    return entries


def _key_is(key: str, flrid: str) -> bool:
    return key == flrid or key.startswith(flrid + "-") or key.startswith(flrid + "_")


def _rename(node, old: str, new: str, depth: int = 0) -> None:
    """Rename floor ``old`` to ``new`` (flrid values and "<flrid>-..." / "<flrid>_..." keys), dropping
    whatever an earlier visit left for ``new``."""
    if depth > 10:
        return
    if isinstance(node, list):
        node[:] = [v for v in node if not (isinstance(v, dict) and v.get("flrid") == new)]
        for v in node:
            if isinstance(v, (dict, list)):
                _rename(v, old, new, depth + 1)
        return
    if not isinstance(node, dict):
        return
    kept = {}
    for key, value in node.items():
        if _key_is(str(key), new):
            continue
        if key == "flrid" and value == old:
            value = new
        elif isinstance(value, (dict, list)):
            _rename(value, old, new, depth + 1)
        kept[new + key[len(old):] if _key_is(str(key), old) else key] = value
    node.clear()
    node.update(kept)


def _pit_convert(save: SaveDocument, heaven: Heaven, flrid: str, old_ref: str, new_ref: str) -> None:
    """Turn the built pit on ``flrid`` into its twin: arena ids, the enemy set, spawn point names."""
    a, b = heaven.kind(old_ref)[1], heaven.kind(new_ref)[1]
    if not a or not b:
        return
    pa, pb = f"HVN_R{a:02d}_", f"HVN_R{b:02d}_"
    floor = save.doc.get("floor") or {}
    for built in _list((floor.get("rlg") or {}).get("archive")):
        if not isinstance(built, dict) or built.get("flrid") != flrid:
            continue
        if built.get("ref_areaid") == old_ref:
            built["ref_areaid"] = new_ref
        if isinstance(built.get("units"), str):
            built["units"] = built["units"].replace(f'KIS_RUSH{a}"', f'KIS_RUSH{b}"').replace(pa, pb)
    flags = floor.get("closed_area_flags")
    if isinstance(flags, dict):
        renamed = {}
        for key, value in flags.items():
            if str(key).startswith(flrid + "_") and str(key).endswith("_" + old_ref):
                key = key[:-len(old_ref)] + new_ref
            renamed[key] = value
        flags.clear()
        flags.update(renamed)

    def walk(node, depth: int) -> None:
        if depth > 8 or not isinstance(node, (dict, list)):
            return
        if isinstance(node, dict) and node.get("flrid") and node.get("flrid") != flrid:
            return
        pairs = node.items() if isinstance(node, dict) else enumerate(node)
        for key, value in list(pairs):
            if key == "pntid" and isinstance(value, str) and value.startswith(pa):
                node[key] = pb + value[len(pa):]
            elif isinstance(value, (dict, list)) and key not in ("rlg", "closed_area_flags"):
                walk(value, depth + 1)

    walk(floor, 0)
    walk(save.doc.get("zombie"), 0)


def move_floor(save: SaveDocument, heaven: Heaven, target: str, seed: int, reroll: bool = True) -> bool:
    """Move the fighter paused on a boss floor to ``target`` (one of move_targets)."""
    if target not in move_targets(save, heaven):
        return False
    soul = save.doc["soul"]
    here = str(soul.get("flrid"))
    roll = roll_move(save, heaven, target, seed)
    swap, old_ref = pit_swap(save, heaven, target), current_arena(save)
    entries = moved_plan(save, heaven, target, roll, reroll)
    soul["flrid"] = target
    if isinstance(soul.get("hvntrinfo"), list):
        soul["hvntrinfo"] = entries
    floor = save.doc.get("floor")
    if isinstance(floor, dict):
        stamp = floor.pop("stamp", None)            # the Stamp Rally has no floor ids
        _rename(floor, here, target)
        if stamp is not None:
            floor["stamp"] = stamp
        level = heaven.levels.get(target, 0)
        for boss in _list((floor.get("mboss") or {}).get("mbss")):
            if level and isinstance(boss, dict) and boss.get("lvl") is not None:
                boss["lvl"] = level
        if reroll:
            items = {r.get("eid"): r for r in _item_records(save) if isinstance(r, dict)}
            for swapped in roll.boxes + ([roll.boss] if roll.boss else []):
                if swapped["eid"] in items:
                    items[swapped["eid"]]["itemid"] = swapped["to"]
            pop = (floor.get("pop") or {}).get("trbox")
            if isinstance(pop, dict):
                for key, rows in pop.items():
                    if str(key).startswith(target + "-"):
                        for e in _list(rows):
                            box = next((b for b in roll.boxes if isinstance(e, dict) and b["pntid"] == e.get("pntid")), None)
                            if box and e.get("contentid") == box["from"]:
                                e["contentid"] = box["to"]
    if swap:
        _pit_convert(save, heaven, target, old_ref, swap)
    chrs = soul.get("chr", {}).get("chrs") if isinstance(soul.get("chr"), dict) else None
    for roster in (chrs.values() if isinstance(chrs, dict) else [chrs] if isinstance(chrs, list) else []):
        for fighter in _list(roster):
            text = fighter.get("bloodnium_result") if isinstance(fighter, dict) else None
            if isinstance(text, str) and f'"{here}"' in text:
                fighter["bloodnium_result"] = text.replace(f'"{here}"', f'"{target}"')
    after = heaven.label(target) + (" (pit converted)" if swap else "")
    save.note("Run: boss floor", heaven.label(here), after + (", rewards rolled again" if reroll and roll.count else ""))
    return True


# -- locking the boss floors ahead --------------------------------------------------------

@dataclass
class LockRow:
    flrid: str
    old: dict | None                # the planned entry now (None: not planned yet)
    entry: dict | None              # what the lock writes (None: left as it is)
    fallback: bool = False          # the floor never has what was asked for
    redrawn: bool = False           # already fitted the lock, drawn again


def lock_choices(mode: str) -> list[tuple[str, str]]:
    """The one-boss choices a lock mode offers: ("boss:2", "Don JIN-DIE Mk-2")."""
    kinds = LOCK_MODES.get(mode, ((),))[0]
    return [(f"{kind}:{n}", ("Don " if kind == "boss" else "Mid-boss ") + BOSS_NAMES[kind][n])
            for kind in ("boss", "mboss") if kind in kinds for n in (1, 2, 3, 4)]


def _wanted_arenas(heaven: Heaven, flrid: str, mode: str, which: str) -> tuple[list, tuple, bool, bool]:
    """The arenas a lock allows on a floor: (rows, kinds, fallback, one boss)."""
    rows = heaven.arenas.get(flrid, [])
    kinds, fallback_kinds = LOCK_MODES[mode][0], LOCK_MODES[mode][1]
    want, fallback = [r for r in rows if heaven.kind(r[0])[0] in kinds], False
    if not want and fallback_kinds:
        backup = [r for r in rows if heaven.kind(r[0])[0] in fallback_kinds]
        if backup:
            want, kinds, fallback = backup, fallback_kinds, True
    one = False
    if which and which in dict(lock_choices(mode)):
        kind, n = which.split(":")
        exact = [r for r in rows if heaven.kind(r[0]) == (kind, int(n))]
        twin = [r for r in rows if heaven.kind(r[0])[0] in ("boss", "mboss") and heaven.kind(r[0])[0] != kind
                and heaven.kind(r[0])[1] == int(n)]
        if exact:
            want, fallback, one = exact, False, True
        elif twin:
            want, fallback, one = twin, True, True
        else:
            fallback = True                         # this floor never has that boss: any of the mode
    return want, kinds, fallback, one


def lock_plan(heaven: Heaven, flrid: str, entries: list, mode: str, which: str = "", extend: bool = True,
              rewards: str = "roll", redraw: bool = False, seed: int = 0) -> list[LockRow]:
    """What a lock writes for the boss floors after ``flrid`` (nothing is written)."""
    if mode not in LOCK_MODES or flrid not in heaven.floors:
        return []
    rng = random.Random(seed)
    out = []
    for f in heaven.ahead(flrid):
        rows = heaven.arenas.get(f, [])
        if not rows:
            continue
        old = _plan_entry(entries, f)
        want, kinds, fallback, one = _wanted_arenas(heaven, f, mode, which)
        keep = old is not None and (not want or (any(r[0] == old.get("refareaid") for r in want) if one
                                                 else heaven.kind(old.get("refareaid"))[0] in kinds))
        if keep and not (redraw and want):
            out.append(LockRow(f, old, None, fallback))
            continue
        if old is None and not extend:
            continue
        row = _pick(rng, want or rows, lambda r: r[1])
        rare = _int(old.get("is_rare")) == 1 if old else rng.random() < RARE_CHANCE
        out.append(LockRow(f, old, heaven.entry(rng, f, row[0], rare, rewards) if row else None, fallback, bool(keep)))
    return out


def locked_plan(entries: list, rows: list[LockRow], extend: bool) -> list:
    entries = copy.deepcopy(entries)
    for row in rows:
        if row.entry is None:
            continue
        at = next((i for i, e in enumerate(entries) if isinstance(e, dict) and e.get("flrid") == row.flrid), None)
        if at is not None:
            entries[at] = copy.deepcopy(row.entry)
        elif extend:
            entries.append(copy.deepcopy(row.entry))
    return entries


def beyond_game(heaven: Heaven, flrid: str) -> list[str]:
    """Boss floors past the ones the game itself would have planned by now."""
    return heaven.ahead(flrid)[GAME_PLAN_AHEAD:]


def extra_planned(heaven: Heaven, flrid: str, entries: list) -> list[str]:
    """Planned floors past the game's own plan (an earlier lock wrote them)."""
    planned = {e.get("flrid") for e in entries if isinstance(e, dict)}
    return [f for f in beyond_game(heaven, flrid) if f in planned]


def lock_floors(save: SaveDocument, heaven: Heaven, mode: str, which: str = "", extend: bool = True,
                rewards: str = "roll", redraw: bool = False, seed: int = 0) -> int:
    """Write a lock onto the save; the number of floors written."""
    if not paused(save):
        return 0
    soul = save.doc["soul"]
    rows = lock_plan(heaven, str(soul.get("flrid")), plan(save), mode, which, extend, rewards, redraw, seed)
    written = [r for r in rows if r.entry is not None and (r.old is not None or extend)]
    if not written:
        return 0
    soul["hvntrinfo"] = locked_plan(plan(save), rows, extend)
    what = dict(lock_choices(mode)).get(which) or LOCK_MODES[mode][2]
    save.note("Run: boss floors ahead", "as rolled", f"{what}, {len(written)} floor(s) written")
    return len(written)


def clear_extra(save: SaveDocument, heaven: Heaven) -> int:
    """Take out the planned floors past the game's own plan, so the game plans them itself."""
    if not paused(save):
        return 0
    soul = save.doc["soul"]
    extra = set(extra_planned(heaven, str(soul.get("flrid")), plan(save)))
    if not extra:
        return 0
    soul["hvntrinfo"] = [e for e in plan(save) if not (isinstance(e, dict) and e.get("flrid") in extra)]
    save.note("Run: boss floors planned past the game's own", f"{len(extra)}", "removed (the game plans them)")
    return len(extra)


# -- I'm feeling lucky --------------------------------------------------------------------

def owned_blueprints(save: SaveDocument) -> set[str]:
    """Every blueprint id anywhere in the save except the planned rewards ("U" folded into the base id)."""
    found: set[str] = set()
    stack = [save.doc]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            stack.extend(v for k, v in node.items() if k != "hvntrinfo" and isinstance(v, (dict, list, str)))
        elif isinstance(node, list):
            stack.extend(v for v in node if isinstance(v, (dict, list, str)))
        elif node.startswith("ITMP_"):
            found.add(node[:-1] if node.endswith("U") else node)
    return found


def researched_parts(save: SaveDocument) -> set[str]:
    research = (save.doc["soul"].get("partresearch") or {}).get("user") \
        if isinstance(save.doc["soul"].get("partresearch"), dict) else None
    return {str(e.get("ptid")) for e in _list(research) if isinstance(e, dict) and e.get("ptid")}


def real_blueprint(heaven: Heaven, itemid: str) -> bool:
    """A blueprint the game hands out for a named, obtainable part that is not PlayStation only."""
    cat = heaven.catalog
    base = itemid[:-1] if itemid.endswith("U") else itemid
    row = cat.items.get(base) or {}
    if row.get("itemtype") != "ITTP_RMAP" or not base.startswith("ITMP_"):
        return False
    ptid = "PT_" + base[len("ITMP_"):]
    part = cat.parts.get(ptid)
    return bool(part and cat._text(part.get("name")) and ptid not in cat.previous and ptid not in NO_BLUEPRINT
                and _int(part.get("platform")) != 1)


def _chances(heaven: Heaven, flrid: str, ref: str, gentype: str) -> dict:
    """Blueprint -> its chance in one draw from that arena's rows."""
    key = (flrid, ref, gentype)
    if key in heaven._chances:
        return heaven._chances[key]
    rows = heaven.drops.get((flrid, heaven.areaid(flrid), gentype), [])
    own = [r for r in rows if ref and ref != "-" and r[0] == ref]
    rows = [r for r in rows if r[0] in (ref, "-")] if own else [r for r in rows if r[0] == "-"]
    groups = [(heaven.groups.get(grp, []), freq) for _, grp, freq in rows if heaven.groups.get(grp)]
    total, out = sum(w for _, w in groups), {}
    for items, w in groups:
        for itemid in items:
            if total and not itemid.endswith("U") and real_blueprint(heaven, itemid):
                out[itemid] = out.get(itemid, 0) + w / (total * len(items))
    heaven._chances[key] = out
    return out


def _slot_type(heaven: Heaven, ref: str) -> str:
    return "PTGENTP_TRZAKO" if heaven.kind(ref)[0] == "pit" else "PTGENTP_TRBOX_L"


def _arena_rows(heaven: Heaven, flrid: str, lock: tuple | None) -> list:
    """The arenas the game (or the lock, when one is set) could give an unplanned boss floor."""
    rows = heaven.arenas.get(flrid, [])
    if not rows or not lock or lock[0] not in LOCK_MODES:
        return rows
    want = _wanted_arenas(heaven, flrid, lock[0], lock[1])[0]
    return want or rows


def _placed_rewards(heaven: Heaven, entries: list) -> set[str]:
    out = set()
    for e in entries:
        if not isinstance(e, dict):
            continue
        ids = [e.get("rwdid")] + [w.get("rwdid") for i, w in enumerate(_list(e.get("rwds")))
                                   if isinstance(w, dict) and i not in heaven.unplaced(e)]
        out |= {(i[:-1] if i.endswith("U") else i) for i in ids if isinstance(i, str) and i.startswith("ITMP_")}
    return out


@dataclass
class Lucky:
    pool: dict                      # unknown blueprint -> its chance
    already: int                    # unknown blueprints already in planned rewards
    ceiling: int
    masks: int = 0                  # unknown masks (NEO routes only)

    @property
    def room(self) -> int:
        return self.ceiling - self.already

    @property
    def ok(self) -> bool:
        return bool(self.pool) and self.room > 0


def lucky_check(heaven: Heaven, flrid: str, entries: list, owned: set, known: set,
                lock: tuple | None = None) -> Lucky:
    """What a press could hide on the boss floors after ``flrid``."""
    total: dict = {}
    for f in heaven.ahead(flrid):
        e = _plan_entry(entries, f)
        for ref in ([e.get("refareaid")] if e else [r[0] for r in _arena_rows(heaven, f, lock)]):
            gentype = _slot_type(heaven, ref)
            for bp, w in _chances(heaven, f, "-" if gentype == "PTGENTP_TRZAKO" else ref, gentype).items():
                total[bp] = total.get(bp, 0) + w

    def unknown(bp: str) -> bool:
        return bp not in owned and "PT_" + bp[len("ITMP_"):] not in known

    unknown_all = {bp: w for bp, w in total.items() if unknown(bp)}
    in_plan = _placed_rewards(heaven, entries)
    already = {bp for bp in in_plan if bp in unknown_all or (real_blueprint(heaven, bp) and unknown(bp))}
    pool = {bp: w for bp, w in unknown_all.items() if bp not in already}
    share = len(unknown_all) / len(total) if total else 0
    route = "main" if heaven.route(flrid) == "main" else "neo"
    tiers = LUCKY_CEILING[route]
    ceiling = next((tiers[i] for i, cut in enumerate(LUCKY_TIERS) if share >= cut), tiers[-1])
    masks = 0
    if route == "neo":
        masks = sum(1 for m in MASKS if real_blueprint(heaven, m) and unknown(m) and m not in in_plan)
    return Lucky(pool, len(already), ceiling, masks)


def feeling_lucky(save: SaveDocument, heaven: Heaven, seed: int, masks: bool = False,
                  lock: tuple | None = None, owned: set | None = None) -> bool:
    """Plan every boss floor left and hide a few unknown blueprints among their rewards."""
    if not seed or not paused(save):
        return False
    soul = save.doc["soul"]
    here = str(soul.get("flrid") or "")
    ahead = heaven.ahead(here) if here in heaven.floors else []
    if not ahead:
        return False
    rng = random.Random(seed)
    entries = copy.deepcopy(plan(save))
    # 1. the whole run: every boss floor not planned yet, and the empty reward lists of lock-written floors
    for f in ahead:
        e = _plan_entry(entries, f)
        if e is None:
            row = _pick(rng, _arena_rows(heaven, f, lock), lambda r: r[1])
            made = row and heaven.entry(rng, f, row[0], rng.random() < RARE_CHANCE)
            if made:
                entries.append(made)
        elif not _list(e.get("rwds")):
            made = heaven.entry(rng, f, e.get("refareaid"), _int(e.get("is_rare")) == 1)
            if made:
                e["rwds"] = made["rwds"]
    entries.sort(key=lambda e: heaven.no(e.get("flrid")) if isinstance(e, dict) else 10 ** 9)
    # 2. what can go where
    owned = owned_blueprints(save) if owned is None else owned
    known = researched_parts(save)
    check = lucky_check(heaven, here, entries, owned, known, lock)
    if not check.ok:
        return False
    floors = []
    for f in ahead:
        e = _plan_entry(entries, f)
        if not e:
            continue
        slots, unplaced = [], heaven.unplaced(e)
        for i, w in enumerate(_list(e.get("rwds"))):
            if not isinstance(w, dict) or i in unplaced or w.get("gentype") not in ("PTGENTP_TRBOX_L", "PTGENTP_TRZAKO"):
                continue
            chances = _chances(heaven, f, "-" if w["gentype"] == "PTGENTP_TRZAKO" else e.get("refareaid"), w["gentype"])
            if any(bp in check.pool for bp in chances):
                slots.append((i, chances))
        if slots:
            floors.append((e, slots))
    if not floors:
        return False
    # 3. how many: never more than the room left, the pool or the floors that can take one
    route = "main" if heaven.route(here) == "main" else "neo"
    most = min(check.room, len(check.pool), len(floors))
    weights = LUCKY_COUNT_WEIGHTS[route][:most]
    count = 1
    if not rng.random() < LUCKY_SHORT_DRAW:
        picked = _pick(rng, list(enumerate(weights, 1)), lambda p: p[1])
        count = picked[0] if picked else 1
    # 4. which floors, then a slot and a blueprint on each
    rng.shuffle(floors)
    chances = sorted(check.pool.values())
    rare_cut = chances[int(0.25 * (len(chances) - 1))] if len(chances) >= 4 else -1
    used: set[str] = set()
    mask_ids = [m for m in MASKS if real_blueprint(heaven, m) and m not in owned
                and "PT_" + m[len("ITMP_"):] not in known and m not in _placed_rewards(heaven, entries)] \
        if masks and route == "neo" else []
    mask_at = int(rng.random() * count) if mask_ids else -1
    placed = 0
    for e, slots in floors:
        if placed >= count:
            break
        order = slots[:]
        rng.shuffle(order)
        if placed == mask_at:
            e["rwds"][order[0][0]]["rwdid"] = mask_ids[int(rng.random() * len(mask_ids))]
            placed += 1
            continue
        for i, chances_here in order:
            options = [(bp, w * (LUCKY_RARE_BOOST if check.pool[bp] <= rare_cut else 1))
                       for bp, w in chances_here.items() if bp in check.pool and bp not in used]
            pick = _pick(rng, options, lambda o: o[1])
            if not pick:
                continue
            e["rwds"][i]["rwdid"] = pick[0]
            used.add(pick[0])
            placed += 1
            break
    if not placed:
        return False
    soul["hvntrinfo"] = entries
    save.note("Run: I'm feeling lucky", "", "the rest of the run's boss floors planned (what and where stay hidden)")
    return True


# -- the Jackals' loot --------------------------------------------------------------------

@dataclass(frozen=True)
class Loot:
    kind: str                       # money, part, item or none
    ident: str = ""
    money: int = 0


@dataclass
class JackalOption:
    key: str
    weight: float
    label: str
    loot: Loot | None               # None for Kill Coins: the amount is rolled in its range
    low: int = 0
    high: int = 0

    def take(self, rng: random.Random) -> Loot:
        return self.loot or Loot("money", money=round(self.low + rng.random() * (self.high - self.low)))


def jackals(save: SaveDocument) -> list[dict]:
    """The Jackals on the floor (floor.jkls), only while the save is in a run: between runs the
    loot they point at is gone and the next floor rolls them again."""
    if not save.doc["soul"].get("pause"):
        return []
    return [j for j in _list((save.doc.get("floor") or {}).get("jkls")) if isinstance(j, dict) and j.get("type")]


def _rwd(jackal: dict) -> dict:
    try:
        found = json.loads(jackal.get("rwd") or "")
    except (TypeError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


def _part_records(save: SaveDocument, create: bool = False) -> list | None:
    pts = (save.doc.get("part") or {}).get("pts") if isinstance(save.doc.get("part"), dict) else None
    if isinstance(pts, list):
        return pts
    if isinstance(pts, dict):
        if not isinstance(pts.get(save.uid), list):
            if not create:
                return None
            pts[save.uid] = []
        return pts[save.uid]
    return None


def jackal_loot(save: SaveDocument, jackal: dict) -> Loot:
    rwd = _rwd(jackal)
    items, pts = _list(rwd.get("items")), _list(rwd.get("pts"))
    if items and isinstance(items[0], dict):
        return Loot("item", str(items[0].get("itemId") or ""))
    if pts and isinstance(pts[0], dict):
        held = next((p for p in _part_records(save) or [] if isinstance(p, dict) and p.get("eid") == pts[0].get("eptid")), None)
        return Loot("part", str((held or {}).get("ptid") or "?"))
    if _int(rwd.get("money")) > 0:
        return Loot("money", money=_int(rwd.get("money")))
    return Loot("none")


def loot_label(heaven: Heaven, loot: Loot) -> str:
    if loot.kind == "money":
        return f"{loot.money:,} Kill Coins"
    if loot.kind == "part":
        return heaven.catalog.part_name(loot.ident)
    if loot.kind == "item":
        return heaven.catalog.item_name(loot.ident)
    return "nothing"


def loot_key(loot: Loot) -> str:
    return {"money": "money", "part": f"pt:{loot.ident}", "item": f"it:{loot.ident}"}.get(loot.kind, "")


def jackal_options(heaven: Heaven, jtype: str) -> list[JackalOption]:
    """What a Jackal can drop (master_jackal, weights per 1000). Unrevealed blueprints can be picked
    but a roll never gives them (the game does hand them out; how often isn't in its data)."""
    row = heaven.jackals.get(jtype)
    if not row:
        return []
    cat, out = heaven.catalog, []

    def blueprint(ptid: str) -> str:
        itemid = "ITMP_" + str(ptid or "")[len("PT_"):]
        return itemid if ptid and itemid in cat.items else ""

    armor = [p for p in (row.get("head"), row.get("body"), row.get("legs")) if p]
    if _int(row.get("drop_coin_rate")):
        low, high = _int(row.get("money_min")), _int(row.get("money_max"))
        out.append(JackalOption("money", _int(row["drop_coin_rate"]), f"Kill Coins ({low:,}-{high:,})", None, low, high))
    if _int(row.get("drop_part_weapon_rate")) and row.get("armr"):
        out.append(JackalOption(f"pt:{row['armr']}", _int(row["drop_part_weapon_rate"]),
                                f"Weapon: {cat.part_name(row['armr'])}", Loot("part", row["armr"])))
    if _int(row.get("drop_part_armor_rate")):
        for p in armor:
            out.append(JackalOption(f"pt:{p}", _int(row["drop_part_armor_rate"]) / len(armor),
                                    f"Armor: {cat.part_name(p)}", Loot("part", p)))
    weapon_bp = blueprint(row.get("armr"))
    if _int(row.get("drop_rmap_weapon_rate")) and weapon_bp:
        out.append(JackalOption(f"it:{weapon_bp}", _int(row["drop_rmap_weapon_rate"]), cat.item_name(weapon_bp),
                                Loot("item", weapon_bp)))
    armor_bps = [b for b in map(blueprint, armor) if b]
    if _int(row.get("drop_rmap_armor_rate")):
        for b in armor_bps:
            out.append(JackalOption(f"it:{b}", _int(row["drop_rmap_armor_rate"]) / len(armor_bps), cat.item_name(b),
                                    Loot("item", b)))
    for itemid, rate in ((row.get("drop_item_id_1"), row.get("drop_item_1_rate")),
                         (row.get("drop_item_id_2"), row.get("drop_item_2_rate"))):
        if itemid and _int(rate) and itemid in cat.items:
            out.append(JackalOption(f"it:{itemid}", _int(rate), cat.item_name(itemid), Loot("item", itemid)))
    for option in out[:]:
        if option.loot and option.loot.kind == "item" and option.loot.ident.startswith("ITMP_") \
                and option.loot.ident + "U" in cat.items:
            hidden = option.loot.ident + "U"
            out.append(JackalOption(f"it:{hidden}", 0, cat.item_name(hidden), Loot("item", hidden)))
    return out


def roll_jackal(heaven: Heaven, jtype: str, rng: random.Random) -> Loot | None:
    options = [o for o in jackal_options(heaven, jtype) if o.weight > 0]
    picked = _pick(rng, options, lambda o: o.weight)
    return picked.take(rng) if picked else None


def set_jackal_loot(save: SaveDocument, heaven: Heaven, jtype: str, loot: Loot, eid: str,
                    now: int | None = None) -> bool:
    """What the Jackal ``jtype`` drops: the old loot record goes, a new one owned by the Jackal is made."""
    jackal = next((j for j in jackals(save) if j.get("type") == jtype), None)
    if jackal is None or _int(jackal.get("killed")):
        return False
    now = int(time.time()) if now is None else int(now)
    before = loot_label(heaven, jackal_loot(save, jackal))
    old = _rwd(jackal)
    gone_items = {x.get("eitemid") for x in _list(old.get("items")) if isinstance(x, dict)}
    gone_parts = {x.get("eptid") for x in _list(old.get("pts")) if isinstance(x, dict)}
    items = (save.doc.get("item") or {}).get("items") if isinstance(save.doc.get("item"), dict) else None
    if isinstance(items, list):
        items[:] = [r for r in items if not (isinstance(r, dict) and r.get("eid") in gone_items)]
    parts = _part_records(save)
    if parts is not None:
        parts[:] = [r for r in parts if not (isinstance(r, dict) and r.get("eid") in gone_parts)]
    rwd = {"money": 0, "spirit": 0, "tbtp": "", "pts": {}, "msrs": {}, "items": {}}
    if loot.kind == "money":
        rwd["money"] = int(loot.money)
    elif loot.kind == "item":
        if not isinstance(items, list):
            save.doc.setdefault("item", {})["items"] = items = []
        items.append({"eid": eid, "gettime": 0, "itemid": loot.ident, "owner": jtype})
        rwd["items"] = [{"eitemid": eid, "itemId": loot.ident, "gettime": 0}]
    elif loot.kind == "part":
        parts = _part_records(save, create=True)
        row = heaven.catalog.parts.get(loot.ident) or {}
        parts.append({"uid": _int(save.uid), "eid": eid, "gettime": 0, "owner": jtype, "created": now, "modified": now,
                      "ptid": loot.ident, "rest": _int(row.get("capacity")), "spare": _int(row.get("spare")), "grade": 0,
                      "dur": heaven.catalog.full_durability(loot.ident, 1), "lvl": 1})
        rwd["pts"] = [{"eptid": eid}]
    jackal["rwd"] = json.dumps(rwd, separators=(",", ":"))
    save.note(f"Run: {jtype.replace('JACKAL_', 'Jackal ')} drops", before, loot_label(heaven, loot))
    return True


def fighter_name(save: SaveDocument) -> str:
    return str((_fighter(save) or {}).get("name") or "the fighter")
