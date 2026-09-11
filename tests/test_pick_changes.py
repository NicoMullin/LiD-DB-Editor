"""Taking part of a reworked masters.db instead of all of it.

A rework arrives as one lump - dozens of tables retuned at once. These tests
cover the two things that break the lump up: filtering a delta down to what the
player ticked, and writing what survives as one mod per table, so the choice is
not a one-off at import but something they can keep changing afterwards.

The GUI half is skipped when PySide6 is not installed; the engine half is not.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fixtures import build_db, query

from lid_db_manager import dbdiff
from lid_db_manager.install import InstallError
from lid_db_manager.manager import Manager
from lid_db_manager.paths import AppPaths

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from lid_db_manager.ui.change_picker import ChangePicker
    from lid_db_manager.ui.install_dialog import InstallDialog

    HAVE_QT = True
except ImportError:  # pragma: no cover - depends on the environment
    HAVE_QT = False


def make_rework(vanilla: Path, target: Path) -> Path:
    """A modded copy touching three tables, the way a shared rework does."""
    import shutil

    shutil.copy2(vanilla, target)
    con = sqlite3.connect(str(target))
    con.execute("UPDATE master_skill SET buy_money = 7")
    con.execute("UPDATE master_shop_product_price SET price = 1")
    con.execute("UPDATE master_body_detail SET price = 3")
    con.commit()
    con.close()
    return target


class FilteringADelta(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.vanilla = build_db(self.root / "vanilla.db")
        self.modded = make_rework(self.vanilla, self.root / "modded.db")
        self.delta = dbdiff.compare(self.vanilla, self.modded)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _tables(self, delta) -> set[str]:
        return {t.table for t in delta.tables}

    def test_the_unfiltered_delta_has_all_three_tables(self) -> None:
        self.assertEqual(
            self._tables(self.delta),
            {"master_skill", "master_shop_product_price", "master_body_detail"},
        )

    def test_a_table_left_out_of_the_selection_is_dropped(self) -> None:
        kept = self.delta.filtered({"master_skill": dbdiff.ALL_ROWS})
        self.assertEqual(self._tables(kept), {"master_skill"})

    def test_selecting_nothing_gives_an_empty_delta(self) -> None:
        self.assertTrue(self.delta.filtered({}).empty)

    def test_individual_rows_can_be_kept(self) -> None:
        skill = next(t for t in self.delta.tables if t.table == "master_skill")
        wanted = {skill.updates[0].key}
        kept = self.delta.filtered({"master_skill": wanted})
        rows = kept.tables[0].updates
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].key, skill.updates[0].key)

    def test_filtering_does_not_disturb_the_original(self) -> None:
        before = self.delta.change_count
        self.delta.filtered({"master_skill": set()})
        self.assertEqual(self.delta.change_count, before)

    def test_split_by_table_gives_one_delta_each(self) -> None:
        pieces = self.delta.split_by_table()
        self.assertEqual(len(pieces), 3)
        self.assertTrue(all(len(p.tables) == 1 for p in pieces))
        self.assertEqual(
            sum(p.change_count for p in pieces), self.delta.change_count
        )

    def test_inserts_and_deletes_can_be_picked_too(self) -> None:
        con = sqlite3.connect(str(self.modded))
        con.execute(
            "INSERT INTO master_skill (id, name, buy_money, val0) "
            "VALUES ('SKL_NEW', 'New', 1, 1)"
        )
        con.execute("DELETE FROM master_skill WHERE id = 'SKL_FREE_01'")
        con.commit()
        con.close()
        delta = dbdiff.compare(self.vanilla, self.modded)
        skill = next(t for t in delta.tables if t.table == "master_skill")
        self.assertEqual(len(skill.inserts), 1)
        self.assertEqual(len(skill.deletes), 1)

        # Keep the insert, drop the delete.
        insert_key = dbdiff.insert_key(skill, skill.inserts[0])
        kept = delta.filtered({"master_skill": {insert_key}}).tables[0]
        self.assertEqual(len(kept.inserts), 1)
        self.assertEqual(len(kept.deletes), 0)


class InstallingARework(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.paths = AppPaths(self.root / "app").ensure()
        self.db = build_db(self.root / "game" / "masters.db")
        self.manager = Manager(self.paths)
        self.manager.set_db_path(self.db)
        self.rework = make_rework(self.manager.vanilla_path, self.root / "rework.db")
        self.candidate = self.manager.inspect_install(self.rework)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_it_becomes_one_mod_per_table(self) -> None:
        mods = self.manager.install_database(
            self.candidate, "Big Rework", split_by_table=True
        )
        self.assertEqual(len(mods), 3)
        self.assertTrue(all(m.name.startswith("Big Rework - ") for m in mods))

    def test_every_piece_arrives_switched_off(self) -> None:
        """Nothing a player imports should start changing their game by itself."""
        mods = self.manager.install_database(
            self.candidate, "Big Rework", split_by_table=True
        )
        for mod in mods:
            self.assertNotIn(mod.id, self.manager.state.enabled_mods)

    def test_the_pieces_can_be_enabled_independently(self) -> None:
        """The whole point: keep one table's changes, leave another's."""
        # Read the untouched values first - the fixture happens to price one row
        # at 1 already, so "no 1s present" would not prove anything.
        before = query(self.manager.vanilla_path, "SELECT id, price FROM master_shop_product_price")

        mods = self.manager.install_database(
            self.candidate, "Big Rework", split_by_table=True
        )
        skill_mod = next(m for m in mods if m.id.endswith("master_skill"))
        self.manager.set_enabled(skill_mod.id, True)
        self.assertTrue(self.manager.save_mod_list().ok)

        self.assertEqual(
            {row[0] for row in query(self.db, "SELECT buy_money FROM master_skill")},
            {7},
            "the table whose mod was enabled should have changed",
        )
        self.assertEqual(
            query(self.db, "SELECT id, price FROM master_shop_product_price"),
            before,
            "the table whose mod was left off should be untouched",
        )

    def test_a_deselected_table_never_reaches_the_disk(self) -> None:
        mods = self.manager.install_database(
            self.candidate,
            "Big Rework",
            selection={"master_skill": dbdiff.ALL_ROWS},
            split_by_table=True,
        )
        self.assertEqual(len(mods), 1)
        self.assertTrue(mods[0].id.endswith("master_skill"))

    def test_selecting_nothing_is_refused_rather_than_writing_an_empty_mod(self) -> None:
        with self.assertRaises(InstallError):
            self.manager.install_database(self.candidate, "Big Rework", selection={})

    def test_one_mod_is_the_default_and_carries_a_part_per_table(self) -> None:
        """The default shape: one entry in the list, switchable table by table."""
        mods = self.manager.install_database(self.candidate, "All Of It")
        self.assertEqual(len(mods), 1)
        self.assertEqual(mods[0].name, "All Of It")
        self.assertEqual(
            sorted(p.key for p in mods[0].patches),
            ["master_body_detail", "master_shop_product_price", "master_skill"],
            "each table should be its own switchable part",
        )

    def test_each_piece_says_what_it_was_split_from(self) -> None:
        """Otherwise a piece read on its own looks like a mod that half-works."""
        self.manager.install_database(
            self.candidate, "Big Rework", split_by_table=True
        )
        folder = self.paths.mods_dir / "Big Rework - master_skill"
        sql = (folder / "master_skill.sql").read_text(encoding="utf-8")
        self.assertIn("One piece of Big Rework", sql)

    def test_reverting_one_piece_leaves_the_others(self) -> None:
        mods = self.manager.install_database(
            self.candidate, "Big Rework", split_by_table=True
        )
        for mod in mods:
            self.manager.set_enabled(mod.id, True)
        self.assertTrue(self.manager.save_mod_list().ok)

        skill_mod = next(m for m in mods if m.id.endswith("master_skill"))
        results = self.manager.revert([skill_mod.id])
        self.assertTrue(results[0].ok, results[0].error)
        self.assertNotIn(
            7, {r[0] for r in query(self.db, "SELECT buy_money FROM master_skill")}
        )
        self.assertEqual(
            {r[0] for r in query(self.db, "SELECT price FROM master_body_detail")},
            {3},
            "reverting one piece must not disturb another",
        )


@unittest.skipUnless(HAVE_QT, "PySide6 is not installed")
class ThePickerWidget(unittest.TestCase):
    app = None

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.vanilla = build_db(self.root / "vanilla.db")
        self.modded = make_rework(self.vanilla, self.root / "modded.db")
        self.delta = dbdiff.compare(self.vanilla, self.modded)
        self.picker = ChangePicker(self.delta)

    def tearDown(self) -> None:
        self.picker.deleteLater()
        self._tmp.cleanup()

    def _table_item(self, name: str):
        tree = self.picker.tree
        for index in range(tree.topLevelItemCount()):
            if tree.topLevelItem(index).text(0) == name:
                return tree.topLevelItem(index)
        raise AssertionError(f"no row for {name}")

    def test_it_lists_a_row_per_table_and_a_child_per_edit(self) -> None:
        item = self._table_item("master_skill")
        skill = next(t for t in self.delta.tables if t.table == "master_skill")
        self.assertEqual(item.childCount(), len(skill.updates))

    def test_everything_starts_ticked(self) -> None:
        kept, offered, _ = self.picker.kept_counts()
        self.assertEqual(kept, offered)
        self.assertEqual(offered, self.delta.change_count)

    def test_unticking_a_table_removes_it_from_the_selection(self) -> None:
        self._table_item("master_skill").setCheckState(0, Qt.CheckState.Unchecked)
        self.assertNotIn("master_skill", self.picker.selection())

    def test_unticking_a_table_unticks_its_rows(self) -> None:
        item = self._table_item("master_skill")
        item.setCheckState(0, Qt.CheckState.Unchecked)
        states = {item.child(i).checkState(0) for i in range(item.childCount())}
        self.assertEqual(states, {Qt.CheckState.Unchecked})

    def test_unticking_one_row_leaves_the_table_partially_checked(self) -> None:
        item = self._table_item("master_skill")
        item.child(0).setCheckState(0, Qt.CheckState.Unchecked)
        self.assertEqual(item.checkState(0), Qt.CheckState.PartiallyChecked)

        chosen = self.picker.selection()["master_skill"]
        self.assertIsNot(chosen, dbdiff.ALL_ROWS)
        self.assertEqual(len(chosen), item.childCount() - 1)

    def test_a_fully_ticked_table_selects_the_whole_table(self) -> None:
        """Not a list of every key - the whole-table case should stay cheap."""
        self.assertIs(self.picker.selection()["master_skill"], dbdiff.ALL_ROWS)

    def test_the_count_follows_what_is_ticked(self) -> None:
        item = self._table_item("master_skill")
        before, offered, _ = self.picker.kept_counts()
        item.child(0).setCheckState(0, Qt.CheckState.Unchecked)
        after, _, _ = self.picker.kept_counts()
        self.assertEqual(after, before - 1)
        self.assertIn(str(after), self.picker.summary.text())

    def test_none_then_all_round_trips(self) -> None:
        self.picker._set_all(False)
        self.assertEqual(self.picker.selection(), {})
        self.picker._set_all(True)
        kept, offered, _ = self.picker.kept_counts()
        self.assertEqual(kept, offered)

    def test_the_filter_hides_tables_that_do_not_match(self) -> None:
        self.picker.filter_edit.setText("master_skill")
        self.assertFalse(self._table_item("master_skill").isHidden())
        self.assertTrue(self._table_item("master_body_detail").isHidden())

    def test_clearing_the_filter_brings_everything_back(self) -> None:
        self.picker.filter_edit.setText("master_skill")
        self.picker.filter_edit.setText("")
        self.assertFalse(self._table_item("master_body_detail").isHidden())

    def test_filtering_does_not_change_the_selection(self) -> None:
        """Hiding a row is not the same as declining it."""
        before = self.picker.kept_counts()
        self.picker.filter_edit.setText("master_skill")
        self.assertEqual(self.picker.kept_counts(), before)


@unittest.skipUnless(HAVE_QT, "PySide6 is not installed")
class TheInstallDialog(unittest.TestCase):
    app = None

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.paths = AppPaths(self.root / "app").ensure()
        self.db = build_db(self.root / "game" / "masters.db")
        self.manager = Manager(self.paths)
        self.manager.set_db_path(self.db)
        self.rework = make_rework(self.manager.vanilla_path, self.root / "rework.db")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_a_database_import_gets_a_picker(self) -> None:
        dialog = InstallDialog(self.manager.inspect_install(self.rework))
        self.addCleanup(dialog.deleteLater)
        self.assertIsNotNone(dialog.picker)
        self.assertIsNotNone(dialog.split_box)

    def test_a_plain_sql_drop_does_not(self) -> None:
        """There is nothing to choose from, so the dialog stays the small one."""
        sql = self.root / "patch.sql"
        sql.write_text("UPDATE master_skill SET val0 = 1;", encoding="utf-8")
        dialog = InstallDialog(self.manager.inspect_install(sql))
        self.addCleanup(dialog.deleteLater)
        self.assertIsNone(dialog.picker)
        self.assertIsNone(dialog.split_box)

    def test_ok_is_disabled_when_nothing_is_ticked(self) -> None:
        dialog = InstallDialog(self.manager.inspect_install(self.rework))
        self.addCleanup(dialog.deleteLater)
        self.assertTrue(dialog.ok_button.isEnabled())
        dialog.picker._set_all(False)
        dialog._validate()
        self.assertFalse(dialog.ok_button.isEnabled())

    def test_details_carry_the_selection_through(self) -> None:
        dialog = InstallDialog(self.manager.inspect_install(self.rework))
        self.addCleanup(dialog.deleteLater)
        dialog.name_edit.setText("Big Rework")
        for index in range(dialog.picker.tree.topLevelItemCount()):
            item = dialog.picker.tree.topLevelItem(index)
            if item.text(0) != "master_skill":
                item.setCheckState(0, Qt.CheckState.Unchecked)

        details = dialog.details()
        self.assertEqual(set(details.selection), {"master_skill"})
        mods = self.manager.install_database(
            self.manager.inspect_install(self.rework),
            details.name,
            selection=details.selection,
            split_by_table=details.split_by_table,
        )
        self.assertEqual([m.id for m in mods], ["Big Rework"])
        self.assertEqual([p.key for p in mods[0].patches], ["master_skill"])


if __name__ == "__main__":
    unittest.main()
