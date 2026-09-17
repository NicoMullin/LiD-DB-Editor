"""The manager's note inside masters.db, and finding mods at the values they have.

Two things a database has to be able to answer after a program update, a copy
to another PC, or a reinstall: which mods are in it, and at which values. The
note answers it when it is there; the cells answer it when it is not. Neither
is ever believed without the cells agreeing.
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fixtures import build_db, query, write_mod

from lid_db_manager import adopt, db_record, dbdiff, migrations
from lid_db_manager.manager import Manager
from lid_db_manager.paths import AppPaths

MULTIPLIER = {"id": "multiplier", "label": "Multiplier", "type": "integer",
              "default": 2, "min": 1, "max": 100, "unit": "x"}

SKILL_PRICES = {
    "name": "Skill Prices",
    "version": "1.0.0",
    "settings": [MULTIPLIER],
    "patches": [{"type": "raw_sql",
                 "sql": "UPDATE master_skill SET buy_money = buy_money * {{multiplier}} "
                        "WHERE buy_money > 1;"}],
}

REVIVE = {
    "name": "Cheap Revive",
    "version": "1.0.0",
    "patches": [{"type": "update_set", "table": "master_shop_product_price",
                 "set": {"price": 1}, "where": "id LIKE 'PRD_CONTINUE%'"}],
}


def run_sql(db: Path, *statements: str) -> None:
    con = sqlite3.connect(str(db))
    try:
        for statement in statements:
            con.execute(statement)
        con.commit()
    finally:
        con.close()


def has_note(db: Path) -> bool:
    return bool(query(db, "SELECT name FROM sqlite_master WHERE name = ?", (db_record.TABLE,)))


class NoteTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db = build_db(self.root / "masters.db")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write(self, mods) -> None:
        con = sqlite3.connect(str(self.db))
        try:
            db_record.write(con, mods)
            con.commit()
        finally:
            con.close()

    def test_what_is_written_reads_back_in_order(self) -> None:
        self._write([
            db_record.RecordedMod("b-mod", "B", "1.2.0", {"multiplier": 7}),
            db_record.RecordedMod("a-mod", "A", "1.0.0"),
        ])
        record = db_record.read(self.db)
        self.assertEqual([m.mod_id for m in record.mods], ["b-mod", "a-mod"])
        self.assertEqual(record.get("b-mod").values, {"multiplier": 7})
        self.assertEqual(record.get("b-mod").version, "1.2.0")

    def test_a_database_without_one_has_none(self) -> None:
        self.assertIsNone(db_record.read(self.db))

    def test_no_mods_means_no_table(self) -> None:
        self._write([db_record.RecordedMod("a-mod")])
        self._write([])
        self.assertFalse(has_note(self.db))

    def test_anything_but_a_number_in_the_values_is_dropped(self) -> None:
        self._write([db_record.RecordedMod("a-mod")])
        run_sql(self.db, f"UPDATE {db_record.TABLE} SET mod_values = "
                         "'{\"multiplier\": \"1; DROP TABLE master_skill\", \"bonus\": 5}'")
        self.assertEqual(db_record.read(self.db).get("a-mod").values, {"bonus": 5})

    def test_values_that_are_not_even_json_are_ignored(self) -> None:
        self._write([db_record.RecordedMod("a-mod")])
        run_sql(self.db, f"UPDATE {db_record.TABLE} SET mod_values = 'nonsense'")
        self.assertEqual(db_record.read(self.db).get("a-mod").values, {})

    def test_a_note_from_a_newer_manager_is_left_unread(self) -> None:
        self._write([db_record.RecordedMod("a-mod")])
        run_sql(self.db, f"UPDATE {db_record.TABLE} SET format = 99")
        record = db_record.read(self.db)
        self.assertEqual(record.mods, [])
        self.assertIn("newer version", record.unreadable)

    def test_the_note_is_not_a_difference_from_stock(self) -> None:
        clean = build_db(self.root / "clean.db")
        self._write([db_record.RecordedMod("a-mod")])
        self.assertTrue(dbdiff.compare(clean, self.db).empty)
        self.assertEqual(dbdiff.check_compatible(clean, self.db), [])

    def test_a_retired_mod_is_read_as_its_replacement(self) -> None:
        record = db_record.Record(mods=[db_record.RecordedMod("weapon-durability-5x")])
        migrated = migrations.migrate_record(record, {"weapon-durability"})
        self.assertEqual(migrated.mods[0].mod_id, "weapon-durability")
        self.assertEqual(migrated.mods[0].values, {"multiplier": 5})
        self.assertEqual(record.mods[0].mod_id, "weapon-durability-5x", "the original changed")


class ManagerNoteTests(unittest.TestCase):
    """The note follows every save, revert and restore."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.paths = AppPaths(self.root).ensure()
        self.db = build_db(self.root / "game" / "masters.db")
        write_mod(self.paths.mods_dir, "skill-prices", SKILL_PRICES)
        write_mod(self.paths.mods_dir, "revive", REVIVE)
        self.manager = Manager(self.paths)
        self.manager.set_db_path(self.db)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _save(self, *mod_ids: str):
        for mod_id in mod_ids:
            self.manager.set_enabled(mod_id, True)
        report = self.manager.save_mod_list()
        self.assertTrue(report.ok, report.error)
        return report

    def test_saving_writes_the_mods_and_their_values_in_load_order(self) -> None:
        self.manager.set_mod_setting("skill-prices", "multiplier", 7)
        self._save("revive", "skill-prices")
        record = db_record.read(self.db)
        self.assertEqual([m.mod_id for m in record.mods], ["revive", "skill-prices"])
        self.assertEqual(record.get("skill-prices").values, {"multiplier": 7})
        self.assertEqual(record.get("revive").values, {})

    def test_the_values_applied_are_remembered_too(self) -> None:
        self._save("skill-prices")
        self.assertEqual(self.manager.state.applied["skill-prices"].values, {"multiplier": 2})

    def test_reverting_one_mod_takes_it_off_the_note(self) -> None:
        self._save("revive", "skill-prices")
        self.manager.revert(["revive"])
        self.assertEqual([m.mod_id for m in db_record.read(self.db).mods], ["skill-prices"])

    def test_reverting_everything_leaves_no_note(self) -> None:
        self._save("revive", "skill-prices")
        self.manager.revert(["revive", "skill-prices"])
        self.assertFalse(has_note(self.db))

    def test_switching_a_mod_off_and_saving_updates_the_note(self) -> None:
        self._save("revive", "skill-prices")
        self.manager.set_enabled("revive", False)
        self._save()
        self.assertEqual([m.mod_id for m in db_record.read(self.db).mods], ["skill-prices"])

    def test_restoring_the_original_has_no_note(self) -> None:
        self._save("revive")
        self.manager.restore_backup(self.db.with_name("masters.db.original"))
        self.assertFalse(has_note(self.db))

    def test_a_newer_version_of_a_mod_replaces_the_old_one_cleanly(self) -> None:
        self._save("revive")
        # Version 1.1.0 only cheapens the first revive.
        newer = dict(REVIVE, version="1.1.0")
        newer["patches"] = [dict(REVIVE["patches"][0], where="id = 'PRD_CONTINUE_G1'")]
        write_mod(self.paths.mods_dir, "revive", newer)
        self.manager.rescan()
        self._save()
        prices = [r[0] for r in query(
            self.db, "SELECT price FROM master_shop_product_price "
                     "WHERE id LIKE 'PRD_CONTINUE%' ORDER BY id")]
        self.assertEqual(prices, [1, 10000], "the row version 1.1.0 no longer changes stayed changed")
        self.assertEqual(db_record.read(self.db).get("revive").version, "1.1.0")


class FindingValuesTests(unittest.TestCase):
    """The adoption scan, for a mod whose values the player chose."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.paths = AppPaths(self.root).ensure()
        library = self.root / "LiD Vanilla DB" / "5.0.3.0"
        library.mkdir(parents=True, exist_ok=True)
        self.clean = build_db(library / "masters.db")
        run_sql(
            self.clean,
            'CREATE TABLE master_const_str ("id" CHARACTER(64) NOT NULL, '
            '"value" TEXT NOT NULL, PRIMARY KEY ("id"))',
            "INSERT INTO master_const_str (id, value) VALUES ('TITLE_VERSION', '5.0.3.0.0 - 1.87')",
        )
        self.theirs = self.root / "game" / "masters.db"
        self.theirs.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.clean, self.theirs)
        write_mod(self.paths.mods_dir, "skill-prices", SKILL_PRICES)
        write_mod(self.paths.mods_dir, "revive", REVIVE)
        self.manager = Manager(self.paths)
        self.manager.set_db_path(self.theirs)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _match(self, report, mod_id):
        for match in report.matches:
            if match.mod_id == mod_id:
                return match
        self.fail(f"{mod_id} was not found at all: {report.summary()}")

    def test_the_values_are_worked_out_from_the_cells(self) -> None:
        run_sql(self.theirs, "UPDATE master_skill SET buy_money = buy_money * 7 WHERE buy_money > 1")
        report = self.manager.adopt_scan()
        match = self._match(report, "skill-prices")
        self.assertTrue(match.applied, match.summary())
        self.assertEqual(match.values, {"multiplier": 7})
        self.assertEqual(match.values_from, adopt.VALUES_FROM_WORKED_OUT)
        self.assertIn("x7", match.summary())
        self.assertFalse(report.has_leftover, report.leftover.summary())

    def test_the_players_own_choice_is_tried_first(self) -> None:
        run_sql(self.theirs, "UPDATE master_skill SET buy_money = buy_money * 9 WHERE buy_money > 1")
        self.manager.set_mod_setting("skill-prices", "multiplier", 9)
        match = self._match(self.manager.adopt_scan(), "skill-prices")
        self.assertTrue(match.applied)
        self.assertEqual(match.values_from, adopt.VALUES_FROM_CHOSEN)

    def test_the_note_gives_the_values(self) -> None:
        self.manager.set_mod_setting("skill-prices", "multiplier", 7)
        self.manager.set_enabled("skill-prices", True)
        self.assertTrue(self.manager.save_mod_list().ok)
        # A reinstall: this manager remembers nothing, the database does.
        self.manager.reset_mod_settings("skill-prices")
        match = self._match(self.manager.adopt_scan(), "skill-prices")
        self.assertTrue(match.applied)
        self.assertEqual(match.values, {"multiplier": 7})
        self.assertEqual(match.values_from, adopt.VALUES_FROM_RECORD)
        self.assertTrue(match.in_record)

    def test_a_note_the_cells_disagree_with_is_not_believed(self) -> None:
        self.manager.set_mod_setting("skill-prices", "multiplier", 7)
        self.manager.set_enabled("skill-prices", True)
        self.assertTrue(self.manager.save_mod_list().ok)
        run_sql(self.theirs, f"UPDATE {db_record.TABLE} SET mod_values = '{{\"multiplier\": 3}}'")
        match = self._match(self.manager.adopt_scan(), "skill-prices")
        self.assertTrue(match.applied)
        self.assertEqual(match.values, {"multiplier": 7}, "the note was taken at its word")

    def test_values_no_single_setting_gives_are_not_guessed(self) -> None:
        run_sql(
            self.theirs,
            "UPDATE master_skill SET buy_money = buy_money * 7 WHERE id = 'SKL_EXPUP_01'",
            "UPDATE master_skill SET buy_money = buy_money * 3 WHERE id = 'SKL_EXPUP_02'",
            "UPDATE master_skill SET buy_money = buy_money * 5 WHERE id = 'SKL_POWER_01'",
        )
        report = self.manager.adopt_scan()
        match = self._match(report, "skill-prices")
        self.assertFalse(match.applied)
        self.assertTrue(match.values_unknown)
        self.assertFalse(match.partial)
        self.assertEqual(report.unknown_values, [match])
        # Nothing is claimed for it, so the changes can still be kept as they are.
        self.assertTrue(report.has_leftover)

    def test_a_listed_mod_that_is_not_installed_is_named(self) -> None:
        self.manager.set_enabled("revive", True)
        self.assertTrue(self.manager.save_mod_list().ok)
        run_sql(self.theirs, f"INSERT INTO {db_record.TABLE} (position, mod_id, name, format) "
                             "VALUES (9, 'gone-mod', 'Gone Mod', 1)")
        report = self.manager.adopt_scan()
        self.assertEqual([e.mod_id for e in report.not_installed], ["gone-mod"])

    def test_a_different_version_in_the_database_is_said(self) -> None:
        self.manager.set_enabled("revive", True)
        self.assertTrue(self.manager.save_mod_list().ok)
        write_mod(self.paths.mods_dir, "revive", dict(REVIVE, version="2.0.0"))
        self.manager.rescan()
        match = self._match(self.manager.adopt_scan(), "revive")
        self.assertTrue(match.version_changed)
        self.assertIn("1.0.0", match.summary())

    def test_a_rebuild_keeps_the_values_that_were_found(self) -> None:
        run_sql(self.theirs, "UPDATE master_skill SET buy_money = buy_money * 7 WHERE buy_money > 1")
        match = self._match(self.manager.adopt_scan(), "skill-prices")
        report = self.manager.adopt_rebuild(["skill-prices"], {"skill-prices": match.values})
        self.assertTrue(report.ok, report.error)
        self.assertEqual(self.manager.state.mod_settings, {"skill-prices": {"multiplier": 7}})
        self.assertEqual(query(self.theirs, "SELECT buy_money FROM master_skill "
                                            "WHERE id = 'SKL_POWER_01'")[0][0], 8400)
        self.assertEqual(db_record.read(self.theirs).get("skill-prices").values, {"multiplier": 7})

    def test_the_dialog_hands_the_found_values_to_the_rebuild(self) -> None:
        try:
            from PySide6.QtWidgets import QApplication
        except ImportError:
            self.skipTest("PySide6 is not installed")
        from lid_db_manager.ui.adopt_dialog import AdoptDialog

        QApplication.instance() or QApplication([])
        run_sql(self.theirs, "UPDATE master_skill SET buy_money = buy_money * 7 WHERE buy_money > 1")
        dialog = AdoptDialog(self.manager.adopt_scan())
        try:
            choice = dialog.choice()
        finally:
            dialog.deleteLater()
        self.assertEqual(choice.mod_ids, ["skill-prices"])
        self.assertEqual(choice.values, {"skill-prices": {"multiplier": 7}})


if __name__ == "__main__":
    unittest.main()
