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
    from PySide6.QtCore import QCoreApplication, QEvent, Qt
    from PySide6.QtWidgets import QApplication

    HAVE_QT = True
except ImportError:  # pragma: no cover - depends on the environment
    HAVE_QT = False


def destroy(window) -> None:
    """Close a test window and actually get rid of it.

    ``close()`` only hides it, and ``deleteLater()`` alone does nothing here
    because ``processEvents()`` deliberately skips DeferredDelete. Without the
    last line this module ended a run with ten thousand live widgets, and every
    later test that hands the application a style sheet - which changing the text
    size does - had to re-style all of them.
    """
    if window is None:
        return
    window.close()
    window.setParent(None)
    window.deleteLater()
    app = QApplication.instance()
    if app is not None:
        # Only the deferred deletes, not processEvents(): pumping the whole queue
        # here also fires the one-shot first-run timer of every window some other
        # test module left alive, each of which puts up a modal box.
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)

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
        destroy(self.window)
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

    def test_right_clicking_a_mod_offers_what_you_can_do_to_it(self) -> None:
        menu = self.window._build_mod_menu(self._row("a-first"))
        labels = [a.text() for a in menu.actions() if not a.isSeparator()]
        for wanted in ("Enable", "Edit details...", "Delete mod...", "Open mod folder"):
            self.assertIn(wanted, labels)
        menu.deleteLater()

    def test_right_clicking_acts_on_the_mod_under_the_cursor(self) -> None:
        self.window.mod_list.select_mods(["c-third"])
        menu = self.window._build_mod_menu(self._row("a-first"))
        self.assertEqual(self.window.mod_list.selected_mod_ids(), ["a-first"])
        menu.deleteLater()

    def test_the_menu_off_a_mod_does_not_offer_mod_actions(self) -> None:
        menu = self.window._build_mod_menu(None)
        labels = [a.text() for a in menu.actions() if not a.isSeparator()]
        self.assertNotIn("Delete mod...", labels)
        self.assertIn("Rescan mods folder", labels)
        menu.deleteLater()

    def test_an_enabled_mod_is_offered_Disable_instead(self) -> None:
        self._tick("a-first")
        menu = self.window._build_mod_menu(self._row("a-first"))
        labels = [a.text() for a in menu.actions() if not a.isSeparator()]
        self.assertIn("Disable", labels)
        self.assertNotIn("Enable", labels)
        menu.deleteLater()

    def test_deleting_a_mod_removes_its_folder(self) -> None:
        from unittest import mock

        from lid_db_manager.ui import main_window as window_module

        folder = self.paths.mods_dir / "a-first"
        self.assertTrue(folder.is_dir())
        self.window.mod_list.select_mods(["a-first"])
        with mock.patch.object(
            window_module.QMessageBox,
            "question",
            return_value=window_module.QMessageBox.StandardButton.Yes,
        ):
            self.window.delete_selected_mod()
        self._settle()
        self.assertFalse(folder.exists())
        self.assertIsNone(self.manager.scan.get("a-first"))
        self.assertNotIn("a-first", self.manager.state.enabled_mods)

    def test_declining_the_delete_keeps_the_mod(self) -> None:
        from unittest import mock

        from lid_db_manager.ui import main_window as window_module

        self.window.mod_list.select_mods(["a-first"])
        with mock.patch.object(
            window_module.QMessageBox,
            "question",
            return_value=window_module.QMessageBox.StandardButton.No,
        ):
            self.window.delete_selected_mod()
        self._settle()
        self.assertTrue((self.paths.mods_dir / "a-first").is_dir())
        self.assertIsNotNone(self.manager.scan.get("a-first"))

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
class ModListFoldingTests(unittest.TestCase):
    """The list folds up, and a rebuild keeps where you were.

    Every toggle rebuilds the whole list, so anything the rebuild does not
    carry across is lost on each tick - which is how the scroll position used
    to be thrown back to the top mid-way down a long list.
    """

    app = None
    COUNT = 30

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
        for index in range(self.COUNT):
            write_mod(
                self.paths.mods_dir,
                f"mod-{index:02d}",
                {
                    "description": LONG_DESCRIPTION,
                    "patches": [
                        {
                            "type": "update_set",
                            "table": "master_skill",
                            "set": {"buy_money": index + 1},
                            "where": "buy_money > 1",
                        }
                    ],
                },
            )
        self.manager = Manager(self.paths)
        self.manager.set_db_path(self.db)
        self.window = MainWindow(self.manager)
        self.window.resize(700, 320)
        self.window.show()
        self._settle()

    def tearDown(self) -> None:
        destroy(self.window)
        self._settle()
        self._tmp.cleanup()

    def _settle(self) -> None:
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

    def test_a_mod_starts_folded_with_an_arrow_to_open_it(self) -> None:
        item = self._row("mod-00")
        self.assertGreater(item.childCount(), 0, "there is nothing to fold")
        self.assertFalse(item.isExpanded())
        self.assertTrue(self.window.mod_list.rootIsDecorated(), "no arrow to click")

    def test_opening_one_mod_survives_the_next_toggle(self) -> None:
        self._row("mod-03").setExpanded(True)
        self._settle()
        self._tick("mod-10")
        self.assertTrue(self._row("mod-03").isExpanded(), "it folded itself back up")
        self.assertFalse(self._row("mod-04").isExpanded(), "everything else opened too")

    def test_folding_one_back_up_is_remembered_too(self) -> None:
        self._row("mod-03").setExpanded(True)
        self._settle()
        self._row("mod-03").setExpanded(False)
        self._settle()
        self._tick("mod-10")
        self.assertFalse(self._row("mod-03").isExpanded())

    def test_expand_all_and_collapse_all(self) -> None:
        self.window.mod_list.set_all_expanded(True)
        self._settle()
        self.assertTrue(all(
            self._row(f"mod-{i:02d}").isExpanded() for i in range(self.COUNT)
        ))
        self.window.mod_list.set_all_expanded(False)
        self._settle()
        self.assertFalse(any(
            self._row(f"mod-{i:02d}").isExpanded() for i in range(self.COUNT)
        ))

    def test_the_scroll_position_survives_a_toggle(self) -> None:
        bar = self.window.mod_list.verticalScrollBar()
        if bar.maximum() == 0:
            self.skipTest("the whole list fits on screen, so there is no scrolling to keep")
        bar.setValue(bar.maximum() // 2)
        where = bar.value()
        self.assertGreater(where, 0)
        self._tick("mod-20")
        self.assertEqual(bar.value(), where, "the list jumped back to the top")

    def _dot_color(self, mod_id: str):
        icon = self._row(mod_id).icon(1)
        if icon.isNull():
            return None
        image = icon.pixmap(12, 12).toImage()
        pixel = image.pixelColor(image.width() // 2, image.height() // 2)
        return None if pixel.alpha() == 0 else pixel.name()

    def test_a_mod_with_a_warning_gets_a_dot_but_stays_folded(self) -> None:
        # It used to open itself, which on a long list opened half of it.
        from lid_db_manager.ui.theme import status_color

        self._tick("mod-00")
        self._tick("mod-01")
        if not self.manager.conflicts().for_mod("mod-01"):
            self.skipTest("these two mods did not register as a conflict")
        self.assertFalse(self._row("mod-01").isExpanded(), "it opened itself")
        # Both write buy_money in the same rows: the same cells, so red.
        red = status_color(self.window.mod_list.dark, "failed").name()
        self.assertEqual(self._dot_color("mod-00"), red)
        self.assertEqual(self._dot_color("mod-01"), red)
        self.assertIn("Red dot", self._row("mod-01").toolTip(1))
        self.assertIsNone(self._dot_color("mod-05"), "a mod with no problem got a dot")

    def test_a_yellow_dot_for_a_warning(self) -> None:
        from lid_db_manager.ui.theme import status_color

        # Requiring a mod that is not ticked is worth a look, not an overwrite.
        write_mod(
            self.paths.mods_dir,
            "needy",
            {
                "requires": ["mod-00"],
                "patches": [
                    {"type": "update_set", "table": "master_text", "set": {"txt": "x"},
                     "where": "id = 'TXT_A'"}
                ],
            },
        )
        self.manager.rescan()
        self.window.refresh()
        self._settle()
        self._tick("needy")
        if not self.manager.conflicts().for_mod("needy"):
            self.skipTest("the missing requirement was not reported")
        yellow = status_color(self.window.mod_list.dark, "pending").name()
        self.assertEqual(self._dot_color("needy"), yellow)
        self.assertFalse(self._row("needy").isExpanded())


@unittest.skipUnless(HAVE_QT, "PySide6 is not installed")
class ModConfigurationTabTests(unittest.TestCase):
    """Changing a mod's values from the Configuration tab on the right.

    Not from rows under the mod in the list: a mod can have several values, and
    a list row is no place for a form.
    """

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
        multiplier = {"id": "multiplier", "label": "Multiplier", "type": "integer",
                      "default": 2, "min": 1, "max": 100, "unit": "x"}
        write_mod(self.paths.mods_dir, "cheap-skills", {
            "apply": "diff",
            "settings": [multiplier],
            "patches": [{"type": "raw_sql",
                         "sql": "UPDATE master_skill SET buy_money = buy_money * {{multiplier}};"}],
        })
        write_mod(self.paths.mods_dir, "two-knobs", {
            "apply": "diff",
            "settings": [
                multiplier,
                {"id": "bonus", "label": "Bonus", "type": "integer",
                 "default": 0, "min": 0, "max": 1000, "unit": "KC"},
            ],
            "patches": [{"type": "raw_sql",
                         "sql": "UPDATE master_skill SET val0 = val0 * {{multiplier}} + {{bonus}};"}],
        })
        write_mod(self.paths.mods_dir, "plain", {"patches": [
            {"type": "update_set", "table": "master_body_detail", "set": {"price": 1},
             "where": "price > 1"}]})
        self.manager = Manager(self.paths)
        self.manager.set_db_path(self.db)
        self.window = MainWindow(self.manager)
        self.window.show()
        self._settle()

    def tearDown(self) -> None:
        destroy(self.window)
        self._settle()
        self._tmp.cleanup()

    def _settle(self) -> None:
        for _ in range(5):
            QCoreApplication.processEvents()

    def _row(self, mod_id: str):
        for index in range(self.window.mod_list.topLevelItemCount()):
            item = self.window.mod_list.topLevelItem(index)
            if item.data(0, Qt.ItemDataRole.UserRole) == mod_id:
                return item
        raise AssertionError(f"{mod_id} is not in the list")

    def _open(self, mod_id: str):
        self.window.mod_list.select_mods([mod_id])
        self._settle()
        return self.window.diff_view

    def test_there_is_a_configuration_tab(self) -> None:
        view = self.window.diff_view
        titles = [view.tabs.tabText(i) for i in range(view.tabs.count())]
        self.assertIn("Configuration", titles)

    def test_a_one_value_mod_shows_its_value_in_its_name(self) -> None:
        self.assertIn("x2", self._row("cheap-skills").text(1))

    def test_a_mod_with_several_values_keeps_its_name_clean(self) -> None:
        self.assertNotIn("x2", self._row("two-knobs").text(1))

    def test_the_list_itself_has_no_value_boxes(self) -> None:
        mod_list = self.window.mod_list
        for mod_id in ("cheap-skills", "two-knobs"):
            row = self._row(mod_id)
            for index in range(row.childCount()):
                self.assertIsNone(mod_list.itemWidget(row.child(index), 0))

    def test_every_value_gets_its_own_box(self) -> None:
        view = self._open("two-knobs")
        self.assertIsNotNone(view.editor_for("multiplier"))
        self.assertIsNotNone(view.editor_for("bonus"))

    def _tab_shown(self, view) -> bool:
        return view.tabs.isTabVisible(view.tabs.indexOf(view.configuration))

    def test_the_tab_only_shows_for_a_mod_with_values(self) -> None:
        self.assertTrue(self._tab_shown(self._open("cheap-skills")))
        view = self._open("plain")
        self.assertFalse(self._tab_shown(view))
        self.assertIsNone(view.editor_for("multiplier"))

    def test_moving_to_a_mod_without_values_leaves_the_hidden_tab(self) -> None:
        self.window._open_configuration("cheap-skills")
        self._settle()
        view = self._open("plain")
        self.assertIs(view.tabs.currentWidget(), view.details)

    def test_finishing_an_edit_chooses_the_value(self) -> None:
        editor = self._open("cheap-skills").editor_for("multiplier")
        editor.spin.setValue(7)
        editor.spin.editingFinished.emit()
        self._settle()
        self.assertEqual(self.manager.state.mod_settings, {"cheap-skills": {"multiplier": 7}})
        self.assertIn("x7", self._row("cheap-skills").text(1))

    def test_default_puts_it_back(self) -> None:
        self.manager.set_mod_setting("cheap-skills", "multiplier", 9)
        editor = self._open("cheap-skills").editor_for("multiplier")
        self.assertEqual(editor.value(), 9)
        editor.reset_button.click()
        self._settle()
        self.assertNotIn("cheap-skills", self.manager.state.mod_settings)

    def test_put_all_back_to_defaults(self) -> None:
        self.manager.set_mod_setting("two-knobs", "multiplier", 9)
        self.manager.set_mod_setting("two-knobs", "bonus", 40)
        from PySide6.QtWidgets import QPushButton

        view = self._open("two-knobs")
        buttons = [b for b in view.configuration.widget().findChildren(QPushButton)
                   if b.text() == "Put all back to defaults"]
        self.assertEqual(len(buttons), 1)
        buttons[0].click()
        self._settle()
        self.assertNotIn("two-knobs", self.manager.state.mod_settings)

    def test_save_takes_a_value_still_being_typed(self) -> None:
        editor = self._open("cheap-skills").editor_for("multiplier")
        editor.spin.blockSignals(True)
        editor.spin.setValue(4)
        editor.spin.blockSignals(False)
        self.window._commit_setting_edits()
        self.assertEqual(self.manager.state.mod_settings, {"cheap-skills": {"multiplier": 4}})

    def test_the_panel_does_not_rebuild_under_someone_typing(self) -> None:
        view = self._open("cheap-skills")
        editor = view.editor_for("multiplier")
        view._editing = lambda: True
        view.refresh()
        self.assertIs(view.editor_for("multiplier"), editor)

    def test_change_values_opens_the_configuration_tab(self) -> None:
        def labels(mod_id):
            menu = self.window._build_mod_menu(self._row(mod_id))
            found = [a.text() for a in menu.actions() if not a.isSeparator()]
            menu.deleteLater()
            return found

        self.assertIn("Change values...", labels("cheap-skills"))
        self.assertNotIn("Change values...", labels("plain"))

        self.window._open_configuration("two-knobs")
        self._settle()
        view = self.window.diff_view
        self.assertIs(view.tabs.currentWidget(), view.configuration)
        self.assertEqual(self.window.mod_list.selected_mod_ids(), ["two-knobs"])
        self.assertIsNotNone(view.editor_for("bonus"))


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
            def __init__(self, candidate, dark, parent, asset_mods=None):
                test.last_candidate = candidate
                test.last_asset_mods = asset_mods

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
        destroy(self.window)
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

    def test_dropping_a_modded_database_makes_a_mod_with_a_part_per_table(self) -> None:
        """One mod, but switchable table by table - that is the point of parts."""
        import sqlite3

        self.manager.save_mod_list()  # writes masters.db.original
        rework = build_db(self.root / "rework" / "masters.db")
        con = sqlite3.connect(str(rework))
        con.execute("UPDATE master_body_detail SET price = 3")
        con.execute("UPDATE master_skill SET buy_money = 9")
        con.commit()
        con.close()

        candidate = self._drop(rework)
        self.assertEqual(candidate.kind, "database")

        mod = self.manager.scan.get("Dropped Mod")
        self.assertIsNotNone(mod, [m.id for m in self.manager.mods])
        self.assertEqual(
            sorted(p.key for p in mod.patches),
            ["master_body_detail", "master_skill"],
            "one switchable part per table",
        )
        for patch in mod.patches:
            self.assertTrue((mod.folder / patch.path.name).is_file())
        self.assertFalse(
            self.manager.state.is_enabled(mod.id), "must arrive disabled"
        )


@unittest.skipUnless(HAVE_QT, "PySide6 is not installed")
class AdoptionFlowTests(unittest.TestCase):
    """Taking over a database that was modded before the manager saw it."""

    app = None

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        import sqlite3

        from lid_db_manager.manager import Manager
        from lid_db_manager.paths import AppPaths
        from lid_db_manager.ui.main_window import MainWindow

        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.paths = AppPaths(self.root).ensure()

        clean_dir = self.root / "LiD Vanilla DB" / "5.0.3.0"
        clean_dir.mkdir(parents=True, exist_ok=True)
        self.clean = build_db(clean_dir / "masters.db")
        con = sqlite3.connect(str(self.clean))
        con.execute(
            'CREATE TABLE master_const_str ("id" CHARACTER(64) NOT NULL, '
            '"value" TEXT NOT NULL, PRIMARY KEY ("id"))'
        )
        con.execute(
            "INSERT INTO master_const_str (id, value) VALUES "
            "('TITLE_VERSION', '5.0.3.0.0 - 1.87')"
        )
        con.commit()
        con.close()

        self.db = self.root / "game" / "masters.db"
        self.db.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.clean, self.db)
        con = sqlite3.connect(str(self.db))
        con.execute("UPDATE master_skill SET buy_money = 1 WHERE id = 'SKL_EXPUP_01'")
        con.execute("UPDATE master_body_detail SET price = 42 WHERE id = 'BODY_01'")
        con.commit()
        con.close()

        write_mod(
            self.paths.mods_dir,
            "cheap-exp",
            {
                "patches": [
                    {
                        "type": "update_set",
                        "table": "master_skill",
                        "set": {"buy_money": 1},
                        "where": "id = 'SKL_EXPUP_01'",
                    }
                ]
            },
        )
        self.manager = Manager(self.paths)
        self.manager.set_db_path(self.db)
        # The window offers this by itself on a first run, from a timer. Here
        # that would pop a real modal dialog in the middle of setUp, so the
        # offer is marked as already made and each test drives it directly.
        self.manager.state.adoption_offered = True
        self.manager.state.save()
        self.window = MainWindow(self.manager)
        self._settle()

    def tearDown(self) -> None:
        destroy(self.window)
        self._settle()
        self._tmp.cleanup()

    def _settle(self) -> None:
        for _ in range(5):
            QCoreApplication.processEvents()

    def test_the_scan_finds_the_mod_and_the_hand_edit(self) -> None:
        report = self.manager.adopt_scan()
        self.assertTrue(report.ok, report.error)
        self.assertEqual([m.mod_id for m in report.recognised], ["cheap-exp"])
        self.assertEqual({t.table for t in report.leftover.tables}, {"master_body_detail"})

    def test_the_dialog_offers_the_mod_ticked_and_the_leftover_named(self) -> None:
        from lid_db_manager.ui.adopt_dialog import DEFAULT_CUSTOM_NAME, AdoptDialog

        dialog = AdoptDialog(self.manager.adopt_scan(), False, self.window)
        choice = dialog.choice()
        self.assertEqual(choice.mod_ids, ["cheap-exp"])
        self.assertTrue(choice.make_custom)
        self.assertEqual(choice.custom_name, DEFAULT_CUSTOM_NAME)
        dialog.deleteLater()

    def _accept_with(self, choice):
        """Stand in for the dialog, answering the way a player would have."""
        from unittest import mock

        from lid_db_manager.ui import main_window as window_module

        class FakeDialog:
            def __init__(self, *args, **kwargs):
                pass

            def exec(self):
                from PySide6.QtWidgets import QDialog

                return QDialog.DialogCode.Accepted

            def choice(self):
                return choice

        return mock.patch.object(window_module, "AdoptDialog", FakeDialog)

    def test_accepting_rebuilds_the_database_and_keeps_the_leftover(self) -> None:
        import sqlite3

        from lid_db_manager.ui.adopt_dialog import AdoptChoice

        report = self.manager.adopt_scan()
        choice = AdoptChoice(
            mod_ids=["cheap-exp"], make_custom=True, custom_name="My changes", selection=None
        )
        with self._accept_with(choice):
            self.window._after_adopt_scan(report)
        self._settle()
        for _ in range(60):
            if self.window.task is None or not self.window.task.isRunning():
                break
            self._settle()
            time.sleep(0.05)
        self._settle()

        self.assertIsNotNone(self.manager.scan.get("My changes"), "the leftover mod was not made")
        self.assertIn("cheap-exp", self.manager.state.enabled_mods)
        self.assertIn("My changes", self.manager.state.enabled_mods)

        con = sqlite3.connect(str(self.db))
        try:
            skill = con.execute(
                "SELECT buy_money FROM master_skill WHERE id = 'SKL_EXPUP_01'"
            ).fetchone()[0]
            body = con.execute(
                "SELECT price FROM master_body_detail WHERE id = 'BODY_01'"
            ).fetchone()[0]
        finally:
            con.close()
        self.assertEqual(skill, 1, "the recognised mod is not applied")
        self.assertEqual(body, 42, "the leftover changes were not kept")

    def test_declining_leaves_the_database_exactly_as_it_was(self) -> None:
        from unittest import mock

        from lid_db_manager.ui import main_window as window_module

        before = self.db.read_bytes()

        class RefusingDialog:
            def __init__(self, *args, **kwargs):
                pass

            def exec(self):
                from PySide6.QtWidgets import QDialog

                return QDialog.DialogCode.Rejected

            def choice(self):  # pragma: no cover - never reached
                raise AssertionError("cancelled dialogs are not read")

        with mock.patch.object(window_module, "AdoptDialog", RefusingDialog):
            self.window._after_adopt_scan(self.manager.adopt_scan())
        self._settle()
        self.assertEqual(self.db.read_bytes(), before)
        self.assertEqual(self.manager.state.enabled_mods, [])

    def test_the_offer_is_only_made_once(self) -> None:
        from unittest import mock

        from lid_db_manager.ui import main_window as window_module

        self.manager.state.adoption_offered = False
        self.assertFalse(self.manager.state.adoption_offered)
        with mock.patch.object(window_module, "AdoptDialog") as dialog:
            dialog.return_value.exec.return_value = 0
            self.window._after_adopt_scan(self.manager.adopt_scan())
        self.assertTrue(self.manager.state.adoption_offered)
        with mock.patch.object(self.window, "scan_for_existing_mods") as again:
            self.window._offer_adoption()
            again.assert_not_called()

    def test_the_slow_steps_say_the_program_has_not_frozen(self) -> None:
        from unittest import mock

        with mock.patch.object(self.window, "_run") as run:
            self.window.scan_for_existing_mods()
        message = run.call_args.kwargs.get("message", "")
        self.assertIn("not frozen", message.lower())
        self.assertIn("comparing", message.lower())

        report = self.manager.adopt_scan()
        from lid_db_manager.ui.adopt_dialog import AdoptChoice

        choice = AdoptChoice(mod_ids=["cheap-exp"], make_custom=False)
        with self._accept_with(choice), mock.patch.object(self.window, "_run") as run:
            self.window._after_adopt_scan(report)
        message = run.call_args.kwargs.get("message", "")
        self.assertIn("not frozen", message.lower())
        self.assertIn("rebuilding", message.lower())

    def test_the_progress_window_is_closed_when_the_work_finishes(self) -> None:
        done = []
        self.window._run(lambda: 42, done.append, message="Working on it...")
        self.assertIsNotNone(self.window._progress, "no progress window was shown")
        for _ in range(80):
            if self.window.task is None:
                break
            self._settle()
            time.sleep(0.05)
        self._settle()
        self.assertEqual(done, [42])
        self.assertIsNone(self.window._progress, "the progress window was left on screen")

    def test_applying_shows_a_bar_that_fills(self) -> None:
        import threading

        reported = threading.Event()
        release = threading.Event()

        def work(progress):
            progress.stage("Writing game files...", 40, 60)
            reported.set()
            release.wait(5)
            return "done"

        done = []
        self.window._run(work, done.append, message="Applying your mods...", progress=True)
        dialog = self.window._progress
        self.assertIsNotNone(dialog, "no progress window was shown")
        self.assertEqual(dialog.maximum(), 100, "a bar that fills, not one that just moves")
        self.assertTrue(reported.wait(5))
        for _ in range(40):
            self._settle()
            if dialog.value() == 40:
                break
            time.sleep(0.05)
        self.assertEqual(dialog.value(), 40)
        self.assertIn("Writing game files", dialog.labelText())
        release.set()
        for _ in range(80):
            if self.window.task is None:
                break
            self._settle()
            time.sleep(0.05)
        self._settle()
        self.assertEqual(done, ["done"])
        self.assertIsNone(self.window._progress)

    def test_save_and_reapply_both_show_the_bar(self) -> None:
        from unittest import mock

        for action in (self.window.save_mod_list, self.window.reapply_all):
            with mock.patch.object(self.window, "_check_database", return_value=True), \
                    mock.patch.object(self.window, "_run") as run:
                action()
            self.assertTrue(run.call_args.kwargs.get("progress"), action.__name__)
            self.assertTrue(run.call_args.kwargs.get("message"), action.__name__)

    def test_a_failing_task_also_closes_the_progress_window(self) -> None:
        from unittest import mock

        from lid_db_manager.ui import main_window as window_module

        def boom():
            raise RuntimeError("nope")

        with mock.patch.object(window_module.QMessageBox, "critical"):
            self.window._run(boom, lambda result: None, message="Working on it...")
            for _ in range(80):
                if self.window.task is None:
                    break
                self._settle()
                time.sleep(0.05)
            self._settle()
        self.assertIsNone(self.window._progress)

    def test_an_existing_install_is_never_asked(self) -> None:
        """Someone already using the manager applied those mods themselves."""
        from unittest import mock

        from lid_db_manager.state import AppliedRecord

        self.manager.state.adoption_offered = False
        self.manager.state.applied["cheap-exp"] = AppliedRecord(
            applied_at="2026-09-16T10:00:00", rows_changed=1, name="cheap-exp"
        )
        with mock.patch.object(self.window, "scan_for_existing_mods") as scan:
            self.window._offer_adoption()
            scan.assert_not_called()
        self.assertTrue(
            self.manager.state.adoption_offered, "it would ask again on the next launch"
        )


@unittest.skipUnless(HAVE_QT, "PySide6 is not installed")
class TheMenusTests(unittest.TestCase):
    """Mods is the list itself; Tools is the database and the game folder."""

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
        self.db = build_db(root / "game" / "masters.db")
        self.manager = Manager(paths)
        self.manager.set_db_path(self.db)
        self.window = MainWindow(self.manager)

    def tearDown(self) -> None:
        destroy(self.window)
        QCoreApplication.processEvents()
        self._tmp.cleanup()

    def _menu(self, title: str):
        for action in self.window.menuBar().actions():
            if action.text().replace("&", "") == title:
                return action.menu()
        raise AssertionError(f"no {title} menu")

    def _items(self, title: str) -> list[str]:
        return [a.text() for a in self._menu(title).actions() if not a.isSeparator()]

    def _sections(self, title: str) -> list[str]:
        return [a.text() for a in self._menu(title).actions() if a.isSeparator() and a.text()]

    def test_the_menu_bar_reads_left_to_right_in_the_order_you_use_it(self) -> None:
        titles = [a.text().replace("&", "") for a in self.window.menuBar().actions()]
        self.assertEqual(titles, ["File", "Mods", "Tools", "View", "Help"])

    def test_the_view_menu_holds_the_text_size(self) -> None:
        items = self._items("View")
        for expected in ("Bigger text", "Smaller text", "Normal size"):
            self.assertIn(expected, items)
        self.assertIn("100%", items)

    def test_the_mods_menu_is_grouped(self) -> None:
        self.assertEqual(
            self._sections("Mods"),
            ["The mod list", "Load order", "Make and change mods"],
        )

    def test_the_load_order_actions_are_all_together(self) -> None:
        items = self._items("Mods")
        order = [i for i in items if "load order" in i]
        self.assertEqual(len(order), 4)
        first = items.index(order[0])
        self.assertEqual(items[first:first + 4], order, "they are not adjacent")

    def test_making_mods_moved_off_the_tools_menu(self) -> None:
        tools = self._items("Tools")
        for gone in ("Build a mod...", "Add a mod from a file...", "Delete mod..."):
            self.assertNotIn(gone, tools)
            self.assertIn(gone, self._items("Mods"))

    def test_tools_keeps_what_acts_on_the_database_and_the_game(self) -> None:
        tools = self._items("Tools")
        for kept in (
            "Validate enabled mods",
            "Save Mod List (apply)",
            "Hash Patcher...",
            "Restore a backup...",
        ):
            self.assertIn(kept, tools)

    def test_no_panel_paints_words_with_qts_own_greys(self) -> None:
        """palette(mid) and friends are not in the theme, so Qt picks them -
        and on the light window that came out grey on grey."""
        import re

        ui = PROJECT_ROOT / "lid_db_manager" / "ui"
        offenders = [
            path.name
            for path in ui.glob("*.py")
            if re.search(r"palette\((mid|dark|light|shadow|midlight)\)", path.read_text(encoding="utf-8"))
        ]
        self.assertEqual(offenders, [], "use setObjectName('dim') instead")

    def test_the_hash_patcher_is_called_that(self) -> None:
        """It was 'The game's file check...', which said what it read rather
        than what it does."""
        self.assertIn("Hash Patcher...", self._items("Tools"))

    def test_every_shortcut_survived_the_move(self) -> None:
        shortcuts = {}
        for title in ("File", "Mods", "Tools", "Help"):
            for action in self._menu(title).actions():
                key = action.shortcut().toString()
                if key:
                    self.assertNotIn(key, shortcuts, f"{key} is bound twice")
                    shortcuts[key] = action.text()
        for key in ("Ctrl+S", "Ctrl+T", "Ctrl+R", "Ctrl+B", "F2", "Ctrl+Up", "Ctrl+Down"):
            self.assertIn(key, shortcuts)


@unittest.skipUnless(HAVE_QT, "PySide6 is not installed")
class LoadOrderControlTests(unittest.TestCase):
    """Sending a mod to an end of the load order, and typing its place."""

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
        self.paths = AppPaths(root).ensure()
        self.db = build_db(root / "game" / "masters.db")
        for mod_id in ("a", "b", "c", "d"):
            write_mod(
                self.paths.mods_dir,
                mod_id,
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
        self.manager = Manager(self.paths)
        self.manager.set_db_path(self.db)
        for mod_id in ("a", "b", "c", "d"):
            self.manager.set_enabled(mod_id, True)
        self.window = MainWindow(self.manager)
        self.window.show()
        self._settle()

    def tearDown(self) -> None:
        destroy(self.window)
        self._settle()
        self._tmp.cleanup()

    def _settle(self) -> None:
        for _ in range(5):
            QCoreApplication.processEvents()

    def _order(self) -> list[str]:
        return list(self.manager.state.enabled_mods)

    def _row(self, mod_id: str):
        for index in range(self.window.mod_list.topLevelItemCount()):
            item = self.window.mod_list.topLevelItem(index)
            if item.data(0, Qt.ItemDataRole.UserRole) == mod_id:
                return item
        raise AssertionError(f"no row for {mod_id}")

    def test_send_to_the_top_and_to_the_bottom(self) -> None:
        self.window.mod_list.select_mods(["d"])
        self.window.send_selected_to("top")
        self.assertEqual(self._order(), ["d", "a", "b", "c"])

        self.window.mod_list.select_mods(["d"])
        self.window.send_selected_to("bottom")
        self.assertEqual(self._order(), ["a", "b", "c", "d"])

    def test_several_mods_sent_at_once_keep_their_own_order(self) -> None:
        self.window.mod_list.select_mods(["a", "b"])
        self.window.send_selected_to("bottom")
        self.assertEqual(self._order(), ["c", "d", "a", "b"])

        self.window.mod_list.select_mods(["a", "b"])
        self.window.send_selected_to("top")
        self.assertEqual(self._order(), ["a", "b", "c", "d"])

    def test_moving_several_mods_one_step_moves_all_of_them(self) -> None:
        """any() used to stop at the first mod that moved, leaving the rest."""
        self.window.mod_list.select_mods(["a", "b"])
        self.window.move_selected(+1)
        self.assertEqual(self._order(), ["c", "a", "b", "d"])

    def test_a_disabled_mod_is_told_it_has_no_place(self) -> None:
        self.manager.set_enabled("a", False)
        self.window.mod_list.refresh()
        self.window.mod_list.select_mods(["a"])
        self.window.send_selected_to("top")
        self.assertEqual(self._order(), ["b", "c", "d"])
        self.assertIn("enabled mod", self.window.statusBar().currentMessage())

    def test_typing_a_number_into_the_column_moves_the_mod(self) -> None:
        item = self._row("d")
        self.assertTrue(item.flags() & Qt.ItemFlag.ItemIsEditable)
        item.setText(0, "1")
        self._settle()
        self.assertEqual(self._order(), ["d", "a", "b", "c"])

    def test_a_number_past_the_end_lands_at_the_end(self) -> None:
        self._row("a").setText(0, "99")
        self._settle()
        self.assertEqual(self._order(), ["b", "c", "d", "a"])

    def test_a_tick_is_still_read_as_a_tick(self) -> None:
        """Column 0 carries the checkbox and the number, and one signal covers
        both - so a tick must not be mistaken for a typed place, or the other
        way round."""
        self._row("c").setCheckState(0, Qt.CheckState.Unchecked)
        self._settle()
        self.assertFalse(self.manager.state.is_enabled("c"))
        self.assertEqual(self._order(), ["a", "b", "d"])

    def test_a_disabled_mod_has_no_number_to_type_into(self) -> None:
        self.manager.set_enabled("a", False)
        self.window.mod_list.refresh()
        item = self._row("a")
        self.assertEqual(item.text(0), "")
        self.assertFalse(item.flags() & Qt.ItemFlag.ItemIsEditable)


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
        destroy(self.window)
        QCoreApplication.processEvents()
        self._tmp.cleanup()

    def test_the_shipped_mods_all_appear(self) -> None:
        self.assertEqual(self.window.mod_list.topLevelItemCount(), len(self.manager.mods))

    def test_selecting_a_mod_fills_the_side_panel(self) -> None:
        self.window.mod_list.select_mods(["revive-cost"])
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


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class TheExecutableMarker(unittest.TestCase):
    """A mod that changes the game executable has to say so in the list.

    The install-time box appears once. The list is where the mod is seen from
    then on, so the marker is read off what the mod actually targets rather
    than off anything the mod says about itself.
    """

    def test_a_mod_touching_the_exe_is_marked(self) -> None:
        from lid_db_manager.ui.mod_list import _touches_executable
        from lid_db_manager.vetted import GAME_EXE

        self.assertTrue(_touches_executable({GAME_EXE}))
        self.assertTrue(_touches_executable({GAME_EXE.replace("/", "\\")}))
        self.assertTrue(
            _touches_executable({GAME_EXE, "BrgGame/CookedPCConsole/Thing_SF.upk"})
        )

    def test_an_ordinary_asset_mod_is_not(self) -> None:
        from lid_db_manager.ui.mod_list import _touches_executable

        self.assertFalse(_touches_executable(set()))
        self.assertFalse(_touches_executable({"BrgGame/CookedPCConsole/Thing_SF.upk"}))
        self.assertFalse(_touches_executable({"Binaries/Win64/other.exe"}))

    def test_the_label_does_not_stutter(self) -> None:
        from lid_db_manager.ui.mod_list import _label

        self.assertEqual(_label("revive-cost-1kc", "Revive Cost"),
                         "revive-cost-1kc  -  Revive Cost")
        self.assertEqual(_label("Buttons v1.2", "Buttons v1.2"), "Buttons v1.2")
        self.assertEqual(_label("buttons-v1.2", "Buttons V1.2"), "Buttons V1.2")


@unittest.skipUnless(HAVE_QT, "PySide6 is not installed")
class TheFirstRunCheckDiesWithTheWindow(unittest.TestCase):
    """A queued first-run check must not outlive the window it belongs to.

    ``QTimer.singleShot(0, self._first_run_checks)`` keeps no receiver, so the
    callback still ran after the window was gone and built a QMessageBox on a
    deleted C++ object. That printed a RuntimeError traceback during teardown
    and intermittently turned a passing suite into a failing one - the worst kind
    of bug, because it makes every other result untrustworthy.
    """

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
        # No database on purpose: that is the branch that opens the message box.
        self.manager = Manager(self.paths)
        self.window = MainWindow(self.manager)

    def tearDown(self) -> None:
        destroy(self.window)
        self._tmp.cleanup()

    def test_the_check_is_queued_on_a_timer_the_window_owns(self) -> None:
        self.assertTrue(self.window._first_run.isActive())
        self.assertIs(self.window._first_run.parent(), self.window)
        self.assertTrue(self.window._first_run.isSingleShot())

    def test_destroying_the_window_takes_the_pending_check_with_it(self) -> None:
        timer = self.window._first_run
        destroy(self.window)
        self.window = None
        # The timer was parented to the window, so Qt deleted it too. Touching
        # it now raises rather than firing _first_run_checks on a dead window.
        with self.assertRaises(RuntimeError):
            timer.isActive()
