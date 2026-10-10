"""Everything a shop list can sell, as the goods window lists it.

The vending machine sells three kinds of thing (its rows' entity_type): an
ITEM of master_item, a MUSHROOM of master_mushroom, or a PART - a weapon or
armour piece. A goods list offers the first two. Items cover the consumables,
the materials and the blueprints; parts stay out of the machine.

Names are the game's English text (language ``int``). A blueprint has none of
its own - all 1,899 are called "RMAP" or "UNKNOWN_RMAP" - so it is named after
the weapon or armour it makes, the way the mod builder shows it. The
unidentified copy of each blueprint (the id ending in U) is left out: it is the
same blueprint, waiting to be identified.

Prices are the game's own for one, in Kill Coins and in Death Metals - what a
list entry costs when it gives no price of its own.

Only ever read: the database is opened read-only.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .decals import _texts

CATEGORIES = {
    "ITTP_MATERIAL": "Materials",
    "ITTP_RMAP": "Blueprints",
    "ITTP_HEAL": "Consumables",
    "ITTP_BIV": "Consumables",
    "ITTP_BACK": "Consumables",
    "ITTP_WOOD": "Consumables",
}
MUSHROOMS = "Mushrooms"


@dataclass(frozen=True)
class Good:
    id: str
    name: str
    category: str
    stars: int = 0
    money: int = 0      # the game's Kill Coin price for one
    medal: int = 0      # ...and its Death Metal price


class GoodsReadError(Exception):
    """The database could not be read as one with items in it."""


def _connect(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)


def blueprint_part(item_id: str) -> str:
    """`ITMP_ARM_WP001_001` -> `PT_ARM_WP001_001`, the part it makes."""
    return "PT_" + item_id[len("ITMP_"):].removesuffix("U")


def read_goods(db_path: Path) -> list[Good]:
    """Every item and mushroom a shop list can sell, by category then name."""
    try:
        con = _connect(db_path)
    except sqlite3.Error as exc:
        raise GoodsReadError(f"could not open {db_path}: {exc}") from exc
    try:
        items = con.execute(
            "SELECT itemid, itemtype, name, rarity, buy_money, buy_medal FROM master_item"
        ).fetchall()
        parts = dict(con.execute("SELECT id, name FROM master_part").fetchall())
        shrooms = con.execute(
            "SELECT id, c_name, price_s_money, price_s_medal FROM master_mushroom"
        ).fetchall()
        keys = {str(name) for _, _, name, _, _, _ in items if name}
        keys |= {str(name) for name in parts.values() if name}
        keys |= {str(name) for _, name, _, _ in shrooms if name}
        texts = _texts(con, keys)
    except sqlite3.Error as exc:
        raise GoodsReadError(f"{db_path} has no items to list: {exc}") from exc
    finally:
        con.close()

    goods = []
    for item_id, item_type, name, rarity, money, medal in items:
        item_id = str(item_id)
        if item_type == "ITTP_RMAP":
            if item_id.endswith("U"):
                continue
            made = parts.get(blueprint_part(item_id))
            label = f"{texts.get(str(made), blueprint_part(item_id))} blueprint"
        else:
            label = texts.get(str(name))
            if not label:
                continue        # nothing to call it: not something the game sells
        goods.append(Good(item_id, label, CATEGORIES.get(str(item_type), "Other"),
                          int(rarity or 0), int(money or 0), int(medal or 0)))
    for shroom_id, name, money, medal in shrooms:
        goods.append(Good(str(shroom_id), texts.get(str(name), str(shroom_id)), MUSHROOMS,
                          0, int(money or 0), int(medal or 0)))
    goods.sort(key=lambda g: (g.category, g.name.casefold(), g.id))
    return goods
