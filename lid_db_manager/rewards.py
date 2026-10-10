"""Every reward in a masters.db, as the reward list window lists it.

A reward is a row of master_reward: what it gives (``type``), how much or how
many (``num``) and of what (``val0``). The daily login bonus, quest prizes and
Uncle Death's gifts all hand out these. Their own names are Japanese, so each
is described here from what it gives, in the game's English text where there
is some: the item, mushroom, beast, decal, part or fighter it names.

How many presents a reward makes in the Rewards Box matters to a player: the
game makes one present per item, mushroom, beast, decal, part or Lost Bag, so
"4 rare metals" is 4 presents, while an amount (Kill Coins, SPLithium, Death
Metals, Bloodnium, Express Passes) is always one.

Only ever read: the database is opened read-only.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .decals import DecalReadError, _texts, read_decals
from .goods import GoodsReadError, read_goods

# Amounts: one present whatever the number.
AMOUNTS = {
    "MONEY": ("Kill Coins", "{:,} Kill Coins"),
    "PAIDMONEY": ("Kill Coins", "{:,} paid Kill Coins"),
    "SPIRIT": ("SPLithium", "{:,} SPLithium"),
    "BLOODNIUM": ("Bloodnium", "{:,} Bloodnium"),
    "MEDAL": ("Death Metals", "{:,} Death Metals"),
    "PAIDMEDAL": ("Death Metals", "{:,} paid Death Metals"),
    "VIP": ("Express Pass", "Express Pass x{:,}"),
    "1VIP": ("Express Pass", "Express one-day pass x{:,}"),
}
# Things: one present each.
PARTS = ("PTTP_ARM", "PTTP_HEAD", "PTTP_BODY", "PTTP_LEGS", "PART")
LOST_BAGS = "Lost Bags"


@dataclass(frozen=True)
class Reward:
    id: str
    name: str           # what it gives, in English
    category: str
    presents: int = 1   # how many presents it makes in the Rewards Box
    amount: int = 0     # for sorting: the number it gives


class RewardsReadError(Exception):
    """The database could not be read as one with rewards in it."""


def _connect(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)


def _times(name: str, num: int) -> str:
    return f"{name} x{num:,}" if num > 1 else name


def read_rewards(db_path: Path) -> list[Reward]:
    """Every reward in the database, by category, then name and amount."""
    try:
        con = _connect(db_path)
    except sqlite3.Error as exc:
        raise RewardsReadError(f"could not open {db_path}: {exc}") from exc
    try:
        rows = con.execute(
            "SELECT rwdid, type, num, val0, val1 FROM master_reward ORDER BY rowid"
        ).fetchall()
        beasts = dict(con.execute("SELECT id, name FROM master_beast").fetchall())
        parts = dict(con.execute("SELECT id, name FROM master_part").fetchall())
        # Decals a reward names without the "_P" a stew decal has (the Express
        # Pass boosters): named from their own row.
        skills = dict(con.execute("SELECT id, name FROM master_skill").fetchall())
        keys = {str(n) for n in beasts.values() if n} | {str(n) for n in parts.values() if n}
        keys |= {str(n) for n in skills.values() if n}
        keys |= {f"FTYPE_NAME.TXT_{val0}" for _, kind, _, val0, _ in rows if kind == "UNLOCK_FIGHTER"}
        texts = _texts(con, keys)
    except sqlite3.Error as exc:
        raise RewardsReadError(f"{db_path} has no rewards to list: {exc}") from exc
    finally:
        con.close()
    try:
        goods = {good.id: good for good in read_goods(db_path)}
    except GoodsReadError:
        goods = {}
    try:
        decals = {decal.id: decal.name for decal in read_decals(db_path)}
    except DecalReadError:
        decals = {}
    for skill_id, name in skills.items():
        if name and str(name) in texts:
            decals.setdefault(str(skill_id), texts[str(name)])

    # A reward made of several rows gives all of them: listed once, its rows joined.
    found: dict[str, list[tuple]] = {}
    for rwdid, kind, num, val0, val1 in rows:
        found.setdefault(str(rwdid), []).append((str(kind or ""), int(num or 0), str(val0 or ""),
                                                 str(val1 or "")))
    rewards = []
    for rwdid, gives in found.items():
        names, categories, presents, amount = [], [], 0, 0
        for kind, num, val0, val1 in gives:
            name, category, count = _describe(kind, num, val0, val1, goods, decals, beasts, parts, texts)
            names.append(name)
            categories.append(category)
            presents += count
            amount = max(amount, num)
        category = categories[0] if len(set(categories)) == 1 else "Mixed"
        name = " + ".join(n for n in names if n.strip())
        if not name:        # names nothing the game has (RWD_PART_ARM_1, say)
            name = f"{rwdid} (gives nothing the game knows)"
        rewards.append(Reward(rwdid, name, category, presents, amount))
    rewards.sort(key=lambda r: (r.category, r.name.casefold() if not r.amount or r.category in (
        "Items", "Materials", "Consumables", "Blueprints", "Mushrooms", "Decals", "Beasts")
        else "", r.amount, r.id))
    return rewards


def _describe(kind, num, val0, val1, goods, decals, beasts, parts, texts) -> tuple[str, str, int]:
    """(what it gives, category, presents) for one row of a reward."""
    if kind in AMOUNTS:
        category, words = AMOUNTS[kind]
        text = words.format(num)
        if num == 1 and text.endswith(("Coins", "Metals")):
            text = text[:-1]        # 1 Kill Coin, 1 Death Metal
        return text, category, 1
    count = max(num, 1)
    if kind == "ITEM":
        good = goods.get(val0)
        return _times(good.name if good else val0, num), (good.category if good else "Items"), count
    if kind == "MUSHROOM":
        good = goods.get(val0)
        return _times(good.name if good else val0, num), "Mushrooms", count
    if kind == "BEAST":
        return _times(texts.get(str(beasts.get(val0)), val0), num), "Beasts", count
    if kind == "SKILL":
        name = decals.get(f"{val0}_P") or decals.get(val0) or val0
        return _times(f"{name} decal", num), "Decals", count
    if kind == "LOSTBAG":
        return _times(f"{val0.title()} Lost Bag", num), LOST_BAGS, count
    if kind in PARTS:
        return _times(texts.get(str(parts.get(val0)), val0), num), "Weapons and armour", count
    if kind == "UNLOCK_FIGHTER":
        fighter = texts.get(f"FTYPE_NAME.TXT_{val0}", val0)
        grade = f" (G{val1})" if val1 else ""
        return f"Unlock fighter: {fighter}{grade}", "Fighters", 1
    return _times(f"{kind} {val0}".strip(), num), "Other", count
