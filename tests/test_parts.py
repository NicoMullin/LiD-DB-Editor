"""Switching parts of a single mod on and off.

An imported rework is one mod with a patch per table. Keeping it one mod means
it keeps one name, one readme and one entry in the list - but the player still
has to be able to take its shop changes and leave its enemy tuning.

The trick is that nothing downstream knows about any of this: the manager hands
out mods with the switched-off patches already removed, so the validator, the
snapshotter, the conflict checker and the runner all just see a smaller mod.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from fixtures import build_db, query, write_mod

from lid_db_manager.manager import Manager
from lid_db_manager.paths import AppPaths
from lid_db_manager.state import State

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QCoreApplication, Qt
    from PySide6.QtWidgets import QApplication

    from lid_db_manager.ui.mod_list import MOD_ID_ROLE, PATCH_KEY_ROLE

    HAVE_QT = True
except ImportError:  # pragma: no cover - depends on the environment
    HAVE_QT = False

THREE_PARTS = {
    "patches": [
        {
            "type": "update_set", "id": "skills", "description": "Skill costs",
            "table": "master_skill", "set": {"buy_money": 1}, "where": "1=1",
        },
        {
            "type": "update_set", "id": "shop", "description": "Shop prices",
            "table": "master_shop_product_price", "set": {"price": 2}, "where": "1=1",
        },
        {
            "type": "update_set", "id": "bodies", "description": "Body prices",
            "table": "master_body_detail", "set": {"price": 3}, "where": "1=1",
        },
    ]
}


class SwitchingPartsOnAndOff(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.paths = AppPaths(self.root / "app").ensure()
        self.db = build_db(self.root / "game" / "masters.db")
        self.manager = Manager(self.paths)
        self.manager.set_db_path(self.db)
        write_mod(self.paths.mods_dir, "rework", THREE_PARTS)
        self.manager.rescan()
        self.manager.set_enabled("rework", True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _prices(self) -> tuple:
        return (
            {r[0] for r in query(self.db, "SELECT buy_money FROM master_skill")},
            {r[0] for r in query(self.db, "SELECT price FROM master_shop_product_price")},
            {r[0] for r in query(self.db, "SELECT price FROM master_body_detail")},
        )

    def test_every_part_is_on_to_begin_with(self) -> None:
        """A mod with no recorded choices is simply a whole mod."""
        self.assertEqual(len(self.manager.enabled_mods()[0].patches), 3)
        self.assertEqual(self.manager.state.disabled_patches, {})

    def test_a_switched_off_part_does_not_reach_the_runner(self) -> None:
        self.manager.set_part_enabled("rework", "shop", False)
        keys = [p.key for p in self.manager.enabled_mods()[0].patches]
        self.assertEqual(keys, ["skills", "bodies"])

    def test_the_list_still_sees_the_whole_mod(self) -> None:
        """Otherwise the switch for a switched-off part would vanish with it."""
        self.manager.set_part_enabled("rework", "shop", False)
        listed = next(m for m in self.manager.listed_mods() if m.id == "rework")
        self.assertEqual(len(listed.patches), 3)

    def test_only_the_parts_left_on_are_applied(self) -> None:
        self.manager.set_part_enabled("rework", "shop", False)
        self.assertTrue(self.manager.save_mod_list().ok)
        skills, shop, bodies = self._prices()
        self.assertEqual(skills, {1}, "skills part was on")
        self.assertEqual(bodies, {3}, "bodies part was on")
        self.assertNotEqual(shop, {2}, "shop part was off and must not have run")

    def test_switching_a_part_back_on_applies_it(self) -> None:
        self.manager.set_part_enabled("rework", "shop", False)
        self.manager.save_mod_list()
        self.manager.set_part_enabled("rework", "shop", True)
        self.assertTrue(self.manager.save_mod_list().ok)
        self.assertEqual(self._prices()[1], {2})

    def test_what_a_part_affects_follows_the_switches(self) -> None:
        """The Affects column should not promise a table the mod will not touch."""
        self.manager.set_part_enabled("rework", "shop", False)
        mod = self.manager.enabled_mods()[0]
        self.assertNotIn("master_shop_product_price", mod.tables())

    def test_reverting_undoes_only_what_was_applied(self) -> None:
        self.manager.set_part_enabled("rework", "shop", False)
        self.manager.save_mod_list()
        before_shop = self._prices()[1]

        results = self.manager.revert(["rework"])
        self.assertTrue(results[0].ok, results[0].error)
        skills, shop, bodies = self._prices()
        self.assertNotEqual(skills, {1}, "the applied part should be undone")
        self.assertEqual(shop, before_shop, "the part that never ran is untouched")

    def test_the_choice_survives_a_restart(self) -> None:
        self.manager.set_part_enabled("rework", "shop", False)
        self.manager.state.save()
        reloaded = State.load(self.paths.state_file)
        self.assertEqual(reloaded.disabled_patches, {"rework": ["shop"]})
        self.assertFalse(reloaded.is_part_enabled("rework", "shop"))
        self.assertTrue(reloaded.is_part_enabled("rework", "skills"))

    def test_turning_every_part_back_on_leaves_no_clutter_in_state(self) -> None:
        self.manager.set_part_enabled("rework", "shop", False)
        self.manager.set_part_enabled("rework", "shop", True)
        self.assertEqual(self.manager.state.disabled_patches, {})

    def test_switching_a_part_off_invalidates_the_cached_delta(self) -> None:
        """No file changed on disk, so the mtime-based key would miss it."""
        mod = self.manager.enabled_mods()[0]
        first = self.manager.mod_delta(mod)
        self.assertIsNotNone(first)
        self.manager.set_part_enabled("rework", "shop", False)
        second = self.manager.mod_delta(self.manager.enabled_mods()[0])
        self.assertLess(len(second.tables), len(first.tables))

    def test_a_part_key_defaults_to_its_position_when_unnamed(self) -> None:
        write_mod(
            self.paths.mods_dir,
            "unnamed",
            {
                "patches": [
                    {"type": "update_set", "table": "master_skill",
                     "set": {"val0": 1}, "where": "1=1"},
                    {"type": "update_set", "table": "master_body_detail",
                     "set": {"price": 1}, "where": "1=1"},
                ]
            },
        )
        self.manager.rescan()
        mod = self.manager.scan.get("unnamed")
        self.assertEqual([p.key for p in mod.patches], ["#1", "#2"])


@unittest.skipUnless(HAVE_QT, "PySide6 is not installed")
class PartsInTheList(unittest.TestCase):
    app = None

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        from lid_db_manager.ui.main_window import MainWindow

        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.paths = AppPaths(self.root).ensure()
        self.db = build_db(self.root / "game" / "masters.db")
        write_mod(self.paths.mods_dir, "rework", THREE_PARTS)
        write_mod(
            self.paths.mods_dir,
            "simple",
            {
                "patches": [
                    {"type": "update_set", "table": "master_skill",
                     "set": {"val0": 8}, "where": "1=1"}
                ]
            },
        )
        self.manager = Manager(self.paths)
        self.manager.set_db_path(self.db)
        self.window = MainWindow(self.manager)
        self.window.show()
        self._settle()

    def tearDown(self) -> None:
        self.window.close()
        self._settle()
        self._tmp.cleanup()

    def _settle(self) -> None:
        for _ in range(6):
            QCoreApplication.processEvents()

    def _row(self, mod_id: str):
        for index in range(self.window.mod_list.topLevelItemCount()):
            item = self.window.mod_list.topLevelItem(index)
            if item.data(0, MOD_ID_ROLE) == mod_id:
                return item
        raise AssertionError(f"{mod_id} is not in the list")

    def _part_rows(self, mod_id: str) -> list:
        row = self._row(mod_id)
        return [
            row.child(i)
            for i in range(row.childCount())
            if row.child(i).data(0, PATCH_KEY_ROLE)
        ]

    def test_a_multi_part_mod_gets_a_switch_per_part(self) -> None:
        keys = [c.data(0, PATCH_KEY_ROLE) for c in self._part_rows("rework")]
        self.assertEqual(keys, ["skills", "shop", "bodies"])

    def test_a_single_patch_mod_gets_none(self) -> None:
        """Nothing to choose between, so no extra rows to wade through."""
        self.assertEqual(self._part_rows("simple"), [])

    def test_the_part_rows_name_what_they_touch(self) -> None:
        rows = self._part_rows("rework")
        self.assertEqual(rows[0].text(1), "Skill costs")
        self.assertEqual(rows[1].text(3), "master_shop_product_price")

    def test_unticking_a_part_records_it(self) -> None:
        self._part_rows("rework")[1].setCheckState(0, Qt.CheckState.Unchecked)
        self._settle()
        self.assertEqual(self.manager.state.disabled_patches, {"rework": ["shop"]})

    def test_the_untick_survives_the_rebuild_it_triggers(self) -> None:
        """Same use-after-free hazard as the mod checkboxes - see test_ui."""
        self._part_rows("rework")[1].setCheckState(0, Qt.CheckState.Unchecked)
        self._settle()
        rows = self._part_rows("rework")
        self.assertEqual(rows[1].checkState(0), Qt.CheckState.Unchecked)
        self.assertEqual(rows[0].checkState(0), Qt.CheckState.Checked)

    def test_ticking_it_back_on_clears_the_record(self) -> None:
        self._part_rows("rework")[1].setCheckState(0, Qt.CheckState.Unchecked)
        self._settle()
        self._part_rows("rework")[1].setCheckState(0, Qt.CheckState.Checked)
        self._settle()
        self.assertEqual(self.manager.state.disabled_patches, {})

    def test_toggling_parts_does_not_touch_the_enabled_list(self) -> None:
        self._part_rows("rework")[0].setCheckState(0, Qt.CheckState.Unchecked)
        self._settle()
        self.assertEqual(self.manager.state.enabled_mods, [])

    def test_the_detail_line_is_not_mistaken_for_a_part(self) -> None:
        row = self._row("simple")
        self.assertGreater(row.childCount(), 0, "the detail line should still be there")
        self.assertIsNone(row.child(0).data(0, PATCH_KEY_ROLE))


if __name__ == "__main__":
    unittest.main()
