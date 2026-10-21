"""The Storage Box (the game's Coin Locker): what is in it, and adding and taking things out.

The box is soul.cl: one entry per slot, {slot, type, eid}, where type says
which table the thing is in (0 weapon or armor, 1 mushroom, 2 beast, 3 item)
and eid is its record there - part.pts[uid], mushroom.msrs, beast.bsts or
item.items, each record with owner "COIN_LOCKER". An empty slot is
{type: -1, eid: ""}. Every item takes a slot of its own; nothing stacks.

A thing added here is written the way the game writes one, checked against a
real save: a fresh id, the time it was got, a weapon or armor at full
durability (the part's dur times dur_c to the power of its level less one,
rounded down - true of every fully repaired part in the save) and full ammo
(capacity loaded, spare in reserve). A beast carries the mushroom it gives:
the game keeps it as a separate mushroom record (owner "USER", in no slot)
that the beast's rwdemsrid points at, so one is made with it. A thing taken
out has its record removed too.

Everything offered comes from the save's own game data, so what a mod adds
can be put in the box. The box holds from 30 slots (a new game's) up to
master_const_int COINLOCKER_EXPAND_LIMIT_COUNT, in steps of
COINLOCKER_EXPAND_COUNT, as the game expands it; shrinking only ever drops
empty slots from the end. Ported with the help of oberlinx's LET IT DIE save
editor (used with permission).
"""

from __future__ import annotations

import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .saveedit import EditError, SaveDocument

OWNER = "COIN_LOCKER"
SLOT_TYPES = {"part": 0, "mushroom": 1, "beast": 2, "item": 3}
EMPTY = -1
MIN_SLOTS = 30                  # a new game's box
KIND_LABELS = {"weapon": "Weapon", "armor": "Armor", "blueprint": "Blueprint", "item": "Material / item",
               "mushroom": "Mushroom", "beast": "Beast"}


# -- what can go in, from masters.db ----------------------------------------------

@dataclass(frozen=True)
class Thing:
    kind: str                   # weapon, armor, blueprint, item, mushroom, beast
    ident: str
    name: str
    platform: int = 0           # 1: PlayStation only
    roastable: bool = False


@dataclass
class Catalog:
    parts: dict = field(default_factory=dict)       # ptid -> master_part row
    items: dict = field(default_factory=dict)       # itemid -> master_item row
    mushrooms: dict = field(default_factory=dict)   # msrid -> master_mushroom row
    beasts: dict = field(default_factory=dict)      # bstid -> master_beast row
    text: dict = field(default_factory=dict)        # "SECTION.ID" -> the English text
    consts: dict = field(default_factory=dict)
    previous: dict = field(default_factory=dict)    # ptid -> the part that upgrades into it

    @classmethod
    def read(cls, db_path: Path | str | None) -> "Catalog":
        cat = cls()
        if not db_path or not Path(db_path).is_file():
            return cat
        try:
            con = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
        except sqlite3.Error:
            return cat
        con.row_factory = sqlite3.Row
        try:
            for table, key, into in (("master_part", "id", cat.parts), ("master_item", "itemid", cat.items),
                                     ("master_mushroom", "id", cat.mushrooms), ("master_beast", "id", cat.beasts)):
                try:
                    into.update({str(row[key]): dict(row) for row in con.execute(f"SELECT * FROM {table}")})
                except (sqlite3.Error, IndexError):
                    into.clear()
            try:
                cat.text = {f"{s}.{i}": str(t) for s, i, t in con.execute(
                    "SELECT sct, id, txt FROM master_text WHERE lang = 'int'") if t}
            except sqlite3.Error:
                pass
            try:
                cat.consts = {str(i): v for i, v in con.execute("SELECT id, value FROM master_const_int")}
            except sqlite3.Error:
                pass
        finally:
            con.close()
        for ptid, row in cat.parts.items():
            if row.get("nextptid"):
                cat.previous[str(row["nextptid"])] = ptid
        return cat

    # -- names --

    def _text(self, key) -> str:
        return self.text.get(str(key or ""), "")

    def part_name(self, ptid: str) -> str:
        return self._text((self.parts.get(ptid) or {}).get("name")) or ptid

    def item_name(self, itemid: str) -> str:
        row = self.items.get(itemid) or {}
        if row.get("itemtype") == "ITTP_RMAP":
            unrevealed = itemid.endswith("U")
            ptid = "PT_" + itemid[len("ITMP_"):].rstrip("U") if itemid.startswith("ITMP_") else ""
            return f"Blueprint: {self.part_name(ptid)}" + (" (unrevealed)" if unrevealed else "")
        return self._text(row.get("name")) or itemid

    def mushroom_name(self, msrid: str, grilled: bool = False) -> str:
        row = self.mushrooms.get(msrid) or {}
        return self._text(row.get("r_name" if grilled else "c_name")) or self._text(row.get("c_name")) or msrid

    def beast_name(self, bstid: str, grilled: bool = False) -> str:
        row = self.beasts.get(bstid) or {}
        return self._text(row.get("bname" if grilled else "name")) or self._text(row.get("name")) or bstid

    # -- the lists --

    def things(self, kind: str) -> list[Thing]:
        if kind in ("weapon", "armor"):
            found = [Thing(kind, ptid, self.part_name(ptid), _int(row.get("platform")))
                     for ptid, row in self.parts.items()
                     if (row.get("type") == "PTTP_ARM") == (kind == "weapon") and self._text(row.get("name"))
                     and self.obtainable(ptid) and not self._folded(ptid)]
        elif kind == "blueprint":
            found = [Thing(kind, i, self.item_name(i), _int((self.parts.get(_blueprint_part(i)) or {}).get("platform")))
                     for i, row in self.items.items() if row.get("itemtype") == "ITTP_RMAP" and not i.endswith("U")
                     and _blueprint_part(i) in self.parts]
        elif kind == "item":
            found = [Thing(kind, i, self.item_name(i), _int(row.get("platform")))
                     for i, row in self.items.items() if row.get("itemtype") != "ITTP_RMAP" and self._text(row.get("name"))]
        elif kind == "mushroom":
            found = [Thing(kind, i, self.mushroom_name(i), roastable=bool(_int(row.get("enable_roast"))))
                     for i, row in self.mushrooms.items() if self._text(row.get("c_name"))]
        elif kind == "beast":
            found = [Thing(kind, i, self.beast_name(i), roastable=True)
                     for i, row in self.beasts.items() if self._text(row.get("name"))]
        else:
            raise EditError(f"{kind} is not something the Storage Box holds")
        return sorted(found, key=lambda t: (t.name.lower(), t.ident))

    # -- weapon and armor levels --

    # The game's own rule (BrgGame-Steam.exe, read in Ghidra): a part's "+N" is its saved level
    # plus master_part.is_limitbreak minus 1 - is_limitbreak is not a yes/no but where the part
    # starts (0 for a normal part, so +0 at level 1; 5 for an uncapped form, so +5). The offline
    # server levels a part up only while its level is under reflvllmt and "+N" stays within
    # master_const_int PART_LIMITBREAK_OFFSET_MAX (24); the client's R&D list checks the same sum.

    def _offset(self, ptid: str) -> int:
        return max(0, _int((self.parts.get(ptid) or {}).get("is_limitbreak")))

    def _shown_max(self) -> int:
        return _int(self.consts.get("PART_LIMITBREAK_OFFSET_MAX"), 24)

    def max_level(self, ptid: str) -> int:
        """The highest saved level a part reaches: reflvllmt, and no further than "+N" may go."""
        cap = _int((self.parts.get(ptid) or {}).get("reflvllmt"), 1)
        return max(1, min(cap, self._shown_max() + 1 - self._offset(ptid)))

    def shown_level(self, ptid: str, raw: int) -> int:
        """The "+N" the game shows for a saved level."""
        raw = max(1, min(int(raw), self.max_level(ptid)))
        return raw + self._offset(ptid) - 1

    def _own_range(self, ptid: str) -> tuple[int, int]:
        return self.shown_level(ptid, 1), self.shown_level(ptid, self.max_level(ptid))

    def uncapped_form(self, ptid: str) -> str:
        """The part this one becomes once uncapped (a Mingo Head) - its own row in master_part
        with the same name, going on from +5 - or "" when it has none."""
        after = str((self.parts.get(ptid) or {}).get("nextptid") or "")
        row = self.parts.get(after)
        if row and _int(row.get("is_limitbreak")) and row.get("name") == self.parts[ptid].get("name"):
            return after
        return ""

    def obtainable(self, ptid: str) -> bool:
        """A part the game can hand out: it has a blueprint, or another part upgrades into it
        (oberlinx's rule). That leaves out Fists, the empty "None" slots, gas masks and test rows."""
        return "ITMP_" + ptid[len("PT_"):] in self.items or ptid in self.previous

    def _folded(self, ptid: str) -> bool:
        """An uncapped form is offered under its capped part, so the name is listed once."""
        before = self.previous.get(ptid)
        return bool(before) and self.uncapped_form(before) == ptid

    def level_range(self, ptid: str) -> tuple[int, int]:
        """The lowest and highest "+N" this part can show - through its uncapped form when it has one."""
        low, high = self._own_range(ptid)
        after = self.uncapped_form(ptid)
        return (low, self._own_range(after)[1]) if after else (low, high)

    def part_at(self, ptid: str, shown: int) -> tuple[str, int]:
        """The part and raw level for "+N": past the capped part's top it is the uncapped form."""
        after = self.uncapped_form(ptid)
        if after and int(shown) > self._own_range(ptid)[1]:
            return after, self.raw_level(after, shown)
        return ptid, self.raw_level(ptid, shown)

    def raw_level(self, ptid: str, shown: int) -> int:
        low, high = self._own_range(ptid)
        shown = max(low, min(int(shown), high))
        return max(1, min(shown - self._offset(ptid) + 1, self.max_level(ptid)))

    def full_durability(self, ptid: str, raw: int) -> int:
        row = self.parts.get(ptid) or {}
        dur = _int(row.get("dur"))
        if dur <= 0:
            return 0
        return calc_part_param(raw, dur, _percent(row.get("dur_c")))

    def requirement(self, ptid: str, raw: int) -> tuple[str, int]:
        """The stat a part needs and how much of it at this level, before any decal discount
        (master_part lvllmttp, and lvllmt grown by lvllmt_c the way durability grows)."""
        row = self.parts.get(ptid) or {}
        stat = str(row.get("lvllmttp") or "").upper()
        return stat, calc_part_param(raw, _int(row.get("lvllmt")), _percent(row.get("lvllmt_c")))

    # -- the box's size --

    def capacity_step(self) -> int:
        return max(1, _int(self.consts.get("COINLOCKER_EXPAND_COUNT"), 10))

    def capacity_max(self) -> int:
        return max(MIN_SLOTS, _int(self.consts.get("COINLOCKER_EXPAND_LIMIT_COUNT"), 2000))


def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _percent(value) -> int:
    """A master_part growth rate (1.05) as the whole percent the game works in (105)."""
    try:
        return round(float(value) * 100)
    except (TypeError, ValueError):
        return 100


def calc_part_param(level: int, base: int, percent: int) -> int:
    """A part's value at a level: base x (percent/100) to the power of level - 1, worked out in
    whole numbers exactly as the game does (BrgDatabase.CalcPartParam, FUN_140fcce00 in the exe):
    a 12-digit fixed point, cut down by 100 after the sixth step so it cannot overflow."""
    level = int(level)
    if level < 2:
        return base
    if level == 2:
        return int(base * percent / 100) if base * percent >= 0 else -(-base * percent // 100)
    grown, steps = percent, 0
    while steps < level - 2:
        grown *= percent
        if steps > 4:
            grown = int(grown / 100)
        steps += 1
    if steps <= 4:
        grown *= 100 ** (5 - steps)
    product = base * grown
    return product // 10 ** 12 if product >= 0 else -(-product // 10 ** 12)


def _blueprint_part(itemid: str) -> str:
    return "PT_" + itemid[len("ITMP_"):].rstrip("U") if itemid.startswith("ITMP_") else ""


# -- what is in the box ------------------------------------------------------------

@dataclass
class Group:
    """One kind of thing in the box: how many, and which slots hold them."""
    kind: str
    ident: str
    name: str
    detail: str                 # "+3", "grilled", "unrevealed" or ""
    slots: list = field(default_factory=list)
    level: int = 0              # a part's raw level
    grilled: bool = False

    @property
    def count(self) -> int:
        return len(self.slots)

    @property
    def key(self) -> tuple:
        return (self.kind, self.ident, self.level, self.grilled)


class Container:
    """Slots holding records - the Storage Box, or a fighter's Death Bag. A subclass says
    where its slot list is, whose records it holds (owner) and how an empty and a filled
    slot are written."""

    owner = OWNER
    label = "Storage Box"

    def __init__(self, save: SaveDocument, catalog: Catalog) -> None:
        self.save, self.catalog = save, catalog
        self.uid = save.uid

    # -- the slots, as the subclass keeps them --

    @property
    def slots(self) -> list:
        raise NotImplementedError

    def _empty(self, index: int) -> dict:
        return {"slot": index, "type": EMPTY, "eid": ""}

    def _filled(self, index: int, number: int, eid: str) -> dict:
        return {"slot": index, "type": number, "eid": eid}

    # -- reading --

    @property
    def capacity(self) -> int:
        return len(self.slots)

    def used(self) -> int:
        return sum(1 for s in self.slots if _int(s.get("type"), EMPTY) != EMPTY)

    def _table(self, kind: str) -> list:
        """The record list a kind of thing lives in (made a list when the game left it {})."""
        doc = self.save.doc
        if kind == "part":
            parts = doc.setdefault("part", {}).setdefault("pts", {})
            if not isinstance(parts.get(self.uid), list):
                parts[self.uid] = []
            return parts[self.uid]
        section, key = {"mushroom": ("mushroom", "msrs"), "beast": ("beast", "bsts"), "item": ("item", "items")}[kind]
        holder = doc.setdefault(section, {})
        if not isinstance(holder.get(key), list):
            holder[key] = []
        return holder[key]

    def _records(self) -> dict:
        """slot type -> {eid: record}."""
        found = {}
        where = {"part": ("part", "pts", self.uid), "mushroom": ("mushroom", "msrs"), "beast": ("beast", "bsts"),
                 "item": ("item", "items")}
        for kind, number in SLOT_TYPES.items():
            rows = self.save.get(where[kind])
            found[number] = {r.get("eid"): r for r in rows if isinstance(r, dict)} if isinstance(rows, list) else {}
        return found

    def groups(self) -> list[Group]:
        records = self._records()
        found: dict[tuple, Group] = {}
        for index, slot in enumerate(self.slots):
            number = _int(slot.get("type"), EMPTY)
            if number == EMPTY:
                continue
            record = records.get(number, {}).get(slot.get("eid"))
            group = self._group_of(number, record, slot.get("eid"))
            found.setdefault(group.key, group).slots.append(index)
        return sorted(found.values(), key=lambda g: (list(KIND_LABELS).index(g.kind), g.name.lower(), g.level))

    def _group_of(self, number: int, record: dict | None, eid) -> Group:
        cat = self.catalog
        if record is None:
            return Group("item", f"missing:{eid}", "(a slot whose record is missing)", "")
        if number == SLOT_TYPES["part"]:
            ptid = str(record.get("ptid") or "")
            kind = "weapon" if (cat.parts.get(ptid) or {}).get("type") == "PTTP_ARM" else "armor"
            raw = _int(record.get("lvl"), 1)
            return Group(kind, ptid, cat.part_name(ptid), f"+{cat.shown_level(ptid, raw)}", level=raw)
        if number == SLOT_TYPES["mushroom"]:
            grilled = _int(record.get("state")) == 1
            msrid = str(record.get("msrid") or "")
            return Group("mushroom", msrid, cat.mushroom_name(msrid, grilled), "grilled" if grilled else "",
                         grilled=grilled)
        if number == SLOT_TYPES["beast"]:
            grilled = _int(record.get("state")) == 1
            bstid = str(record.get("bstid") or "")
            return Group("beast", bstid, cat.beast_name(bstid, grilled), "grilled" if grilled else "",
                         grilled=grilled)
        itemid = str(record.get("itemid") or "")
        kind = "blueprint" if (cat.items.get(itemid) or {}).get("itemtype") == "ITTP_RMAP" else "item"
        return Group(kind, itemid, cat.item_name(itemid), "")

    def free_slots(self) -> list[int]:
        return [i for i, s in enumerate(self.slots) if _int(s.get("type"), EMPTY) == EMPTY]

    # -- changing --

    def add(self, kind: str, ident: str, count: int = 1, level: int | None = None, grilled: bool = False,
            unrevealed: bool = False, now: int | None = None, ids=None) -> int:
        """``count`` of a thing into the first free slots. ``level`` is a part's raw level
        (its lowest when not given). ``ids`` gives the new records' ids, two a thing (the
        second for a beast's mushroom), so a change played again makes the same records.
        Returns how many went in - fewer when it fills."""
        cat = self.catalog
        now = int(now if now is not None else time.time())
        given = iter(ids or ())
        new_id = lambda: next(given, None) or str(uuid.uuid4())
        free = self.free_slots()
        count = max(0, min(int(count), len(free)))
        if kind in ("weapon", "armor"):
            row = cat.parts.get(ident)
            if row is None:
                raise EditError(f"{ident} is not a weapon or armor in this game's data")
            raw = max(1, min(int(level or 1), cat.max_level(ident)))
            name = f"{cat.part_name(ident)} +{cat.shown_level(ident, raw)}"
        elif kind in ("blueprint", "item"):
            ident = ident + "U" if kind == "blueprint" and unrevealed and not ident.endswith("U") else ident
            if ident not in cat.items:
                raise EditError(f"{ident} is not an item in this game's data")
            name = cat.item_name(ident)
        elif kind == "mushroom":
            row = cat.mushrooms.get(ident)
            if row is None:
                raise EditError(f"{ident} is not a mushroom in this game's data")
            grilled = grilled and bool(_int(row.get("enable_roast")))
            name = cat.mushroom_name(ident, grilled)
        elif kind == "beast":
            row = cat.beasts.get(ident)
            if row is None:
                raise EditError(f"{ident} is not a beast in this game's data")
            name = cat.beast_name(ident, grilled)
        else:
            raise EditError(f"{kind} is not something the {self.label} holds")
        for index in free[:count]:
            eid, second = new_id(), new_id()
            if kind in ("weapon", "armor"):
                self._table("part").append({
                    "uid": _int(self.uid), "eid": eid, "gettime": now, "owner": self.owner, "created": now,
                    "modified": now, "ptid": ident, "rest": _int(row.get("capacity")),
                    "spare": _int(row.get("spare")), "grade": 0, "dur": cat.full_durability(ident, raw), "lvl": raw})
                number = SLOT_TYPES["part"]
            elif kind in ("blueprint", "item"):
                self._table("item").append({"eid": eid, "gettime": now, "itemid": ident, "owner": self.owner})
                number = SLOT_TYPES["item"]
            elif kind == "mushroom":
                self._table("mushroom").append({"eid": eid, "gettime": now, "owner": self.owner, "msrid": ident,
                                                "eefcid": "", "tefcid": "", "posonce": 1, "state": int(grilled)})
                number = SLOT_TYPES["mushroom"]
            else:
                reward = ""
                if row.get("rwdmsrid"):
                    reward = second
                    self._table("mushroom").append({"eid": reward, "gettime": now, "owner": "USER",
                                                    "msrid": str(row["rwdmsrid"]), "eefcid": "", "tefcid": "",
                                                    "posonce": 0, "state": 0})
                self._table("beast").append({"eid": eid, "gettime": now, "owner": self.owner, "bstid": ident,
                                             "rwdemsrid": reward, "state": int(grilled), "lvl": 1, "posonce": 1})
                number = SLOT_TYPES["beast"]
            self.slots[index] = self._filled(index, number, eid)
        if count:
            self.save.note(f"{self.label}: added {name}", "", f"{count:,}")
        return count

    def remove(self, slots: list[int], label: str = "") -> int:
        """Empty these slots, and remove what was in them. Returns how many were emptied."""
        records = self._records()
        gone = {number: set() for number in SLOT_TYPES.values()}
        emptied = 0
        for index in sorted(set(slots)):
            if not 0 <= index < self.capacity:
                continue
            slot = self.slots[index]
            number = _int(slot.get("type"), EMPTY)
            if number == EMPTY:
                continue
            record = records.get(number, {}).get(slot.get("eid"))
            if record is not None and record.get("owner") == self.owner:
                gone[number].add(slot.get("eid"))
                if number == SLOT_TYPES["beast"] and record.get("rwdemsrid"):
                    gone[SLOT_TYPES["mushroom"]].add(record["rwdemsrid"])      # the mushroom it carried
            self.slots[index] = self._empty(index)
            emptied += 1
        for kind, number in SLOT_TYPES.items():
            if gone[number]:
                table = self._table(kind)
                table[:] = [r for r in table if not (isinstance(r, dict) and r.get("eid") in gone[number])]
        if emptied:
            self.save.note(f"{self.label}: took out {label or 'things'}", "", f"{emptied:,}")
        return emptied

    def take(self, group_key: tuple, count: int) -> int:
        """``count`` of one group out (the most worn first, for weapons and armor)."""
        group = next((g for g in self.groups() if g.key == group_key), None)
        if group is None:
            return 0
        slots = group.slots
        if group.kind in ("weapon", "armor"):
            records = self._records()[SLOT_TYPES["part"]]
            slots = sorted(slots, key=lambda i: _int((records.get(self.slots[i].get("eid")) or {}).get("dur")))
        return self.remove(slots[:max(0, int(count))], f"{group.name} {group.detail}".strip())

    def picks(self, group_key: tuple, count: int, best: bool = True) -> list[str]:
        """The record ids of ``count`` of one group: the best kept first (or the most worn)."""
        group = next((g for g in self.groups() if g.key == group_key), None)
        if group is None:
            return []
        slots = group.slots
        if group.kind in ("weapon", "armor"):
            records = self._records()[SLOT_TYPES["part"]]
            slots = sorted(slots, key=lambda i: _int((records.get(self.slots[i].get("eid")) or {}).get("dur")),
                           reverse=best)
        return [self.slots[i].get("eid") for i in slots[:max(0, int(count))]]

    def _release(self, eids: set) -> None:
        """Whatever has to happen before these leave (a Death Bag takes off what is worn)."""

    def move(self, eids: list, target: "Container", label: str = "") -> int:
        """Move these things (by record id) into the first free slots of ``target`` - the Storage
        Box and a Death Bag, in the same save - as the game moves them: the record stays, only
        its owner changes. Returns how many moved (fewer when ``target`` fills)."""
        at = {s.get("eid"): i for i, s in enumerate(self.slots) if _int(s.get("type"), EMPTY) != EMPTY}
        chosen = list(dict.fromkeys(e for e in eids if e in at))
        free = target.free_slots()
        chosen = chosen[:len(free)]
        if not chosen:
            return 0
        self._release(set(chosen))
        records = self._records()
        for eid, into in zip(chosen, free):
            index = at[eid]
            number = _int(self.slots[index].get("type"), EMPTY)
            record = records.get(number, {}).get(eid)
            if record is not None:
                record["owner"] = target.owner
            target.slots[into] = target._filled(into, number, eid)
            self.slots[index] = self._empty(index)
        self.save.note(f"{self.label} to {target.label}: {label or 'things'}", "", f"{len(chosen):,}")
        return len(chosen)

    def repair_all(self) -> int:
        """Every weapon and armor in it to full durability and ammo (never lowered).
        Returns how many changed."""
        records = self._records()[SLOT_TYPES["part"]]
        changed = 0
        for slot in self.slots:
            if _int(slot.get("type"), EMPTY) != SLOT_TYPES["part"]:
                continue
            record = records.get(slot.get("eid"))
            row = self.catalog.parts.get(str((record or {}).get("ptid")))
            if record is None or row is None:
                continue
            wanted = {"dur": self.catalog.full_durability(record["ptid"], _int(record.get("lvl"), 1)),
                      "rest": _int(row.get("capacity")), "spare": _int(row.get("spare"))}
            touched = False
            for key, value in wanted.items():
                if key in record and _int(record[key]) < value:
                    record[key] = value
                    touched = True
            changed += touched
        if changed:
            self.save.note(f"{self.label}: repaired and refilled", "", f"{changed:,}")
        return changed


class Box(Container):
    """The Storage Box of one save, read and ready to change."""

    def __init__(self, save: SaveDocument, catalog: Catalog) -> None:
        super().__init__(save, catalog)
        if not isinstance(save.get(("soul", "cl")), list):
            raise EditError("this save has no Storage Box")

    @property
    def slots(self) -> list:
        return self.save.doc["soul"]["cl"]

    def set_capacity(self, wanted: int) -> int:
        """Grow (empty slots at the end) or shrink (empty slots off the end, never one in use)
        to ``wanted``, held to the game's sizes. Returns the new size."""
        step, top = self.catalog.capacity_step(), self.catalog.capacity_max()
        before = self.capacity
        wanted = max(MIN_SLOTS, min(int(wanted), max(top, before)))
        if wanted > before:
            wanted = min(top, wanted) if before <= top else before
            for index in range(before, wanted):
                self.slots.append({"slot": index, "type": EMPTY, "eid": ""})
        elif wanted < before:
            last_used = max((i for i, s in enumerate(self.slots) if _int(s.get("type"), EMPTY) != EMPTY), default=-1)
            floor = last_used + 1
            if floor % step:
                floor += step - floor % step
            wanted = max(wanted, floor, MIN_SLOTS)
            del self.slots[wanted:]
        if self.capacity != before:
            self.save.note("Storage Box: slots", before, self.capacity)
        return self.capacity
