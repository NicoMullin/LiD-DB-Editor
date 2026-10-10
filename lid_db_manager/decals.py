"""The decals in a masters.db, as the decal picker lists them.

A decal is a row of master_skill whose id ends in ``_P``: those are the ones a
stew gives and a player owns. The rows without it are the effects behind them
(SKL_ATKUP_01 is what SKL_ATKUP_01_P does), which nobody picks.

Names and category names are the game's English text (language ``int`` in
master_text), found by the ``SCT.ID`` key the row carries. A decal added by a
mod is listed like any other, as long as that mod is in the database - the
picker reads the database the manager is pointed at, as it is now.

What a decal does is its description text the way the game fills it in:
``#0`` to ``#5`` are the row's val0 to val5, and a decal whose type belongs to
a group (master_skill_type.group) adds the group's limit, its own ``#0`` being
the group's max_value. The texts are wrapped for the game's narrow box - a
line break every few words, ``//`` between pages - so they are joined back
into plain sentences here.

Only ever read: the database is opened read-only.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

ENGLISH = "int"
PARAMETER = re.compile(r"#([0-9])")


@dataclass(frozen=True)
class Decal:
    id: str
    name: str        # English, or the id when it has no text
    stars: int       # master_skill.rarity, 1 to 5
    category: str    # English, or the category id
    description: str = ""   # what it does, numbers filled in
    limit: str = ""         # its group's cap, for a decal whose effect stacks
    how_to_get: str = ""    # master_skill.getroute's text


def game_text(text: str, values: list | tuple = ()) -> str:
    """A text as the game shows it, as one plain paragraph: its #0..#5 filled
    in from ``values``, the box's line breaks and page breaks taken out."""
    def fill(match: re.Match) -> str:
        index = int(match.group(1))
        if index >= len(values) or values[index] is None:
            return match.group(0)
        value = values[index]
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        return str(value)

    text = PARAMETER.sub(fill, str(text or ""))
    return " ".join(text.replace("//", " ").split())


class DecalReadError(Exception):
    """The database could not be read as one with decals in it."""


def _connect(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)


def _texts(con: sqlite3.Connection, keys: set[str]) -> dict[str, str]:
    """English text for "SCT.ID" keys."""
    sections = {key.split(".", 1)[0] for key in keys if "." in key}
    found: dict[str, str] = {}
    for section in sections:
        for sct, text_id, txt in con.execute(
            "SELECT sct, id, txt FROM master_text WHERE lang = ? AND sct = ?", (ENGLISH, section)
        ):
            key = f"{sct}.{text_id}"
            if key in keys and txt:
                found[key] = " ".join(str(txt).split())
    return found


def read_decals(db_path: Path) -> list[Decal]:
    """Every decal in the database, in the database's own order."""
    try:
        con = _connect(db_path)
    except sqlite3.Error as exc:
        raise DecalReadError(f"could not open {db_path}: {exc}") from exc
    try:
        rows = con.execute(
            "SELECT id, name, rarity, category FROM master_skill "
            "WHERE substr(id, -2) = '_P' ORDER BY rowid"
        ).fetchall()
        categories = dict(con.execute("SELECT id, name FROM master_skill_category").fetchall())
        details = _details(con)
        keys = {str(name) for _, name, _, _ in rows if name}
        keys |= {str(name) for name in categories.values() if name}
        for desc, getroute, _, group in details.values():
            keys |= {str(desc), str(getroute)} | ({str(group[1])} if group else set())
        texts = _texts(con, keys)
    except sqlite3.Error as exc:
        raise DecalReadError(f"{db_path} has no decals to list: {exc}") from exc
    finally:
        con.close()
    decals = []
    for decal_id, name, rarity, category in rows:
        category_key = categories.get(category)
        desc, getroute, values, group = details.get(decal_id, ("", "", (), None))
        decals.append(
            Decal(
                id=str(decal_id),
                name=texts.get(str(name), str(decal_id)),
                stars=int(rarity or 0),
                category=texts.get(str(category_key), str(category or "")),
                description=game_text(texts.get(str(desc), ""), values),
                limit=game_text(texts.get(str(group[1]), ""), (group[0],)) if group else "",
                how_to_get=game_text(texts.get(str(getroute), "")),
            )
        )
    return decals


def _details(con: sqlite3.Connection) -> dict[str, tuple]:
    """Per decal: (description key, how-to-get key, val0..val5, (group max,
    group text key) or None). A database without the columns or tables these
    come from - an old or cut-down one - gives none rather than failing."""
    try:
        rows = con.execute(
            "SELECT id, desc, getroute, val0, val1, val2, val3, val4, val5, type FROM master_skill "
            "WHERE substr(id, -2) = '_P'"
        ).fetchall()
    except sqlite3.Error:
        return {}
    try:
        groups = {
            type_id: (max_value, desc)
            for type_id, max_value, desc in con.execute(
                'SELECT t.id, g.max_value, g.desc FROM master_skill_type t '
                'JOIN master_skill_group g ON g.id = t."group"'
            )
        }
    except sqlite3.Error:
        groups = {}
    return {
        row[0]: (row[1] or "", row[2] or "", tuple(row[3:9]), groups.get(row[9]))
        for row in rows
    }


def default_ids(db_path: Path, default_sql: str) -> list[str]:
    """The decals a setting's own default query picks in this database ([] if
    it cannot be run - the picker then starts with nothing ticked)."""
    try:
        con = _connect(db_path)
    except sqlite3.Error:
        return []
    try:
        return [str(row[0]) for row in con.execute(default_sql) if row and row[0]]
    except sqlite3.Error:
        return []
    finally:
        con.close()
