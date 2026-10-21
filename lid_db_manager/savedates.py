"""Dates in a save: finding the ones time-jumping broke, setting one, a new game day, and timers.

Every time the save keeps is Unix seconds. Moving the PC clock forward and back
leaves dates "in the future", and going past 2038-01-19 makes them overflow a
signed 32-bit number (or wrap round to negative); any of these can crash the
game. Fixing sets a wrong date to the reference time (this PC's clock unless a
time is given), drops login-bonus days that have not come yet, and changes a
deadline (an expiry, a box's opening time) only when it overflowed - a deadline
is meant to be in the future.

A new game day: the game starts one at its daily reset (10:00 UTC) and knows the
last from user.last_login_date (see savegame.start_new_game_day); setting that a
day back makes the next start a new day.

Timers (soul.waiting): crafting, research and facility waits, {wait_type,
part_id, part_lvl, facility_type, created, period}, period the time it ends. The
offline game finishes every wait at once, so these only run with the Timers mod
(lid-timers.dll); the game hands out a wait whose end time has passed
(BrgGame-Steam.exe 0x1413902c0 compares the clock with it), so finishing one
here sets its end time to now. Ported with the help of oberlinx's LET IT DIE
save editor (used with permission).
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from .saveedit import SaveDocument
from .savegame import DAY, game_day

INT32_MAX = 2147483647
TOLERANCE = 300                 # a save written a moment ago, or a fast clock, is not "future"
KEY = re.compile(r"(time|date|created|modified|updated|expire|opentime|^order$|whistle_limit)", re.I)
DEADLINE = re.compile(r"(expire|opentime|whistle_limit|guard_time)", re.I)
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
LOGIN_BONUS = "login_bonus[].login_date"
TIMERS_MOD = "Timers"

LABELS = {
    "user.created": "Account created",
    "user.modified": "Account last saved",
    "user.last_login_date": "Last game day",
    "user.migrated": "Save moved to offline",
    LOGIN_BONUS: "Login bonus history (one per day)",
    "soul.modified": "Save data last written",
    "soul.area_start_time": "Current run started",
    "soul.last_hub_time": "Last time in the Waiting Room",
    "soul.last_visiting_shop_time": "Last shop visit",
    "soul.last_rcv_deathbox_time": "Last Death Box received",
    "soul.last_tdm_reset_time": "Last Tokyo Death Metro weekly reset",
    "soul.abduct_guard_time": "Kidnap protection ends",
    "soul.whistle_limit": "Whistle expires",
    "soul.vip.expired_time": "Death Metal Express Pass (VIP) ends",
    "soul.vip.last_use_day": "Express Pass last used",
    "soul.mail[].created": "Mail received",
    "soul.present[].created": "Reward Box items received",
    "soul.skl.psskl[].updated": "Decals got",
    "soul.deathbox[].created": "Death Boxes made",
    "soul.deathbox[].opentime": "Death Boxes open at",
    "soul.prison.*[].expire_time": "Prison (kidnapped fighters) timers",
    "soul.prison.*[].last_collect_time": "Prison last collected",
    "soul.quest.ord[].order": "Quests taken",
    "soul.quest.ord[].expire": "Quests expire",
    "soul.relationship.revenge.*[].modified": "Revenge list",
    "soul.screenshot[].created": "Screenshots",
    "soul.unlockfighter[].created": "Fighters unlocked",
    "abduct[].created": "Kidnappings started",
    "abduct[].expire": "Kidnappings expire",
    "abduct[].modified": "Kidnappings changed",
    "item.items[].gettime": "Items got",
    "part.pts.*[].created": "Weapons and armor made",
    "part.pts.*[].modified": "Weapons and armor changed",
    "part.pts.*[].gettime": "Weapons and armor got",
    "mushroom.msrs[].gettime": "Mushrooms got",
    "beast.bsts[].gettime": "Beasts got",
    "clear_times.*": "Floor clear times",
    "diedchara.dchrarcs.*[].created": "Dead fighter archive",
    "diedchara.dchrs.*[].created": "Dead fighters",
    "diedchara.hunter.match.*[].expire": "Hunter matching expires",
    "dummy.user[].created": "Other players (stand-ins) made",
    "floor.match[].expire": "Floor matching expires",
    "floor.stamp.stamps[].created": "Floor stamps",
    "fortresult[].endtime": "Tokyo Death Metro results",
    "gameflg.cl[].modified": "Game flags (client)",
    "gameflg.sv[].modified": "Game flags (server)",
    "shp.expired_time": "Shop stock ends",
    "team[].last_war_time": "Teams: last war",
    "team[].modified": "Teams changed",
    "teamhate[].modified": "Team grudges (Tokyo Death Metro)",
    "teammember.created": "Joined a team",
    "teammember.modified": "Team membership changed",
}


def is_date(value) -> bool:
    """A Unix time (2001 to 2106), or one that already wrapped round to negative."""
    return (isinstance(value, int) and not isinstance(value, bool)
            and (10 ** 9 <= value < 2 ** 32 or -2 ** 31 <= value <= -10 ** 8))


@dataclass
class Entry:
    holder: object              # the dict or list the value is in
    key: object
    value: int


@dataclass
class Group:
    """One kind of date: every value found at the same place in the save."""
    pattern: str
    label: str
    deadline: bool
    entries: list = field(default_factory=list)


def scan(doc: dict) -> list[Group]:
    groups: dict[str, Group] = {}

    def walk(node, path: str, parent: str) -> None:
        if isinstance(node, list):
            for item in node:
                if isinstance(item, (dict, list)):
                    walk(item, path + "[]", parent)
            return
        if not isinstance(node, dict):
            return
        for key, value in node.items():
            wild = bool(re.fullmatch(r"-?\d+", str(key))) or bool(UUID.match(str(key))) or parent == "clear_times"
            here = (path + "." if path else "") + ("*" if wild else str(key))
            if isinstance(value, (dict, list)):
                walk(value, here, str(key))
                continue
            name = parent if wild else str(key)
            if KEY.search(name) and is_date(value):
                group = groups.get(here)
                if group is None:
                    group = groups[here] = Group(here, LABELS.get(here, here), bool(DEADLINE.search(name)))
                group.entries.append(Entry(node, key, value))

    walk(doc, "", "")
    return list(groups.values())


def problem(group: Group, value: int, ref: int) -> str:
    """'wrapped' (negative), 'post2038', 'future' (never for a deadline), or ''."""
    if value < 0:
        return "wrapped"
    if value > INT32_MAX:
        return "post2038"
    if not group.deadline and value > ref + TOLERANCE:
        return "future"
    return ""


def problems(groups: list[Group], ref: int) -> dict[str, int]:
    counts = {"future": 0, "post2038": 0, "wrapped": 0}
    for g in groups:
        for e in g.entries:
            kind = problem(g, e.value, ref)
            if kind:
                counts[kind] += 1
    return counts


def when(value: int) -> str:
    if value < 0:
        return f"wrapped past 2038 ({value})"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(value)) if value <= 32503680000 else str(value)


def fix(save: SaveDocument, ref: int, pattern: str = "*") -> int:
    """Set wrong dates (every kind, or one) to ``ref``; returns how many changed."""
    changed = 0
    for g in scan(save.doc):
        if pattern != "*" and g.pattern != pattern:
            continue
        if g.pattern == LOGIN_BONUS:
            days = save.doc.get("login_bonus")
            if isinstance(days, list):
                today = game_day(ref)
                keep = [d for d in days if not (isinstance(d, dict) and is_date(d.get("login_date"))
                                                and (d["login_date"] < 0 or d["login_date"] > today))]
                if len(keep) != len(days):
                    save.note(f"Dates: {g.label}", f"{len(days) - len(keep)} day(s) not come yet", "removed")
                    changed += len(days) - len(keep)
                    days[:] = keep
            continue
        day = g.pattern.endswith("last_login_date")
        fixed = 0
        for e in g.entries:
            if problem(g, e.value, ref):
                e.holder[e.key] = game_day(ref) if day else ref
                fixed += 1
        if fixed:
            save.note(f"Dates: {g.label}", f"{fixed} wrong", f"set to {when(game_day(ref) if day else ref)}")
            changed += fixed
    return changed


def set_one(save: SaveDocument, pattern: str, value: int) -> bool:
    """Set a kind of date the save keeps once (say the Express Pass end)."""
    for g in scan(save.doc):
        if g.pattern == pattern and len(g.entries) == 1:
            e = g.entries[0]
            if e.value != value:
                e.holder[e.key] = int(value)
                save.note(f"Dates: {g.label}", when(e.value), when(int(value)))
                return True
    return False


def new_game_day(save: SaveDocument, now: float | None = None) -> bool:
    """Make the game's next start a new game day (user.last_login_date a day back)."""
    user = save.doc.get("user")
    if not isinstance(user, dict) or not is_date(user.get("last_login_date")):
        return False
    yesterday = game_day(time.time() if now is None else now) - DAY
    if user["last_login_date"] <= yesterday:
        return False
    save.note("Dates: the next start", "the same game day", "a new game day")
    user["last_login_date"] = yesterday
    return True


# -- timers ---------------------------------------------------------------------------------

FACILITIES = {"SAFE": "Bank", "TANK": "SPLithium Tank", "PRISON": "Prison", "FREEZER": "Freezer"}


def timers(save: SaveDocument) -> list[dict]:
    rows = save.doc["soul"].get("waiting")
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def timer_key(row: dict) -> tuple:
    return (row.get("wait_type"), row.get("part_id"), row.get("part_lvl"), row.get("facility_type"))


def timer_label(row: dict, catalog=None) -> str:
    kind, part = str(row.get("wait_type") or ""), str(row.get("part_id") or "")
    if kind == "RESEARCHFACILITY":
        facility = str(row.get("facility_type") or "")
        return f"Upgrade: {FACILITIES.get(facility, facility.title())}"
    name, level = part, ""
    if catalog is not None and part in catalog.parts:
        name = catalog.part_name(part)
        try:
            level = f" +{catalog.shown_level(part, int(row.get('part_lvl') or 1))}"
        except (TypeError, ValueError):
            level = ""
    return {"RESEARCHPART": "Research", "BUYPART": "Crafting", "BUYFLOORSHOP": "Floor shop"}.get(kind, kind) \
        + f": {name}{level}"


def finish_timers(save: SaveDocument, keys, now: float | None = None, catalog=None) -> int:
    """End the chosen timers now (their end time to now); the game hands them out next time."""
    now = int(time.time() if now is None else now)
    keys = set(keys)
    done = 0
    for row in timers(save):
        if timer_key(row) in keys and isinstance(row.get("period"), int) and row["period"] > now:
            save.note(f"Timer: {timer_label(row, catalog)}", f"ends {when(row['period'])}", "finished now")
            row["period"] = now
            done += 1
    return done


def timers_shown(mods: list, save: SaveDocument, now: float | None = None) -> bool:
    """Whether to show the timers: the save is played with the Timers mod, or its mods are not
    known and it holds a timer still running (only the Timers mod leaves one running)."""
    if TIMERS_MOD in (mods or []):
        return True
    if mods:
        return False
    now = time.time() if now is None else now
    return any(isinstance(r.get("period"), int) and r["period"] > now for r in timers(save))
