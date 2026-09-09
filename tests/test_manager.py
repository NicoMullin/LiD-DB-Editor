"""The controller both front ends drive, plus a sanity check on the shipped mods."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from fixtures import build_db, query, write_mod

from lid_db_manager.manager import MOD_APPLIED, MOD_DISABLED, MOD_PENDING, Manager
from lid_db_manager.mod_loader import scan_mods
from lid_db_manager.paths import AppPaths

PROJECT_ROOT = Path(__file__).resolve().parent.parent

COST_MOD = {
    "patches": [
        {
            "type": "update_set",
            "table": "master_skill",
            "set": {"buy_money": 1},
            "where": "buy_money > 1",
        }
    ]
}


class ManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.paths = AppPaths(self.root).ensure()
        self.db = build_db(self.root / "game" / "masters.db")
        write_mod(self.paths.mods_dir, "cost", COST_MOD)
        self.manager = Manager(self.paths)
        self.manager.set_db_path(self.db)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_save_mod_list_backs_up_applies_and_stamps(self) -> None:
        self.manager.set_enabled("cost", True)
        report = self.manager.save_mod_list()

        self.assertTrue(report.ok, report.error)
        self.assertEqual(query(self.db, "SELECT DISTINCT buy_money FROM master_skill"), [(1,)])
        self.assertEqual(len(self.manager.dated_backups()), 1)
        self.assertTrue((self.db.parent / "masters.db.backup").is_file())
        self.assertTrue((self.db.parent / "masters.db.original").is_file())
        self.assertEqual(self.manager.db_status().state, "ok")
        self.assertEqual(self.manager.mod_status("cost"), MOD_APPLIED)

    def test_state_survives_a_restart(self) -> None:
        self.manager.set_enabled("cost", True)
        self.manager.save_mod_list()

        reopened = Manager(self.paths)
        self.assertEqual(reopened.state.enabled_mods, ["cost"])
        self.assertEqual(reopened.db_status().state, "ok")
        self.assertEqual(reopened.mod_status("cost"), MOD_APPLIED)

    def test_a_replaced_database_goes_stale_and_auto_re_applies(self) -> None:
        self.manager.set_enabled("cost", True)
        self.manager.save_mod_list()

        build_db(self.db)  # the game pushes a fresh copy
        status = self.manager.db_status()
        self.assertTrue(status.changed)
        self.assertEqual(self.manager.mod_status("cost"), MOD_PENDING)

        report = self.manager.on_db_changed(status)
        self.assertIsNotNone(report)
        self.assertTrue(report.ok)
        self.assertEqual(query(self.db, "SELECT DISTINCT buy_money FROM master_skill"), [(1,)])
        self.assertEqual(self.manager.db_status().state, "ok")

    def test_auto_re_apply_can_be_turned_off(self) -> None:
        self.manager.set_enabled("cost", True)
        self.manager.save_mod_list()
        self.manager.state.settings.auto_reapply = False

        build_db(self.db)
        self.assertIsNone(self.manager.on_db_changed(self.manager.db_status()))
        self.assertNotEqual(query(self.db, "SELECT DISTINCT buy_money FROM master_skill"), [(1,)])

    def test_re_apply_all_does_not_take_another_backup(self) -> None:
        self.manager.set_enabled("cost", True)
        self.manager.save_mod_list()
        self.manager.reapply_all()
        self.assertEqual(len(self.manager.dated_backups()), 1)

    def test_revert_disables_the_mod_and_restores_the_rows(self) -> None:
        self.manager.set_enabled("cost", True)
        self.manager.save_mod_list()

        results = self.manager.revert(["cost"])
        self.assertTrue(results[0].ok)
        self.assertEqual(self.manager.mod_status("cost"), MOD_DISABLED)
        self.assertEqual(
            sorted(query(self.db, "SELECT buy_money FROM master_skill")),
            [(1,), (500,), (1200,), (5000,)],
        )

    def test_a_deleted_mod_folder_is_detected(self) -> None:
        self.manager.set_enabled("cost", True)
        self.manager.save_mod_list()
        shutil.rmtree(self.paths.mods_dir / "cost")
        self.manager.rescan()

        self.assertEqual(self.manager.deleted_mods(), ["cost"])
        results = self.manager.revert(["cost"])
        self.assertTrue(results[0].ok)
        self.assertEqual(self.manager.deleted_mods(), [])

    def test_forget_drops_bookkeeping_without_touching_the_database(self) -> None:
        self.manager.set_enabled("cost", True)
        self.manager.save_mod_list()
        shutil.rmtree(self.paths.mods_dir / "cost")
        self.manager.rescan()

        self.manager.forget(["cost"])
        self.assertEqual(self.manager.deleted_mods(), [])
        self.assertEqual(query(self.db, "SELECT DISTINCT buy_money FROM master_skill"), [(1,)])

    def test_modpacks_round_trip(self) -> None:
        self.manager.set_enabled("cost", True)
        self.manager.save_modpack("everything")
        self.manager.set_enabled("cost", False)
        self.assertEqual(self.manager.load_modpack("everything"), ["cost"])

    def test_pointing_at_a_different_database_clears_the_stamp(self) -> None:
        self.manager.set_enabled("cost", True)
        self.manager.save_mod_list()
        other = build_db(self.root / "game" / "other.db")
        self.manager.set_db_path(other)
        self.assertEqual(self.manager.db_status().state, "unstamped")

    def test_applying_with_no_database_reports_instead_of_crashing(self) -> None:
        manager = Manager(AppPaths(self.root / "empty").ensure())
        report = manager.save_mod_list()
        self.assertFalse(report.ok)
        self.assertIn("no database", report.error)

    def test_restoring_a_backup_resets_the_applied_record(self) -> None:
        self.manager.set_enabled("cost", True)
        self.manager.save_mod_list()
        self.manager.restore_backup(self.manager.dated_backups()[0])
        self.assertEqual(self.manager.state.applied, {})
        self.assertEqual(self.manager.db_status().state, "ok")

    def test_the_backup_list_includes_the_files_beside_the_database(self) -> None:
        self.manager.set_enabled("cost", True)
        self.manager.save_mod_list()
        kinds = [entry.kind for entry in self.manager.backups()]
        self.assertEqual(kinds, ["original", "rolling", "dated"])

    def test_restoring_the_original_undoes_every_save(self) -> None:
        self.manager.set_enabled("cost", True)
        self.manager.save_mod_list()
        self.manager.save_mod_list()  # second save: rolling now holds modded data

        original = self.manager.backups()[0]
        self.assertEqual(original.kind, "original")
        self.manager.restore_backup(original)
        self.assertEqual(
            sorted(query(self.db, "SELECT buy_money FROM master_skill")),
            [(1,), (500,), (1200,), (5000,)],
        )


class ShippedModTests(unittest.TestCase):
    """Every mod in the repo's mods/ folder must at least parse and validate."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = build_db(Path(self._tmp.name) / "masters.db")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    SHIPPED = {
        "revive-cost-1kc",
        "body-prices-1kc",
        "nitro-boost-100000pct",
        "nitro-boost-text",
        "Floor Material Names",
        "Shop Always Appears",
    }

    # Not a real mod: it exists to collide with Floor Material Names so the
    # conflict warning can be seen. Excluded from the "nothing conflicts" check
    # below, and given a test of its own.
    DIAGNOSTIC = {"overlap-test"}

    def _real_mods(self):
        return [m for m in scan_mods(PROJECT_ROOT / "mods").mods if m.id not in self.DIAGNOSTIC]

    def test_every_shipped_mod_loads(self) -> None:
        result = scan_mods(PROJECT_ROOT / "mods")
        found = {mod.id for mod in result.mods}
        self.assertEqual(result.failures, [], f"mods failed to load: {result.failures}")
        self.assertEqual(self.SHIPPED - found, set(), "a documented mod is missing")
        # The diagnostic mod is optional - it is not published - but anything
        # else turning up here is a mod nobody has written a test for yet.
        self.assertEqual(found - self.SHIPPED - self.DIAGNOSTIC, set(), "unexpected mod folder")

    def test_every_shipped_mod_validates_against_the_test_schema(self) -> None:
        from lid_db_manager.validator import validate

        mods = scan_mods(PROJECT_ROOT / "mods").mods
        report = validate(self.db, mods)
        errors = {r.mod_id: r.errors for r in report.results if r.errors}
        self.assertEqual(errors, {})

    def test_the_templates_are_hidden_from_the_mod_list(self) -> None:
        result = scan_mods(PROJECT_ROOT / "mods")
        skipped = {entry.folder for entry in result.skipped}
        self.assertEqual(skipped & {"_example", "_example-sql"}, {"_example", "_example-sql"})
        self.assertFalse({mod.id for mod in result.mods} & skipped)

    def _analyze(self, mods, with_db: bool = True):
        from lid_db_manager.conflict import analyze
        from lid_db_manager.sqlutil import connect

        if not with_db:
            return analyze(mods, {mod.id for mod in mods})
        con = connect(self.db, read_only=True)
        try:
            return analyze(mods, {mod.id for mod in mods}, con)
        finally:
            con.close()

    def test_the_real_mods_do_not_conflict_with_each_other(self) -> None:
        """Floor Material Names and nitro-boost-text share a table, not a row."""
        report = self._analyze(self._real_mods())
        self.assertEqual([c.message() for c in report.conflicts], [])
        self.assertEqual([r.message() for r in report.missing_requirements], [])

    def test_the_diagnostic_mod_really_does_conflict(self) -> None:
        """The other half: a genuine row collision must still be reported.

        This is what stops the row-level check from 'passing' by simply never
        warning about anything. Skipped where mods/overlap-test has been
        deleted, since it is a local demo rather than a published mod - the
        same guarantee is covered synthetically in test_conflict.py.
        """
        mods = scan_mods(PROJECT_ROOT / "mods").mods
        if not any(mod.id in self.DIAGNOSTIC for mod in mods):
            self.skipTest("mods/overlap-test is not installed")
        report = self._analyze(mods)
        messages = [c.message() for c in report.conflicts]
        self.assertEqual(len(messages), 1, messages)
        self.assertIn("overlap-test", messages[0])
        self.assertIn("Floor Material Names", messages[0])
        self.assertIn("1 shared row(s)", messages[0])

    def test_without_a_connection_everything_on_one_table_warns(self) -> None:
        """The coarse fallback is still there when there is no database."""
        report = self._analyze(self._real_mods(), with_db=False)
        self.assertTrue(
            any("master_text" in c.detail for c in report.conflicts),
            "table-level warning should survive when rows cannot be checked",
        )


if __name__ == "__main__":
    unittest.main()
