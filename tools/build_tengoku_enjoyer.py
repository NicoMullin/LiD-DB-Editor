"""Build the Tengoku Enjoyer and Pre-Nerf Gear mods from ZeroUnderOne's release.

A maintenance tool, not something a player runs. Tengoku Enjoyer, by
ZeroUnderOne, takes Tengoku from 450F to 1000F. It comes in two versions that
differ only in the Haters: Original, and Route Pure, where each Hater carries a
weapon from its tower's route. ZeroUnderOne publishes Route Pure as one SQL
file; the Original is in S3er0i9ng's Mod Manager as a before/after overlay
(tengoku/patch.db), made from ZeroUnderOne's database.

The mod is written in parts, so a player can pick and choose. Each changed
column belongs to exactly one part, and a column this does not know stops the
build:

    tower.sql                 Tengoku to 1000F and the routes to 250F: the new
                              floors, escalators, drops, bosses' new levels,
                              and 451F no longer being the top
    old-floors.sql            floors 51F-451F and the routes to 140F: their Haters,
                              how hard those hit, and what they drop on the routes
    enemies.sql               tougher enemies on 62 floors from 145F to 450F
    bosses.sql                the bosses' and Four Forcemen's strength, retuned
    gear.sql                  gear values from before the offline update
    haters-original.sql       the Haters of the Original version
    haters-route-pure.sql     the Haters of Route Pure

gear.sql is written for mods/Pre-Nerf Gear too, for players who want only that.

Each file is our own diff of the built database against a stock one. Before
anything is written: every part applies to a fresh stock copy on its own, and
all of them together give exactly ZeroUnderOne's database, for each version.
When S3er0i9ng's Route Pure overlay is given too, it has to give the same
database as ZeroUnderOne's SQL - the check that the overlay, and so the
Original taken from it, is a faithful copy:

    py -3 tools/build_tengoku_enjoyer.py --sql PATH/TO/TengokuEnjoyer.sql \\
        --original PATH/TO/tengoku/patch.db --route-pure-overlay PATH/TO/tengoku/route_pure.db \\
        --vanilla PATH/TO/stock/masters.db
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import tempfile
from collections import Counter
from dataclasses import replace
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from lid_db_manager import dbdiff  # noqa: E402

TENGOKU = REPO / "mods" / "Tengoku Enjoyer"
GEAR = REPO / "mods" / "Pre-Nerf Gear"
HATER_TABLES = ["master_custom_part", "master_dummy", "master_dummy_chara_equip_gen",
                "master_dummy_chara_skill_gen", "master_dummy_group", "master_dummy_name"]
GEAR_TABLES = ["master_atk_scale", "master_const_int", "master_part", "master_sklmv"]
FLOOR_TABLES = ("master_floor", "master_tmpfloor_stage")

# Where each change to a row the stock game already has belongs. New rows are
# all the tower's (or the Haters'); these are the edits.
FLOOR_COLUMNS = {                       # master_floor and master_tmpfloor_stage
    "matching1_dummy_group": "old-floors", "matching2_dummy_group": "old-floors",
    "matching3_dummy_group": "old-floors", "matching4_dummy_group": "old-floors",
    "peatkup": "old-floors", "pedefup": "old-floors",
    "zmbatkup": "enemies", "zmbdefup": "enemies",
    # 451F (HVN_FLR_0401) stops being the top
    "zmbmin": "tower", "zmbmax": "tower", "diedmvtype": "tower", "is_closed_area": "tower",
}
TABLE_PART = {                          # every edit in these tables goes to one part
    "master_area_connect_escalator": "tower",           # 451F's (and 140F's) way up
    "master_area_connect_node_repeat_straight": "tower",
    "master_const_str": "tower",                        # FLR_END_ID
    "master_lastfloor": "tower",
    "master_floor_drop_gen": "old-floors",              # what Haters drop on the routes
    "master_mboss_param_int": "bosses",
    "master_fourforcemen_param_int": "bosses",
    **{table: "gear" for table in GEAR_TABLES},
}
PARTS = {
    "tower": "Tengoku to 1000F and the routes to 250F: new floors, escalators, drops and bosses.",
    "old-floors": "Floors 51F-451F and the routes to 140F: their Haters, how hard those hit, and what they drop.",
    "enemies": "Tougher enemies on 62 floors from 145F to 450F.",
    "bosses": "The bosses' and Four Forcemen's strength, retuned to lead into the new floors.",
    "gear": "Gear values from before the offline update, as ZeroUnderOne put them back.",
}

parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
parser.add_argument("--sql", required=True, type=Path, help="ZeroUnderOne's TengokuEnjoyer.sql (Route Pure)")
parser.add_argument("--original", required=True, type=Path, help="S3er0i9ng's tengoku/patch.db (Original)")
parser.add_argument("--route-pure-overlay", type=Path, help="S3er0i9ng's tengoku/route_pure.db, to check against")
parser.add_argument("--vanilla", required=True, type=Path, help="a stock masters.db")
args = parser.parse_args()


def q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def overlay(db_path: Path, patch_path: Path) -> None:
    """S3er0i9ng's overlay, switched on: each row it names goes from before to after."""
    patch = sqlite3.connect(f"file:{patch_path.as_posix()}?mode=ro", uri=True)
    con = sqlite3.connect(str(db_path))
    con.execute("BEGIN")
    for name, pk, mode, _, _, schema in patch.execute("SELECT * FROM manifest ORDER BY name"):
        actual = con.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
        if not actual or actual[0] != schema:
            sys.exit(f"{name}: not the table the overlay was made for")
        columns = [r[1] for r in con.execute(f"PRAGMA table_info({q(name)})")]
        before = patch.execute(f"SELECT * FROM {q('before_' + name)}").fetchall()
        after = patch.execute(f"SELECT * FROM {q('after_' + name)}").fetchall()
        if mode == "keyed":
            pos = [columns.index(c) for c in json.loads(pk)]
            was = {tuple(r[i] for i in pos): r for r in before}
            now = {tuple(r[i] for i in pos): r for r in after}
            cond = " AND ".join(q(columns[i]) + " IS ?" for i in pos)
            for key in set(was) | set(now):
                found = con.execute(f"SELECT * FROM {q(name)} WHERE {cond}", key).fetchall()
                found = found[0] if found else None
                if found == now.get(key):
                    continue
                if found != was.get(key):
                    sys.exit(f"{name} {key}: the stock database is not what the overlay expects")
                if key not in now:
                    con.execute(f"DELETE FROM {q(name)} WHERE {cond}", key)
                elif key not in was:
                    con.execute(f"INSERT INTO {q(name)} VALUES ({','.join('?' * len(columns))})", now[key])
                else:
                    sets = ",".join(q(c) + "=?" for c in columns)
                    con.execute(f"UPDATE {q(name)} SET {sets} WHERE {cond}", (*now[key], *key))
        else:
            was, now = Counter(before), Counter(after)
            cond = " AND ".join(q(c) + " IS ?" for c in columns)
            for row in set(was) | set(now):
                delta = now[row] - was[row]
                if delta > 0:
                    con.executemany(f"INSERT INTO {q(name)} VALUES ({','.join('?' * len(row))})", [row] * delta)
                elif delta < 0:
                    con.execute(f"DELETE FROM {q(name)} WHERE rowid IN "
                                f"(SELECT rowid FROM {q(name)} WHERE {cond} LIMIT ?)", (*row, -delta))
    con.commit()
    con.close()
    patch.close()


def tables(path: Path, names: list[str]) -> dict:
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        return {n: Counter(con.execute(f"SELECT * FROM {q(n)}").fetchall()) for n in names}
    finally:
        con.close()


def run_sql(path: Path, *scripts: str) -> None:
    con = sqlite3.connect(str(path))
    try:
        for sql in scripts:
            con.executescript("BEGIN;\n" + sql + "\nCOMMIT;")
    finally:
        con.close()


def split(delta: dbdiff.DbDelta) -> dict[str, dbdiff.DbDelta]:
    """Route Pure's changes outside the Hater tables, sorted into the parts."""
    parts = {name: dbdiff.DbDelta() for name in PARTS}
    for table in delta.tables:
        if table.table in HATER_TABLES:
            continue
        pieces = {name: replace(table, updates=[], inserts=[], deletes=[]) for name in PARTS}
        # new rows and removed ones are the tower's, or a gear table's own
        owner = "gear" if table.table in GEAR_TABLES else "tower"
        pieces[owner].inserts = list(table.inserts)
        pieces[owner].deletes = list(table.deletes)
        for update in table.updates:
            if table.table in FLOOR_TABLES:
                by_part: dict[str, dict] = {}
                for column, value in update.changes.items():
                    part = FLOOR_COLUMNS.get(column)
                    if part is None:
                        sys.exit(f"{table.table}.{column}: a change this does not know where to put")
                    by_part.setdefault(part, {})[column] = value
                for part, changes in by_part.items():
                    before = {c: update.before[c] for c in changes}
                    pieces[part].updates.append(replace(update, changes=changes, before=before))
            elif table.table in TABLE_PART:
                pieces[TABLE_PART[table.table]].updates.append(update)
            else:
                sys.exit(f"{table.table}: edits to stock rows this does not know where to put")
        for name, piece in pieces.items():
            if not piece.empty:
                parts[name].tables.append(piece)
    return parts


credit = ("Tengoku Enjoyer by ZeroUnderOne, included with permission. Recorded by "
          "tools/build_tengoku_enjoyer.py - do not edit by hand.")

with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    route_pure = tmp / "route_pure.db"
    shutil.copyfile(args.vanilla, route_pure)
    con = sqlite3.connect(str(route_pure))
    con.executescript(args.sql.read_text(encoding="utf-8"))     # it has its own transaction
    con.close()
    original = tmp / "original.db"
    shutil.copyfile(args.vanilla, original)
    overlay(original, args.original)
    con = sqlite3.connect(f"file:{args.vanilla.as_posix()}?mode=ro", uri=True)
    names = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    con.close()

    if args.route_pure_overlay:
        check = tmp / "check.db"
        shutil.copyfile(args.vanilla, check)
        overlay(check, args.route_pure_overlay)
        if tables(check, names) != tables(route_pure, names):
            sys.exit("S3er0i9ng's Route Pure overlay does not give the same database as ZeroUnderOne's SQL")
        print("S3er0i9ng's Route Pure overlay gives exactly ZeroUnderOne's database")

    shared = [n for n in names if n not in HATER_TABLES]
    if tables(original, shared) != tables(route_pure, shared):
        sys.exit("the two versions differ outside the Hater tables")

    files: dict[str, str] = {}
    for name, part in split(dbdiff.compare(args.vanilla, route_pure)).items():
        files[name] = dbdiff.to_sql(part, header=f"{PARTS[name]}\n{credit}")
    for name, built, what in (("haters-original", original, "The Haters of the Original version."),
                              ("haters-route-pure", route_pure,
                               "The Haters of Route Pure: each carries a weapon from its tower's route.")):
        files[name] = dbdiff.to_sql(dbdiff.compare(args.vanilla, built, only_tables=HATER_TABLES),
                                    header=f"{what}\n{credit}")

    # every part on its own applies cleanly to a stock copy
    for name, sql in files.items():
        alone = tmp / "alone.db"
        shutil.copyfile(args.vanilla, alone)
        try:
            run_sql(alone, sql)
        except sqlite3.Error as exc:
            sys.exit(f"{name}.sql does not apply on its own: {exc}")
    # and all of them together give exactly each version
    common = [files[n] for n in PARTS]
    for version, built in (("route-pure", route_pure), ("original", original)):
        whole = tmp / "whole.db"
        shutil.copyfile(args.vanilla, whole)
        run_sql(whole, files[f"haters-{version}"], *common)
        if tables(whole, names) != tables(built, names):
            sys.exit(f"the parts together do not give the {version} database")
    print("verified: each part applies on its own, and all together give each version exactly")

for old in ("tengoku.sql",):
    if (TENGOKU / old).exists():
        (TENGOKU / old).unlink()
for name, sql in files.items():
    targets = [TENGOKU / f"{name}.sql"] + ([GEAR / "gear.sql"] if name == "gear" else [])
    for out in targets:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(sql, encoding="utf-8")
    print(f"wrote {name}.sql ({len(sql) // 1024:,} KB)" + (" - and Pre-Nerf Gear's copy" if name == "gear" else ""))
