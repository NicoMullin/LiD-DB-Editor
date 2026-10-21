"""Quests: the quests taken now, and every quest's history.

The save keeps them in soul.quest:
- user: one entry per quest the game offers offline, {qid, ordcnt, clrcnt} -
  how many times it was taken and cleared. A quest never cleared pays its
  first-clear reward (master_quest.first_rwd), later clears the normal one, and a
  quest needing another (cond_quest_id) opens once that one is cleared.
- ord: the quests taken now, {qid, prgnow, prgmax, timer, expire, term, rwd,
  order}; the game writes it {} when there are none.
- dis, dfs, dss: per-quest extra data (left alone), and hash: the game's check
  of the quest data, which it works out again itself (left alone - editing
  quests with it unchanged is confirmed in game by oberlinx).

Resetting puts every quest back to never taken, never cleared (a quest taken now
stays taken once), so first-clear rewards come again. The game's four one-time
quests (master_quest.event_type QUESTEVENT_ONETIME: the Grade 6 fighter unlocks,
BODY_001-004) are left alone unless asked for. Resetting them is safe - the game
keeps unlocked fighters in soul.unlockfighter, apart from the quests, and adds an
unlock only when that class and grade is not there yet (BrgGame-Steam.exe,
FUN_1413ca210) - but there is nothing to gain from it. Ported with the help of
oberlinx's LET IT DIE save editor (used with permission).
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from .saveedit import EditError, SaveDocument

ONE_TIME = "QUESTEVENT_ONETIME"
COUNT_MAX = 999_999


def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@dataclass
class QuestCatalog:
    quests: dict = field(default_factory=dict)      # qid -> master_quest row
    names: dict = field(default_factory=dict)       # qid -> {no: text key} for the name
    texts: dict = field(default_factory=dict)       # "SECTION.ID" -> English text
    categories: dict = field(default_factory=dict)  # catid -> text key

    @classmethod
    def read(cls, db_path: Path | str | None) -> "QuestCatalog":
        cat = cls()
        if not db_path or not Path(db_path).is_file():
            return cat
        try:
            con = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
        except sqlite3.Error:
            return cat
        con.row_factory = sqlite3.Row
        try:
            for query, read in (
                ("SELECT * FROM master_quest", lambda r: cat.quests.__setitem__(str(r["qid"]), dict(r))),
                ("SELECT qid, no, val FROM master_quest_param_text",
                 lambda r: cat.names.setdefault(str(r["qid"]), {}).__setitem__(_int(r["no"]), str(r["val"]))),
                ("SELECT sct, id, txt FROM master_text WHERE lang = 'int'",
                 lambda r: cat.texts.__setitem__(f"{r['sct']}.{r['id']}", str(r["txt"] or ""))),
                ("SELECT catid, name FROM master_quest_category",
                 lambda r: cat.categories.__setitem__(str(r["catid"]), str(r["name"]))),
            ):
                try:
                    for row in con.execute(query):
                        read(row)
                except sqlite3.Error:
                    pass
        finally:
            con.close()
        return cat

    def _fill(self, text: str, params: dict) -> str:
        def one(m):
            key = params.get(int(m.group(1)))
            if not key:
                return m.group(0)
            if key in self.texts:
                return self.texts[key]
            bare = key.split(".TXT_")[-1]           # a text the database lacks: ENEMY.TXT_SCREAMER, NUMBER.TXT_030
            return str(int(bare)) if bare.isdigit() else bare.replace("_", " ").title()
        return re.sub(r"\s+", " ", re.sub(r"#(\d+)", one, text).replace("##", "#")).strip()

    def name(self, qid: str) -> str:
        row = self.quests.get(qid)
        text = self.texts.get(str((row or {}).get("name") or ""))
        return self._fill(text, self.names.get(qid, {})) if text else qid

    def category(self, qid: str) -> str:
        cat = str((self.quests.get(qid) or {}).get("cat") or "")
        return self.texts.get(self.categories.get(cat, ""), "") or cat.replace("QUESTCAT_", "").title()

    def one_time(self, qid: str) -> bool:
        return (self.quests.get(qid) or {}).get("event_type") == ONE_TIME


def _quest(save: SaveDocument) -> dict:
    q = save.doc["soul"].get("quest")
    if not isinstance(q, dict):
        raise EditError("this save has no quest data")
    return q


def history(save: SaveDocument) -> list[dict]:
    q = save.doc["soul"].get("quest")
    rows = q.get("user") if isinstance(q, dict) else None
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def taken(save: SaveDocument) -> list[dict]:
    q = save.doc["soul"].get("quest")
    rows = q.get("ord") if isinstance(q, dict) else None
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def counts(save: SaveDocument) -> dict[str, tuple[int, int]]:
    """qid -> (times taken, times cleared)."""
    return {str(r.get("qid")): (_int(r.get("ordcnt")), _int(r.get("clrcnt"))) for r in history(save)}


def reset_counts(save: SaveDocument, catalog: QuestCatalog, one_time: bool = False) -> dict[str, tuple[int, int]]:
    """What every quest's counts become on a reset (only those that change)."""
    held = {str(o.get("qid")) for o in taken(save)}
    wanted = {}
    for qid, (ordcnt, clrcnt) in counts(save).items():
        if catalog.one_time(qid) and not one_time:
            continue
        after = (1 if qid in held else 0, 0)
        if after != (ordcnt, clrcnt):
            wanted[qid] = after
    return wanted


def set_counts(save: SaveDocument, catalog: QuestCatalog, wanted: dict[str, tuple[int, int]],
               summary: str = "") -> None:
    """Set quests' (times taken, times cleared); a quest is never cleared more than it was taken.
    With ``summary`` the change list gets that one line instead of one per quest."""
    rows = {str(r.get("qid")): r for r in history(save)}
    changed = 0
    for qid, (ordcnt, clrcnt) in wanted.items():
        row = rows.get(qid)
        if row is None:
            continue
        clrcnt = max(0, min(COUNT_MAX, int(clrcnt)))
        ordcnt = max(clrcnt, min(COUNT_MAX, int(ordcnt)))
        before = (_int(row.get("ordcnt")), _int(row.get("clrcnt")))
        if before == (ordcnt, clrcnt):
            continue
        row["ordcnt"], row["clrcnt"] = ordcnt, clrcnt
        changed += 1
        if not summary:
            save.note(f"Quest: {catalog.name(qid)}", f"taken {before[0]}, cleared {before[1]}",
                      f"taken {ordcnt}, cleared {clrcnt}")
    if summary and changed:
        save.note("Quests", summary, f"{changed:,} quest(s) changed")


def set_progress(save: SaveDocument, catalog: QuestCatalog, progress: dict[str, int]) -> None:
    """A taken quest's progress, 0 to its goal (at the goal it is complete; the reward is
    collected in the game)."""
    for row in taken(save):
        qid = str(row.get("qid"))
        if qid not in progress:
            continue
        goal = _int(row.get("prgmax"))
        value = max(0, min(int(progress[qid]), goal if goal > 0 else int(progress[qid])))
        if value != _int(row.get("prgnow")):
            save.note(f"Quest taken: {catalog.name(qid)}", f"{_int(row.get('prgnow'))} of {goal}", f"{value} of {goal}")
            row["prgnow"] = value


def drop(save: SaveDocument, catalog: QuestCatalog, qids) -> None:
    """Drop taken quests (an empty list goes back to the game's {})."""
    qids = set(qids)
    q = _quest(save)
    rows = taken(save)
    keep = [r for r in rows if str(r.get("qid")) not in qids]
    if len(keep) == len(rows):
        return
    for r in rows:
        if str(r.get("qid")) in qids:
            save.note(f"Quest taken: {catalog.name(str(r.get('qid')))}", "taken", "dropped")
    q["ord"] = keep if keep else {}
