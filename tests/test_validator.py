"""Pre-apply validation: hard failures vs warnings."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fixtures import build_db, query, write_mod

from lid_db_manager.mod_loader import scan_mods
from lid_db_manager.validator import validate


class ValidatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.mods = self.root / "mods"
        self.mods.mkdir()
        self.db = build_db(self.root / "masters.db")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _validate(self):
        return validate(self.db, scan_mods(self.mods).mods)

    def test_a_good_mod_passes(self) -> None:
        write_mod(
            self.mods,
            "good",
            {"patches": [{"type": "update_set", "table": "master_skill", "set": {"buy_money": 1}}]},
        )
        report = self._validate()
        self.assertTrue(report.ok, report.summary_line())

    def test_missing_table_fails(self) -> None:
        write_mod(
            self.mods,
            "bad-table",
            {"patches": [{"type": "update_set", "table": "no_such_table", "set": {"x": 1}}]},
        )
        report = self._validate()
        self.assertFalse(report.ok)
        self.assertIn("does not exist", report.failed[0].errors[0])

    def test_missing_column_names_the_columns_that_do_exist(self) -> None:
        write_mod(
            self.mods,
            "bad-column",
            {"patches": [{"type": "update_set", "table": "master_skill", "set": {"nope": 1}}]},
        )
        report = self._validate()
        self.assertFalse(report.ok)
        message = report.failed[0].errors[0]
        self.assertIn("nope", message)
        self.assertIn("buy_money", message)

    def test_broken_where_clause_fails(self) -> None:
        write_mod(
            self.mods,
            "bad-where",
            {
                "patches": [
                    {
                        "type": "update_set",
                        "table": "master_skill",
                        "set": {"buy_money": 1},
                        "where": "buy_money >>> ",
                    }
                ]
            },
        )
        report = self._validate()
        self.assertFalse(report.ok)
        self.assertIn("not valid SQL", report.failed[0].errors[0])

    def test_broken_raw_sql_fails_without_running(self) -> None:
        write_mod(
            self.mods,
            "bad-sql",
            {"patches": [{"type": "raw_sql", "sql": "DELETE FROM master_skill WHERE"}]},
        )
        report = self._validate()
        self.assertFalse(report.ok)
        self.assertIn("not valid", report.failed[0].errors[0])
        # ... and validation is read-only, so nothing was deleted.
        self.assertEqual(len(query(self.db, "SELECT id FROM master_skill")), 4)

    def test_raw_sql_against_a_missing_table_fails(self) -> None:
        write_mod(
            self.mods, "ghost", {"patches": [{"type": "raw_sql", "sql": "UPDATE ghost SET a = 1"}]}
        )
        self.assertFalse(self._validate().ok)

    def test_expected_rows_mismatch_is_only_a_warning(self) -> None:
        write_mod(
            self.mods,
            "drifted",
            {
                "patches": [
                    {
                        "type": "update_set",
                        "table": "master_skill",
                        "set": {"buy_money": 1},
                        "where": "buy_money > 1",
                        "expected_rows": 320,
                    }
                ]
            },
        )
        report = self._validate()
        self.assertTrue(report.ok)
        self.assertIn("expected 320 rows", report.results[0].warnings[0])

    def test_matching_no_rows_is_only_a_warning(self) -> None:
        write_mod(
            self.mods,
            "no-op",
            {
                "patches": [
                    {
                        "type": "update_set",
                        "table": "master_skill",
                        "set": {"buy_money": 1},
                        "where": "buy_money > 999999",
                    }
                ]
            },
        )
        report = self._validate()
        self.assertTrue(report.ok)
        self.assertIn("0 rows", report.results[0].warnings[0])

    def test_absent_find_text_warns_by_default(self) -> None:
        write_mod(
            self.mods,
            "text-drift",
            {
                "patches": [
                    {
                        "type": "text_replace",
                        "table": "master_text",
                        "match": {"id": "TXT_BIBLE_19_NOTE_G", "lang": "int"},
                        "replace": [{"find": "not in there", "with": "x"}],
                    }
                ]
            },
        )
        report = self._validate()
        self.assertTrue(report.ok)
        self.assertIn("is not present", report.results[0].warnings[0])

    def test_absent_find_text_fails_when_require_find_is_set(self) -> None:
        write_mod(
            self.mods,
            "text-strict",
            {
                "patches": [
                    {
                        "type": "text_replace",
                        "table": "master_text",
                        "match": {"id": "TXT_BIBLE_19_NOTE_G", "lang": "int"},
                        "replace": [{"find": "not in there", "with": "x"}],
                        "require_find": True,
                    }
                ]
            },
        )
        self.assertFalse(self._validate().ok)

    def test_missing_sql_file_fails(self) -> None:
        write_mod(
            self.mods, "no-file", {"patches": [{"type": "raw_sql_file", "path": "gone.sql"}]}
        )
        report = self._validate()
        self.assertFalse(report.ok)
        self.assertIn("missing", report.failed[0].errors[0])

    def test_missing_database_is_a_fatal_report_not_a_crash(self) -> None:
        report = validate(self.root / "nope.db", [])
        self.assertFalse(report.ok)
        self.assertIn("not found", report.fatal)

    def test_requirements_are_reported(self) -> None:
        write_mod(
            self.mods,
            "needs-other",
            {
                "requires": ["not-installed"],
                "patches": [{"type": "update_set", "table": "master_skill", "set": {"buy_money": 1}}],
            },
        )
        report = self._validate()
        self.assertTrue(report.ok)  # a missing requirement warns, it does not block
        self.assertIn("not installed", report.conflicts.missing_requirements[0].message())


if __name__ == "__main__":
    unittest.main()
