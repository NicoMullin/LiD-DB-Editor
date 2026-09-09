"""GUI tests. Skipped entirely when PySide6 is not installed.

These run offscreen, so they need no display and pop no windows.

The toggle tests exist because of a real crash: rebuilding the mod list from
inside ``QTreeWidget.itemChanged`` frees the item Qt is still emitting for, and
the process segfaults. A regression there takes the whole test run down with a
segfault rather than a failure - which is the loudest possible signal.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from fixtures import build_db, write_mod

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QCoreApplication, Qt
    from PySide6.QtWidgets import QApplication

    HAVE_QT = True
except ImportError:  # pragma: no cover - depends on the environment
    HAVE_QT = False

PROJECT_ROOT = Path(__file__).resolve().parent.parent

LONG_DESCRIPTION = (
    "Every grade's revive cost becomes 1 KC, including the default revive. The sign "
    "held up in-game is a texture, so it still shows the old price - you pay 1 KC "
    "either way, but matching the picture means replacing that texture yourself."
)


@unittest.skipUnless(HAVE_QT, "PySide6 is not installed")
class ModListToggleTests(unittest.TestCase):
    app = None

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        from lid_db_manager.manager import Manager
        from lid_db_manager.paths import AppPaths
        from lid_db_manager.ui.main_window import MainWindow

        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.paths = AppPaths(self.root).ensure()
        self.db = build_db(self.root / "game" / "masters.db")

        for mod_id, description in (
            ("a-first", "short"),
            ("b-second", LONG_DESCRIPTION),
            ("c-third", "short"),
        ):
            write_mod(
                self.paths.mods_dir,
                mod_id,
                {
                    "description": description,
                    "patches": [
                        {
                            "type": "update_set",
                            "table": "master_skill",
                            "set": {"buy_money": 1},
                            "where": "buy_money > 1",
                        }
                    ],
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
        """Run the event loop briefly - toggles are handed over on a timer."""
        for _ in range(5):
            QCoreApplication.processEvents()

    def _row(self, mod_id: str):
        for index in range(self.window.mod_list.topLevelItemCount()):
            item = self.window.mod_list.topLevelItem(index)
            if item.data(0, Qt.ItemDataRole.UserRole) == mod_id:
                return item
        raise AssertionError(f"{mod_id} is not in the list")

    def _tick(self, mod_id: str, checked: bool = True) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        self._row(mod_id).setCheckState(0, state)
        self._settle()

    def test_ticking_every_box_in_turn_does_not_crash(self) -> None:
        for mod_id in ("a-first", "b-second", "c-third"):
            self._tick(mod_id)
        self.assertEqual(
            sorted(self.manager.state.enabled_mods), ["a-first", "b-second", "c-third"]
        )

    def test_a_toggle_survives_the_rebuild_it_triggers(self) -> None:
        self._tick("b-second")
        self.assertEqual(self.manager.state.enabled_mods, ["b-second"])
        # The list was rebuilt, so this is a fresh item, not the one we ticked.
        self.assertEqual(self._row("b-second").checkState(0), Qt.CheckState.Checked)

    def test_unticking_works_too(self) -> None:
        self._tick("a-first")
        self._tick("a-first", checked=False)
        self.assertEqual(self.manager.state.enabled_mods, [])
        self.assertEqual(self._row("a-first").checkState(0), Qt.CheckState.Unchecked)

    def test_a_toggle_is_written_to_state_immediately(self) -> None:
        from lid_db_manager.state import State

        self._tick("c-third")
        self.assertEqual(State.load(self.paths.state_file).enabled_mods, ["c-third"])

    def test_enabling_everything_at_once_rebuilds_the_list_only_once(self) -> None:
        rebuilds = []
        original = self.window.mod_list.refresh
        self.window.mod_list.refresh = lambda: (rebuilds.append(1), original())[1]

        self.window.mod_list.set_all_checked(True)
        self._settle()

        self.assertEqual(sorted(self.manager.state.enabled_mods), ["a-first", "b-second", "c-third"])
        self.assertEqual(len(rebuilds), 1, "three toggles should coalesce into one rebuild")

    def test_disabling_everything_at_once(self) -> None:
        self.window.mod_list.set_all_checked(True)
        self._settle()
        self.window.mod_list.set_all_checked(False)
        self._settle()
        self.assertEqual(self.manager.state.enabled_mods, [])


@unittest.skipUnless(HAVE_QT, "PySide6 is not installed")
class WindowSmokeTests(unittest.TestCase):
    """The window builds against the real mods folder and survives a refresh."""

    app = None

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        from lid_db_manager.manager import Manager
        from lid_db_manager.paths import AppPaths
        from lid_db_manager.ui.main_window import MainWindow

        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        paths = AppPaths(root).ensure()
        shutil.rmtree(paths.mods_dir)
        shutil.copytree(PROJECT_ROOT / "mods", paths.mods_dir)
        self.db = build_db(root / "game" / "masters.db")

        self.manager = Manager(paths)
        self.manager.set_db_path(self.db)
        self.window = MainWindow(self.manager)
        self.window.show()
        for _ in range(5):
            QCoreApplication.processEvents()

    def tearDown(self) -> None:
        self.window.close()
        QCoreApplication.processEvents()
        self._tmp.cleanup()

    def test_the_shipped_mods_all_appear(self) -> None:
        self.assertEqual(self.window.mod_list.topLevelItemCount(), len(self.manager.mods))

    def test_selecting_a_mod_fills_the_side_panel(self) -> None:
        self.window.mod_list.select_mods(["revive-cost-1kc"])
        QCoreApplication.processEvents()
        self.assertIn("Revive Cost", self.window.diff_view.title.text())
        self.assertIn("master_shop_product_price", self.window.diff_view.details.toPlainText())

    def test_the_theme_toggle_rebuilds_cleanly(self) -> None:
        self.window.dark_box.setChecked(False)
        QCoreApplication.processEvents()
        self.window.dark_box.setChecked(True)
        QCoreApplication.processEvents()
        self.assertTrue(self.window.dark)


if __name__ == "__main__":
    unittest.main()
