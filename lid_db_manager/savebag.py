"""A fighter's Death Bag, and what they wear and hold.

The bag is soul.deathbag[uid][cid]: a list of slots {uid, cid, slot, type, eid,
site, arm_slot}, with type and eid as in the Storage Box (savestorage.py) and the
records in the same tables, owner "USER". A worn or held piece is a bag slot
too: site says where it is worn (EQSITE_HEAD, EQSITE_BODY, EQSITE_LEGS) or which
hand holds it (EQSITE_ARMR, EQSITE_ARML), and a weapon in a quick slot has
arm_slot 0-2 (right hand) or 3-5 (left). chr.select_arm_slots is "right,left":
the quick slot (0-2) each hand last took up. All of this checked on every bag in
a real save.

Room: the fighter's Death Bag space, and VIP_INCREASE_DEATHBAG (10) more while
the Express Pass runs; the game keeps the list that long, so it is padded to it
when something needs the room.

The rules for wearing, from the game itself:
- Grips (BrgCommonPawn_CustomChara.uc): a double-grip weapon (master_ptarm ->
  master_ptarm_type.grip) leaves the other hand empty, and a weapon taken up next
  to one puts the double-grip one down. Whatever was taken up last wins.
- The stat a part needs (the exe): master_part lvllmt grown by lvllmt_c the way
  durability grows (savestorage.calc_part_param), times what is left after
  decal discounts (1.0 less each worn discount decal's share, added up), cut to
  a whole number; the fighter's stat is master_bodylvl_status_value at that
  stat's level. Every piece worn in a real save passes. How each discount decal
  counts is oberlinx's, from in-game numbers.

Ported with the help of oberlinx's LET IT DIE save editor (used with permission).
"""

from __future__ import annotations

import re
import sqlite3
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path

from .saveedit import EditError, SaveDocument
from .savestorage import EMPTY, SLOT_TYPES, Catalog, Container, _int

OWNER = "USER"
QUICK = 3                                    # quick slots per hand
HANDS = {"right": ("EQSITE_ARMR", 0, 0), "left": ("EQSITE_ARML", 3, 1)}   # site, first arm_slot, place in select
HAND_LABELS = {"right": "Right hand", "left": "Left hand"}
ARMOR_SITES = {"EQSITE_HEAD": "PTTP_HEAD", "EQSITE_BODY": "PTTP_BODY", "EQSITE_LEGS": "PTTP_LEGS"}
SITE_LABELS = {"EQSITE_HEAD": "Head", "EQSITE_BODY": "Body", "EQSITE_LEGS": "Legs",
               "EQSITE_ARMR": "Right hand", "EQSITE_ARML": "Left hand"}
NEVER_WORN = re.compile(r"^PT_(ARM_(FirstAid|Food|Sand)$|NONE_|MASK_|PANTS_)")
DOUBLE = "GRIP_DOUBLE"


def _f32(value: float) -> float:
    return struct.unpack("<f", struct.pack("<f", value))[0]


@dataclass
class GearRules:
    status: dict = field(default_factory=dict)      # (class, grade, stat level) -> master_bodylvl_status_value row
    discounts: dict = field(default_factory=dict)   # sklid -> (type, val0, val1)
    grips: dict = field(default_factory=dict)       # ptid -> GRIP_SINGLE / GRIP_DOUBLE
    consts: dict = field(default_factory=dict)

    @classmethod
    def read(cls, db_path: Path | str | None) -> "GearRules":
        rules = cls()
        if not db_path or not Path(db_path).is_file():
            return rules
        try:
            con = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
        except sqlite3.Error:
            return rules
        con.row_factory = sqlite3.Row
        try:
            for name, sql, fill in (
                ("status", "SELECT * FROM master_bodylvl_status_value",
                 lambda r: rules.status.__setitem__((str(r["type"]), _int(r["grade"]), _int(r["lvl"])), dict(r))),
                ("discounts", "SELECT id, type, val0, val1 FROM master_skill",
                 lambda r: rules.discounts.__setitem__(str(r["id"]), (str(r["type"] or ""), r["val0"], r["val1"]))),
                ("grips", "SELECT a.id, t.grip FROM master_ptarm a JOIN master_ptarm_type t ON t.id = a.subtype",
                 lambda r: rules.grips.__setitem__(str(r["id"]), str(r["grip"] or ""))),
                ("consts", "SELECT id, value FROM master_const_int",
                 lambda r: rules.consts.__setitem__(str(r["id"]), r["value"])),
            ):
                try:
                    for row in con.execute(sql):
                        fill(row)
                except (sqlite3.Error, IndexError):
                    getattr(rules, name).clear()
        finally:
            con.close()
        return rules

    def double_grip(self, ptid: str) -> bool:
        return self.grips.get(ptid) == DOUBLE

    def stat_value(self, kind: str, grade: int, stat: str, level: int) -> int | None:
        row = self.status.get((kind, int(grade), int(level)))
        value = row.get(stat.lower()) if row else None
        return _int(value) if value is not None else None

    def rate(self, stat: str, sklids: list[str]) -> float:
        """What is left of a requirement after the worn decals' discounts (1.0 = none)."""
        off = 0.0
        for sklid in sklids:
            kind, val0, val1 = self.discounts.get(sklid, ("", None, None))
            value = None
            if kind == f"SKLTP_DISCOUNT_{stat.upper()}" or kind in ("SKLTP_DISCOUNT_ALL", "SKLTP_DISCOUNT_ALL_NMH"):
                value = val0
            elif kind == "SKLTP_DISALL_ATKUP_HPDWN":
                value = val1
            try:
                value = float(value)
            except (TypeError, ValueError):
                continue
            if 0 < value < 100:
                off = _f32(off + _f32((100 - value) / 100))
        return _f32(1.0 - off)

    def vip_extra(self, save: SaveDocument, now: float | None = None) -> int:
        """The Death Bag room the Express Pass adds while it runs."""
        vip = save.get(("soul", "vip"))
        if not isinstance(vip, dict) or not _int(vip.get("flag", vip.get("vip_flag"))):
            return 0
        ends = _int(vip.get("expired_time"))
        if 0 < ends < (time.time() if now is None else now):
            return 0
        return max(0, _int(self.consts.get("VIP_INCREASE_DEATHBAG"), 10))


@dataclass
class Wearer:
    """What decides what a fighter may wear: the class, grade and stat levels as they will be
    saved, and the decals they wear."""
    name: str
    kind: str
    grade: int
    stats: dict                                     # hp..luk -> stat level
    decals: list = field(default_factory=list)      # worn sklids


def worn_decals(save: SaveDocument, cid: str) -> list[str]:
    rows = save.get(("soul", "skl", "eqskl", save.uid))
    return [str(r.get("sklid")) for r in rows if isinstance(r, dict) and r.get("cid") == cid] \
        if isinstance(rows, list) else []


@dataclass
class Gear:
    quick: dict = field(default_factory=dict)       # arm_slot 0-5 -> eid
    held: dict = field(default_factory=dict)        # right/left -> quick index 0-2, or None: an empty hand
    armor: dict = field(default_factory=dict)       # site -> eid
    chosen: dict = field(default_factory=dict)      # right/left -> the index select_arm_slots keeps

    def held_eid(self, hand: str) -> str | None:
        index = self.held.get(hand)
        return self.quick.get(HANDS[hand][1] + index) if index is not None else None


class Bag(Container):
    """One fighter's Death Bag, read and ready to change."""

    owner = OWNER

    def __init__(self, save: SaveDocument, catalog: Catalog, rules: GearRules, cid: str, room: int,
                 name: str = "", chr_path: tuple | None = None) -> None:
        super().__init__(save, catalog)
        self.rules, self.cid, self.room = rules, cid, int(room)
        self.label = f"{name or 'Fighter'}'s Death Bag"
        self.chr_path = chr_path
        bags = save.get(("soul", "deathbag", self.uid))
        if not isinstance(bags, dict) or not isinstance(bags.get(cid), list):
            raise EditError(f"{name or 'This fighter'} has no Death Bag in this save")

    @property
    def slots(self) -> list:
        return self.save.doc["soul"]["deathbag"][self.uid][self.cid]

    def _empty(self, index: int) -> dict:
        return {"uid": _int(self.uid), "cid": self.cid, "slot": index, "type": EMPTY, "eid": "", "site": "",
                "arm_slot": -1}

    def _filled(self, index: int, number: int, eid: str) -> dict:
        return {"uid": _int(self.uid), "cid": self.cid, "slot": index, "type": number, "eid": eid, "site": "",
                "arm_slot": -1}

    def free_slots(self) -> list[int]:
        """Empty slots, no more than the room left (the list grows to the room when it must)."""
        left = max(0, self.room - self.used())
        if left:
            while len(self.slots) < self.room:
                self.slots.append(self._empty(len(self.slots)))
        return super().free_slots()[:left]

    # -- what is worn --

    def _parts(self) -> dict:
        return self._records()[SLOT_TYPES["part"]]

    def _part_slots(self) -> dict:
        """eid -> its slot, for the weapons and armor in the bag."""
        return {s.get("eid"): s for s in self.slots if _int(s.get("type"), EMPTY) == SLOT_TYPES["part"]}

    def gear(self) -> Gear:
        gear = Gear()
        chosen = self._chosen()
        for slot in self._part_slots().values():
            arm, site, eid = _int(slot.get("arm_slot"), -1), str(slot.get("site") or ""), slot.get("eid")
            if 0 <= arm < 2 * QUICK:
                gear.quick[arm] = eid
            if site in ARMOR_SITES:
                gear.armor[site] = eid
        for hand, (site, base, place) in HANDS.items():
            gear.chosen[hand] = chosen[place]
            held = next((s for s in self._part_slots().values() if s.get("site") == site), None)
            arm = _int(held.get("arm_slot"), -1) if held else -1
            gear.held[hand] = arm - base if base <= arm < base + QUICK else None
        return gear

    def _chosen(self) -> list[int]:
        text = str(self.save.get(self.chr_path + ("select_arm_slots",)) or "") if self.chr_path else ""
        parts = [_int(p, 0) for p in text.split(",")] if text else []
        parts = (parts + [0, 0])[:2]
        return [p if 0 <= p < QUICK else 0 for p in parts]

    def _places(self, gear: Gear) -> dict:
        """Each place gear goes -> what is there, in words."""
        places = {}
        for hand, (_, base, _) in HANDS.items():
            places[HAND_LABELS[hand]] = self._name(gear.held_eid(hand))
            for index in range(QUICK):
                places[f"{HAND_LABELS[hand]} quick slot {index + 1}"] = self._name(gear.quick.get(base + index))
        for site in ARMOR_SITES:
            places[SITE_LABELS[site]] = self._name(gear.armor.get(site))
        return places

    def _name(self, eid) -> str:
        if not eid:
            return "(empty)"
        record = self._parts().get(eid) or {}
        ptid = str(record.get("ptid") or "")
        return f"{self.catalog.part_name(ptid)} +{self.catalog.shown_level(ptid, _int(record.get('lvl'), 1))}"

    def _put(self, gear: Gear) -> None:
        """Write the gear back onto the bag's slots and the fighter's select_arm_slots."""
        before = self._places(self.gear())
        quick = {eid: arm for arm, eid in gear.quick.items() if eid}
        held = {gear.held_eid(hand): site for hand, (site, _, _) in HANDS.items() if gear.held_eid(hand)}
        worn = {eid: site for site, eid in gear.armor.items() if eid}
        for eid, slot in self._part_slots().items():
            site = held.get(eid) or worn.get(eid) or ""
            arm = quick.get(eid, -1)
            if slot.get("site") != site:
                slot["site"] = site
            if _int(slot.get("arm_slot"), -1) != arm:
                slot["arm_slot"] = arm
        if self.chr_path:
            for hand in HANDS:
                if gear.held.get(hand) is not None:
                    gear.chosen[hand] = gear.held[hand]
            text = f"{gear.chosen['right']},{gear.chosen['left']}"
            stored = self.save.get(self.chr_path + ("select_arm_slots",))
            if stored != text and (stored or any(gear.held.get(h) is not None for h in HANDS)):
                self.save.get(self.chr_path)["select_arm_slots"] = text
        after = self._places(self.gear())
        for place, was in before.items():
            if after[place] != was:
                self.save.note(f"{self.label}: {place.lower()}", was, after[place])

    def describe(self, gear: Gear) -> str:
        bits = [f"{HAND_LABELS[h]} {self._name(gear.held_eid(h))}" for h in HANDS if gear.held_eid(h)]
        bits += [f"{SITE_LABELS[s]} {self._name(e)}" for s, e in sorted(gear.armor.items()) if e]
        return ", ".join(bits) or "nothing"

    def _settle(self, gear: Gear, prefer: str) -> None:
        """One double-grip weapon takes both hands: whichever hand was set last keeps its weapon."""
        other = "left" if prefer == "right" else "right"
        mine, theirs = gear.held_eid(prefer), gear.held_eid(other)
        if not mine or not theirs:
            return
        parts = self._parts()
        double = lambda eid: self.rules.double_grip(str((parts.get(eid) or {}).get("ptid") or ""))
        if double(mine) or double(theirs):
            gear.held[other] = None

    def set_quick(self, hand: str, index: int, eid: str | None, wearer: Wearer | None = None) -> None:
        """Put a weapon from the bag in one of a hand's quick slots (or empty it)."""
        site, base, _ = HANDS[hand]
        if eid:
            why = self.problem(eid, site, wearer)
            if why:
                raise EditError(f"That cannot go in a quick slot: {why}")
        gear = self.gear()
        target = base + index
        for arm, there in list(gear.quick.items()):
            if eid and there == eid and arm != target:      # a weapon is in one quick slot only
                del gear.quick[arm]
                for h, (_, b, _) in HANDS.items():          # and leaves the hand that held it there
                    if gear.held.get(h) is not None and b + gear.held[h] == arm:
                        gear.held[h] = None
        if eid:
            gear.quick[target] = eid
        else:
            gear.quick.pop(target, None)
        if gear.held.get(hand) == index and not eid:
            gear.held[hand] = None
        self._settle(gear, hand)
        self._put(gear)

    def set_held(self, hand: str, index: int | None) -> None:
        """Which quick slot a hand takes up (None: an empty hand)."""
        gear = self.gear()
        gear.held[hand] = index if index is not None and gear.quick.get(HANDS[hand][1] + index) else None
        if index is not None:
            gear.chosen[hand] = index
        self._settle(gear, hand)
        self._put(gear)

    def set_armor(self, site: str, eid: str | None, wearer: Wearer | None = None) -> None:
        if eid:
            why = self.problem(eid, site, wearer)
            if why:
                raise EditError(f"That cannot be worn there: {why}")
        gear = self.gear()
        gear.armor = {s: e for s, e in gear.armor.items() if e != eid}
        if eid:
            gear.armor[site] = eid
        else:
            gear.armor.pop(site, None)
        self._put(gear)

    def _release(self, eids: set) -> None:
        self.take_off(eids)

    def take_off(self, eids: set) -> int:
        """Take these off (they stay in the bag). Returns how many came off."""
        gear = self.gear()
        count = 0
        for arm, eid in list(gear.quick.items()):
            if eid in eids:
                del gear.quick[arm]
                count += 1
        for hand in HANDS:
            if gear.held.get(hand) is not None and not gear.quick.get(HANDS[hand][1] + gear.held[hand]):
                gear.held[hand] = None
        for site, eid in list(gear.armor.items()):
            if eid in eids:
                del gear.armor[site]
                count += 1
        if count:
            self._put(gear)
        return count

    # -- may it be worn --

    def problem(self, eid: str, site: str, wearer: Wearer | None = None) -> str:
        """Why the game would not let this piece go there, or ""."""
        if eid not in self._part_slots():
            return "it is not in this Death Bag"
        record = self._parts().get(eid)
        ptid = str((record or {}).get("ptid") or "")
        row = self.catalog.parts.get(ptid)
        if record is None or row is None:
            return "this game's data does not have it"
        wanted = "PTTP_ARM" if site in (HANDS["right"][0], HANDS["left"][0]) else ARMOR_SITES.get(site)
        if row.get("type") != wanted:
            return "it does not go there"
        if NEVER_WORN.match(ptid) or _int(row.get("is_consume")):
            return "it is not something to wear or hold"
        if _int(row.get("dur")) > 0 and _int(record.get("dur")) <= 0:
            return "it is broken"
        if wearer is None:
            return ""
        stat, base = self.catalog.requirement(ptid, _int(record.get("lvl"), 1))
        if not stat or base <= 0:
            return ""
        need = int(_f32(_f32(float(base)) * self.rules.rate(stat, wearer.decals)))
        if need <= 0:
            return ""
        have = self.rules.stat_value(wearer.kind, wearer.grade, stat, _int(wearer.stats.get(stat.lower()), 1))
        if have is None:
            return f"it needs {stat} {need:,}, and this fighter's {stat} could not be read"
        if have < need:
            return f"it needs {stat} {need:,} and {wearer.name or 'this fighter'} has {have:,}"
        return ""

    def problems(self, wearer: Wearer | None) -> list[tuple[str, str, str]]:
        """Everything worn or in a quick slot that the game would not allow: (eid, where, why)."""
        gear, out = self.gear(), []
        for arm, eid in sorted(gear.quick.items()):
            hand = "right" if arm < QUICK else "left"
            why = self.problem(eid, HANDS[hand][0], wearer)
            if why:
                out.append((eid, f"{HAND_LABELS[hand].lower()} quick slot {arm % QUICK + 1}", why))
        for site, eid in sorted(gear.armor.items()):
            why = self.problem(eid, site, wearer)
            if why:
                out.append((eid, SITE_LABELS[site].lower(), why))
        return out

    def wearable(self, site: str, wearer: Wearer | None) -> list[tuple[str, str, str]]:
        """The bag's pieces that go in this place: (eid, label, why not or "")."""
        wanted = "PTTP_ARM" if site in (HANDS["right"][0], HANDS["left"][0]) else ARMOR_SITES.get(site)
        parts, out = self._parts(), []
        for eid in self._part_slots():
            record = parts.get(eid) or {}
            ptid = str(record.get("ptid") or "")
            row = self.catalog.parts.get(ptid) or {}
            if row.get("type") != wanted or NEVER_WORN.match(ptid):
                continue
            out.append((eid, self.piece_label(eid), self.problem(eid, site, wearer)))
        return sorted(out, key=lambda x: x[1].lower())

    def piece_label(self, eid: str) -> str:
        record = self._parts().get(eid) or {}
        ptid = str(record.get("ptid") or "")
        row = self.catalog.parts.get(ptid) or {}
        label = f"{self.catalog.part_name(ptid)} +{self.catalog.shown_level(ptid, _int(record.get('lvl'), 1))}"
        full = self.catalog.full_durability(ptid, _int(record.get("lvl"), 1))
        if full > 0:
            label += f" ({max(0, min(100, _int(record.get('dur')) * 100 // full))}%)"
        if self.rules.double_grip(ptid):
            label += " - two hands"
        return label if row else ptid
