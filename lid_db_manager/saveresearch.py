"""Research (Choku-Funsha's R&D): which weapons and armor are developed and how far, which
blueprints are known, and the Funshots research gives.

The save keeps it in soul.partresearch.user, one entry per part and level:
{ptid, lvl, research_type, receive_type, is_announced, is_checked, before_ptid,
before_lvl}. Read from the game's offline server (BrgGame-Steam.exe, in Ghidra):

- A developed level is research_type FINISHED. Finishing one turns the waiting
  entry itself into FINISHED with receive_type CHARGE (the level the shop sells),
  so a part's levels run 1 to its top, all FINISHED, the top one CHARGE.
- What can be researched next is a marker. A part under its top level has a
  LEVELUP entry at the next level, pointing back at itself (before_ptid, before_lvl
  = the part and its level now). A part at its top opens the next part of its
  chain (master_part.nextptid - the next tier, or the uncapped form of a +4 part)
  with a REMODEL entry at level 1 pointing back at the finished part and its top.
- A blueprint the player has analysed but not developed is a MAP entry at level 1,
  one picked up but not analysed is research_type "" (both before "", 0).
- The two starting weapons (master_part_research.is_initial) come at level 1, and
  their level-2 marker points at nothing ("", 0); they cannot be taken away.
- A level is no higher than the part's limit (see savestorage.Catalog.max_level).

Funshots (soul.researchstamp, [{type, rate}]): each part at level 1 FINISHED and
announced adds master_const_float PART_RESEARCH_STAMP_ADD_RATE_<type> once, by its
type - a weapon by its attack (master_part_atkattr SLASH/SHOOT/HIT), armor by its
place - when master_part.is_stamp. The game adds them as it announces finished
research, and adds the whole lot once, the first time Choku-Funsha opens
(gameflg SGF_INIT_RESEARCH_STAMP). So research written here is written announced,
and the Funshots move by what a change adds or takes away; on a save that has not
had that first count yet they are set to the full total and the count marked done,
or the game would count this research a second time (seen in game by oberlinx).

Armor researched to +4 can be worn as an Armor Skin (soul.armorskin); a skin whose
research is taken below +4 is taken off.

Only parts in master_part_research with is_open are researched here, and only
the chains a change touches are rewritten - everything else is left as the game
wrote it. Ported with the help of oberlinx's LET IT DIE save editor (used with
permission).
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

from .saveedit import EditError, SaveDocument
from .savestorage import Catalog, _int

FINISHED, LEVELUP, REMODEL, MAP, UNREVEALED = "FINISHED", "LEVELUP", "REMODEL", "MAP", ""
MARKERS = (LEVELUP, REMODEL)
STAMP_TYPES = ("SLASH", "SHOOT", "HIT", "BODY", "HEAD", "LEGS")      # the order the game writes them
STAMP_LABELS = {"SLASH": "Slash", "SHOOT": "Shoot", "HIT": "Hit", "BODY": "Body", "HEAD": "Head", "LEGS": "Legs"}
STAMP_FLAG = "SGF_INIT_RESEARCH_STAMP"
SLOTS = {"PTTP_ARM": "Weapon", "PTTP_HEAD": "Head", "PTTP_BODY": "Body", "PTTP_LEGS": "Legs"}
SKIN_LEVEL = 4                  # armor researched to +4 can be an Armor Skin
NOT_RESEARCHED = None


# -- what the game has ----------------------------------------------------------------------

@dataclass(frozen=True)
class Row:
    """One line of the research list: a part, with its uncapped form folded in."""
    ptid: str
    name: str
    slot: str
    offered: bool               # the bulk buttons may research it
    platform: int = 0


@dataclass
class ResearchRules:
    catalog: Catalog
    research: dict = field(default_factory=dict)    # ptid -> (is_initial, is_open)
    attack: dict = field(default_factory=dict)      # ptid -> SLASH / SHOOT / HIT
    rates: dict = field(default_factory=dict)       # stamp type -> rate

    @classmethod
    def read(cls, db_path: Path | str | None, catalog: Catalog | None = None) -> "ResearchRules":
        rules = cls(catalog if catalog is not None else Catalog.read(db_path))
        if not db_path or not Path(db_path).is_file():
            return rules
        try:
            con = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
        except sqlite3.Error:
            return rules
        try:
            for query, read in (
                ("SELECT ptid, is_initial, is_open FROM master_part_research",
                 lambda r: rules.research.__setitem__(str(r[0]), (bool(_int(r[1])), bool(_int(r[2]))))),
                ("SELECT id, attr FROM master_part_atkattr WHERE attr IN ('ATKATTR_SLASH', 'ATKATTR_SHOOT', "
                 "'ATKATTR_HIT') ORDER BY rowid",
                 lambda r: rules.attack.setdefault(str(r[0]), str(r[1])[len("ATKATTR_"):])),
                ("SELECT id, value FROM master_const_float WHERE id LIKE 'PART_RESEARCH_STAMP_ADD_RATE_%'",
                 lambda r: rules.rates.__setitem__(str(r[0])[len("PART_RESEARCH_STAMP_ADD_RATE_"):], float(r[1]))),
            ):
                try:
                    for row in con.execute(query):
                        read(row)
                except (sqlite3.Error, TypeError, ValueError):
                    pass
        finally:
            con.close()
        return rules

    # -- parts --

    def researchable(self, ptid: str) -> bool:
        return ptid in self.catalog.parts and self.research.get(ptid, (False, False))[1]

    def initial(self, ptid: str) -> bool:
        return self.research.get(ptid, (False, False))[0]

    def max_level(self, ptid: str) -> int:
        return self.catalog.max_level(ptid)

    def previous(self, ptid: str) -> str:
        return self.catalog.previous.get(ptid, "")

    def next(self, ptid: str) -> str:
        after = str((self.catalog.parts.get(ptid) or {}).get("nextptid") or "")
        return after if after in self.catalog.parts else ""

    def chain(self, ptid: str) -> list[str]:
        """The upgrade chain a part is in, first part first."""
        root, seen = ptid, {ptid}
        while self.previous(root) and self.previous(root) not in seen:
            root = self.previous(root)
            seen.add(root)
        chain, seen = [root], {root}
        while self.next(chain[-1]) and self.next(chain[-1]) not in seen:
            chain.append(self.next(chain[-1]))
            seen.add(chain[-1])
        return chain

    def uncapped_form(self, ptid: str) -> str:
        """The uncapped form shown on the part's row - when it can be researched."""
        after = self.catalog.uncapped_form(ptid)
        return after if after and self.researchable(after) else ""

    def level_range(self, ptid: str) -> tuple[int, int]:
        """The lowest and highest "+N" a row's research can be at."""
        low, high = self.catalog._own_range(ptid)
        after = self.uncapped_form(ptid)
        return (low, self.catalog._own_range(after)[1]) if after else (low, high)

    def name(self, ptid: str) -> str:
        return self.catalog.part_name(ptid)

    def offered(self, ptid: str) -> bool:
        """A part the bulk buttons research: open for research, one the game hands out, named,
        and not PlayStation-only in this save's game data (the Crossover pack makes those PC
        parts; without such a mod they do not work on PC)."""
        row = self.catalog.parts.get(ptid) or {}
        return (self.researchable(ptid) and self.catalog.obtainable(ptid) and bool(self.catalog._text(row.get("name")))
                and _int(row.get("platform")) != 1 and row.get("type") in SLOTS)

    def row_of(self, ptid: str) -> str:
        """The part whose row shows this one (an uncapped form is shown on its capped part's)."""
        return self.previous(ptid) if self.catalog._folded(ptid) and self.researchable(ptid) else ptid

    def rows(self, save: SaveDocument | None = None) -> list[Row]:
        """Every part the list shows - what can be researched here, and anything this save
        has research for - weapons first, each chain together and in order."""
        held = {self.row_of(p) for p in tops(save)} if save is not None else set()
        listed = {p for p in self.catalog.parts if not self.catalog._folded(p) or not self.researchable(p)}
        wanted = [p for p in listed if (self.offered(p) or p in held) and self.catalog.parts[p].get("type") in SLOTS]
        order = list(SLOTS)
        roots = {p: self.chain(p) for p in wanted}

        def key(p: str):
            chain = roots[p]
            return (order.index(self.catalog.parts[p]["type"]), self.name(chain[0]).lower(), chain[0], chain.index(p))

        return [Row(p, self.name(p), SLOTS[self.catalog.parts[p]["type"]], self.offered(p),
                    _int(self.catalog.parts[p].get("platform"))) for p in sorted(wanted, key=key)]

    # -- Funshots and skins --

    def stamp_type(self, ptid: str) -> str:
        row = self.catalog.parts.get(ptid) or {}
        if not _int(row.get("is_stamp")):
            return ""
        kind = row.get("type")
        if kind == "PTTP_ARM":
            return self.attack.get(ptid, "")
        return {"PTTP_HEAD": "HEAD", "PTTP_BODY": "BODY", "PTTP_LEGS": "LEGS"}.get(kind, "")

    def skin(self, ptid: str) -> bool:
        row = self.catalog.parts.get(ptid) or {}
        return _int(row.get("skin")) == 1 and row.get("type") in ("PTTP_HEAD", "PTTP_BODY", "PTTP_LEGS")

    # -- "+N" for a row --

    def shown(self, ptid: str, levels: dict) -> int | None:
        """The "+N" a row's research is at (None: not researched)."""
        after = self.uncapped_form(ptid)
        if after and levels.get(after, 0) > 0:
            return self.catalog.shown_level(after, levels[after])
        if levels.get(ptid, 0) > 0:
            return self.catalog.shown_level(ptid, levels[ptid])
        return NOT_RESEARCHED

    def targets(self, ptid: str, shown: int | None) -> list[tuple[str, int]]:
        """The parts and saved levels that put a row at "+N" (or not researched)."""
        after = self.uncapped_form(ptid)
        if shown is NOT_RESEARCHED and self.initial(ptid):
            shown = self.level_range(ptid)[0]
        if shown is NOT_RESEARCHED:
            return [(ptid, 0)] + ([(after, 0)] if after else [])
        low, high = self.level_range(ptid)
        shown = max(low, min(int(shown), high))
        own_high = self.catalog._own_range(ptid)[1]
        if after and shown > own_high:
            return [(ptid, self.max_level(ptid)), (after, self.catalog.raw_level(after, shown))]
        return [(ptid, self.catalog.raw_level(ptid, shown))] + ([(after, 0)] if after else [])


# -- what the save has ----------------------------------------------------------------------

def _entries(save: SaveDocument, make: bool = False) -> list | None:
    soul = save.doc["soul"]
    holder = soul.get("partresearch")
    if not isinstance(holder, dict):
        if not make:
            return None
        if holder not in (None, [], {}):
            raise EditError("this save's research is not in a form the editor knows")
        holder = soul["partresearch"] = {}
    if not isinstance(holder.get("user"), list):
        if not make:
            return None
        holder["user"] = []
    return holder["user"]


def tops(save: SaveDocument | None) -> dict[str, int]:
    """Each researched part's top finished level."""
    found: dict[str, int] = {}
    for e in (_entries(save) or []) if save is not None else []:
        if isinstance(e, dict) and e.get("research_type") == FINISHED:
            ptid = str(e.get("ptid"))
            found[ptid] = max(found.get(ptid, 0), _int(e.get("lvl")))
    return found


def known(save: SaveDocument) -> dict[str, str]:
    """Blueprints known but not developed: ptid -> MAP (analysed) or "" (not analysed yet)."""
    finished = tops(save)
    found: dict[str, str] = {}
    for e in _entries(save) or []:
        if not isinstance(e, dict) or str(e.get("ptid")) in finished:
            continue
        kind = e.get("research_type")
        if kind == MAP or (kind == UNREVEALED and str(e.get("ptid")) not in found):
            found[str(e.get("ptid"))] = kind
    return found


def remodel_ready(save: SaveDocument) -> set[str]:
    """Parts the R&D offers to remodel into (the part before them is at its top)."""
    return {str(e.get("ptid")) for e in _entries(save) or [] if isinstance(e, dict) and e.get("research_type") == REMODEL}


def resolve(save: SaveDocument, rules: ResearchRules, wanted: dict) -> dict[str, int]:
    """The saved level each part ends at, for the parts that change, when rows are set to "+N"
    (``wanted``: row ptid -> "+N" or None, applied in order). A part researched needs every part
    before it in its chain at its top; a part below its top has nothing after it researched."""
    now = tops(save)
    work = dict(now)
    for ptid, shown in wanted.items():
        for part, raw in rules.targets(ptid, shown):
            _set_one(work, rules, part, raw)
    return {p: work.get(p, 0) for p in sorted(set(work) | set(now)) if work.get(p, 0) != now.get(p, 0)}


def _set_one(work: dict, rules: ResearchRules, ptid: str, raw: int) -> None:
    if not rules.researchable(ptid):
        return
    raw = max(1 if rules.initial(ptid) else 0, min(int(raw), rules.max_level(ptid)))
    work[ptid] = raw
    chain = rules.chain(ptid)
    at = chain.index(ptid)
    if raw > 0:
        for earlier in chain[:at]:
            if rules.researchable(earlier):
                work[earlier] = rules.max_level(earlier)
    if raw < rules.max_level(ptid):
        for later in chain[at + 1:]:
            if rules.researchable(later):
                work[later] = 1 if rules.initial(later) else 0


def stamps(save: SaveDocument) -> dict[str, float]:
    rows = save.doc["soul"].get("researchstamp")
    return {str(r.get("type")): float(r.get("rate") or 0) for r in rows if isinstance(r, dict)} \
        if isinstance(rows, list) else {}


def _first_levels(entries: list) -> set[str]:
    return {str(e.get("ptid")) for e in entries if isinstance(e, dict) and _int(e.get("lvl")) == 1
            and e.get("research_type") == FINISHED and _int(e.get("is_announced"))}


def _totals(rules: ResearchRules, ptids) -> dict[str, float]:
    totals = dict.fromkeys(STAMP_TYPES, 0.0)
    for ptid in ptids:
        kind = rules.stamp_type(ptid)
        if kind in totals:
            totals[kind] += rules.rates.get(kind, 0.0)
    return totals


def stamp_flag(save: SaveDocument) -> bool:
    """Whether the game has made its one-time Funshot count on this save."""
    flags = save.doc.get("gameflg")
    lists = [v for v in flags.values() if isinstance(v, list)] if isinstance(flags, dict) else []
    return any(isinstance(f, dict) and f.get("var") == STAMP_FLAG and _int(f.get("value")) == 1
               for lst in lists for f in lst)


def stamps_after(save: SaveDocument, rules: ResearchRules, changes: dict[str, int]) -> dict[str, float]:
    """The Funshots once ``changes`` (from resolve) are written."""
    before, was = _first_levels(_entries(save) or []), tops(save)
    # a part newly researched gets its level 1 written announced; one already researched keeps its own
    after = {p for p in before if changes.get(p, 1) > 0} | {p for p, raw in changes.items()
                                                            if raw > 0 and not was.get(p)}
    have = stamps(save)
    if before == after:
        return {k: have.get(k, 0.0) for k in STAMP_TYPES}
    if not stamp_flag(save):
        return {k: round(v, 1) for k, v in _totals(rules, after).items()}
    gained, lost = _totals(rules, after - before), _totals(rules, before - after)
    return {k: max(0.0, round(have.get(k, 0.0) + gained[k] - lost[k], 1)) for k in STAMP_TYPES}


# -- writing --------------------------------------------------------------------------------

def _entry(ptid: str, lvl: int, kind: str, receive: str, announced: int, checked: int,
           before_ptid: str, before_lvl: int) -> dict:
    return {"ptid": ptid, "lvl": lvl, "research_type": kind, "receive_type": receive, "is_announced": announced,
            "is_checked": checked, "before_ptid": before_ptid, "before_lvl": before_lvl}


def _ladder(entries: list, rules: ResearchRules, ptid: str, top: int, mine: list) -> None:
    """A part's entries (``mine``) to levels 1..top finished (none at 0), changing what is there in
    place the way the game turns a waiting entry into a finished one."""
    keep: dict[int, dict] = {}
    for lvl in range(1, top + 1):
        done = next((e for e in mine if _int(e.get("lvl")) == lvl and e.get("research_type") == FINISHED), None)
        if done is None:
            done = next((e for e in mine if _int(e.get("lvl")) == lvl and e.get("research_type") in
                         (LEVELUP, REMODEL, MAP, UNREVEALED)), None)
        receive = "CHARGE" if lvl == top else FINISHED
        if done is None:
            before = _before(rules, ptid, lvl)
            done = _entry(ptid, lvl, FINISHED, receive, 1, 1, *before)
            entries.append(done)
        elif done.get("research_type") != FINISHED:
            done.update(research_type=FINISHED, receive_type=receive, is_announced=1, is_checked=1)
        elif done.get("receive_type") != receive:
            done["receive_type"] = receive
        keep[lvl] = done
    held = set(map(id, keep.values()))
    drop = {id(e) for e in mine if id(e) not in held
            and not (top == 0 and e.get("research_type") in (MAP, UNREVEALED))}   # a blueprint known stays known
    if drop:
        entries[:] = [e for e in entries if id(e) not in drop]


def _before(rules: ResearchRules, ptid: str, lvl: int) -> tuple[str, int]:
    """What a new level points back at, as the game writes it."""
    if lvl == 1:
        prev = rules.previous(ptid)
        return (prev, rules.max_level(prev)) if prev and rules.researchable(prev) else ("", 0)
    if lvl == 2 and rules.initial(ptid):
        return "", 0
    return ptid, lvl - 1


def _marker(entries: list, rules: ResearchRules, ptid: str, levels: dict, mine: list) -> None:
    """Put the part's "what next" marker right: LEVELUP under its top, REMODEL when the part
    before it is at its top, none otherwise. A chain's first part not researched is left as is."""
    if not rules.researchable(ptid):
        return
    top, most = levels.get(ptid, 0), rules.max_level(ptid)
    want = None
    if 0 < top < most:
        before = ("", 0) if rules.initial(ptid) and top == 1 else (ptid, top)
        want = _entry(ptid, top + 1, LEVELUP, "UNKNOWN", 0, 1, *before)
    elif top == 0:
        prev = rules.previous(ptid)
        if not prev:
            return
        if rules.researchable(prev) and levels.get(prev, 0) >= rules.max_level(prev):
            want = _entry(ptid, 1, REMODEL, "UNKNOWN", 0, 1, prev, rules.max_level(prev))
    have = [e for e in mine if e.get("research_type") in MARKERS]
    if want is not None and len(have) == 1 and have[0].get("research_type") == want["research_type"] \
            and _int(have[0].get("lvl")) == want["lvl"]:
        return
    if want is None and not have:
        return
    if have:
        gone = set(map(id, have))
        entries[:] = [e for e in entries if id(e) not in gone]
    if want is not None:
        entries.append(want)


def blueprint_wanted(save: SaveDocument, rules: ResearchRules, ptid: str) -> bool:
    """Whether giving this part's blueprint changes anything: the first part of a chain, open for
    research, with no research and no analysed blueprint yet."""
    return (rules.researchable(ptid) and not rules.previous(ptid) and tops(save).get(ptid, 0) == 0
            and known(save).get(ptid) != MAP)


def set_research(save: SaveDocument, rules: ResearchRules, wanted: dict, blueprints=(), now: int | None = None) -> None:
    """Set rows to "+N" (``wanted``: row ptid -> "+N" or None for not researched, in order) and
    give blueprints (analysed, ready to develop) for ``blueprints``."""
    changes = resolve(save, rules, wanted)
    gifts = [p for p in dict.fromkeys(blueprints) if blueprint_wanted(save, rules, p) and changes.get(p, 0) == 0]
    if not changes and not gifts:
        return
    now = int(now if now is not None else time.time())
    entries = _entries(save, make=True)
    was = tops(save)
    first_before = _first_levels(entries)
    stamps_before, flagged = stamps(save), stamp_flag(save)
    stamps_new = stamps_after(save, rules, changes)

    by_part: dict[str, list] = {}
    for e in entries:
        if isinstance(e, dict):
            by_part.setdefault(str(e.get("ptid")), []).append(e)
    for ptid, top in changes.items():
        _ladder(entries, rules, ptid, top, by_part.get(ptid, []))
    levels = tops(save)
    touched = set(changes) | {rules.next(p) for p in changes} - {""}
    by_part = {}
    for e in entries:
        if isinstance(e, dict) and e.get("ptid") in touched:
            by_part.setdefault(str(e.get("ptid")), []).append(e)
    for ptid in sorted(touched):
        _marker(entries, rules, ptid, levels, by_part.get(ptid, []))

    for ptid in gifts:
        waiting = next((e for e in entries if isinstance(e, dict) and e.get("ptid") == ptid and _int(e.get("lvl")) == 1
                        and e.get("research_type") == UNREVEALED), None)
        if waiting is not None:
            waiting["research_type"] = MAP
            save.note(f"Blueprint: {rules.name(ptid)}", "not analysed", "known")
        else:
            entries.append(_entry(ptid, 1, MAP, "UNKNOWN", 0, 0, "", 0))
            save.note(f"Blueprint: {rules.name(ptid)}", "unknown", "known")

    for row in dict.fromkeys(rules.row_of(p) for p in changes):
        before, after = rules.shown(row, was), rules.shown(row, levels)
        if before != after:
            save.note(f"Research: {rules.name(row)}", _shown_text(before), _shown_text(after))

    if _first_levels(entries) != first_before:
        _write_stamps(save, stamps_before, stamps_new, flagged, now)
    _check_skins(save, rules, was, levels)


def _shown_text(shown: int | None) -> str:
    return "not researched" if shown is NOT_RESEARCHED else f"+{shown}"


def _write_stamps(save: SaveDocument, before: dict, after: dict, flagged: bool, now: int) -> None:
    soul = save.doc["soul"]
    rows = soul.get("researchstamp")
    if not isinstance(rows, list):
        rows = soul["researchstamp"] = []
    for kind in STAMP_TYPES:
        value = float(after.get(kind, 0.0))
        row = next((r for r in rows if isinstance(r, dict) and r.get("type") == kind), None)
        if row is None:
            rows.append({"type": kind, "rate": value})
        elif abs(float(row.get("rate") or 0) - value) > 1e-9:
            row["rate"] = value
        else:
            continue
        save.note(f"Funshots: {STAMP_LABELS[kind]}", f"{before.get(kind, 0.0):.1f}", f"{value:.1f}")
    if not flagged:
        flags = save.doc.get("gameflg")
        if not isinstance(flags, dict):
            flags = save.doc["gameflg"] = {}
        if not isinstance(flags.get("sv"), list):
            flags["sv"] = []
        old = next((f for f in flags["sv"] if isinstance(f, dict) and f.get("var") == STAMP_FLAG), None)
        if old is not None:
            old.update(value=1, modified=now)
        else:
            flags["sv"].append({"var": STAMP_FLAG, "value": 1, "modified": now})
        save.note("Funshots: the game's first count", "not made yet", "marked done (the total is set here)")


def skin_unlocked(rules: ResearchRules, ptid: str, levels: dict) -> bool:
    shown = rules.shown(ptid, levels)
    return rules.skin(ptid) and shown is not NOT_RESEARCHED and shown >= SKIN_LEVEL


def _check_skins(save: SaveDocument, rules: ResearchRules, before: dict, after: dict) -> None:
    worn = save.doc["soul"].get("armorskin")
    if not isinstance(worn, list):
        return
    for skin in list(worn):
        ptid = str((skin or {}).get("ptid") or "") if isinstance(skin, dict) else ""
        if ptid and skin_unlocked(rules, ptid, before) and not skin_unlocked(rules, ptid, after):
            worn.remove(skin)
            save.note(f"Armor Skin: {rules.name(ptid)}", "worn", f"taken off (research below +{SKIN_LEVEL})")


def skins_lost(save: SaveDocument, rules: ResearchRules, changes: dict[str, int]) -> list[str]:
    """Armor Skins worn now that ``changes`` would take off."""
    worn = save.doc["soul"].get("armorskin")
    before = tops(save)
    after = {**before, **changes}
    return [rules.name(str(s.get("ptid"))) for s in worn or [] if isinstance(s, dict)
            and skin_unlocked(rules, str(s.get("ptid")), before)
            and not skin_unlocked(rules, str(s.get("ptid")), after)] if isinstance(worn, list) else []
