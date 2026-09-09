"""state.json, backups and the watchdog."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

from fixtures import build_db

from lid_db_manager import backup as backup_module
from lid_db_manager.state import State
from lid_db_manager.watchdog import (
    STATUS_MISSING,
    STATUS_OK,
    STATUS_STALE,
    STATUS_UNSTAMPED,
    DbWatcher,
)


class StateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.path = self.root / "state.json"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_missing_file_gives_empty_defaults(self) -> None:
        state = State.load(self.path)
        self.assertEqual(state.enabled_mods, [])
        self.assertTrue(state.settings.auto_reapply)

    def test_round_trip(self) -> None:
        state = State.load(self.path)
        state.set_enabled("a-cost-mod", True)
        state.set_enabled("b-price-mod", True)
        state.save_modpack("all-cheats")
        state.settings.dark_mode = False
        state.save()

        reloaded = State.load(self.path)
        self.assertEqual(reloaded.enabled_mods, ["a-cost-mod", "b-price-mod"])
        self.assertEqual(reloaded.modpacks["all-cheats"], ["a-cost-mod", "b-price-mod"])
        self.assertFalse(reloaded.settings.dark_mode)

    def test_enabling_twice_does_not_duplicate(self) -> None:
        state = State.load(self.path)
        state.set_enabled("a", True)
        state.set_enabled("a", True)
        self.assertEqual(state.enabled_mods, ["a"])

    def test_loading_a_modpack_replaces_the_enabled_list(self) -> None:
        state = State.load(self.path)
        state.enabled_mods = ["a", "b"]
        state.save_modpack("two")
        state.enabled_mods = ["c"]
        self.assertEqual(state.load_modpack("two"), ["a", "b"])
        with self.assertRaises(KeyError):
            state.load_modpack("nope")

    def test_prune_missing_drops_uninstalled_ids(self) -> None:
        state = State.load(self.path)
        state.enabled_mods = ["here", "gone"]
        self.assertEqual(state.prune_missing({"here"}), ["gone"])
        self.assertEqual(state.enabled_mods, ["here"])

    def test_a_corrupt_state_file_is_set_aside_not_fatal(self) -> None:
        self.path.write_text("{not json", encoding="utf-8")
        state = State.load(self.path)
        self.assertEqual(state.enabled_mods, [])
        self.assertTrue(self.path.with_suffix(".json.corrupt").is_file())

    def test_stamp_records_the_current_hash(self) -> None:
        db = build_db(self.root / "masters.db")
        state = State.load(self.path)
        state.stamp_db(db)
        self.assertEqual(len(state.db_sha256_at_last_save), 64)
        self.assertEqual(state.db_mtime_at_last_save, os.path.getmtime(db))


class BackupTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db = build_db(self.root / "masters.db")
        self.backups = self.root / "backups"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _edit_db(self) -> None:
        import sqlite3

        con = sqlite3.connect(str(self.db))
        con.execute("DELETE FROM master_skill")
        con.commit()
        con.close()

    def _skill_count(self) -> int:
        import sqlite3

        con = sqlite3.connect(str(self.db))
        try:
            return con.execute("SELECT COUNT(*) FROM master_skill").fetchone()[0]
        finally:
            con.close()

    def test_take_backups_writes_original_rolling_and_dated_copies(self) -> None:
        result = backup_module.take_backups(self.db, self.backups, keep=5)
        self.assertEqual(result.original.name, "masters.db.original")
        self.assertEqual(result.rolling.name, "masters.db.backup")
        self.assertTrue(result.original.is_file())
        self.assertTrue(result.rolling.is_file())
        self.assertTrue(result.dated.is_file())

    def test_the_original_is_written_once_and_never_overwritten(self) -> None:
        first = backup_module.take_backups(self.db, self.backups)
        self.assertIsNotNone(first.original)

        # Second save, with the database now "modded".
        self._edit_db()
        second = backup_module.take_backups(self.db, self.backups)
        self.assertIsNone(second.original, "the original must not be rewritten")

        # The rolling backup followed the edit; the original did not.
        backup_module.restore_backup(first.original, self.db)
        self.assertEqual(self._skill_count(), 4)

    def test_the_original_never_rotates_away(self) -> None:
        backup_module.take_backups(self.db, self.backups)
        for index in range(8):
            (self.backups / f"2026-09-0{index}_00-00-00.db").write_bytes(b"x")
        backup_module.rotate(self.backups, keep=1)
        self.assertTrue(backup_module.original_backup_path(self.db).is_file())

    def test_the_listing_finds_the_files_next_to_the_database(self) -> None:
        backup_module.take_backups(self.db, self.backups)

        # Without the db path, only backups/ is searched - that was the bug.
        self.assertEqual(len(backup_module.list_backups(self.backups)), 1)

        entries = backup_module.list_backups(self.backups, self.db)
        self.assertEqual([e.kind for e in entries], ["original", "rolling", "dated"])
        self.assertEqual(entries[0].name, "masters.db.original")
        self.assertIn("ORIGINAL", entries[0].label())

    def test_two_saves_in_the_same_second_do_not_collide(self) -> None:
        first = backup_module.take_backups(self.db, self.backups)
        second = backup_module.take_backups(self.db, self.backups)
        self.assertNotEqual(first.dated, second.dated)
        self.assertEqual(len(backup_module.list_backups(self.backups)), 2)

    def test_rotation_keeps_only_the_newest(self) -> None:
        self.backups.mkdir()
        for index in range(8):
            (self.backups / f"2026-09-0{index}_00-00-00.db").write_bytes(b"x")
        removed = backup_module.rotate(self.backups, keep=5)
        self.assertEqual(len(removed), 3)
        remaining = [entry.name for entry in backup_module.list_backups(self.backups)]
        self.assertEqual(remaining[0], "2026-09-07_00-00-00.db")
        self.assertEqual(len(remaining), 5)

    def test_restore_puts_the_backup_back(self) -> None:
        result = backup_module.take_backups(self.db, self.backups)
        self._edit_db()
        backup_module.restore_backup(result.dated, self.db)
        self.assertEqual(self._skill_count(), 4)


class WatchdogTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db = build_db(self.root / "masters.db")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_unstamped_then_ok_then_stale(self) -> None:
        watcher = DbWatcher(self.db)
        self.assertEqual(watcher.check().state, STATUS_UNSTAMPED)

        status = watcher.check()
        watcher.stamp(status.sha256, status.mtime)
        self.assertEqual(watcher.check().state, STATUS_OK)

        time.sleep(0.01)
        import sqlite3

        con = sqlite3.connect(str(self.db))
        con.execute("UPDATE master_skill SET buy_money = 42")
        con.commit()
        con.close()

        changed = watcher.check()
        self.assertEqual(changed.state, STATUS_STALE)
        self.assertTrue(changed.changed)
        self.assertIn("stale", changed.label)

    def test_a_missing_file_is_reported_not_raised(self) -> None:
        watcher = DbWatcher(self.root / "nope.db")
        self.assertEqual(watcher.check().state, STATUS_MISSING)
        self.assertEqual(DbWatcher(None).check().state, STATUS_MISSING)

    def test_an_unchanged_file_reuses_the_cached_hash(self) -> None:
        watcher = DbWatcher(self.db)
        first = watcher.check()
        second = watcher.check()
        self.assertEqual(first.sha256, second.sha256)


if __name__ == "__main__":
    unittest.main()
