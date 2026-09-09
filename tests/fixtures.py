"""A miniature stand-in for masters.db, plus helpers for building mod folders.

The real database is tens of megabytes of game data. The tests only need the
tables and columns the shipped mods touch, with a handful of rows each.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SCHEMA = """
CREATE TABLE master_skill (
    id TEXT PRIMARY KEY,
    name TEXT,
    buy_money INTEGER,
    val0 INTEGER
);
CREATE TABLE master_shop_product_price (
    id TEXT PRIMARY KEY,
    price INTEGER,
    medal INTEGER
);
-- Mirrors the real master_text, whose DDL the Floor Material Names mod carries:
-- the section tag, the sound key and the type column all matter to mods that
-- filter on them.
CREATE TABLE master_text (
    "sct" CHARACTER(32) NOT NULL DEFAULT '',
    "id" CHARACTER(64) NOT NULL DEFAULT '',
    "snd" CHARACTER(3) NOT NULL,
    "lang" CHARACTER(3) NOT NULL,
    "txt" TEXT NOT NULL,
    "type" INTEGER NOT NULL,
    PRIMARY KEY ("sct", "id", "snd", "lang")
);
CREATE TABLE master_body_detail (
    id TEXT PRIMARY KEY,
    price INTEGER
);
CREATE TABLE master_part_research (
    id TEXT PRIMARY KEY,
    init_waiting_minute INTEGER,
    add_waiting_minute INTEGER,
    craft_spirit INTEGER,
    lvup_spirit INTEGER,
    lvup_spirit_c REAL
);
CREATE TABLE master_equip_rank_point (
    id TEXT PRIMARY KEY,
    weapon_point INTEGER,
    armor_point INTEGER
);
CREATE TABLE master_skillgacha_odds (
    id TEXT PRIMARY KEY,
    odds REAL
);
CREATE TABLE master_const_int (
    "id" CHARACTER(64) NOT NULL,
    "value" INTEGER NOT NULL,
    PRIMARY KEY ("id")
);
CREATE TABLE master_shop_appearance (
    "id" CHARACTER(64) NOT NULL,
    "templateid" CHARACTER(64) NOT NULL,
    "flrid" CHARACTER(64) NOT NULL,
    "areaid" CHARACTER(64) NOT NULL,
    "rate" INTEGER NOT NULL,
    PRIMARY KEY ("id")
);
"""

BIBLE_TEXT_EN = (
    "Revive prices:\n"
    "Grade 1: 5000 Kill Coins\n"
    "Grade 2: 10000 Kill Coins\n"
    "Grade 3: 20000 Kill Coins"
)
BIBLE_TEXT_JP = (
    "\u5fa9\u6d3b\u4fa1\u683c:\n"
    "\u30b0\u30ec\u30fc\u30c91\uff1a5000 \u30ad\u30eb\u30b3\u30a4\u30f3\n"
    "\u30b0\u30ec\u30fc\u30c92\uff1a10000 \u30ad\u30eb\u30b3\u30a4\u30f3"
)

# Skill descriptions end with a newline and a space in the real database - the
# x'0a20' the shipped text mods append. Keep that here so the mods are tested
# against the same shape.
SKILL_TEXT_EN_40 = "Increases EXP gained by 40%.\n "
SKILL_TEXT_EN_10 = "Increases EXP gained by 10%.\n "
SKILL_TEXT_DE_40 = "Erh\u00f6ht erhaltene EP um 40 %.\n "
SKILL_TEXT_FR_10 = "Augmente le taux d'EXP gagn\u00e9e\nde 10 %.\n "


def build_db(path: Path) -> Path:
    """Create a small masters.db-shaped database at ``path``."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    con = sqlite3.connect(str(path))
    try:
        con.executescript(SCHEMA)
        con.executemany(
            "INSERT INTO master_skill (id, name, buy_money, val0) VALUES (?, ?, ?, ?)",
            [
                ("SKL_EXPUP_01", "Exp Up", 500, 10),
                ("SKL_EXPUP_02", "Nitro Boost", 5000, 40),
                ("SKL_POWER_01", "Power Up", 1200, 5),
                ("SKL_FREE_01", "Freebie", 1, 0),
            ],
        )
        con.executemany(
            "INSERT INTO master_shop_product_price (id, price, medal) VALUES (?, ?, ?)",
            [
                ("PRD_CONTINUE_G1", 5000, 0),
                ("PRD_CONTINUE_G2", 10000, 0),
                ("PRD_ITEM_01", 300, 5),
                ("PRD_ITEM_02", 1, 1),
            ],
        )
        con.executemany(
            "INSERT INTO master_text (sct, id, snd, lang, txt, type) VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("BIBLE", "TXT_BIBLE_19_NOTE_G", "", "int", BIBLE_TEXT_EN, 0),
                ("BIBLE", "TXT_BIBLE_19_NOTE_G", "", "jpn", BIBLE_TEXT_JP, 0),
                ("BIBLE", "TXT_OTHER", "", "int", "Nothing to see here", 0),
                # Skill descriptions carry an 'sct' section tag, and the game
                # stores them with a trailing newline + space - both of which
                # the shipped text mods depend on.
                ("SKILL_DESCRIPTION", "TXT_SKL_EXPUP_02", "", "int", SKILL_TEXT_EN_40, 0),
                ("SKILL_DESCRIPTION", "TXT_SKL_EXPUP_02", "", "deu", SKILL_TEXT_DE_40, 0),
                ("SKILL_DESCRIPTION", "TXT_SKL_EXPUP_01", "", "int", SKILL_TEXT_EN_10, 0),
                ("SKILL_DESCRIPTION", "TXT_SKL_EXPUP_01", "", "fra", SKILL_TEXT_FR_10, 0),
                # Area names live in the same table but a different section -
                # this is the pair that must NOT be reported as conflicting.
                ("AREA_NAME", "TXT_AMS_0000", "", "int", "AKAMI", 0),
                ("AREA_NAME", "TXT_AMS_0000", "", "jpn", "あかみ", 0),
                ("AREA_NAME", "TXT_AMS_0001", "", "int", "ARIGURE", 0),
                ("AREA_NAME", "TXT_AMS_0002", "", "int", "IWAJIMA", 0),
            ],
        )
        con.executemany(
            "INSERT INTO master_body_detail (id, price) VALUES (?, ?)",
            [("BODY_01", 2000), ("BODY_02", 8000), ("BODY_03", 1)],
        )
        con.executemany(
            "INSERT INTO master_part_research "
            "(id, init_waiting_minute, add_waiting_minute, craft_spirit, lvup_spirit, lvup_spirit_c)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("PRT_01", 60, 30, 100, 50, 1.5),
                ("PRT_02", 240, 120, 400, 200, 2.0),
                ("PRT_03", 1, 1, 1, 1, 1.0),
            ],
        )
        con.executemany(
            "INSERT INTO master_equip_rank_point (id, weapon_point, armor_point) VALUES (?, ?, ?)",
            [("RNK_01", 10, 20), ("RNK_02", 40, 80)],
        )
        con.executemany(
            "INSERT INTO master_skillgacha_odds (id, odds) VALUES (?, ?)",
            [("GCH_01", 100.0), ("GCH_02", 4004.0)],
        )
        con.executemany(
            "INSERT INTO master_const_int (id, value) VALUES (?, ?)",
            [("SHOP_APPEARANCE_TIME", 300), ("SHOP_INCIDANCE_INCREMENT", 5)],
        )
        con.executemany(
            "INSERT INTO master_shop_appearance "
            "(id, templateid, flrid, areaid, rate) VALUES (?, ?, ?, ?, ?)",
            [("SHOP_AP_05", "B", "MET_FLR_05", "MET_AREA_V030", 10)],
        )
        con.commit()
    finally:
        con.close()
    return path


def write_mod(mods_dir: Path, mod_id: str, data: dict, **files: str) -> Path:
    """Write a JSON mod folder. Extra keyword args become files in the folder."""
    folder = Path(mods_dir) / mod_id
    folder.mkdir(parents=True, exist_ok=True)
    payload = {
        "id": mod_id,
        "name": mod_id,
        "description": "test mod",
        "version": "1.0.0",
        "author": "tests",
        **data,
    }
    (folder / "mod.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    for name, text in files.items():
        (folder / name.replace("__", ".")).write_text(text, encoding="utf-8")
    return folder


def write_sql_mod(mods_dir: Path, mod_id: str, sql: str, inverse: str | None = None) -> Path:
    folder = Path(mods_dir) / mod_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "mod.sql").write_text(sql, encoding="utf-8")
    if inverse is not None:
        (folder / "inverse.sql").write_text(inverse, encoding="utf-8")
    return folder


def query(db_path: Path, sql: str, params: tuple = ()) -> list[tuple]:
    con = sqlite3.connect(str(db_path))
    try:
        return [tuple(row) for row in con.execute(sql, params)]
    finally:
        con.close()
