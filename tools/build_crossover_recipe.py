"""Record the Crossover Content pack's database changes as a reusable recipe.

This is a maintenance tool, not something a player runs. It exists so that a new
release of the pack can be supported by regenerating two files rather than by
anyone hand-writing SQL.

What it does:

    1. runs the pack's own installer against a copy of our vanilla masters.db,
    2. diffs the result with our own dbdiff,
    3. writes that difference to ``lid_db_manager/recipes/crossover-<ver>.sql``,
    4. writes a fingerprint beside it so the manager can recognise the pack.

Step 1 is the important one: the pack's installer is the authority on what the
content should be, so the recipe is a recording of its output rather than a
reimplementation of its logic. The recording is verified by replaying it onto a
fresh vanilla copy and confirming the result is identical - if that check ever
fails, nothing is written.

The pack is not redistributed. Only the recipe - our own diff of a database we
already ship a vanilla copy of - lives in this repository.

Usage::

    py -3 tools/build_crossover_recipe.py --pack "C:/.../LetItDieCrossoverContent-..."
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lid_db_manager.sqlutil import split_statements  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
RECIPES = REPO / "lid_db_manager" / "recipes"
VANILLA = Path(r"E:\Vanilla DB\masters.db")


def sha256_of(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def load_pack_installer(pack: Path):
    """Import the pack's own installer module from the folder given."""
    for needed in ("installer.py", "asset_integrity.py", "catalog.json"):
        if not (pack / needed).is_file():
            raise SystemExit(f"{pack} does not look like the pack: no {needed}")
    sys.path.insert(0, str(pack))
    import installer  # noqa: PLC0415

    if Path(installer.__file__).resolve().parent != pack.resolve():
        raise SystemExit(f"imported the wrong installer.py: {installer.__file__}")
    return installer


def stub_missing_models(pack: Path, catalogue: dict, into: Path) -> list[str]:
    """Create empty stand-ins for base-game models the pack does not bundle.

    The installer checks each quest's models exist, in the game folder or its own
    assets. Thirteen are ordinary game files it expects to already be installed.
    They are only ever tested with ``is_file()`` and never read, so an empty file
    satisfies the check without affecting a single row of the output.
    """
    bundled = {p.name for p in (pack / "assets").iterdir()}
    cooked = into / "BrgGame" / "CookedPCConsole"
    cooked.mkdir(parents=True, exist_ok=True)
    stubbed = set()
    for quest in catalogue["quests"]:
        for model in quest["models"]:
            if model not in bundled:
                (cooked / model).touch()
                stubbed.add(model)
    return sorted(stubbed)


def _replay(sql: str, database: Path) -> None:
    """Apply the recorded SQL the way the manager will - same guarantees.

    Foreign keys on, one transaction, checked at commit. The first version of
    this used a bare connection, where foreign keys default to off, and so it
    happily recorded a recipe that could not be applied to a database that
    already had the content in it.
    """
    import sqlite3  # noqa: PLC0415

    con = sqlite3.connect(database, isolation_level=None)
    try:
        con.execute("PRAGMA foreign_keys = ON")
        con.execute("BEGIN IMMEDIATE")
        con.execute("PRAGMA defer_foreign_keys = ON")
        for statement in split_statements(sql):
            if statement.strip():
                con.execute(statement)
        con.execute("COMMIT")
    finally:
        con.close()  # a context manager commits but leaves the file locked


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", type=Path, required=True,
                        help="the extracted Crossover Content pack folder")
    parser.add_argument("--vanilla", type=Path, default=VANILLA)
    parser.add_argument("--game", type=Path,
                        help="your LET IT DIE folder; without it, base-game models "
                             "the pack does not bundle are stubbed instead")
    parser.add_argument("--author", default="S3er0i9ng",
                        help="who made the mod being recorded")
    parser.add_argument("--source", default="https://letitdiemods.pages.dev/",
                        help="where players should download it from")
    args = parser.parse_args()

    pack = args.pack.resolve()
    if not args.vanilla.is_file():
        raise SystemExit(f"no vanilla database at {args.vanilla}")

    # Which game build this was recorded against. The recipe is a diff, so it
    # only strictly describes the database it was taken from - and the game does
    # get patched. Recording it means a later mismatch can be noticed rather
    # than guessed at.
    import sqlite3  # noqa: PLC0415

    probe = sqlite3.connect(f"file:{args.vanilla.as_posix()}?mode=ro", uri=True)
    try:
        row = probe.execute(
            "SELECT value FROM master_const_str WHERE id = 'TITLE_VERSION'"
        ).fetchone()
        game_version = row[0] if row else ""
    except sqlite3.Error:
        game_version = ""
    finally:
        probe.close()
    print(f"baseline       : {args.vanilla}  (game {game_version or 'unknown'})")

    sys.path.insert(0, str(REPO))
    from lid_db_manager import dbdiff  # noqa: PLC0415

    installer = load_pack_installer(pack)
    catalogue = installer.catalog()
    version = str(catalogue["version"])
    print(f"pack v{version}: {len(catalogue['clones'])} clones, "
          f"{len(catalogue['quests'])} quests, {len(catalogue['decals'])} decals, "
          f"{len(catalogue['files'])} packages")

    with tempfile.TemporaryDirectory(prefix="crossover-recipe-") as tmpdir:
        tmp = Path(tmpdir)
        if args.game:
            game = args.game
        else:
            game = tmp / "stub-game"
            stubbed = stub_missing_models(pack, catalogue, game)
            print(f"stubbed {len(stubbed)} base-game model name(s) the pack does not bundle")

        merged = tmp / "merged.db"
        shutil.copy2(args.vanilla, merged)
        installer.modify_database(merged, catalogue, game)
        print("the pack's installer ran against a vanilla copy")

        delta = dbdiff.compare(args.vanilla, merged)
        inserts = sum(len(t.inserts) for t in delta.tables)
        updates = sum(len(t.updates) for t in delta.tables)
        deletes = sum(len(t.deletes) for t in delta.tables)
        print(f"difference: {inserts} inserts, {updates} updates, {deletes} deletes, "
              f"across {len(delta.tables)} tables")
        if deletes:
            raise SystemExit("refusing to record a recipe that deletes rows")

        header = (
            f"LET IT DIE Crossover Content Pack v{version}\n"
            f"Database changes only. The artwork comes from the pack itself.\n"
            f"Recorded from the pack's own installer on {date.today().isoformat()} "
            f"by tools/build_crossover_recipe.py - do not edit by hand."
        )
        sql = dbdiff.to_sql(delta, header=header)

        # Replay it onto a fresh vanilla copy: the recipe has to reproduce the
        # installer's own result exactly, or it is not worth shipping.
        import sqlite3  # noqa: PLC0415

        replay = tmp / "replay.db"
        shutil.copy2(args.vanilla, replay)
        _replay(sql, replay)
        check = dbdiff.compare(replay, merged)
        if not check.empty:
            raise SystemExit(
                "the recorded SQL does not reproduce the installer's result; "
                f"{len(check.tables)} table(s) still differ - nothing written"
            )
        print("verified: replaying the recipe reproduces the installer's result exactly")

        # And again onto a database that already has it. A player can easily
        # have installed this content another way first, and INSERT OR REPLACE
        # is a delete followed by an insert - which several of these tables
        # refuse while something still points at the row. Applying the recipe
        # the way the manager does is the only way to find that out here.
        _replay(sql, replay)
        again = dbdiff.compare(replay, merged)
        if not again.empty:
            raise SystemExit(
                "replaying the recipe a second time changed the result; "
                "it is not safe to re-apply - nothing written"
            )
        print("verified: applying it again over itself changes nothing and still commits")

    RECIPES.mkdir(parents=True, exist_ok=True)
    sql_name = f"crossover-{version}.sql"
    (RECIPES / sql_name).write_text(sql, encoding="utf-8")

    fingerprint = {
        "pack": "LET IT DIE Crossover Content",
        "version": version,
        "catalog_sha256": sha256_of(pack / "catalog.json"),
        "sql": sql_name,
        "recorded": date.today().isoformat(),
        "game_version": game_version,
        "author": args.author,
        "source": args.source,
        "counts": {
            "decals": len(catalogue["decals"]),
            "quests": len(catalogue["quests"]),
            "clones": len(catalogue["clones"]),
            "packages": len(catalogue["files"]),
        },
        "changes": {"inserts": inserts, "updates": updates, "tables": len(delta.tables)},
    }
    (RECIPES / f"crossover-{version}.json").write_text(
        json.dumps(fingerprint, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {RECIPES / sql_name} ({len(sql):,} bytes)")
    print(f"wrote {RECIPES / f'crossover-{version}.json'}")


if __name__ == "__main__":
    main()
