"""Folding a fresh capture into the snapshot kept from a mod's first apply.

The rows a mod writes are worked out by comparing it against vanilla, so a row
that already holds the value the mod is set to is not in its snapshot. Move the
setting and that row does change - and the kept snapshot has never heard of it,
so reverting used to leave it at the mod's value for good.

Found with the shipped `revive-cost` mod: at 1,000 KC it does not touch
PRD_CONTINUE_MONEY, which is 1,000 already. Move the setting to 5,000 and that
row changes too, and switching the mod off left it at 5,000 rather than 1,000.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fixtures import build_db, query, write_mod

from lid_db_manager import snapshot as snapshot_module
from lid_db_manager.mod_loader import scan_mods
from lid_db_manager.runner import apply_mods, revert_mods
from lid_db_manager.session_log import SessionLog


class MergedSnapshotTests(unittest.TestCase):
    """The merge itself. One-way: nothing recorded is ever overwritten."""

    def snap(self, mod_id="m", entries=(), at="first", sha="aaa"):
        return snapshot_module.Snapshot(
            mod_id=mod_id, captured_at=at, db_path="/db",
            db_sha256_before=sha, entries=list(entries),
        )

    def rows(self, table="t", columns=("price",), rows=()):
        return snapshot_module.SnapshotEntry(
            "rows", table, list(columns), [list(r) for r in rows]
        )

    def test_a_row_only_the_fresh_capture_knows_about_is_added(self):
        kept = self.snap(entries=[self.rows(rows=[[14, 10000]])])
        fresh = self.snap(entries=[self.rows(rows=[[14, 1000], [15, 1000]])])
        out = snapshot_module.merged(kept, fresh)
        self.assertEqual(out.entries[0].rows, [[14, 10000], [15, 1000]])

    def test_a_row_already_recorded_keeps_the_first_value(self):
        # The whole point: 1000 is what the mod wrote, 10000 is the real original.
        kept = self.snap(entries=[self.rows(rows=[[14, 10000]])])
        fresh = self.snap(entries=[self.rows(rows=[[14, 1000]])])
        out = snapshot_module.merged(kept, fresh)
        self.assertEqual(out.entries[0].rows, [[14, 10000]])

    def test_the_kept_snapshots_own_details_are_the_ones_carried(self):
        out = snapshot_module.merged(self.snap(at="first", sha="aaa"),
                                     self.snap(at="second", sha="bbb"))
        self.assertEqual(out.captured_at, "first")
        self.assertEqual(out.db_sha256_before, "aaa")

    def test_merging_does_not_change_either_snapshot_it_was_given(self):
        kept = self.snap(entries=[self.rows(rows=[[14, 10000]])])
        fresh = self.snap(entries=[self.rows(rows=[[15, 1000]])])
        snapshot_module.merged(kept, fresh)
        self.assertEqual(kept.entries[0].rows, [[14, 10000]])
        self.assertEqual(fresh.entries[0].rows, [[15, 1000]])

    def test_a_table_the_kept_snapshot_never_had_is_added_whole(self):
        kept = self.snap(entries=[self.rows(table="a", rows=[[1, 1]])])
        fresh = self.snap(entries=[self.rows(table="a", rows=[[1, 9]]),
                                   self.rows(table="b", rows=[[2, 2]])])
        out = snapshot_module.merged(kept, fresh)
        self.assertEqual(sorted(e.table for e in out.entries), ["a", "b"])
        self.assertEqual(out.entries[0].rows, [[1, 1]])

    def test_a_whole_table_copy_is_never_added_to_row_by_row(self):
        # It already holds every row of that table; a later, narrower capture
        # could only duplicate what it has.
        whole = snapshot_module.SnapshotEntry("table", "t", ["id", "price"], [[1, "a", 5]])
        out = snapshot_module.merged(self.snap(entries=[whole]),
                                     self.snap(entries=[self.rows(rows=[[2, 7]])]))
        self.assertEqual(len(out.entries), 1)
        self.assertEqual(out.entries[0].kind, "table")

    def test_an_absent_table_marker_is_not_diluted(self):
        absent = snapshot_module.SnapshotEntry("absent", "t", [], [])
        out = snapshot_module.merged(self.snap(entries=[absent]),
                                     self.snap(entries=[self.rows(rows=[[1, 1]])]))
        self.assertEqual([e.kind for e in out.entries], ["absent"])

    def test_entries_for_the_same_table_with_different_columns_both_survive(self):
        kept = self.snap(entries=[self.rows(columns=["price"], rows=[[1, 5]])])
        fresh = self.snap(entries=[self.rows(columns=["spirit"], rows=[[1, 7]])])
        out = snapshot_module.merged(kept, fresh)
        self.assertEqual([e.columns for e in out.entries], [["price"], ["spirit"]])

    def test_merging_with_an_empty_fresh_capture_changes_nothing(self):
        kept = self.snap(entries=[self.rows(rows=[[14, 10000]])])
        out = snapshot_module.merged(kept, self.snap())
        self.assertEqual(out.entries[0].rows, [[14, 10000]])

    def test_merging_into_an_empty_kept_snapshot_takes_everything(self):
        fresh = self.snap(entries=[self.rows(rows=[[14, 10000]])])
        out = snapshot_module.merged(self.snap(), fresh)
        self.assertEqual(out.entries[0].rows, [[14, 10000]])

    def test_it_survives_a_round_trip_through_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            kept = self.snap(entries=[self.rows(rows=[[14, 10000]])])
            fresh = self.snap(entries=[self.rows(rows=[[14, 1], [15, 1000]])])
            snapshot_module.save(Path(tmp), snapshot_module.merged(kept, fresh))
            back = snapshot_module.load(Path(tmp), "m")
            self.assertEqual(back.entries[0].rows, [[14, 10000], [15, 1000]])


class SettingChangedThenRevertedTests(unittest.TestCase):
    """The behaviour the merge exists for, end to end through the manager.

    It has to be the manager rather than the runner on its own: the narrow,
    delta-scoped snapshot that leaves an unchanged row out is only built when a
    vanilla database is there to compare against, and that is the manager's
    doing. Driven through Save Mod List, which is how anybody meets it.
    """

    # The fixture ships these two. G1 starts at exactly the value the first
    # save sets, so the first snapshot never records it - the shape of the bug.
    PRICES = {"PRD_CONTINUE_G1": 5000, "PRD_CONTINUE_G2": 10000}
    FIRST = 5000

    def setUp(self) -> None:
        from lid_db_manager.manager import Manager
        from lid_db_manager.paths import AppPaths

        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.paths = AppPaths(self.root / "app").ensure()
        self.db = build_db(self.root / "game" / "masters.db")
        self.assertEqual(self._prices(), self.PRICES, "the fixture moved")

        write_mod(self.paths.mods_dir, "priced", {
            "settings": [{"id": "price", "label": "Price", "type": "integer",
                          "default": 5000, "min": 1, "max": 100000}],
            "patches": [{"type": "update_set", "table": "master_shop_product_price",
                         "set": {"price": "{{price}}"},
                         "where": "id LIKE 'PRD_CONTINUE%'"}],
        })
        self.manager = Manager(self.paths)
        self.manager.set_db_path(self.db)
        self.manager.rescan()
        self.manager.state.set_enabled("priced", True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _save(self, price: int) -> None:
        self.manager.set_mod_setting("priced", "price", price)
        report = self.manager.save_mod_list()
        self.assertTrue(report.ok, report.error)

    def _revert(self) -> None:
        for result in self.manager.revert(["priced"]):
            self.assertTrue(result.ok, result.error)

    def _prices(self) -> dict:
        return dict(query(
            self.db,
            "SELECT id, price FROM master_shop_product_price WHERE id LIKE 'PRD_CONTINUE%'",
        ))

    def _snapshot_rows(self) -> list:
        kept = snapshot_module.load(self.paths.snapshots_dir, "priced")
        return [row for entry in kept.entries for row in entry.rows]

    def test_the_setup_really_does_leave_one_row_out_of_the_first_snapshot(self):
        # Without this the rest of the class proves nothing.
        self._save(self.FIRST)
        self.assertEqual(set(self._prices().values()), {self.FIRST})
        self.assertEqual(len(self._snapshot_rows()), 1,
                         "only G2 should be in the first snapshot")

    def test_a_row_first_changed_by_a_later_save_still_reverts(self):
        self._save(self.FIRST)   # G1 is already 5,000 - not snapshotted
        self._save(9000)         # now it changes too
        self.assertEqual(set(self._prices().values()), {9000})
        self.assertEqual(len(self._snapshot_rows()), 2, "the merge should have added G1")
        self._revert()
        self.assertEqual(self._prices(), self.PRICES)

    def test_the_value_put_back_is_the_one_recorded_first(self):
        self._save(self.FIRST)
        self._save(9000)
        self._revert()
        # 10,000 is the real original. 5,000 is what the second save found
        # there, and is what re-capturing rather than merging would record.
        self.assertEqual(self._prices()["PRD_CONTINUE_G2"], 10000)

    def test_three_saves_at_three_values_still_revert_to_stock(self):
        self._save(self.FIRST)
        self._save(9000)
        self._save(12000)
        self._revert()
        self.assertEqual(self._prices(), self.PRICES)

    def test_going_back_to_the_first_value_before_reverting_is_still_clean(self):
        self._save(self.FIRST)
        self._save(9000)
        self._save(self.FIRST)
        self._revert()
        self.assertEqual(self._prices(), self.PRICES)

    def test_reverting_after_a_single_save_works_as_it_always_did(self):
        self._save(9000)
        self._revert()
        self.assertEqual(self._prices(), self.PRICES)


if __name__ == "__main__":
    unittest.main()
