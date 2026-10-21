"""Which mods a save shows signs of, for a save whose mod list nobody noted.

Only mods that leave something in a save matter to its editor - a mod that
changes prices, drop rates or the HUD changes nothing the save holds, so not
knowing about it changes nothing the editor does. What a mod can leave:

- things the clean game does not have. A save names its parts, decals, items,
  quests and so on by their id in masters.db; an id the clean database has no
  row for came from a mod. The game lists every quest it has offered in the
  save, so one session with a mod that adds quests is enough to show. The mod
  that adds an id is found by its own files naming it.
- more than the clean game holds: Kill Coins past what the bank holds at its
  level, Splithium past the tank, Bloodnium past its most - a mod that raises
  the limit (or another save editor) put it there.

A save can still have been played with a mod that left no sign yet (one that
adds weapons none of which were picked up, say). Editing it as found is safe
all the same: a value the clean game holds, every game holds - mods only ever
raise the limits.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from .saveedit import Limits, SaveDocument

# What a save's field names, and the table (and column) of masters.db it is a row of.
ID_FIELDS: dict[str, tuple[str, str, str]] = {
    "ptid": ("master_part", "id", "parts"),
    "before_ptid": ("master_part", "id", "parts"),
    "part_id": ("master_part", "id", "parts"),
    "sklid": ("master_skill", "id", "decals"),
    "sklids": ("master_skill", "id", "decals"),
    "itemid": ("master_item", "itemid", "items"),
    "qid": ("master_quest", "qid", "quests"),
    "cstmid": ("master_hubcustomize", "id", "hub looks"),
    "msrid": ("master_mushroom", "id", "mushrooms"),
    "bstid": ("master_beast", "id", "beasts"),
    "body": ("master_body", "id", "bodies"),
    "gasmask": ("master_gasmask", "id", "gas masks"),
}
# Sections that key a dict by quest id rather than naming it in a field.
QUEST_KEYED = (("soul", "quest", "dis"), ("soul", "quest", "dfs"), ("soul", "quest", "dss"))
TEXT_SUFFIXES = (".sql", ".json")
# What a game id looks like - a field of the same name holding anything else (the
# play log keeps a "body" of its own, as text) is not one.
ID_SHAPE = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Za-z0-9]+)+$")


@dataclass
class Sign:
    """One sign of a mod: what was found, and the mods that would explain it."""
    what: str                                   # in words: "14 quests the clean game does not have"
    mods: list[str] = field(default_factory=list)   # installed mods that explain it, if any
    examples: list[str] = field(default_factory=list)


@dataclass
class SaveCheck:
    signs: list[Sign] = field(default_factory=list)

    @property
    def found_mods(self) -> list[str]:
        """Every installed mod a sign points at, first seen first."""
        return list(dict.fromkeys(m for sign in self.signs for m in sign.mods))

    @property
    def unexplained(self) -> list[Sign]:
        """Signs no mod installed here explains: a mod this PC does not have."""
        return [sign for sign in self.signs if not sign.mods]

    @property
    def clean(self) -> bool:
        return not self.signs

    def lines(self) -> list[str]:
        out = []
        for sign in self.signs:
            why = f" - from {', '.join(sign.mods)}" if sign.mods else " - from a mod not installed here"
            eg = f" (such as {', '.join(sign.examples[:3])})" if sign.examples else ""
            out.append(f"{sign.what}{eg}{why}")
        return out


def save_ids(doc: dict) -> dict[tuple[str, str], set[str]]:
    """(table, column) -> every id the save names in it."""
    found: dict[tuple[str, str], set[str]] = {}

    def walk(node, key: str) -> None:
        if isinstance(node, dict):
            for k, value in node.items():
                walk(value, str(k))
        elif isinstance(node, list):
            for item in node:
                walk(item, key)
        elif isinstance(node, str) and key in ID_FIELDS and ID_SHAPE.match(node):
            table, column, _ = ID_FIELDS[key]
            found.setdefault((table, column), set()).add(node)
    walk(doc, "")
    for path in QUEST_KEYED:
        node = doc
        for key in path:
            node = node.get(key) if isinstance(node, dict) else None
        if isinstance(node, dict):
            found.setdefault(("master_quest", "qid"), set()).update(str(k) for k in node if ID_SHAPE.match(str(k)))
    return found


def _known(clean_db: Path, table: str, column: str, ids: set[str]) -> set[str]:
    con = sqlite3.connect(f"file:{Path(clean_db).as_posix()}?mode=ro", uri=True)
    try:
        return {row[0] for row in con.execute(f'SELECT "{column}" FROM "{table}"')}
    except sqlite3.Error:
        return set(ids)                 # a table this build has not got: nothing to say about it
    finally:
        con.close()


def _mod_text(mod) -> str:
    parts = []
    for path in sorted(Path(mod.folder).rglob("*")):
        if path.suffix.lower() in TEXT_SUFFIXES and path.is_file():
            try:
                parts.append(path.read_text(encoding="utf-8", errors="ignore"))
            except OSError:
                pass
    return "\n".join(parts)


def check(save: SaveDocument, clean_db: Path, mods: list) -> SaveCheck:
    """``mods``: the mods installed here (scanned), any of which may explain a sign."""
    result = SaveCheck()
    texts: dict[str, str] = {}

    def text_of(mod) -> str:
        if mod.id not in texts:
            texts[mod.id] = _mod_text(mod)
        return texts[mod.id]

    labels = {(t, c): label for t, c, label in ID_FIELDS.values()}
    for (table, column), ids in sorted(save_ids(save.doc).items()):
        new = sorted(ids - _known(clean_db, table, column, ids))
        if not new:
            continue
        explaining = [mod.id for mod in mods if table in mod.tables() and any(i in text_of(mod) for i in new)]
        result.signs.append(Sign(f"{len(new)} {labels[(table, column)]} the clean game does not have",
                                 explaining, new[:3]))

    stock = Limits.read(clean_db)
    soul = ("soul",)
    bank_level, kc = save.number(soul + ("safe_level",), 1), save.number(soul + ("free_money",))
    tank_level, spl = save.number(soul + ("spirit_tank_level",), 1), save.number(soul + ("spirit",))
    bloodnium = save.number(soul + ("bloodnium_point",))
    for over, table, what in (
        (stock.bank and bank_level > stock.top_level(stock.bank), "master_safe_level",
         f"a Kill Coin Bank at level {bank_level}, past the clean game's {stock.top_level(stock.bank)}"),
        (stock.bank and kc > stock.cap(stock.bank, bank_level), "master_safe_level",
         f"{kc:,} Kill Coins, more than the clean game's bank holds at level {bank_level}"),
        (stock.tank and tank_level > stock.top_level(stock.tank), "master_spirit_tank_level",
         f"an SPL tank at level {tank_level}, past the clean game's {stock.top_level(stock.tank)}"),
        (stock.tank and spl > stock.cap(stock.tank, tank_level), "master_spirit_tank_level",
         f"{spl:,} Splithium, more than the clean game's tank holds at level {tank_level}"),
    ):
        if over:
            result.signs.append(Sign(what, [m.id for m in mods if table in m.tables()]))
    # Fighters past the clean game's grades and limit breaks (a G7+ fighter mod). Stats a point or
    # two over their cap are not counted: real saves from the stock game have them.
    from .savefighters import Rules, fighters
    rules = Rules.read(clean_db)
    if rules.details:
        beyond = []
        for f in fighters(save):
            if f.kind not in rules.classes() and f.kind != "OLD":
                beyond.append(f"{f.name or '?'} (class {f.kind})")
            elif f.grade > rules.max_grade(f.kind) or f.limit_break > rules.max_limit_break(f.kind, f.grade):
                beyond.append(f"{f.name or '?'} (G{f.grade}, limit break {f.limit_break})")
        if beyond:
            fighter_tables = ("master_body_detail", "master_bodylvl_status_value", "master_bodylvl_exp")
            result.signs.append(Sign(f"{len(beyond)} fighter(s) past the clean game's grades and limit breaks",
                                     [m.id for m in mods if set(fighter_tables) & set(m.tables())], beyond[:3]))
    if bloodnium > stock.bloodnium:
        result.signs.append(Sign(f"{bloodnium:,} Bloodnium, more than the clean game's {stock.bloodnium:,}",
                                 [m.id for m in mods if "BLOODNIUM_POINT_MAX" in text_of(m)]))
    return result
