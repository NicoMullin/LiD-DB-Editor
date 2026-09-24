"""The filter row above the mod list, and what it does to the list.

The deciding is tested in test_browse.py; this is the wiring - that the boxes
offer what the mods say, that the list narrows, that sorting takes the load-order
controls away rather than quietly moving mods, and that an empty list says why.
"""

from __future__ import annotations

import os
import tempfile
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

    ``close()`` only hides it. Every MainWindow left alive makes the whole run
    slower, because handing the application a new style sheet - which the text
    size does - re-styles every live widget in the process, not just the visible
    ones. Left alone, a full run ends up applying sheets across hundreds.
    """
    if window is None:
        return
    window.close()
    window.setParent(None)
    window.deleteLater()
    app = QApplication.instance()
    if app is not None:
        # Only the deferred deletes. deleteLater() on its own frees nothing here
        # because processEvents() deliberately skips DeferredDelete - but
        # processEvents() is wrong too, because it also fires the one-shot
        # first-run timer of every window another test module left alive, and
        # each of those puts up a modal box.
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Three mods in two categories, with one tag shared and one not.
THE_MODS = (
    ("coin-locker", "Coin Locker", "Economy", ["coins", "storage"]),
    ("bank-limit", "Bank Limit", "Economy", ["coins"]),
    ("sharp-swords", "Sharp Swords", "Gear", ["weapons"]),
    ("loose-mod", "Loose Mod", "", []),
)


@unittest.skipUnless(HAVE_QT, "PySide6 is not installed")
class FilterBarTests(unittest.TestCase):
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
        for mod_id, name, category, tags in THE_MODS:
            write_mod(self.paths.mods_dir, mod_id, {
                "name": name,
                "description": f"{name} does something.",
                "category": category,
                "tags": tags,
                "patches": [{"type": "update_set", "table": "master_skill",
                             "set": {"buy_money": 1}, "where": "buy_money > 1"}],
            })
        self.manager = Manager(self.paths)
        self.manager.set_db_path(self.db)
        self.window = MainWindow(self.manager)
        self.bar = self.window.filter_bar
        self.list = self.window.mod_list
        self._settle()

    def tearDown(self) -> None:
        destroy(self.window)
        self._settle()
        self._tmp.cleanup()

    def _settle(self) -> None:
        for _ in range(5):
            QCoreApplication.processEvents()

    def _shown(self) -> list[str]:
        out = []
        for index in range(self.list.topLevelItemCount()):
            mod_id = self.list.topLevelItem(index).data(0, Qt.ItemDataRole.UserRole)
            if mod_id:
                out.append(mod_id)
        return out

    # -- the choices --------------------------------------------------------

    def test_the_category_box_offers_what_the_mods_say(self):
        offered = [self.bar.category_box.itemData(i)
                   for i in range(self.bar.category_box.count())]
        self.assertEqual(offered, ["", "Economy", "Gear", "Uncategorised"])

    def test_the_category_box_counts_them(self):
        self.assertEqual(self.bar.category_box.itemText(0), "All categories (4)")
        self.assertEqual(self.bar.category_box.itemText(1), "Economy (2)")

    def test_the_tag_menu_offers_every_tag_with_its_count(self):
        self.bar.tags_menu.aboutToShow.emit()
        labels = [a.text() for a in self.bar.tags_menu.actions() if a.isCheckable()]
        self.assertEqual(labels, ["coins (2)", "storage (1)", "weapons (1)"])

    def test_a_mod_arriving_brings_its_category_with_it(self):
        write_mod(self.paths.mods_dir, "noisy", {
            "category": "Audio", "tags": ["radio"],
            "patches": [{"type": "raw_sql", "sql": "SELECT 1;"}]})
        self.window.refresh(rescan=True)
        self._settle()
        offered = [self.bar.category_box.itemData(i)
                   for i in range(self.bar.category_box.count())]
        self.assertIn("Audio", offered)

    # -- narrowing the list -------------------------------------------------

    def test_everything_shows_with_no_filter(self):
        self.assertEqual(len(self._shown()), 4)
        self.assertEqual(self.list.shown_count, 4)
        self.assertEqual(self.list.total_count, 4)

    def test_the_count_line_says_nothing_while_nothing_is_hidden(self):
        self.assertEqual(self.bar.count_label.text(), "")

    def test_searching_narrows_the_list_and_says_by_how_much(self):
        self.bar.set_text("coin")
        self._settle()
        # Disabled mods list in folder order, which is alphabetical.
        self.assertEqual(self._shown(), ["bank-limit", "coin-locker"])
        self.assertEqual(self.bar.count_label.text(), "Showing 2 of 4")

    def test_a_category_narrows_the_list(self):
        self.bar.set_category("Gear")
        self._settle()
        self.assertEqual(self._shown(), ["sharp-swords"])

    def test_a_tag_narrows_the_list(self):
        self.bar.set_tags(["storage"])
        self._settle()
        self.assertEqual(self._shown(), ["coin-locker"])

    def test_clear_puts_everything_back(self):
        self.bar.set_text("coin")
        self.bar.set_category("Economy")
        self.bar.set_tags(["coins"])
        self._settle()
        self.bar.clear()
        self._settle()
        self.assertEqual(len(self._shown()), 4)
        self.assertEqual(self.bar.count_label.text(), "")
        self.assertFalse(self.bar.current_filter().on)

    def test_clear_leaves_the_sort_alone_because_a_sort_is_not_a_filter(self):
        self.bar.set_sort("name")
        self.bar.set_text("coin")
        self._settle()
        self.bar.clear()
        self._settle()
        self.assertEqual(self.bar.current_sort(), "name")

    def test_the_clear_button_is_only_live_when_there_is_something_to_clear(self):
        self.assertFalse(self.bar.clear_button.isEnabled())
        self.bar.set_category("Gear")
        self._settle()
        self.assertTrue(self.bar.clear_button.isEnabled())

    def test_the_tags_button_counts_what_is_ticked(self):
        self.assertEqual(self.bar.tags_button.text(), "Tags")
        self.bar.set_tags(["coins", "weapons"])
        self._settle()
        self.assertEqual(self.bar.tags_button.text(), "Tags (2)")

    def test_a_filter_that_matches_nothing_says_so_instead_of_looking_empty(self):
        self.bar.set_text("nothing-matches-this")
        self._settle()
        self.assertEqual(self._shown(), [])
        self.assertEqual(self.list.topLevelItemCount(), 1)
        said = self.list.topLevelItem(0).text(0)
        self.assertIn("None of your 4 mods match", said)
        self.assertIn("Nothing has been removed", said)
        self.assertIn("nothing-matches-this", said)

    def test_nothing_about_filtering_touches_the_mods_or_the_order(self):
        before = list(self.manager.state.enabled_mods)
        self.bar.set_text("coin")
        self.bar.set_category("Economy")
        self.bar.set_sort("name")
        self._settle()
        self.assertEqual(self.manager.state.enabled_mods, before)
        self.assertEqual(len(self.manager.mods), 4)

    # -- sorting ------------------------------------------------------------

    def test_sorting_by_name_reorders_the_rows_on_screen(self):
        self.bar.set_sort("name")
        self._settle()
        self.assertEqual(self._shown(), ["bank-limit", "coin-locker", "loose-mod",
                                         "sharp-swords"])

    def test_sorting_does_not_renumber_anything(self):
        for mod_id, _n, _c, _t in THE_MODS:
            self.manager.set_enabled(mod_id, True)
        self.window.refresh()
        self._settle()
        before = list(self.manager.state.enabled_mods)
        self.bar.set_sort("name")
        self._settle()
        self.assertEqual(self.manager.state.enabled_mods, before)

    def test_the_number_shown_is_still_the_real_load_order_while_sorted(self):
        self.manager.set_enabled("sharp-swords", True)
        self.manager.set_enabled("bank-limit", True)
        self.window.refresh()
        self._settle()
        self.bar.set_sort("name")
        self._settle()
        numbers = {}
        for index in range(self.list.topLevelItemCount()):
            item = self.list.topLevelItem(index)
            mod_id = item.data(0, Qt.ItemDataRole.UserRole)
            if mod_id:
                numbers[mod_id] = item.text(0)
        self.assertEqual(numbers["sharp-swords"], "1")
        self.assertEqual(numbers["bank-limit"], "2")

    def test_the_sort_is_remembered_between_sessions(self):
        from lid_db_manager.manager import Manager

        self.bar.set_sort("category")
        self._settle()
        self.assertEqual(self.manager.state.settings.mod_sort, "category")
        reopened = Manager(self.paths)
        self.assertEqual(reopened.state.settings.mod_sort, "category")

    def test_the_search_and_the_filters_are_not_remembered(self):
        from lid_db_manager.manager import Manager

        self.bar.set_text("coin")
        self.bar.set_category("Economy")
        self.bar.set_tags(["coins"])
        self._settle()
        reopened = Manager(self.paths)
        stored = reopened.state.settings.to_dict()
        self.assertNotIn("coin", str(stored))
        self.assertNotIn("Economy", str(stored))

    # -- the load-order controls -------------------------------------------

    def test_they_are_live_on_a_plain_unfiltered_list(self):
        self.assertTrue(self.window.move_up_button.isEnabled())
        self.assertTrue(self.window.move_down_button.isEnabled())
        self.assertIn("top applies first", self.window.order_hint.text())

    def test_sorting_takes_the_move_buttons_away_and_says_why(self):
        self.bar.set_sort("name")
        self._settle()
        self.assertFalse(self.window.move_up_button.isEnabled())
        self.assertFalse(self.window.move_down_button.isEnabled())
        hint = self.window.order_hint.text()
        self.assertIn("Sorted by Name", hint)
        self.assertIn("Load order", hint)

    def test_sorting_makes_the_number_uneditable(self):
        self.manager.set_enabled("bank-limit", True)
        self.window.refresh()
        self._settle()
        row = next(self.list.topLevelItem(i) for i in range(self.list.topLevelItemCount())
                   if self.list.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole)
                   == "bank-limit")
        self.assertTrue(row.flags() & Qt.ItemFlag.ItemIsEditable)
        self.bar.set_sort("name")
        self._settle()
        row = next(self.list.topLevelItem(i) for i in range(self.list.topLevelItemCount())
                   if self.list.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole)
                   == "bank-limit")
        self.assertFalse(row.flags() & Qt.ItemFlag.ItemIsEditable)

    def test_a_filter_takes_the_move_buttons_but_leaves_the_number(self):
        # Move up and down are relative, so they mislead while rows are hidden.
        # Typing a number is absolute, so it still means what it says.
        self.manager.set_enabled("bank-limit", True)
        self.window.refresh()
        self.bar.set_text("bank")
        self._settle()
        self.assertFalse(self.window.move_up_button.isEnabled())
        self.assertIn("Some mods are hidden", self.window.order_hint.text())
        row = next(self.list.topLevelItem(i) for i in range(self.list.topLevelItemCount())
                   if self.list.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole)
                   == "bank-limit")
        self.assertTrue(row.flags() & Qt.ItemFlag.ItemIsEditable)

    def test_clearing_gives_the_controls_back(self):
        self.bar.set_sort("name")
        self.bar.set_text("coin")
        self._settle()
        self.bar.set_sort("order")
        self.bar.clear()
        self._settle()
        self.assertTrue(self.window.move_up_button.isEnabled())
        self.assertIn("top applies first", self.window.order_hint.text())

    # -- from the mod itself ------------------------------------------------

    def test_right_clicking_a_mod_offers_its_category_and_its_tags(self):
        row = next(self.list.topLevelItem(i) for i in range(self.list.topLevelItemCount())
                   if self.list.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole)
                   == "coin-locker")
        menu = self.window._build_mod_menu(row)
        labels = [a.text() for a in menu.actions()]
        self.assertIn("Show only Economy", labels)
        submenus = [a.menu() for a in menu.actions() if a.menu()]
        tagged = [m for m in submenus if m.title() == "Show only mods tagged"]
        self.assertEqual(len(tagged), 1)
        self.assertEqual([a.text() for a in tagged[0].actions()], ["coins", "storage"])

    def test_an_uncategorised_untagged_mod_offers_neither(self):
        row = next(self.list.topLevelItem(i) for i in range(self.list.topLevelItemCount())
                   if self.list.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole)
                   == "loose-mod")
        labels = [a.text() for a in self.window._build_mod_menu(row).actions()]
        self.assertFalse([label for label in labels if label.startswith("Show only")])

    def test_show_only_a_category_narrows_the_list(self):
        self.window._filter_to(category="Gear")
        self._settle()
        self.assertEqual(self._shown(), ["sharp-swords"])

    def test_show_only_a_tag_replaces_whatever_was_on(self):
        self.bar.set_text("coin")
        self.bar.set_category("Economy")
        self._settle()
        self.window._filter_to(tags=["weapons"])
        self._settle()
        self.assertEqual(self._shown(), ["sharp-swords"])
        self.assertEqual(self.bar.search.text(), "")
        self.assertEqual(self.bar.current_category(), "")

    def test_the_detail_line_says_where_a_mod_is_filed(self):
        row = next(self.list.topLevelItem(i) for i in range(self.list.topLevelItemCount())
                   if self.list.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole)
                   == "coin-locker")
        detail = row.child(0).text(0)
        self.assertIn("Economy", detail)
        self.assertIn("#coins", detail)

    def test_an_unfiled_mod_gets_no_filing_line(self):
        row = next(self.list.topLevelItem(i) for i in range(self.list.topLevelItemCount())
                   if self.list.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole)
                   == "loose-mod")
        self.assertNotIn("#", row.child(0).text(0))


@unittest.skipUnless(HAVE_QT, "PySide6 is not installed")
class EditFilingTests(unittest.TestCase):
    """Setting a mod's own category and tags, and where they end up."""

    app = None

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        from lid_db_manager.manager import Manager
        from lid_db_manager.paths import AppPaths

        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.paths = AppPaths(self.root).ensure()
        self.db = build_db(self.root / "game" / "masters.db")
        write_mod(self.paths.mods_dir, "plain", {
            "patches": [{"type": "raw_sql", "sql": "SELECT 1;"}]})
        self.manager = Manager(self.paths)
        self.manager.set_db_path(self.db)
        self.manager.rescan()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def mod(self):
        return self.manager.scan.get("plain")

    def test_the_dialog_shows_what_the_mod_has(self):
        from lid_db_manager.ui.edit_dialog import EditModDialog

        self.manager.set_mod_filing("plain", category="Gear", tags=["weapons", "guns"])
        dialog = EditModDialog(self.mod())
        self.assertEqual(dialog.category_box.currentText(), "Gear")
        self.assertEqual(dialog.tags_edit.text(), "weapons, guns")

    def test_the_dialog_offers_the_suggestions_plus_what_is_already_in_use(self):
        from lid_db_manager.browse import SUGGESTED_CATEGORIES
        from lid_db_manager.ui.edit_dialog import EditModDialog

        dialog = EditModDialog(self.mod(), known_categories={"Homebrew": 1})
        offered = [dialog.category_box.itemText(i)
                   for i in range(dialog.category_box.count())]
        for name in SUGGESTED_CATEGORIES:
            self.assertIn(name, offered)
        self.assertIn("Homebrew", offered)

    def test_a_category_nobody_suggested_is_kept_as_typed(self):
        from lid_db_manager.ui.edit_dialog import EditModDialog

        dialog = EditModDialog(self.mod())
        dialog.category_box.setCurrentText("  My   Own Thing ")
        self.assertEqual(dialog.details().category, "My Own Thing")

    def test_the_dialog_says_what_a_messy_tag_will_be_saved_as(self):
        from lid_db_manager.ui.edit_dialog import EditModDialog

        dialog = EditModDialog(self.mod())
        dialog.tags_edit.setText("coins, storage")
        self.assertEqual(dialog.tags_note.text(), "")  # nothing to warn about
        dialog.tags_edit.setText("Coin Locker, COINS, coins")
        self.assertEqual(dialog.tags_note.text(), "Saved as: coin-locker, coins")
        self.assertEqual(dialog.details().tags, ["coin-locker", "coins"])

    def test_saving_writes_them_into_mod_json(self):
        import json

        self.manager.set_mod_filing("plain", category="Economy", tags=["Coins", "coins"])
        raw = json.loads((self.paths.mods_dir / "plain" / "mod.json").read_text(
            encoding="utf-8-sig"))
        self.assertEqual(raw["category"], "Economy")
        self.assertEqual(raw["tags"], ["coins"])

    def test_clearing_them_takes_them_out_of_the_file_rather_than_writing_blanks(self):
        import json

        self.manager.set_mod_filing("plain", category="Economy", tags=["coins"])
        self.manager.set_mod_filing("plain", category="", tags=[])
        raw = json.loads((self.paths.mods_dir / "plain" / "mod.json").read_text(
            encoding="utf-8-sig"))
        self.assertNotIn("category", raw)
        self.assertNotIn("tags", raw)

    def test_setting_the_filing_changes_nothing_else_about_the_mod(self):
        before = self.mod()
        self.manager.set_mod_filing("plain", category="Gear")
        after = self.mod()
        self.assertEqual(after.id, before.id)
        self.assertEqual(after.name, before.name)
        self.assertEqual(after.version, before.version)
        self.assertEqual(len(after.patches), len(before.patches))

    def test_a_bare_sql_mod_gains_a_mod_json_the_way_naming_it_does(self):
        folder = self.paths.mods_dir / "loose"
        folder.mkdir()
        (folder / "mod.sql").write_text("SELECT 1;", encoding="utf-8")
        self.manager.rescan()
        self.manager.set_mod_filing("loose", category="Examples", tags=["sql"])
        self.assertTrue((folder / "mod.json").is_file())
        self.assertEqual(self.manager.scan.get("loose").category, "Examples")

    def test_the_manager_counts_what_is_in_use(self):
        self.manager.set_mod_filing("plain", category="Gear", tags=["weapons"])
        self.assertEqual(self.manager.categories(), {"Gear": 1})
        self.assertEqual(self.manager.tags(), {"weapons": 1})


if __name__ == "__main__":
    unittest.main()
