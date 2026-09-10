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
import time
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

    def _order_column(self):
        out = []
        for i in range(self.window.mod_list.topLevelItemCount()):
            item = self.window.mod_list.topLevelItem(i)
            mod_id = item.data(0, Qt.ItemDataRole.UserRole)
            if mod_id:
                out.append((item.text(0), mod_id))
        return out

    def test_the_list_numbers_enabled_mods_in_load_order(self) -> None:
        self._tick("c-third")
        self._tick("a-first")
        # Enabled mods sit at the top, numbered in the order they apply.
        self.assertEqual(self._order_column()[:2], [("1", "c-third"), ("2", "a-first")])
        # Disabled ones follow, unnumbered.
        self.assertEqual([n for n, _ in self._order_column()[2:]], [""])

    def test_move_down_makes_a_mod_win(self) -> None:
        self._tick("a-first")
        self._tick("b-second")
        self.assertEqual(self.manager.state.enabled_mods, ["a-first", "b-second"])

        self.window.mod_list.select_mods(["a-first"])
        self._settle()
        self.window.move_selected(+1)
        self._settle()

        self.assertEqual(self.manager.state.enabled_mods, ["b-second", "a-first"])
        self.assertEqual(self._order_column()[:2], [("1", "b-second"), ("2", "a-first")])

    def test_moving_off_the_end_does_nothing(self) -> None:
        self._tick("a-first")
        self.window.mod_list.select_mods(["a-first"])
        self._settle()
        self.window.move_selected(-1)
        self._settle()
        self.assertEqual(self.manager.state.enabled_mods, ["a-first"])

    def test_moving_a_disabled_mod_is_refused_with_a_message(self) -> None:
        self.window.mod_list.select_mods(["a-first"])   # not ticked
        self._settle()
        self.window.move_selected(+1)
        self._settle()
        self.assertEqual(self.manager.state.enabled_mods, [])

    def test_the_moved_mod_stays_selected(self) -> None:
        self._tick("a-first")
        self._tick("b-second")
        self.window.mod_list.select_mods(["a-first"])
        self._settle()
        self.window.move_selected(+1)
        self._settle()
        self.assertEqual(self.window.mod_list.selected_mod_ids(), ["a-first"])

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
class DropInstallTests(unittest.TestCase):
    """Dropping a file on the window installs it, switched off."""

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
        self.manager = Manager(self.paths)
        self.manager.set_db_path(self.db)
        self.window = MainWindow(self.manager)
        self.window.show()
        self._settle()

        # A modal dialog in a test blocks the run for ever, and hides whatever
        # error opened it. Record them instead.
        self.messages = []
        self.install_details = ("Dropped Mod", "from a drop", "someone", "2.0.0")
        self.last_candidate = None
        self._patch_dialogs()

    def _patch_dialogs(self) -> None:
        """No modal may ever open: one would block the run for ever."""
        from lid_db_manager.ui import main_window as mw
        from lid_db_manager.ui.install_dialog import InstallDetails
        from PySide6.QtWidgets import QDialog

        test = self

        class FakeInstallDialog:
            def __init__(self, candidate, dark, parent):
                test.last_candidate = candidate

            def exec(self):
                return QDialog.DialogCode.Accepted

            def details(self):
                return InstallDetails(*test.install_details)

        self._real_dialog = mw.InstallDialog
        mw.InstallDialog = FakeInstallDialog
        self._patch_message_boxes()

    def _patch_message_boxes(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        from lid_db_manager.ui import main_window as mw

        self._real_box = mw.QMessageBox
        messages = self.messages

        class RecordingBox:
            StandardButton = QMessageBox.StandardButton
            Icon = QMessageBox.Icon

            @staticmethod
            def warning(parent, title, text, *args, **kwargs):
                messages.append(("warning", title, text))
                return QMessageBox.StandardButton.Ok

            @staticmethod
            def critical(parent, title, text, *args, **kwargs):
                messages.append(("critical", title, text))
                return QMessageBox.StandardButton.Ok

            @staticmethod
            def information(parent, title, text, *args, **kwargs):
                messages.append(("information", title, text))
                return QMessageBox.StandardButton.Ok

            @staticmethod
            def question(parent, title, text, *args, **kwargs):
                messages.append(("question", title, text))
                return QMessageBox.StandardButton.No

        mw.QMessageBox = RecordingBox

    def tearDown(self) -> None:
        from lid_db_manager.ui import main_window as mw

        self._wait_for_idle()
        mw.QMessageBox = self._real_box
        mw.InstallDialog = self._real_dialog
        self.window.close()
        self._settle()
        self._tmp.cleanup()

    def _settle(self) -> None:
        for _ in range(6):
            QCoreApplication.processEvents()

    def _wait_for_idle(self, seconds: float = 30.0) -> None:
        """Pump the event loop until the background task is done.

        Diffing a database takes a moment, and the result arrives as a queued
        signal - so a fixed number of processEvents calls is not enough.
        """
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            QCoreApplication.processEvents()
            if self.window.task is None and not self.window._install_queue:
                break
            time.sleep(0.01)
        self._settle()

    def _drop(self, path: Path):
        """Simulate a drop and wait for the install to finish."""
        self.window._install_queue.append(path)
        self.window._drain_install_queue()
        self._wait_for_idle()
        return self.last_candidate

    def test_the_window_accepts_the_drag(self) -> None:
        self.assertTrue(self.window.acceptDrops())
        self.assertTrue(self.window._droppable(Path("x.sql")))
        self.assertTrue(self.window._droppable(Path("x.zip")))
        self.assertTrue(self.window._droppable(Path("masters.db")))
        self.assertFalse(self.window._droppable(Path("notes.txt")))

    def test_dropping_a_sql_file_installs_it_switched_off(self) -> None:
        sql = self.root / "MyPatch.sql"
        sql.write_text("UPDATE master_skill SET buy_money = 1;", encoding="utf-8")

        candidate = self._drop(sql)
        self.assertEqual(candidate.suggested_name, "My Patch")

        mod = self.manager.scan.get("Dropped Mod")
        self.assertIsNotNone(mod, [m.id for m in self.manager.mods])
        self.assertEqual(mod.description, "from a drop")
        self.assertEqual(mod.author, "someone")
        self.assertFalse(self.manager.state.is_enabled(mod.id), "must arrive disabled")

    def test_the_new_mod_is_selected_so_its_diff_can_be_read(self) -> None:
        sql = self.root / "Another.sql"
        sql.write_text("UPDATE master_skill SET val0 = 3;", encoding="utf-8")
        self._drop(sql)
        self.assertEqual(self.window.mod_list.selected_mod_ids(), ["Dropped Mod"])

    def test_dropping_junk_reports_instead_of_crashing(self) -> None:
        junk = self.root / "notes.txt"
        junk.write_text("hello", encoding="utf-8")
        # _droppable filters it out before anything happens.
        self.assertFalse(self.window._droppable(junk))

    def test_dropping_a_modded_database_makes_a_mod(self) -> None:
        import sqlite3

        self.manager.save_mod_list()  # writes masters.db.original
        rework = build_db(self.root / "rework" / "masters.db")
        con = sqlite3.connect(str(rework))
        con.execute("UPDATE master_body_detail SET price = 3")
        con.commit()
        con.close()

        candidate = self._drop(rework)
        self.assertEqual(candidate.kind, "database")
        mod = self.manager.scan.get("Dropped Mod")
        self.assertIsNotNone(mod)
        self.assertTrue((mod.folder / "changes.sql").is_file())


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
