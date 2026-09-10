"""Mods that add tables of their own, not just rows and values.

Modders keep finding more they can do to masters.db, and a manager that quietly
drops the parts it does not recognise is worse than one that refuses them. So a
table vanilla has never heard of is carried across whole: its schema, its
indexes and its rows. Reverting the mod drops it again.

The other half of this file is the case that looks the same but is not: a dump
that opens with CREATE TABLE IF NOT EXISTS for a table already in the database.
That creates nothing, and must not be described or snapshotted as if it did.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from fixtures import build_db, query, write_mod

from lid_db_manager import dbdiff
from lid_db_manager.manager import Manager
from lid_db_manager.paths import AppPaths

# A table vanilla does not have, with an index and a couple of rows - the shape
# someone gets when they add a feature rather than retune an existing one.
NEW_TABLE_SQL = """
CREATE TABLE master_custom_vending (
    id TEXT PRIMARY KEY,
    product TEXT NOT NULL,
    price INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_custom_vending_product ON master_custom_vending (product);
INSERT INTO master_custom_vending (id, product, price)
VALUES ('CV_0001', 'PRD_MUSHROOM', 100), ('CV_0002', 'PRD_STEAK', 250);
"""


def _tables(db: Path) -> set[str]:
    return {r[0] for r in query(db, "SELECT name FROM sqlite_master WHERE type='table'")}


class DiffCarriesNewTables(unittest.TestCase):
    """dbdiff.compare, on a modded database that invents a table."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.vanilla = build_db(self.root / "vanilla.db")
        self.modded = build_db(self.root / "modded.db")
        con = sqlite3.connect(str(self.modded))
        try:
            con.executescript(NEW_TABLE_SQL)
            con.commit()
        finally:
            con.close()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_the_new_table_is_part_of_the_delta(self) -> None:
        delta = dbdiff.compare(self.vanilla, self.modded)
        new = [t for t in delta.tables if t.table == "master_custom_vending"]
        self.assertEqual(len(new), 1, "the new table should be in the delta")
        table_delta = new[0]
        self.assertTrue(table_delta.is_new_table)
        self.assertEqual(len(table_delta.inserts), 2, "both rows should come across")
        self.assertIn("CREATE TABLE", table_delta.create_sql.upper())
        self.assertEqual(len(table_delta.index_sql), 1, "the index should come across too")
        self.assertEqual(delta.new_table_count, 1)

    def test_it_is_not_reported_as_an_incompatible_database(self) -> None:
        """Adding a table is a mod. Removing one is a different game version."""
        notes = dbdiff.check_compatible(self.vanilla, self.modded)
        self.assertTrue(any("carried across" in note for note in notes), notes)

    def test_the_generated_sql_rebuilds_the_table(self) -> None:
        sql = dbdiff.to_sql(dbdiff.compare(self.vanilla, self.modded))
        self.assertIn("CREATE TABLE IF NOT EXISTS", sql)
        self.assertIn("CREATE INDEX IF NOT EXISTS", sql)
        self.assertIn("CV_0001", sql)

        # And running it against vanilla really does reproduce the table.
        con = sqlite3.connect(str(self.vanilla))
        try:
            con.executescript(sql)
            con.commit()
        finally:
            con.close()
        self.assertEqual(
            query(self.vanilla, "SELECT id, product, price FROM master_custom_vending"),
            [("CV_0001", "PRD_MUSHROOM", 100), ("CV_0002", "PRD_STEAK", 250)],
        )

    def test_an_empty_new_table_still_comes_across(self) -> None:
        """A table with no rows yet is still something the mod put there."""
        con = sqlite3.connect(str(self.modded))
        try:
            con.execute("CREATE TABLE master_notes (id TEXT PRIMARY KEY)")
            con.commit()
        finally:
            con.close()
        delta = dbdiff.compare(self.vanilla, self.modded)
        self.assertIn("master_notes", {t.table for t in delta.tables})

    def test_a_missing_vanilla_table_is_still_refused(self) -> None:
        """The guard that matters is unchanged: this is a version mismatch."""
        con = sqlite3.connect(str(self.modded))
        try:
            con.execute("DROP TABLE master_skillgacha_odds")
            con.commit()
        finally:
            con.close()
        with self.assertRaises(dbdiff.IncompatibleDatabase):
            dbdiff.check_compatible(self.vanilla, self.modded)


class ApplyingAndRevertingANewTable(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.paths = AppPaths(self.root).ensure()
        self.db = build_db(self.root / "game" / "masters.db")
        self.manager = Manager(self.paths)
        self.manager.set_db_path(self.db)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write_mod(self, apply_mode: str = "direct") -> None:
        folder = self.paths.mods_dir / "adds-a-table"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "new.sql").write_text(NEW_TABLE_SQL, encoding="utf-8")
        write_mod(
            self.paths.mods_dir,
            "adds-a-table",
            {
                "apply": apply_mode,
                "patches": [{"type": "raw_sql_file", "path": "new.sql"}],
            },
        )
        self.manager.rescan()
        self.manager.set_enabled("adds-a-table", True)

    def test_a_mod_may_create_a_table_without_failing_validation(self) -> None:
        """The table does not exist yet - because this mod is what makes it."""
        self._write_mod()
        report = self.manager.validate()
        self.assertTrue(report.ok, report.summary_line())

    def test_direct_apply_creates_it_and_revert_drops_it(self) -> None:
        self._write_mod("direct")
        self.assertTrue(self.manager.save_mod_list().ok)
        self.assertIn("master_custom_vending", _tables(self.db))
        self.assertEqual(
            query(self.db, "SELECT count(*) FROM master_custom_vending"), [(2,)]
        )

        results = self.manager.revert(["adds-a-table"])
        self.assertTrue(results[0].ok, results[0].error)
        self.assertNotIn(
            "master_custom_vending", _tables(self.db), "revert should take it away again"
        )

    def test_diff_apply_creates_it_and_revert_drops_it(self) -> None:
        """The same, but measured against vanilla rather than run as SQL."""
        self._write_mod("diff")
        report = self.manager.save_mod_list()
        self.assertTrue(report.ok, report.error)
        self.assertEqual(
            query(self.db, "SELECT id, product, price FROM master_custom_vending"),
            [("CV_0001", "PRD_MUSHROOM", 100), ("CV_0002", "PRD_STEAK", 250)],
        )

        results = self.manager.revert(["adds-a-table"])
        self.assertTrue(results[0].ok, results[0].error)
        self.assertNotIn("master_custom_vending", _tables(self.db))

    def test_re_applying_does_not_fall_over_on_the_existing_table(self) -> None:
        """Second apply: the CREATE has to be harmless, not a duplicate-table error."""
        self._write_mod("diff")
        self.assertTrue(self.manager.save_mod_list().ok)
        report = self.manager.reapply_all()
        self.assertTrue(report.ok, report.error)
        self.assertEqual(
            query(self.db, "SELECT count(*) FROM master_custom_vending"),
            [(2,)],
            "re-applying must not duplicate the rows",
        )


class CreateTableIfNotExistsOnATableThatExists(unittest.TestCase):
    """The lookalike: shared dumps open this way, and create nothing."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.paths = AppPaths(self.root).ensure()
        self.db = build_db(self.root / "game" / "masters.db")
        self.manager = Manager(self.paths)
        self.manager.set_db_path(self.db)

        folder = self.paths.mods_dir / "dump-style"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "dump.sql").write_text(
            'CREATE TABLE IF NOT EXISTS "master_skill" (\n'
            '  id TEXT PRIMARY KEY, name TEXT, buy_money INTEGER, val0 INTEGER\n'
            ");\n"
            "UPDATE master_skill SET buy_money = 1 WHERE id = 'SKL_POWER_01';\n",
            encoding="utf-8",
        )
        write_mod(
            self.paths.mods_dir,
            "dump-style",
            {"patches": [{"type": "raw_sql_file", "path": "dump.sql"}]},
        )
        self.manager.rescan()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_it_does_not_claim_to_create_anything(self) -> None:
        self.manager.set_enabled("dump-style", True)
        report = self.manager.validate()
        self.assertTrue(report.ok, report.summary_line())
        warnings = " ".join(w for r in report.results for w in r.warnings)
        self.assertNotIn("reverting will drop", warnings)

    def test_the_create_does_not_blunt_row_level_conflict_detection(self) -> None:
        """A CREATE touches no rows, so it must not turn the table into 'unknown'.

        Otherwise two mods that write different rows of master_skill would start
        being reported as clashing, which is the false positive row-level
        detection exists to avoid.
        """
        write_mod(
            self.paths.mods_dir,
            "elsewhere",
            {
                "patches": [{
                    "type": "update_set", "table": "master_skill",
                    "set": {"val0": 7}, "where": "id = 'SKL_EXPUP_01'",
                }]
            },
        )
        self.manager.rescan()
        self.manager.set_enabled("dump-style", True)
        self.manager.set_enabled("elsewhere", True)
        report = self.manager.validate()
        self.assertTrue(report.ok, report.summary_line())
        conflicts = " ".join(c.message() for c in report.conflicts.conflicts)
        self.assertNotIn("master_skill", conflicts, conflicts)

    def test_revert_restores_rather_than_drops(self) -> None:
        self.manager.set_enabled("dump-style", True)
        self.assertTrue(self.manager.save_mod_list().ok)
        results = self.manager.revert(["dump-style"])
        self.assertTrue(results[0].ok, results[0].error)
        self.assertIn(
            "master_skill", _tables(self.db), "the table was never this mod's to remove"
        )


if __name__ == "__main__":
    unittest.main()
