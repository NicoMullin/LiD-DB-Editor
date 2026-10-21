"""What a save holds, in a few lines: for the save library's list and details.

Everything here only reads. The numbers come straight from the save's JSON
(see savegame.py for the container):

- money: ``soul.free_money`` + ``paid_money`` (Kill Coins), ``soul.spirit``
  (Splithium), ``soul.bloodnium_point``, ``user.free_medal`` + ``paid_medal``
  (Death Metals), ``soul.recycle_point``;
- progress: ``soul.rank``, the KC bank and SPL tank levels, and the game's own
  lifetime statistics in ``playlog`` - ``base.total_play_time`` (seconds),
  ``base.max_floor``, ``died.total_died_cnt``, ``kill.total_enemy_cnt``;
- blueprints: ``soul.partresearch.user`` has a row for each blueprint and
  level the player has found; ``research_type`` "FINISHED" is one researched;
- fighters: ``soul.chr.chrs.<uid>`` (name, class, grade, state) joined by
  ``cid`` to ``bodyuser.<uid>`` (level and the points put into each stat, plus
  the stat bonuses rolled when the fighter was bought).

The player's own entries are found by ``user.uid``, never by assuming one.
The game's database, when given, names the fighter classes and counts the
blueprints there are to find; without it the class codes stand in.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from . import savegame

STATS = ("hp", "str", "dex", "vit", "stm", "luk")
STAT_NAMES = {"hp": "HP", "str": "STR", "dex": "DEX", "vit": "VIT", "stm": "STM", "luk": "LUK"}
# The game's own names (master_text FTYPE_NAME), for when no database is to hand
CLASS_CODES = {
    "BAL": "All-rounder", "BRE": "Striker", "COL": "Collector", "DEF": "Defender",
    "LUK": "Lucky Star", "OLD": "Original", "SHT": "Shooter", "SKI": "Skill Master", "TEC": "Attacker",
}
# soul.chr.chrs[].state, as the Fighter Freezer would put it
STATES = {
    "USE": "Playing", "FREE": "In the freezer", "GUARD": "Guarding",
    "ENEMY": "Dead (a Hater)", "DEAD": "Dead", "ABDUCT": "Taken",
}


@dataclass(frozen=True)
class Fighter:
    cid: str
    name: str
    class_code: str
    class_name: str
    grade: int
    limit_break: int
    level: int
    state: str
    points: dict = field(default_factory=dict)     # stat -> points put in
    bonus: dict = field(default_factory=dict)      # stat -> bonus rolled when bought

    @property
    def state_label(self) -> str:
        return STATES.get(self.state, self.state.title() or "-")

    @property
    def title(self) -> str:
        tier = f" +{self.limit_break}" if self.limit_break else ""
        return f"{self.name} - {self.class_name} G{self.grade}{tier}, level {self.level}"

    def stat_line(self) -> str:
        parts = []
        for stat in STATS:
            text = f"{STAT_NAMES[stat]} {self.points.get(stat, 0)}"
            if self.bonus.get(stat):
                text += f"+{self.bonus[stat]}"
            parts.append(text)
        return "  ".join(parts)


@dataclass(frozen=True)
class SaveSummary:
    player: str
    uid: str
    created: int                    # Unix time the account was made, 0 if unknown
    kill_coins: int
    death_metals: int
    splithium: int
    bloodnium: int
    re_points: int
    rank: int
    bank_level: int
    tank_level: int
    play_seconds: int
    highest_floor: int
    deaths: int
    kills: int
    blueprints_found: int
    blueprints_researched: int
    blueprints_total: int | None    # what the database has to find; None without one
    fighters: tuple = ()

    @property
    def play_time(self) -> str:
        return play_time_text(self.play_seconds)

    @property
    def blueprints(self) -> str:
        text = f"{self.blueprints_found:,} found, {self.blueprints_researched:,} researched"
        if self.blueprints_total:
            text += f" (of {self.blueprints_total:,})"
        return text

    def lines(self) -> list[tuple[str, str]]:
        """Label -> value, in the order the details show them."""
        return [
            ("Player", self.player or "-"),
            ("Time played", self.play_time),
            ("Highest floor", f"{self.highest_floor}F" if self.highest_floor else "-"),
            ("Kill Coins", f"{self.kill_coins:,}"),
            ("Splithium", f"{self.splithium:,}"),
            ("Bloodnium", f"{self.bloodnium:,}"),
            ("Death Metals", f"{self.death_metals:,}"),
            ("Re points", f"{self.re_points:,}"),
            ("Player rank", str(self.rank)),
            ("KC bank / SPL tank", f"level {self.bank_level} / level {self.tank_level}"),
            ("Blueprints", self.blueprints),
            ("Fighters", str(len(self.fighters))),
            ("Deaths", f"{self.deaths:,}"),
            ("Kills", f"{self.kills:,}"),
        ]


def play_time_text(seconds: int) -> str:
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes = rest // 60
    return f"{hours:,} h {minutes:02d} min" if hours else f"{minutes} min"


def _int(value, default: int = 0) -> int:
    """A number the game may have stored as a number or as text."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _list(value) -> list:
    return value if isinstance(value, list) else []


def _by_player(section, uid: str) -> list:
    """section.<uid>, whether the game keyed it as text or a number."""
    section = _dict(section)
    return _list(section.get(uid, section.get(_int(uid, -1))))


class Names:
    """Class names and the blueprint count from a masters.db, read once."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self.classes: dict[str, str] = dict(CLASS_CODES)
        self.blueprints: int | None = None
        if db_path is None or not Path(db_path).is_file():
            return
        try:
            con = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
        except sqlite3.Error:
            return
        try:
            for ident, text in con.execute(
                    "SELECT id, txt FROM master_text WHERE sct = 'FTYPE_NAME' AND lang = 'int'"):
                if isinstance(ident, str) and ident.startswith("TXT_") and text:
                    self.classes[ident[4:]] = str(text)
            self.blueprints = con.execute(
                "SELECT COUNT(DISTINCT ptid) FROM master_part_research WHERE is_open = 1").fetchone()[0]
        except sqlite3.Error:
            pass
        finally:
            con.close()

    def class_name(self, code: str) -> str:
        return self.classes.get(code, code or "?")


def fighters(doc: dict, uid: str, names: Names) -> tuple[Fighter, ...]:
    bodies = {str(b.get("cid")): b for b in _by_player(doc.get("bodyuser"), uid) if isinstance(b, dict)}
    roster = _by_player(_dict(_dict(doc.get("soul")).get("chr")).get("chrs"), uid)
    found = []
    for chara in roster:
        if not isinstance(chara, dict):
            continue
        cid = str(chara.get("cid", ""))
        body = bodies.get(cid, {})
        code = str(chara.get("type", "") or "")
        found.append(Fighter(
            cid=cid,
            name=str(chara.get("name", "") or "(no name)"),
            class_code=code,
            class_name=names.class_name(code),
            grade=_int(chara.get("grade")),
            limit_break=_int(chara.get("limit_break")),
            level=_int(body.get("lvl")),
            state=str(chara.get("state", "") or ""),
            points={stat: _int(body.get(stat)) for stat in STATS},
            bonus={stat: _int(body.get(f"{stat}_bonus")) for stat in STATS},
        ))
    return tuple(found)


def read_summary(save: Path | str, names: Names | None = None) -> SaveSummary:
    """The summary of a .sav file (savegame.SaveError if it cannot be read)."""
    try:
        raw = Path(save).read_bytes()
    except OSError as exc:
        raise savegame.SaveError(f"could not read the save: {exc}") from None
    _, payload = savegame.read_container(raw)
    try:
        doc = json.loads(payload)
    except ValueError:
        raise savegame.SaveError("the save's contents are not readable - it may be damaged") from None
    if not isinstance(doc, dict):
        raise savegame.SaveError("the save's contents are not what the game writes")
    return summarize(doc, names)


def summarize(doc: dict, names: Names | None = None) -> SaveSummary:
    names = names or Names()
    user, soul = _dict(doc.get("user")), _dict(doc.get("soul"))
    uid = str(user.get("uid", soul.get("uid", "")))
    playlog = _dict(doc.get("playlog"))
    base, died, kill = (_dict(playlog.get(k)) for k in ("base", "died", "kill"))
    research = [r for r in _list(_dict(soul.get("partresearch")).get("user")) if isinstance(r, dict)]
    return SaveSummary(
        player=str(user.get("nm", "") or ""),
        uid=uid,
        created=_int(user.get("created")),
        kill_coins=_int(soul.get("free_money")) + _int(soul.get("paid_money")),
        death_metals=_int(user.get("free_medal")) + _int(user.get("paid_medal")),
        splithium=_int(soul.get("spirit")),
        bloodnium=_int(soul.get("bloodnium_point")),
        re_points=_int(soul.get("recycle_point")),
        rank=_int(soul.get("rank")),
        bank_level=_int(soul.get("safe_level")),
        tank_level=_int(soul.get("spirit_tank_level")),
        play_seconds=_int(base.get("total_play_time")),
        highest_floor=_int(base.get("max_floor")),
        deaths=_int(died.get("total_died_cnt")),
        kills=_int(kill.get("total_enemy_cnt")),
        blueprints_found=len({r.get("ptid") for r in research if r.get("ptid")}),
        blueprints_researched=len({r.get("ptid") for r in research
                                   if r.get("ptid") and r.get("research_type") == "FINISHED"}),
        blueprints_total=names.blueprints,
        fighters=fighters(doc, uid, names),
    )
