"""Applying mods: the transaction, the ordering, and what happens when it fails."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fixtures import build_db, query, write_mod, write_sql_mod

from lid_db_manager.mod_loader import scan_mods
from lid_db_manager.runner import apply_mods, preview_mods, revert_mods
from lid_db_manager.session_log import SessionLog


class RunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.mods = self.root / "mods"
        self.mods.mkdir()
        self.snapshots = self.root / "snapshots"
        self.db = build_db(self.root / "masters.db")
        self.log = SessionLog()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _apply(self, **kwargs):
        return apply_mods(
            self.db, scan_mods(self.mods).mods, snapshots_dir=self.snapshots, log=self.log, **kwargs
        )

    def test_update_set_applies_and_reports_rows(self) -> None:
        write_mod(
            self.mods,
            "a-cost-mod",
            {
                "patches": [
                    {
                        "type": "update_set",
                        "table": "master_skill",
                        "set": {"buy_money": 1},
                        "where": "buy_money > 1",
                    }
                ]
            },
        )
        report = self._apply()
        self.assertTrue(report.ok, report.error)
        self.assertEqual(report.rows_changed, 3)
        self.assertEqual(
            query(self.db, "SELECT DISTINCT buy_money FROM master_skill"), [(1,)]
        )

    def test_text_replace_rewrites_only_the_matched_row(self) -> None:
        write_mod(
            self.mods,
            "text",
            {
                "patches": [
                    {
                        "type": "text_replace",
                        "table": "master_text",
                        "match": {"id": "TXT_BIBLE_19_NOTE_G", "lang": "int"},
                        "replace": [{"find": "5000 Kill Coins", "with": "1 Kill Coin"}],
                    }
                ]
            },
        )
        self.assertTrue(self._apply().ok)
        english = query(
            self.db, "SELECT txt FROM master_text WHERE id = ? AND lang = 'int'",
            ("TXT_BIBLE_19_NOTE_G",),
        )[0][0]
        other = query(self.db, "SELECT txt FROM master_text WHERE id = 'TXT_OTHER'")[0][0]
        self.assertIn("Grade 1: 1 Kill Coin", english)
        self.assertEqual(other, "Nothing to see here")

    def test_raw_sql_file_is_read_at_apply_time(self) -> None:
        folder = write_mod(
            self.mods,
            "from-file",
            {"patches": [{"type": "raw_sql_file", "path": "patch.sql"}]},
            patch__sql="UPDATE master_body_detail SET price = 1 WHERE price > 1;",
        )
        (folder / "patch.sql").write_text(
            "UPDATE master_body_detail SET price = 7 WHERE price > 1;", encoding="utf-8"
        )
        self.assertTrue(self._apply().ok)
        self.assertEqual(
            sorted(query(self.db, "SELECT price FROM master_body_detail")), [(1,), (7,), (7,)]
        )

    def test_dependencies_apply_in_order(self) -> None:
        write_mod(
            self.mods,
            "second",
            {
                "requires": ["first"],
                "patches": [{"type": "raw_sql", "sql": "UPDATE master_skill SET name = name || '2'"}],
            },
        )
        write_mod(
            self.mods,
            "first",
            {"patches": [{"type": "raw_sql", "sql": "UPDATE master_skill SET name = name || '1'"}]},
        )
        report = self._apply()
        self.assertEqual([r.mod_id for r in report.results], ["first", "second"])
        self.assertTrue(
            all(name.endswith("12") for (name,) in query(self.db, "SELECT name FROM master_skill"))
        )

    def test_sql_wrapped_in_its_own_transaction_still_applies(self) -> None:
        # Mod SQL is usually written for `sqlite3 masters.db < patch.sql`, so it
        # wraps itself in BEGIN ... COMMIT. Those must be ignored, not run.
        write_sql_mod(
            self.mods,
            "self-wrapped",
            "BEGIN TRANSACTION;\n"
            "UPDATE master_body_detail SET price = 1 WHERE price > 1;\n"
            "COMMIT;\n",
        )
        report = self._apply()
        self.assertTrue(report.ok, report.error)
        self.assertEqual(sorted(query(self.db, "SELECT price FROM master_body_detail")), [(1,), (1,), (1,)])
        warnings = " ".join(report.validation.results[0].warnings)
        self.assertIn("BEGIN TRANSACTION", warnings)
        self.assertIn("COMMIT", warnings)

    def test_a_commit_inside_mod_sql_cannot_break_atomicity(self) -> None:
        """The whole point: a mod's COMMIT must not make earlier work permanent."""
        write_mod(
            self.mods,
            "a-good",
            {"patches": [{"type": "update_set", "table": "master_skill", "set": {"buy_money": 1}}]},
        )
        write_sql_mod(
            self.mods,
            "b-commits-then-fails",
            # Passes validation; the INSERT collides with the primary key at run time.
            "UPDATE master_body_detail SET price = 1;\n"
            "COMMIT;\n"
            "INSERT INTO master_skill (id, buy_money) VALUES ('SKL_FREE_01', 5);\n",
        )
        report = self._apply()
        self.assertFalse(report.ok)
        # Both mods are rolled back - including the one that ran before the COMMIT.
        self.assertEqual(
            sorted(query(self.db, "SELECT buy_money FROM master_skill")),
            [(1,), (500,), (1200,), (5000,)],
        )
        self.assertEqual(
            sorted(query(self.db, "SELECT price FROM master_body_detail")), [(1,), (2000,), (8000,)]
        )

    def test_a_failing_mod_rolls_back_everything(self) -> None:
        write_mod(
            self.mods,
            "a-good",
            {"patches": [{"type": "update_set", "table": "master_skill", "set": {"buy_money": 1}}]},
        )
        # Passes validation (the table and columns exist) but fails at runtime.
        write_mod(
            self.mods,
            "b-bad",
            {
                "patches": [
                    {
                        "type": "raw_sql",
                        "sql": "INSERT INTO master_skill (id, buy_money) VALUES ('SKL_FREE_01', 5)",
                    }
                ]
            },
        )
        report = self._apply()
        self.assertFalse(report.ok)
        self.assertEqual(report.failed_mod, "b-bad")
        # The good mod's changes are gone too - the whole apply is one transaction.
        self.assertEqual(
            sorted(query(self.db, "SELECT buy_money FROM master_skill")),
            [(1,), (500,), (1200,), (5000,)],
        )
        self.assertFalse((self.snapshots / "a-good.json").exists())

    def test_validation_failure_applies_nothing(self) -> None:
        write_mod(
            self.mods,
            "fine",
            {"patches": [{"type": "update_set", "table": "master_skill", "set": {"buy_money": 1}}]},
        )
        write_mod(
            self.mods,
            "broken",
            {"patches": [{"type": "update_set", "table": "ghost_table", "set": {"x": 1}}]},
        )
        report = self._apply()
        self.assertFalse(report.ok)
        self.assertEqual(query(self.db, "SELECT COUNT(*) FROM master_skill WHERE buy_money = 1"), [(1,)])

    def test_snapshots_are_written_only_after_a_successful_commit(self) -> None:
        write_mod(
            self.mods,
            "snapshotted",
            {"patches": [{"type": "update_set", "table": "master_skill", "set": {"buy_money": 1}}]},
        )
        self.assertTrue(self._apply().ok)
        self.assertTrue((self.snapshots / "snapshotted.json").is_file())

    def test_revert_uses_inverse_sql_when_the_mod_ships_one(self) -> None:
        write_sql_mod(
            self.mods,
            "paired",
            "UPDATE master_body_detail SET price = 1;",
            "UPDATE master_body_detail SET price = 999;",
        )
        self.assertTrue(self._apply().ok)
        results = revert_mods(
            self.db,
            ["paired"],
            snapshots_dir=self.snapshots,
            mods_by_id=scan_mods(self.mods).by_id,
            log=self.log,
        )
        self.assertEqual(results[0].method, "inverse.sql")
        self.assertEqual(sorted(set(query(self.db, "SELECT price FROM master_body_detail"))), [(999,)])

    def test_revert_without_a_snapshot_reports_instead_of_crashing(self) -> None:
        results = revert_mods(self.db, ["never-applied"], snapshots_dir=self.snapshots, log=self.log)
        self.assertFalse(results[0].ok)
        self.assertIn("no snapshot", results[0].error)

    def test_preview_does_not_touch_the_database(self) -> None:
        write_mod(
            self.mods,
            "previewed",
            {
                "patches": [
                    {
                        "type": "update_set",
                        "table": "master_skill",
                        "set": {"buy_money": 1},
                        "where": "buy_money > 1",
                    }
                ]
            },
        )
        previews = preview_mods(self.db, scan_mods(self.mods).mods)
        preview = previews["previewed"][0]
        self.assertEqual(preview.total_rows, 3)
        self.assertIn("buy_money=500", preview.rows[0].before)
        self.assertIn("buy_money=1", preview.rows[0].after)
        self.assertEqual(
            sorted(query(self.db, "SELECT buy_money FROM master_skill")),
            [(1,), (500,), (1200,), (5000,)],
        )

    def test_applying_twice_is_idempotent(self) -> None:
        write_mod(
            self.mods,
            "twice",
            {
                "patches": [
                    {
                        "type": "update_set",
                        "table": "master_skill",
                        "set": {"buy_money": 1},
                        "where": "buy_money > 1",
                    }
                ]
            },
        )
        self.assertTrue(self._apply().ok)
        second = self._apply()
        self.assertTrue(second.ok)
        self.assertEqual(second.rows_changed, 0)


if __name__ == "__main__":
    unittest.main()
