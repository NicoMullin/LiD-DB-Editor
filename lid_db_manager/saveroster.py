"""The Fighter Freezer: buying a fighter, deleting one, bringing a dead one back, and how
many hangers the freezer has.

Each does what the game's own offline server does (BrgGame-Steam.exe, read in Ghidra
2026-10-20), checked against a real save; oberlinx's LET IT DIE save editor (used with
permission) found the fields first from saves taken before and after.

- The freezer is soul.chr.slots[uid]: one hanger {uid, slot, cid} each, cid "" when empty.
  Its level is only how many hangers there are (master_freezer: level -> count, 3 to 10).
  A level can come down only while the hangers it drops are empty.
- Buying (0x141347db0) puts a FREE fighter in an empty hanger: its levels all 1 (level
  1), each stat's bonus rolled (savefighters.Rules.bonus_choices - the roll is 0x1411c52c0),
  a Death Bag of empty slots, and the class's starting gear (master_body_detail
  initial_parts, a gas mask) worn on the head. A fighter never used in a real save looks
  exactly so. The editor does not charge the price.
- Deleting (0x14115b1f0 -> 0x14132b9f0) is only for a FREE fighter: everything in its Death
  Bag goes, and the bag is left {}; the decals it wears come off (a premium one back to the
  stock, as oberlinx saw); its level row, its record and its hanger's cid go; the Kill Coins,
  Splithium and Bloodnium it carried are lost; and the account gets master_body_detail
  discard_spirit (its class and grade) times its level in Splithium, no more than the SPL
  tank holds (0x1413615c0, 0x1413262d0). playlog.fighter.destory_fighter (sic) goes up by 1.
- Bringing a dead fighter back (0x141162b60 -> 0x141349da0 -> 0x1413323e0): only one that
  died (state ENEMY, or HUNTER): its diedchara records go (the Hater), it is FREE again with
  full HP (999999, the game's "full") and an empty run result, its Death Bag - which it kept
  - is padded back to its room, and playlog.fighter.recover_fighter goes up by 1. The game
  charges the Fighter Depot's price (diedchara rcvrymn Kill Coins); the editor does not.
"""

from __future__ import annotations

import json
import random
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .saveedit import EditError, Limits, SaveDocument
from .savedecals import COUNT_MAX, DecalCatalog
from .savefighters import STATS, Fighter, Rules, check_name, fighters
from .savestorage import EMPTY, SLOT_TYPES, Catalog, _int

EMPTY_RESULT = '{"enemy_count":0,"bloodnium":0,"elapsed_time":0,"max_floor_id":""}'
FULL_HP = 999_999
DEAD = ("ENEMY", "HUNTER")


@dataclass
class Freezer:
    sizes: dict = field(default_factory=dict)       # freezer level -> hangers

    @classmethod
    def read(cls, db_path: Path | str | None) -> "Freezer":
        found = cls()
        if db_path and Path(db_path).is_file():
            try:
                con = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
            except sqlite3.Error:
                return found
            try:
                found.sizes = {_int(n): _int(c) for n, c in con.execute("SELECT number, count FROM master_freezer")}
            except sqlite3.Error:
                pass
            finally:
                con.close()
        return found

    def level_of(self, count: int) -> int | None:
        return next((n for n, c in sorted(self.sizes.items()) if c == count), None)

    @property
    def most(self) -> int:
        return max(self.sizes.values(), default=10)


# -- the hangers -------------------------------------------------------------------------

def hangers(save: SaveDocument) -> list:
    chr_table = save.get(("soul", "chr"))
    slots = chr_table.get("slots") if isinstance(chr_table, dict) else None
    rows = slots.get(save.uid) if isinstance(slots, dict) else None
    if not isinstance(rows, list):
        raise EditError("this save has no Fighter Freezer")
    return rows


def free_hangers(save: SaveDocument) -> list[int]:
    return [i for i, h in enumerate(hangers(save)) if isinstance(h, dict) and not h.get("cid")]


def set_freezer(save: SaveDocument, freezer: Freezer, level: int) -> int:
    """The freezer to ``level``'s number of hangers. Returns the new count."""
    if level not in freezer.sizes:
        raise EditError(f"the freezer has no level {level}")
    rows = hangers(save)
    before, wanted = len(rows), freezer.sizes[level]
    if wanted > before:
        rows.extend({"uid": _int(save.uid), "slot": i, "cid": ""} for i in range(before, wanted))
    elif wanted < before:
        if any(isinstance(h, dict) and h.get("cid") for h in rows[wanted:]):
            raise EditError(f"hangers {wanted + 1} to {before} hold fighters, so the freezer cannot go down to "
                            f"level {level} - delete or move them first")
        del rows[wanted:]
    if len(rows) != before:
        save.note("Fighter Freezer: hangers", before, len(rows))
    return len(rows)


# -- buying a fighter ----------------------------------------------------------------------

def roll_bonus(rules: Rules, kind: str, grade: int, rng: random.Random | None = None) -> dict:
    """Each stat's bonus as the purchase rolls it: BODY_BONUS_N percent of limit break 0's cap,
    N picked by its weight, rounded down."""
    rng = rng or random.Random()
    odds = rules.bonus_odds or {0: 50, 5: 20, 10: 15, 15: 10, 20: 5}
    cap = rules.cap(kind, grade, 0)
    rows = [(pct, w) for pct, w in sorted(odds.items()) if w > 0] or [(0, 1)]
    total = sum(w for _, w in rows)
    out = {}
    for stat in STATS:
        x = rng.random() * total
        pick = rows[-1][0]
        for pct, w in rows:
            x -= w
            if x < 0:
                pick = pct
                break
        out[stat] = pick * cap // 100
    return out


def starting_gear(rules: Rules, catalog: Catalog, kind: str, grade: int) -> list[str]:
    """master_body_detail initial_parts: what a bought fighter comes with (a gas mask)."""
    row = rules.details.get((kind, grade, 0)) or {}
    return [p.strip() for p in str(row.get("initial_parts") or "").split(",") if p.strip() in catalog.parts]


def add_fighter(save: SaveDocument, rules: Rules, catalog: Catalog, *, name: str, kind: str, grade: int,
                body: str, gasmask: str, bonus: dict, room: int, cid: str, eids: tuple = (),
                now: int | None = None) -> str:
    """A fighter bought into the first empty hanger. Returns its cid."""
    free = free_hangers(save)
    if not free:
        raise EditError("every hanger in the freezer holds a fighter: raise the freezer level first")
    name = check_name(name)
    if kind not in rules.classes():
        raise EditError(f"{kind} is not a fighter class in this game's data")
    grade = max(1, min(int(grade), rules.max_grade(kind)))
    if rules.bodies and body not in {b for b, _ in rules.bodies}:
        raise EditError(f"{body} is not a fighter model in this game's data")
    masks = rules.masks_for(body)
    if masks and gasmask not in masks:
        gasmask = masks[0]
    now = int(now if now is not None else time.time())
    uid = _int(save.uid)
    soul = save.doc["soul"]
    roster = save.get(("soul", "chr", "chrs", save.uid))
    if not isinstance(roster, list):
        raise EditError("this save has no fighter list")
    roster.append({"uid": uid, "cid": cid, "body": body, "gasmask": gasmask, "type": kind, "grade": grade,
                   "limit_break": 0, "money": 0, "spirit": 0, "bloodnium": 0, "bloodnium_result": "",
                   "hp": FULL_HP, "escdie": 0, "total_exp": 0, "rest_exp": 0, "gain_exp": 0, "start_exp": 0,
                   "sklgauge": 0, "pause": "", "abid": "", "name": name, "state": "FREE", "select_arm_slots": "",
                   "hunter_win": -1, "hunter_lose": -1, "hunter_draw": -1})
    bodies = save.doc.setdefault("bodyuser", {})
    if not isinstance(bodies.get(save.uid), list):
        bodies[save.uid] = []
    bodies[save.uid].append({"uid": uid, "cid": cid, "lvl": 1, **{k: 1 for k in STATS}, "skill": 0, "bag": 0,
                             "rage": 0, **{f"{k}_bonus": int(bonus.get(k, 0)) for k in STATS}})
    bag = [{"uid": uid, "cid": cid, "slot": i, "type": EMPTY, "eid": "", "site": "", "arm_slot": -1}
           for i in range(max(1, int(room)))]
    parts = save.doc.setdefault("part", {}).setdefault("pts", {})
    if not isinstance(parts.get(save.uid), list):
        parts[save.uid] = []
    given = iter(eids)
    head = False
    for index, ptid in enumerate(starting_gear(rules, catalog, kind, grade)[:len(bag)]):
        row = catalog.parts[ptid]
        eid = next(given, None) or str(uuid.uuid4())
        parts[save.uid].append({"uid": uid, "eid": eid, "gettime": now, "owner": "USER", "created": now,
                                "modified": now, "ptid": ptid, "rest": _int(row.get("capacity")),
                                "spare": _int(row.get("spare")), "grade": 0, "dur": catalog.full_durability(ptid, 1),
                                "lvl": 1})
        wear = row.get("type") == "PTTP_HEAD" and not head
        head = head or wear
        bag[index].update(type=SLOT_TYPES["part"], eid=eid, site="EQSITE_HEAD" if wear else "")
    deathbags = soul.setdefault("deathbag", {})
    if not isinstance(deathbags.get(save.uid), dict):
        deathbags[save.uid] = {}
    deathbags[save.uid][cid] = bag
    hanger = free[0]
    hangers(save)[hanger]["cid"] = cid
    save.note(f"Fighter Freezer: bought {name}",
              "", f"{rules.names.class_name(kind)} grade {grade}, hanger {hanger + 1}, bonuses "
              + "/".join(str(int(bonus.get(k, 0))) for k in STATS))
    return cid


# -- deleting a fighter --------------------------------------------------------------------

def delete_reason(fighter: Fighter) -> str:
    """Why the game would not delete this fighter, or ""."""
    if fighter.state == "USE":
        return "the fighter you play: pick another in the game first"
    if fighter.state == "GUARD":
        return "on defense: take them off defense in the game first"
    if fighter.state in DEAD:
        return "dead: bring them back first, or leave them be"
    if fighter.kidnapped:
        return "kidnapped"
    if fighter.state != "FREE":
        return "not a fighter of the freezer's own"
    return ""


def _drop_records(save: SaveDocument, eids: set) -> None:
    """Remove the records with these ids (and the mushroom a beast carries)."""
    beasts = save.get(("beast", "bsts"))
    if isinstance(beasts, list):
        eids |= {b.get("rwdemsrid") for b in beasts if isinstance(b, dict) and b.get("eid") in eids and b.get("rwdemsrid")}
    for path in (("part", "pts", save.uid), ("mushroom", "msrs"), ("beast", "bsts"), ("item", "items")):
        rows = save.get(path)
        if isinstance(rows, list):
            rows[:] = [r for r in rows if not (isinstance(r, dict) and r.get("eid") in eids)]


def refund(rules: Rules, fighter: Fighter) -> int:
    """The Splithium the game gives back for deleting this fighter."""
    row = rules.details.get((fighter.kind, fighter.grade, 0)) or {}
    return max(0, _int(row.get("discard_spirit"))) * max(0, fighter.level)


def delete_fighter(save: SaveDocument, rules: Rules, decals: DecalCatalog, limits: Limits, cid: str) -> int:
    """Delete a FREE fighter as the game does. Returns the Splithium given back."""
    fighter = next((f for f in fighters(save) if f.cid == cid), None)
    if fighter is None:
        raise EditError("that fighter is not in the save")
    why = delete_reason(fighter)
    if why:
        raise EditError(f"{fighter.name or 'That fighter'} is {why}")
    soul, who = save.doc["soul"], fighter.name or "A fighter"
    chara = save.get(fighter.chr_path)
    lost = [f"{_int(chara.get(k)):,} {label}" for k, label in (("money", "Kill Coins"), ("spirit", "Splithium"),
                                                               ("bloodnium", "Bloodnium")) if _int(chara.get(k))]
    bags = soul.get("deathbag", {}).get(save.uid) if isinstance(soul.get("deathbag"), dict) else None
    bag = bags.get(cid) if isinstance(bags, dict) else None
    carried = {s.get("eid") for s in bag if isinstance(s, dict) and s.get("eid")} if isinstance(bag, list) else set()
    _drop_records(save, set(carried))
    if isinstance(bags, dict):
        bags[cid] = {}
    worn = save.get(("soul", "skl", "eqskl", save.uid))
    if isinstance(worn, list):
        mine = [r for r in worn if isinstance(r, dict) and r.get("cid") == cid]
        worn[:] = [r for r in worn if not (isinstance(r, dict) and r.get("cid") == cid)]
        back = [str(r.get("sklid")) for r in mine if (d := decals.decals.get(str(r.get("sklid")))) and d.premium]
        if back:
            rows = save.get(("soul", "skl", "psskl"))
            if not isinstance(rows, list):
                save.doc["soul"]["skl"]["psskl"] = rows = []
            for sklid in back:
                row = next((r for r in rows if isinstance(r, dict) and r.get("sklid") == sklid), None)
                if row is None:
                    rows.append({"sklid": sklid, "cnt": 1, "updated": int(time.time()), "is_checked": 0})
                else:
                    row["cnt"] = min(COUNT_MAX, _int(row.get("cnt")) + 1)
            save.note(f"{who}: premium decals back to the stock", "", ", ".join(decals.label(s) for s in back))
    given = refund(rules, fighter)
    if given:
        tank = _int(soul.get("spirit_tank_level"), 1)
        had = _int(soul.get("spirit"))
        top = max(had, limits.cap(limits.tank, tank))
        soul["spirit"] = min(top, had + given)
        given = soul["spirit"] - had
        save.note("Splithium for the deleted fighter", f"{had:,}", f"{soul['spirit']:,}")
    bodies = save.get(("bodyuser", save.uid))
    if isinstance(bodies, list):
        bodies[:] = [r for r in bodies if not (isinstance(r, dict) and r.get("cid") == cid)]
    roster = save.get(("soul", "chr", "chrs", save.uid))
    roster[:] = [c for c in roster if not (isinstance(c, dict) and c.get("cid") == cid)]
    for hanger in hangers(save):
        if isinstance(hanger, dict) and hanger.get("cid") == cid:
            hanger["cid"] = ""
    _count(save, "destory_fighter")
    save.note(f"Fighter Freezer: deleted {who}", f"Lv {fighter.level}, {len(carried)} thing(s) carried",
              "gone" + (f" (lost: {', '.join(lost)})" if lost else ""))
    return given


def _count(save: SaveDocument, key: str) -> None:
    log = save.get(("playlog", "fighter"))
    if isinstance(log, dict) and isinstance(log.get(key), int) and not isinstance(log.get(key), bool):
        log[key] += 1


# -- bringing a dead fighter back ----------------------------------------------------------

@dataclass
class Death:
    did: str
    flrid: str
    created: int
    price: int                      # the Fighter Depot's price in Kill Coins


def deaths(save: SaveDocument, cid: str) -> list[Death]:
    rows = save.get(("diedchara", "dchrs", save.uid))
    return [Death(str(r.get("did")), str(r.get("flrid") or ""), _int(r.get("created")), _int(r.get("rcvrymn")))
            for r in rows if isinstance(r, dict) and r.get("cid") == cid] if isinstance(rows, list) else []


def recover_fighter(save: SaveDocument, rules: Rules, cid: str) -> None:
    """A dead fighter (roaming as a Hater) back in the freezer, as the Fighter Depot does it."""
    fighter = next((f for f in fighters(save) if f.cid == cid), None)
    if fighter is None:
        raise EditError("that fighter is not in the save")
    if fighter.state not in DEAD:
        raise EditError(f"{fighter.name or 'That fighter'} is not dead")
    who = fighter.name or "A fighter"
    found = deaths(save, cid)
    dids = {d.did for d in found}
    died = save.doc.get("diedchara")
    if isinstance(died, dict):
        records = (died.get("dchrs") or {}).get(save.uid) if isinstance(died.get("dchrs"), dict) else None
        if isinstance(records, list):
            kept = [r for r in records if not (isinstance(r, dict) and r.get("did") in dids)]
            died["dchrs"][save.uid] = kept or {}
        levels = (died.get("bodylvls") or {}).get(save.uid) if isinstance(died.get("bodylvls"), dict) else None
        if isinstance(levels, list):
            levels[:] = [r for r in levels if not (isinstance(r, dict) and r.get("did") in dids)]
        for key in ("pspts", "eqpts", "eqskls"):
            held = (died.get(key) or {}).get(save.uid) if isinstance(died.get(key), dict) else None
            if isinstance(held, dict):
                for did in dids:
                    held.pop(did, None)
    chara = save.get(fighter.chr_path)
    try:
        lost = _int(json.loads(chara.get("bloodnium_result") or "{}").get("bloodnium"))
    except (TypeError, ValueError, AttributeError):
        lost = 0
    chara.update(state="FREE", hp=FULL_HP, bloodnium_result=EMPTY_RESULT)
    bag = save.get(("soul", "deathbag", save.uid, cid))
    room = rules.capacity(fighter.kind, fighter.grade, 0, "bag") + fighter.bag
    if isinstance(bag, list):
        while len(bag) < room:
            bag.append({"uid": _int(save.uid), "cid": cid, "slot": len(bag), "type": EMPTY, "eid": "", "site": "",
                        "arm_slot": -1})
    _count(save, "recover_fighter")
    price = max((d.price for d in found), default=0)
    save.note(f"Fighter Freezer: brought {who} back", "dead (a Hater)",
              "in the freezer, full HP" + (f" (the Fighter Depot's {price:,} Kill Coins not charged)" if price else "")
              + (f"; the {lost:,} Bloodnium of their last run is not paid" if lost else ""))
