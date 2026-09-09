"""Capturing pre-state and putting it back."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fixtures import build_db, query, write_mod

from lid_db_manager import snapshot as snapshot_module
from lid_db_manager.mod_loader import scan_mods
from lid_db_manager.patch import SnapshotSpec
from lid_db_manager.runner import apply_mods, revert_mods
from lid_db_manager.session_log import SessionLog
from lid_db_manager.sqlutil import connect


class SnapshotUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db = build_db(self.root / "masters.db")
        self.con = connect(self.db)

    def tearDown(self) -> None:
        self.con.close()
        self._tmp.cleanup()

    def test_row_snapshot_captures_only_the_named_columns(self) -> None:
        captured = snapshot_module.capture(
            self.con, "m", [SnapshotSpec("rows", "master_skill", ["buy_money"], "buy_money > 1")]
        )
        self.assertEqual(captured.entries[0].columns, ["buy_money"])
        self.assertEqual(len(captured.entries[0].rows), 3)

    def test_restoring_puts_the_old_values_back(self) -> None:
        captured = snapshot_module.capture(
            self.con, "m", [SnapshotSpec("rows", "master_skill", ["buy_money"], None)]
        )
        self.con.execute("UPDATE master_skill SET buy_money = 1")
        restored, warnings = snapshot_module.restore(self.con, captured)
        self.con.commit()
        self.assertEqual(restored, 4)
        self.assertEqual(warnings, [])
        self.assertEqual(
            sorted(query(self.db, "SELECT buy_money FROM master_skill")),
            [(1,), (500,), (1200,), (5000,)],
        )

    def test_full_table_snapshot_removes_rows_the_patch_inserted(self) -> None:
        captured = snapshot_module.capture(self.con, "m", [SnapshotSpec("table", "master_skill")])
        self.con.execute("INSERT INTO master_skill (id, buy_money) VALUES ('SKL_NEW', 9)")
        self.con.execute("DELETE FROM master_skill WHERE id = 'SKL_FREE_01'")
        snapshot_module.restore(self.con, captured)
        self.con.commit()
        ids = sorted(row[0] for row in query(self.db, "SELECT id FROM master_skill"))
        self.assertEqual(ids, ["SKL_EXPUP_01", "SKL_EXPUP_02", "SKL_FREE_01", "SKL_POWER_01"])

    def test_a_vanished_row_warns_rather_than_failing(self) -> None:
        captured = snapshot_module.capture(
            self.con, "m", [SnapshotSpec("rows", "master_skill", ["buy_money"], None)]
        )
        self.con.execute("DELETE FROM master_skill WHERE id = 'SKL_FREE_01'")
        restored, warnings = snapshot_module.restore(self.con, captured)
        self.assertEqual(restored, 3)
        self.assertIn("no longer exist", warnings[0])

    def test_a_dropped_column_warns_rather_than_failing(self) -> None:
        captured = snapshot_module.capture(
            self.con, "m", [SnapshotSpec("rows", "master_skill", ["buy_money"], None)]
        )
        self.con.execute("ALTER TABLE master_skill DROP COLUMN buy_money")
        _, warnings = snapshot_module.restore(self.con, captured)
        self.assertIn("no longer has column", warnings[0])

    def test_blob_values_survive_the_json_round_trip(self) -> None:
        self.con.execute("CREATE TABLE blobs (id INTEGER PRIMARY KEY, data BLOB)")
        self.con.execute("INSERT INTO blobs VALUES (1, ?)", (b"\x00\x01\xfe\xff",))
        captured = snapshot_module.capture(self.con, "m", [SnapshotSpec("table", "blobs")])
        saved = snapshot_module.save(self.root, captured)
        reloaded = snapshot_module.load(self.root, "m")
        self.assertTrue(saved.is_file())
        self.con.execute("UPDATE blobs SET data = NULL")
        snapshot_module.restore(self.con, reloaded)
        self.assertEqual(self.con.execute("SELECT data FROM blobs").fetchone()[0], b"\x00\x01\xfe\xff")

    def test_load_returns_none_when_there_is_no_snapshot(self) -> None:
        self.assertIsNone(snapshot_module.load(self.root, "never-seen"))


class SnapshotThroughRunnerTests(unittest.TestCase):
    """The layered case: two mods on the same table, unwound one at a time."""

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

    def test_reverting_one_mod_leaves_the_other_applied(self) -> None:
        write_mod(
            self.mods,
            "a-cost",
            {"patches": [{"type": "update_set", "table": "master_skill", "set": {"buy_money": 1}}]},
        )
        write_mod(
            self.mods,
            "b-exp",
            {
                "patches": [
                    {
                        "type": "update_set",
                        "table": "master_skill",
                        "set": {"val0": 100000},
                        "where": "id = 'SKL_EXPUP_02'",
                    }
                ]
            },
        )
        found = scan_mods(self.mods)
        self.assertTrue(
            apply_mods(self.db, found.mods, snapshots_dir=self.snapshots, log=self.log).ok
        )

        results = revert_mods(
            self.db, ["a-cost"], snapshots_dir=self.snapshots, mods_by_id=found.by_id, log=self.log
        )
        self.assertTrue(results[0].ok)
        rows = dict(query(self.db, "SELECT id, buy_money FROM master_skill"))
        self.assertEqual(rows["SKL_EXPUP_02"], 5000)  # cost mod undone
        exp = dict(query(self.db, "SELECT id, val0 FROM master_skill"))
        self.assertEqual(exp["SKL_EXPUP_02"], 100000)  # exp mod still applied

    def test_a_reverted_mod_drops_its_snapshot(self) -> None:
        write_mod(
            self.mods,
            "gone-after",
            {"patches": [{"type": "update_set", "table": "master_skill", "set": {"buy_money": 1}}]},
        )
        found = scan_mods(self.mods)
        apply_mods(self.db, found.mods, snapshots_dir=self.snapshots, log=self.log)
        self.assertTrue((self.snapshots / "gone-after.json").is_file())
        revert_mods(
            self.db, ["gone-after"], snapshots_dir=self.snapshots, mods_by_id=found.by_id, log=self.log
        )
        self.assertFalse((self.snapshots / "gone-after.json").exists())

    def test_a_deleted_mod_can_still_be_reverted_from_its_snapshot(self) -> None:
        import shutil

        write_mod(
            self.mods,
            "deleted-later",
            {"patches": [{"type": "update_set", "table": "master_skill", "set": {"buy_money": 1}}]},
        )
        apply_mods(self.db, scan_mods(self.mods).mods, snapshots_dir=self.snapshots, log=self.log)
        shutil.rmtree(self.mods / "deleted-later")

        from lid_db_manager.runner import orphaned_snapshots

        self.assertEqual(orphaned_snapshots(self.snapshots, set()), ["deleted-later"])
        results = revert_mods(self.db, ["deleted-later"], snapshots_dir=self.snapshots, log=self.log)
        self.assertTrue(results[0].ok)
        self.assertEqual(
            sorted(query(self.db, "SELECT buy_money FROM master_skill")),
            [(1,), (500,), (1200,), (5000,)],
        )


if __name__ == "__main__":
    unittest.main()
