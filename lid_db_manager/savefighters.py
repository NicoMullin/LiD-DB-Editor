"""The fighters in a save, and changing them within what the game allows.

A fighter is two records: soul.chr.chrs[uid] has who it is (name, class,
grade, limit break, model, gas mask, state) and bodyuser[uid] - matched by cid
- has its levels: the six stats, the decal slots, Death Bag space and rage
bars bought on top of what the class gives for free, each stat's bonus, and
the fighter level. The level is not a value of its own: the game keeps it as
the six stat levels added up, less 5, plus what was bought (true of every
fighter in a real save), so it is worked out again after a change.

The rules are the game's own, read out of its scripts (BrgGame.upk's
BrgDatabase, decompiled) and checked against a real save:

- A fighter's limit break is worked out from its levels, not taken from the
  save (GetLimitBreakByBodyLvl): its highest stat level - with the bag, decal
  and rage purchases counted as levels too - is looked up in
  master_bodylvl_status_value. Limit break n starts one level past the cap of
  n - 1. So the editor never sets a limit break: raising the stats does, and
  the save's own limit_break is written to match.
- A stat's cap is master_body_detail's param_lv_max for that limit break
  (GetBodyLvlMax). At limit break 0 each stat's bonus (bodyuser.*_bonus) is
  added to its cap - so a stat a point over the cap is that bonus, not a
  mistake.
- The bonuses are rolled when a fighter is bought (in the exe: the purchase
  handler at 0x141347db0 calls 0x1411c52c0, which rolls 0x1411b1d40 once for
  each of the six stats): a percentage from master_const_int's BODY_BONUS_N
  rows (N percent, weighted by the row's value - 0/5/10/15/20 at 50/20/15/10/5
  in the stock game) of the grade's limit break 0 cap, rounded down. The class
  plays no part. So only those values can be a bonus - at grade 6, 0, 1, 2, 3
  or 5 - and the editor offers only them.
- Decal slots, Death Bag space and rage bars can only be bought past limit
  break 0, up to that limit break's capacity less the base (GetBodyLvlMax).

oberlinx's LET IT DIE save editor (used with permission) showed the way to the
fields and its "Max all fighters", confirmed in game.

A value that is not changed is left as it is, even outside these rules (a real
save had a fighter with a decal slot more than its grade gives); a new class or
grade fits everything to it. Fighters the game is in the middle of something
with are left alone: the dead (roaming as Haters), the kidnapped, placeholders,
and the one in a run. A fighter on defense is also kept as the defense list
has it (fortzmbsetting holds its own copy of its class, grade and level) -
only its name and look change.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from .saveedit import EditError, SaveDocument
from .savesummary import CLASS_CODES, STATS, Names

STAT_LABELS = {"hp": "HP", "str": "STR", "dex": "DEX", "vit": "VIT", "stm": "STM", "luk": "LUK"}
NAME_MAX = 16                   # BrgUIMenu_BodyFreezer / CreatePlayerName: mCharacterNameLimit=16
DEFAULT_BODIES = (("BODY_FEMALE_001", "GENDER_FEMALE"), ("BODY_MALE_001", "GENDER_MALE"))
BOUGHT = ("skill", "bag", "rage")       # bodyuser's purchases, in the order the totals are named
TOTALS = {"skill": "decal_slots", "bag": "death_bag", "rage": "rage"}


# -- the rules, from masters.db ----------------------------------------------------

@dataclass(frozen=True)
class Limits:
    """What a fighter of one class and grade allows at the limit break its stats give."""
    limit_break: int
    stat_top: int                   # the highest any stat can go at this grade (its last limit break's cap)
    bonus_choices: tuple            # the bonuses the game can roll for this grade, lowest first
    base: dict                      # skill/bag/rage -> what the class has for free (limit break 0)
    bought_max: dict                # skill/bag/rage -> the most that can be bought at this limit break

    @property
    def bonus_max(self) -> int:
        return max(self.bonus_choices)

    def total_range(self, what: str) -> tuple[int, int]:
        return self.base[what], self.base[what] + self.bought_max[what]


@dataclass
class Rules:
    details: dict = field(default_factory=dict)     # (class, grade, limit break) -> master_body_detail row
    status: dict = field(default_factory=dict)      # (class, grade, stat level) -> limit break
    bodies: list = field(default_factory=list)      # (body id, gender)
    gasmasks: list = field(default_factory=list)    # (gas mask id, gender)
    bonus_odds: dict = field(default_factory=dict)  # percent of the cap -> its weight (BODY_BONUS_N)
    names: Names = field(default_factory=Names)

    @classmethod
    def read(cls, db_path: Path | str | None) -> "Rules":
        rules = cls(names=Names(db_path))
        if not db_path or not Path(db_path).is_file():
            return rules
        try:
            con = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
        except sqlite3.Error:
            return rules
        con.row_factory = sqlite3.Row
        # a database without these tables (or columns) just has no rules to give
        try:
            try:
                for row in con.execute("SELECT * FROM master_body_detail"):
                    rules.details[(str(row["type"]), int(row["grade"]), int(row["limit_break"]))] = dict(row)
                for row in con.execute("SELECT type, grade, lvl, limit_break FROM master_bodylvl_status_value"):
                    rules.status[(str(row[0]), int(row[1]), int(row[2]))] = int(row[3])
            except (sqlite3.Error, IndexError, TypeError, ValueError):
                rules.details, rules.status = {}, {}
            try:
                for ident, weight in con.execute("SELECT id, value FROM master_const_int WHERE id LIKE 'BODY_BONUS_%'"):
                    number = re.fullmatch(r"BODY_BONUS_(\d+)", str(ident))
                    if number:
                        rules.bonus_odds[int(number.group(1))] = int(weight or 0)
            except (sqlite3.Error, TypeError, ValueError):
                rules.bonus_odds = {}
            for table, into in (("master_body", rules.bodies), ("master_gasmask", rules.gasmasks)):
                try:
                    into.extend((str(i), str(g)) for i, g in con.execute(
                        f"SELECT id, gender FROM {table} ORDER BY id"))
                except sqlite3.Error:
                    into.clear()
        finally:
            con.close()
        return rules

    # -- the tables --

    def classes(self) -> list[str]:
        """The classes a fighter can be (the legacy OLD one left out)."""
        found = sorted({t for t, _, _ in self.details if t != "OLD"})
        return found or [c for c in CLASS_CODES if c != "OLD"]

    def max_grade(self, kind: str) -> int:
        grades = [g for t, g, _ in self.details if t == kind]
        return max(grades) if grades else 6

    def max_limit_break(self, kind: str, grade: int) -> int:
        found = [lb for t, g, lb in self.details if t == kind and g == grade]
        return max(found) if found else 0

    def _row(self, kind: str, grade: int, lb: int) -> dict | None:
        return self.details.get((kind, grade, lb))

    def cap(self, kind: str, grade: int, lb: int) -> int:
        return _num(self._row(kind, grade, lb), "param_lv_max", 1)

    def capacity(self, kind: str, grade: int, lb: int, what: str) -> int:
        row = self._row(kind, grade, lb)
        if what == "skill":
            return len([x for x in str((row or {}).get("skill_slots") or "").split(",") if x.strip()])
        return _num(row, "bag_capacity" if what == "bag" else "rage_capacity", 0)

    # -- the game's own sums (BrgDatabase) --

    def param_level(self, bought: int, kind: str, grade: int, what: str) -> int:
        """_GetBagParamLevel (and its skill and rage twins): what was bought, as the stat
        level it stands for - the limit break it was bought at, and how far into it."""
        if self._row(kind, grade, 0) is None:
            return 0
        if bought <= 0:
            return self.cap(kind, grade, 0)
        lb = 1
        if self._row(kind, grade, lb) is None:
            return self.cap(kind, grade, 0) + bought
        step = self.capacity(kind, grade, 1, what) - self.capacity(kind, grade, 0, what)
        before = self.capacity(kind, grade, 1, what)
        while bought > step:
            bought -= step
            lb += 1
            if self._row(kind, grade, lb) is None:
                break
            step = self.capacity(kind, grade, lb, what) - before
            before = self.capacity(kind, grade, lb, what)
        return self.cap(kind, grade, lb - 1) + bought

    def limit_break_of(self, kind: str, grade: int, stats: dict, bonus: dict,
                       skill: int = 0, bag: int = 0, rage: int = 0) -> int:
        """_GetLimitBreakByBodyLvl: the limit break the game reads from these levels."""
        if self._row(kind, grade, 1) is None:
            return 0
        bought = [self.param_level(skill, kind, grade, "skill"), self.param_level(bag, kind, grade, "bag"),
                  self.param_level(rage, kind, grade, "rage")]
        top = max([stats.get(k, 1) for k in STATS] + bought)
        if top <= self.cap(kind, grade, 1):
            # still within limit break 1's reach: the bonuses do not count towards it
            top = max([stats.get(k, 1) - bonus.get(k, 0) for k in STATS] + bought)
        return self.status.get((kind, grade, top), 0)

    def bonus_choices(self, kind: str, grade: int) -> tuple:
        """The bonuses the purchase roll can give a stat at this grade: each BODY_BONUS_N with
        a chance, as N percent of limit break 0's cap, rounded down."""
        cap = self.cap(kind, grade, 0) if self._row(kind, grade, 0) else 0
        odds = self.bonus_odds or {0: 50, 5: 20, 10: 15, 15: 10, 20: 5}
        return tuple(sorted({0} | {pct * cap // 100 for pct, weight in odds.items() if weight > 0}))

    def limits(self, kind: str, grade: int, stats: dict, bonus: dict) -> Limits:
        lb = self.limit_break_of(kind, grade, stats, bonus)
        base = {w: self.capacity(kind, grade, 0, w) for w in BOUGHT}
        bought = {w: max(0, self.capacity(kind, grade, lb, w) - base[w]) if lb > 0 else 0 for w in BOUGHT}
        return Limits(limit_break=lb, stat_top=self.cap(kind, grade, self.max_limit_break(kind, grade)),
                      bonus_choices=self.bonus_choices(kind, grade), base=base, bought_max=bought)

    def stat_cap(self, kind: str, grade: int, lb: int, bonus: int) -> int:
        """GetBodyLvlMax: a stat's cap at a limit break - its bonus on top at limit break 0."""
        return self.cap(kind, grade, lb) + (bonus if lb == 0 else 0)

    # -- looks --

    def gender(self, body: str) -> str:
        for ident, gender in self.bodies or DEFAULT_BODIES:
            if ident == body:
                return gender
        return "GENDER_MALE" if "MALE_" in body and "FEMALE" not in body else "GENDER_FEMALE"

    def masks_for(self, body: str) -> list[str]:
        gender = self.gender(body)
        return [ident for ident, g in self.gasmasks if g == gender]


def _num(row, key: str, default: int = 0) -> int:
    try:
        return int((row or {}).get(key, default) or default)
    except (TypeError, ValueError):
        return default


def body_label(body: str, rules: Rules) -> str:
    number = re.search(r"(\d+)$", body)
    who = "Male" if rules.gender(body) == "GENDER_MALE" else "Female"
    return f"{who} {int(number.group(1))}" if number else body


def mask_label(mask: str) -> str:
    number = re.search(r"(\d+)$", mask)
    return f"Gas mask {int(number.group(1))}" if number else mask


# -- the fighters in a save ------------------------------------------------------

@dataclass
class Fighter:
    cid: str
    name: str
    kind: str                   # the class code, chr.type
    grade: int
    limit_break: int
    state: str
    body: str
    gasmask: str
    kidnapped: bool
    level: int
    stats: dict                 # hp..luk -> stat level
    bonus: dict                 # hp..luk -> its bonus: that much more cap at limit break 0
    skill: int                  # decal slots bought on top of the base
    bag: int                    # Death Bag space bought on top of the base
    rage: int                   # rage bars on top of the base
    chr_path: tuple             # where in the save its two records are
    body_path: tuple | None

    def bought(self) -> dict:
        return {"skill": self.skill, "bag": self.bag, "rage": self.rage}

    def game_limit_break(self, rules: Rules) -> int:
        """The limit break the game reads from this fighter's levels."""
        return rules.limit_break_of(self.kind, self.grade, self.stats, self.bonus, self.skill, self.bag, self.rage)

    def values(self, rules: Rules) -> dict:
        """Everything the page edits, as the game shows it (the purchases as totals)."""
        values = {"name": self.name, "type": self.kind, "grade": self.grade, "body": self.body,
                  "gasmask": self.gasmask}
        values.update(self.stats)
        values.update({f"{k}_bonus": v for k, v in self.bonus.items()})
        for what in BOUGHT:
            values[TOTALS[what]] = rules.capacity(self.kind, self.grade, 0, what) + self.bought()[what]
        return values


def player_uid(save: SaveDocument) -> str:
    return save.uid


def fighters(save: SaveDocument) -> list[Fighter]:
    uid = player_uid(save)
    chrs_key = _player_key(save.get(("soul", "chr", "chrs")), uid)
    body_key = _player_key(save.get(("bodyuser",)), uid)
    roster = save.get(("soul", "chr", "chrs", chrs_key)) if chrs_key is not None else None
    bodies = save.get(("bodyuser", body_key)) if body_key is not None else None
    by_cid = {}
    for n, row in enumerate(bodies if isinstance(bodies, list) else []):
        if isinstance(row, dict):
            by_cid[str(row.get("cid", ""))] = (n, row)
    found = []
    for n, chara in enumerate(roster if isinstance(roster, list) else []):
        if not isinstance(chara, dict):
            continue
        cid = str(chara.get("cid", ""))
        where, row = by_cid.get(cid, (None, {}))
        found.append(Fighter(
            cid=cid, name=str(chara.get("name") or ""), kind=str(chara.get("type") or "BAL"),
            grade=_int(chara.get("grade"), 1), limit_break=_int(chara.get("limit_break"), 0),
            state=str(chara.get("state") or ""), body=str(chara.get("body") or ""),
            gasmask=str(chara.get("gasmask") or ""), kidnapped=bool(chara.get("abid")),
            level=_int(row.get("lvl"), 1),
            stats={k: _int(row.get(k), 1) for k in STATS},
            bonus={k: _int(row.get(f"{k}_bonus"), 0) for k in STATS},
            skill=_int(row.get("skill"), 0), bag=_int(row.get("bag"), 0), rage=_int(row.get("rage"), 0),
            chr_path=("soul", "chr", "chrs", chrs_key, n),
            body_path=("bodyuser", body_key, where) if where is not None else None,
        ))
    return found


def _player_key(section, uid: str):
    if not isinstance(section, dict):
        return None
    for key in (uid, str(uid)):
        if key in section:
            return key
    return None


def _int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def lock_reason(fighter: Fighter, save: SaveDocument) -> str:
    """Why nothing about this fighter may be changed, or ""."""
    if fighter.state == "ENEMY":
        return "dead, and roaming the Tower as a Hater"
    if fighter.state == "DUMMY":
        return "not a fighter of the freezer's own"
    if fighter.kidnapped:
        return "kidnapped"
    if fighter.state == "USE" and save.get(("soul", "pause")):
        return "out on a run right now - the save is in the middle of it"
    if fighter.body_path is None:
        return "its levels are missing from the save"
    return ""


def look_only(fighter: Fighter) -> bool:
    """A fighter on defense: only its name and look may change."""
    return fighter.state == "GUARD"


LOOK_KEYS = ("name", "body", "gasmask")


def level_of(stats: dict, skill: int, bag: int, rage: int) -> int:
    return max(1, sum(stats.get(k, 1) for k in STATS) - 5 + skill + bag + rage)


def check_name(name: str) -> str:
    name = str(name).strip()
    if not 1 <= len(name) <= NAME_MAX or any(ord(c) < 32 for c in name):
        raise EditError(f"a fighter's name must be 1 to {NAME_MAX} characters")
    return name


# -- changing one ------------------------------------------------------------------

def max_values(fighter: Fighter, rules: Rules, kind: str | None = None) -> dict:
    """The top of everything for this fighter (as ``kind``, when given): the highest grade,
    every stat at its last limit break's cap, and all that can be bought there. The bonuses
    are left as they are."""
    if look_only(fighter):
        return {}
    kind = kind or fighter.kind
    grade = rules.max_grade(kind)
    top_lb = rules.max_limit_break(kind, grade)
    values = {"type": kind, "grade": grade}
    values.update({k: rules.cap(kind, grade, top_lb) for k in STATS})
    for what in BOUGHT:
        values[TOTALS[what]] = rules.capacity(kind, grade, top_lb, what) if top_lb > 0 \
            else rules.capacity(kind, grade, 0, what)
    return values


def fit(rules: Rules, kind: str, grade: int, values: dict, before: dict, refit: bool) -> dict:
    """``values`` held to what the class and grade allow. A value that is not changed is kept
    as it is - unless ``refit`` (a new class or grade), when everything is fitted."""
    out = dict(values)

    def held(key: str, low: int, high: int) -> None:
        if refit or out[key] != before.get(key):
            out[key] = max(low, min(int(out[key]), high))

    choices = rules.bonus_choices(kind, grade)
    for k in STATS:
        key = f"{k}_bonus"
        if refit or out[key] != before.get(key):
            # only what the purchase roll can give: the highest of those at or below the one asked for
            out[key] = max(c for c in choices if c <= max(0, int(out[key])))
    bonus = {k: out[f"{k}_bonus"] for k in STATS}
    stat_top = rules.cap(kind, grade, rules.max_limit_break(kind, grade))
    for k in STATS:
        held(k, 1, max(stat_top, rules.stat_cap(kind, grade, 0, bonus[k])))
    # what can be bought follows the limit break the stats now give
    limits = rules.limits(kind, grade, {k: out[k] for k in STATS}, bonus)
    for what in BOUGHT:
        held(TOTALS[what], *limits.total_range(what))
    return out


def set_fighter(save: SaveDocument, rules: Rules, cid: str, values: dict) -> None:
    """``values`` (keys as Fighter.values gives them) into the fighter with ``cid``. The limit
    break is not one of them: it is worked out from the levels, and the save's is set to match."""
    fighter = next((f for f in fighters(save) if f.cid == cid), None)
    if fighter is None:
        raise EditError("that fighter is not in the save any more")
    before = fighter.values(rules)
    wanted = {k: v for k, v in values.items() if k in before and v != before[k]}
    if not wanted:
        return
    who = fighter.name or "A fighter"
    reason = lock_reason(fighter, save)
    if reason:
        raise EditError(f"{who} is {reason}, so it is left as it is")
    if look_only(fighter) and set(wanted) - set(LOOK_KEYS):
        raise EditError(f"{who} is on defense: take them off defense in the game to change more than the "
                        "name and look")

    def put(path_end: str, value, label: str, in_body: bool = False) -> None:
        path = (fighter.body_path if in_body else fighter.chr_path) + (path_end,)
        save.set(path, value, f"{who}: {label}")

    if "name" in wanted:
        put("name", check_name(wanted["name"]), "Name")
    body = wanted.get("body", fighter.body)
    if "body" in wanted:
        if rules.bodies and body not in {b for b, _ in rules.bodies}:
            raise EditError(f"{body} is not a fighter model in this game's data")
        put("body", body, "Model")
        for n, entry in enumerate(save.get(("fortzmbsetting",)) or []):
            if isinstance(entry, dict) and entry.get("cid") == cid and "body" in entry:
                save.set(("fortzmbsetting", n, "body"), body, f"{who}: Model on defense")
    mask = wanted.get("gasmask", fighter.gasmask)
    masks = rules.masks_for(body)
    if masks and mask not in masks:
        # a mask of the other gender does not fit: the same number of this one's, else the first
        number = re.search(r"(\d+)$", mask)
        mask = next((m for m in masks if number and m.endswith(number.group(1))), masks[0])
    if mask != fighter.gasmask:
        put("gasmask", mask, "Gas mask")
    if look_only(fighter):
        return

    kind = wanted.get("type", fighter.kind)
    if kind not in rules.classes():
        raise EditError(f"{kind} is not a fighter class in this game's data")
    grade = max(1, min(int(wanted.get("grade", fighter.grade)), rules.max_grade(kind)))
    refit = (kind, grade) != (fighter.kind, fighter.grade)
    if kind != fighter.kind:
        put("type", kind, "Class")
    if grade != fighter.grade:
        put("grade", grade, "Grade")
    got = fit(rules, kind, grade, {**before, **wanted}, before, refit)

    stats = {k: int(got[k]) for k in STATS}
    bonus = {k: int(got[f"{k}_bonus"]) for k in STATS}
    for k in STATS:
        if stats[k] != fighter.stats[k]:
            put(k, stats[k], STAT_LABELS[k], in_body=True)
        if bonus[k] != fighter.bonus[k]:
            put(f"{k}_bonus", bonus[k], f"{STAT_LABELS[k]} bonus", in_body=True)
    bought = {w: int(got[TOTALS[w]]) - rules.capacity(kind, grade, 0, w) for w in BOUGHT}
    for what, label in (("skill", "Decal slots bought"), ("bag", "Death Bag space bought"),
                        ("rage", "Rage bars bought")):
        if bought[what] != fighter.bought()[what]:
            put(what, bought[what], label, in_body=True)
    levels_changed = (stats, bonus, bought) != (fighter.stats, fighter.bonus, fighter.bought())
    if levels_changed or refit:
        lb = rules.limit_break_of(kind, grade, stats, bonus, bought["skill"], bought["bag"], bought["rage"])
        if lb != fighter.limit_break:
            put("limit_break", lb, "Limit Break")
        level = level_of(stats, bought["skill"], bought["bag"], bought["rage"])
        if level != fighter.level:
            put("lvl", level, "Level", in_body=True)


def max_all(save: SaveDocument, rules: Rules) -> tuple[list[str], list[str]]:
    """Every fighter that may be changed, to the top. Returns (maxed, "name (why not)")."""
    done, skipped = [], []
    for fighter in fighters(save):
        reason = lock_reason(fighter, save) or ("on defense" if look_only(fighter) else "")
        if reason:
            skipped.append(f"{fighter.name or '?'} ({reason})")
            continue
        set_fighter(save, rules, fighter.cid, max_values(fighter, rules))
        done.append(fighter.name or "?")
    return done, skipped
