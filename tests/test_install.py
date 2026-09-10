"""Turning dropped files and modded databases into mods."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

from fixtures import build_db, query, write_mod

from lid_db_manager import dbdiff
from lid_db_manager.install import InstallError, inspect, install, safe_folder_name
from lid_db_manager.manager import Manager
from lid_db_manager.mod_loader import scan_mods
from lid_db_manager.paths import AppPaths


def modify(db: Path, *statements: str) -> Path:
    con = sqlite3.connect(str(db))
    for statement in statements:
        con.execute(statement)
    con.commit()
    con.close()
    return db


class DbDiffTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.vanilla = build_db(self.root / "vanilla.db")
        self.modded = build_db(self.root / "modded.db")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_identical_databases_produce_nothing(self) -> None:
        delta = dbdiff.compare(self.vanilla, self.modded)
        self.assertTrue(delta.empty)
        self.assertIn("no differences", delta.summary())

    def test_a_changed_value_is_found_with_its_before_and_after(self) -> None:
        modify(self.modded, "UPDATE master_skill SET buy_money = 1 WHERE id = 'SKL_EXPUP_01'")
        delta = dbdiff.compare(self.vanilla, self.modded)
        self.assertEqual(delta.cell_count, 1)
        update = delta.tables[0].updates[0]
        self.assertEqual(update.key, ("SKL_EXPUP_01",))
        self.assertEqual(update.changes, {"buy_money": 1})
        self.assertEqual(update.before, {"buy_money": 500})

    def test_added_and_removed_rows(self) -> None:
        modify(
            self.modded,
            "INSERT INTO master_skill (id, name, buy_money, val0) VALUES ('SKL_NEW','New',7,7)",
            "DELETE FROM master_skill WHERE id = 'SKL_FREE_01'",
        )
        delta = dbdiff.compare(self.vanilla, self.modded)
        self.assertEqual(delta.insert_count, 1)
        self.assertEqual(delta.delete_count, 1)

    def test_only_the_tables_that_changed_are_reported(self) -> None:
        modify(self.modded, "UPDATE master_body_detail SET price = 1")
        delta = dbdiff.compare(self.vanilla, self.modded)
        self.assertEqual([t.table for t in delta.tables], ["master_body_detail"])

    def test_a_different_schema_is_refused(self) -> None:
        modify(self.modded, "ALTER TABLE master_skill ADD COLUMN extra INTEGER DEFAULT 0")
        with self.assertRaises(dbdiff.IncompatibleDatabase) as caught:
            dbdiff.compare(self.vanilla, self.modded)
        self.assertIn("different game version", str(caught.exception))

    def test_a_missing_table_is_refused(self) -> None:
        modify(self.modded, "DROP TABLE master_skillgacha_odds")
        with self.assertRaises(dbdiff.IncompatibleDatabase):
            dbdiff.compare(self.vanilla, self.modded)

    def test_the_generated_sql_reproduces_the_changes(self) -> None:
        """The whole point: replaying the SQL must land on the modded values."""
        modify(
            self.modded,
            "UPDATE master_skill SET buy_money = 1, val0 = 42 WHERE id = 'SKL_EXPUP_01'",
            "UPDATE master_text SET txt = 'CHANGED' WHERE sct = 'AREA_NAME'",
            "INSERT INTO master_skill (id, name, buy_money, val0) VALUES ('SKL_NEW','N',3,3)",
        )
        delta = dbdiff.compare(self.vanilla, self.modded)
        sql = dbdiff.to_sql(delta)

        replayed = build_db(self.root / "replayed.db")
        con = sqlite3.connect(str(replayed))
        con.executescript(sql)
        con.commit()
        con.close()

        for table in ("master_skill", "master_text"):
            self.assertEqual(
                query(replayed, f"SELECT * FROM {table} ORDER BY id"),
                query(self.modded, f"SELECT * FROM {table} ORDER BY id"),
                f"{table} did not round-trip through the generated SQL",
            )

    def test_text_with_quotes_and_blobs_survives_the_round_trip(self) -> None:
        modify(
            self.modded,
            "UPDATE master_text SET txt = 'it''s \"quoted\" & odd' WHERE id = 'TXT_OTHER'",
        )
        delta = dbdiff.compare(self.vanilla, self.modded)
        replayed = build_db(self.root / "replayed2.db")
        con = sqlite3.connect(str(replayed))
        con.executescript(dbdiff.to_sql(delta))
        con.commit()
        con.close()
        self.assertEqual(
            query(replayed, "SELECT txt FROM master_text WHERE id = 'TXT_OTHER'"),
            [('it\'s "quoted" & odd',)],
        )


class InstallTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.paths = AppPaths(self.root).ensure()
        self.mods = self.paths.mods_dir
        self.vanilla = build_db(self.root / "vanilla.db")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_a_dropped_sql_file_becomes_a_mod(self) -> None:
        sql = self.root / "FloorText.sql"
        sql.write_text("UPDATE master_skill SET buy_money = 1;", encoding="utf-8")

        candidate = inspect(sql)
        self.assertEqual(candidate.kind, "sql")
        self.assertEqual(candidate.suggested_name, "Floor Text")

        folder = install(candidate, self.mods, "Floor Text", description="renames things")
        mod = scan_mods(self.mods).get(folder.name)
        self.assertEqual(mod.name, "Floor Text")
        self.assertEqual(mod.description, "renames things")
        self.assertEqual(mod.patches[0].type, "raw_sql_file")

    def test_a_blank_description_gets_a_usable_default(self) -> None:
        sql = self.root / "thing.sql"
        sql.write_text("UPDATE master_skill SET buy_money = 1;", encoding="utf-8")
        folder = install(inspect(sql), self.mods, "Thing")
        mod = scan_mods(self.mods).get(folder.name)
        self.assertTrue(mod.description, "description must never be empty - it fails to load")
        self.assertIn("Imported from", mod.description)

    def test_a_dropped_mod_folder_is_copied_in(self) -> None:
        source = write_mod(
            self.root / "elsewhere",
            "handmade",
            {"patches": [{"type": "raw_sql", "sql": "UPDATE master_skill SET val0 = 1"}]},
        )
        folder = install(inspect(source), self.mods, "handmade")
        self.assertTrue((folder / "mod.json").is_file())
        self.assertIsNotNone(scan_mods(self.mods).get("handmade"))

    def test_a_zip_is_unpacked_and_unwrapped(self) -> None:
        source = write_mod(
            self.root / "packed",
            "zipped-mod",
            {"patches": [{"type": "raw_sql", "sql": "UPDATE master_skill SET val0 = 2"}]},
        )
        archive = self.root / "zipped-mod.zip"
        with zipfile.ZipFile(archive, "w") as z:
            z.write(source / "mod.json", "zipped-mod/mod.json")

        folder = install(inspect(archive), self.mods, "Zipped Mod")
        self.assertTrue((folder / "mod.json").is_file())

    def test_a_zip_with_an_escaping_path_is_refused(self) -> None:
        archive = self.root / "evil.zip"
        with zipfile.ZipFile(archive, "w") as z:
            z.writestr("../escape.sql", "UPDATE master_skill SET val0 = 1;")
        with self.assertRaises(InstallError) as caught:
            install(inspect(archive), self.mods, "Evil")
        self.assertIn("unsafe path", str(caught.exception))

    def test_an_unknown_file_type_says_what_is_accepted(self) -> None:
        junk = self.root / "notes.txt"
        junk.write_text("hello", encoding="utf-8")
        with self.assertRaises(InstallError) as caught:
            inspect(junk)
        self.assertIn(".sql", str(caught.exception))

    def test_installing_over_an_existing_name_is_refused_unless_asked(self) -> None:
        sql = self.root / "dupe.sql"
        sql.write_text("UPDATE master_skill SET buy_money = 1;", encoding="utf-8")
        install(inspect(sql), self.mods, "Dupe")
        with self.assertRaises(InstallError):
            install(inspect(sql), self.mods, "Dupe")
        install(inspect(sql), self.mods, "Dupe", overwrite=True)  # allowed when asked

    def test_folder_names_are_made_safe(self) -> None:
        self.assertEqual(safe_folder_name("Floor: Material/Names?"), "Floor MaterialNames")
        self.assertTrue(safe_folder_name("_hidden").startswith("mod"))
        self.assertTrue(safe_folder_name("...").startswith("mod"))


class ImportModdedDatabaseTests(unittest.TestCase):
    """The headline case: someone hands you a reworked masters.db."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.paths = AppPaths(self.root).ensure()
        self.db = build_db(self.root / "game" / "masters.db")
        self.manager = Manager(self.paths)
        # Picking the database is what keeps the vanilla reference.
        self.manager.set_db_path(self.db)

        self.rework = build_db(self.root / "rework" / "masters.db")
        modify(
            self.rework,
            "UPDATE master_skill SET buy_money = 1",
            "UPDATE master_body_detail SET price = 2",
            "UPDATE master_text SET txt = 'REWORKED' WHERE sct = 'AREA_NAME'",
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_a_reworked_database_becomes_a_toggleable_mod(self) -> None:
        candidate = self.manager.inspect_install(self.rework)
        self.assertEqual(candidate.kind, "database")
        self.assertIn("changed value", candidate.note)

        mod = self.manager.install(candidate, "Big Rework", description="someone else's work")
        self.assertIsNotNone(mod)
        self.assertEqual(mod.name, "Big Rework")

        # It arrives switched off.
        self.assertFalse(self.manager.state.is_enabled(mod.id))

        # And applying it reproduces the rework.
        self.manager.set_enabled(mod.id, True)
        report = self.manager.save_mod_list()
        self.assertTrue(report.ok, report.error)
        self.assertEqual(query(self.db, "SELECT DISTINCT buy_money FROM master_skill"), [(1,)])
        self.assertEqual(query(self.db, "SELECT DISTINCT price FROM master_body_detail"), [(2,)])

    def test_the_generated_sql_records_which_vanilla_it_was_built_against(self) -> None:
        mod = self.manager.install(self.manager.inspect_install(self.rework), "Rework")
        sql = (mod.folder / "changes.sql").read_text(encoding="utf-8")
        self.assertIn("vanilla sha256", sql)
        self.assertIn("Generated from", sql)

    def test_a_database_from_a_different_game_version_is_refused(self) -> None:
        modify(self.rework, "ALTER TABLE master_skill ADD COLUMN new_thing INTEGER DEFAULT 0")
        with self.assertRaises(dbdiff.IncompatibleDatabase):
            self.manager.inspect_install(self.rework)

    def test_an_unchanged_database_is_refused_rather_than_making_an_empty_mod(self) -> None:
        untouched = build_db(self.root / "same" / "masters.db")
        with self.assertRaises(InstallError) as caught:
            self.manager.inspect_install(untouched)
        self.assertIn("identical", str(caught.exception))

    def test_without_a_vanilla_copy_it_explains_rather_than_guessing(self) -> None:
        """Picking a database now keeps a copy, so this only happens if it is
        deleted - but then the message still has to explain, not guess."""
        fresh = Manager(AppPaths(self.root / "fresh").ensure())
        db = build_db(self.root / "fresh" / "masters.db")
        fresh.set_db_path(db)
        Path(str(db) + ".original").unlink()
        self.assertIsNone(fresh.vanilla_path)

        with self.assertRaises(InstallError) as caught:
            fresh.inspect_install(self.rework)
        self.assertIn("masters.db.original", str(caught.exception))

    def test_a_small_mod_can_override_the_big_one_for_one_value(self) -> None:
        """The load-order case: big rework, then a small mod that wins."""
        big = self.manager.install(self.manager.inspect_install(self.rework), "Big Rework")
        write_mod(
            self.paths.mods_dir,
            "one-tweak",
            {"patches": [{"type": "update_set", "table": "master_skill",
                          "set": {"buy_money": 777}, "where": "id = 'SKL_EXPUP_01'"}]},
        )
        self.manager.rescan()
        self.manager.set_enabled(big.id, True)
        self.manager.set_enabled("one-tweak", True)  # enabled second, so it applies last

        self.assertTrue(self.manager.save_mod_list().ok)
        values = dict(query(self.db, "SELECT id, buy_money FROM master_skill"))
        self.assertEqual(values["SKL_EXPUP_01"], 777, "the small mod should win")
        self.assertEqual(values["SKL_POWER_01"], 1, "the rework should still apply elsewhere")


if __name__ == "__main__":
    unittest.main()
