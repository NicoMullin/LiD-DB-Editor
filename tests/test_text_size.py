"""Making every bit of text in the window bigger or smaller.

The rules live in textsize.py with no Qt in them, so the arithmetic is tested
without a window. The rest is wiring: one stylesheet drives the whole window, and
the two places that size text themselves - the details panel's own HTML and the
status column's font - have to be told.

Also the regression for the `&nbsp;` that used to show up in the Configuration
tab: Qt only treats a string as rich text when it spots something that looks like
a tag, so a label holding entities and nothing else printed them literally.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from fixtures import build_db, write_mod

from lid_db_manager import textsize
from lid_db_manager.state import Settings

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtWidgets import QApplication, QLabel

    HAVE_QT = True
except ImportError:  # pragma: no cover - depends on the environment
    HAVE_QT = False


def destroy(window) -> None:
    """Close a test window and actually get rid of it.

    ``close()`` only hides it. Every window left alive makes applying a style
    sheet slower for the rest of the run, because it re-styles every live widget
    in the process - and changing the text size is exactly that.
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

A_SETTING = {
    "id": "slots", "label": "Coin Locker slots", "type": "integer",
    "default": 100, "min": 1, "max": 1000, "unit": "slots",
    "help": "Stock is 10. Whole slots only.",
}


class ClampTests(unittest.TestCase):
    def test_the_default_is_a_hundred_percent(self):
        self.assertEqual(textsize.DEFAULT_SCALE, 100)
        self.assertEqual(textsize.clamp_scale(100), 100)

    def test_it_will_not_go_past_either_end(self):
        self.assertEqual(textsize.clamp_scale(5), textsize.MIN_SCALE)
        self.assertEqual(textsize.clamp_scale(9999), textsize.MAX_SCALE)

    def test_anything_unreadable_becomes_the_default(self):
        # A state.json edited by hand must not stop the window opening.
        for junk in (None, "", "big", [], {}, float("nan")):
            self.assertEqual(textsize.clamp_scale(junk), textsize.DEFAULT_SCALE, junk)

    def test_a_number_written_as_text_is_taken(self):
        self.assertEqual(textsize.clamp_scale("125"), 125)

    def test_a_fraction_is_rounded_rather_than_refused(self):
        self.assertEqual(textsize.clamp_scale(124.6), 125)

    def test_every_preset_is_one_it_will_accept(self):
        for percent in textsize.SCALE_PRESETS:
            self.assertEqual(textsize.clamp_scale(percent), percent)
        self.assertIn(textsize.DEFAULT_SCALE, textsize.SCALE_PRESETS)


class SteppingTests(unittest.TestCase):
    def test_one_step_up_and_down_from_normal(self):
        self.assertEqual(textsize.stepped(100, up=True), 110)
        self.assertEqual(textsize.stepped(100, up=False), 90)

    def test_stepping_from_a_preset_lands_back_on_the_grid(self):
        # 125 is a preset, so without this it would step 135, 145, forever odd.
        self.assertEqual(textsize.stepped(125, up=True), 130)
        self.assertEqual(textsize.stepped(125, up=False), 120)

    def test_it_stops_at_the_ends_instead_of_running_past_them(self):
        self.assertEqual(textsize.stepped(textsize.MAX_SCALE, up=True), textsize.MAX_SCALE)
        self.assertEqual(textsize.stepped(textsize.MIN_SCALE, up=False), textsize.MIN_SCALE)

    def test_stepping_up_then_down_comes_back(self):
        for start in (80, 100, 150):
            self.assertEqual(textsize.stepped(textsize.stepped(start, up=True), up=False),
                             start, start)

    def test_it_walks_the_whole_range_without_getting_stuck(self):
        seen, scale = [], textsize.MIN_SCALE
        for _ in range(100):
            seen.append(scale)
            nxt = textsize.stepped(scale, up=True)
            if nxt == scale:
                break
            scale = nxt
        self.assertEqual(seen[0], textsize.MIN_SCALE)
        self.assertEqual(seen[-1], textsize.MAX_SCALE)


class SettingsTests(unittest.TestCase):
    def test_it_starts_at_normal(self):
        self.assertEqual(Settings().text_scale, 100)

    def test_it_round_trips(self):
        self.assertEqual(Settings.from_dict({"text_scale": 150}).text_scale, 150)
        self.assertIn("text_scale", Settings().to_dict())

    def test_a_stored_value_out_of_range_is_brought_back_in(self):
        self.assertEqual(Settings.from_dict({"text_scale": 5000}).text_scale,
                         textsize.MAX_SCALE)

    def test_a_stored_value_that_is_not_a_number_falls_back(self):
        self.assertEqual(Settings.from_dict({"text_scale": "huge"}).text_scale, 100)

    def test_an_old_state_file_without_it_still_loads(self):
        self.assertEqual(Settings.from_dict({"dark_mode": True}).text_scale, 100)


@unittest.skipUnless(HAVE_QT, "PySide6 is not installed")
class StylesheetTests(unittest.TestCase):
    """The sheet itself, built rather than applied.

    Handing a new sheet to the application re-styles and re-lays-out every live
    widget, which in a full test run is every window every other module left
    behind - slow enough to look like a hang. What a size works out to is a
    question about the string, so it is asked of the string.
    """

    def test_the_font_size_follows_the_scale(self):
        from lid_db_manager.ui.theme import BASE_FONT_PX, font_px

        self.assertEqual(font_px(100), BASE_FONT_PX)
        self.assertGreater(font_px(150), font_px(100))
        self.assertLess(font_px(80), font_px(100))

    def test_nothing_ever_scales_away_to_zero(self):
        from lid_db_manager.ui.theme import scaled

        self.assertGreaterEqual(scaled(1, textsize.MIN_SCALE), 1)
        self.assertGreaterEqual(scaled(2, textsize.MIN_SCALE), 1)

    def _base_px(self, sheet: str) -> int:
        found = re.search(r"QWidget \{ font-size: (\d+)px", sheet)
        self.assertIsNotNone(found, "the base font rule went missing")
        return int(found.group(1))

    def test_the_sheet_carries_the_scaled_size(self):
        from lid_db_manager.ui.theme import font_px, stylesheet

        for scale in (80, 100, 175):
            self.assertEqual(self._base_px(stylesheet(True, scale)), font_px(scale), scale)

    def test_padding_grows_with_the_text(self):
        # Larger type in boxes padded for the old font clips its own descenders.
        from lid_db_manager.ui.theme import stylesheet

        def button_padding(scale: int) -> int:
            block = re.search(r"QPushButton \{(.*?)\}", stylesheet(True, scale),
                              re.DOTALL).group(1)
            return int(re.search(r"padding: (\d+)px", block).group(1))

        self.assertGreater(button_padding(200), button_padding(100))

    def test_the_panel_heading_grows_too(self):
        # It is styled here rather than with a point size on the widget, because
        # a stylesheet pixel size beats one of those and the heading never grew.
        from lid_db_manager.ui.theme import stylesheet

        def title_px(scale: int) -> int:
            return int(re.search(r"QLabel#panelTitle \{\s*font-size: (\d+)px",
                                 stylesheet(True, scale)).group(1))

        self.assertGreater(title_px(100), self._base_px(stylesheet(True, 100)))
        self.assertGreater(title_px(200), title_px(100))

    def test_an_out_of_range_scale_is_clamped_rather_than_obeyed(self):
        from lid_db_manager.ui.theme import font_px, stylesheet

        self.assertEqual(self._base_px(stylesheet(True, 10000)),
                         font_px(textsize.MAX_SCALE))

    def test_light_and_dark_differ_but_scale_the_same(self):
        from lid_db_manager.ui.theme import stylesheet

        self.assertNotEqual(stylesheet(True, 100), stylesheet(False, 100))
        self.assertEqual(self._base_px(stylesheet(True, 150)),
                         self._base_px(stylesheet(False, 150)))


@unittest.skipUnless(HAVE_QT, "PySide6 is not installed")
class ThroughTheWindowTests(unittest.TestCase):
    """The control itself: the menu, the shortcuts, and what they reach.

    One window for the whole class, not one per test. Changing the size hands the
    application a new style sheet, which re-styles every live widget in the
    process - so building a window per test would make each test slower than the
    last, and a full run slower than the sum of its parts.
    """

    app = None
    window = None

    @classmethod
    def setUpClass(cls) -> None:
        from lid_db_manager.manager import Manager
        from lid_db_manager.paths import AppPaths
        from lid_db_manager.ui.main_window import MainWindow

        cls.app = QApplication.instance() or QApplication([])
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        cls.paths = AppPaths(root).ensure()
        db = build_db(root / "game" / "masters.db")
        write_mod(cls.paths.mods_dir, "sized", {
            "settings": [A_SETTING],
            "patches": [{"type": "update_set", "table": "master_skill",
                         "set": {"buy_money": "{{slots}}"}, "where": "buy_money > 1"}],
        })
        cls.manager = Manager(cls.paths)
        cls.manager.set_db_path(db)
        cls.window = MainWindow(cls.manager)

    @classmethod
    def tearDownClass(cls) -> None:
        destroy(cls.window)
        cls.window = None
        cls._tmp.cleanup()

    def setUp(self) -> None:
        self.window.set_text_size(textsize.DEFAULT_SCALE)

    def _stylesheet_px(self) -> int:
        found = re.search(r"QWidget \{ font-size: (\d+)px", self.app.styleSheet())
        return int(found.group(1))

    def test_the_view_menu_offers_every_preset(self):
        offered = [action.data() for action in self.window.text_size_actions]
        self.assertEqual(offered, list(textsize.SCALE_PRESETS))

    def test_bigger_and_smaller_move_it(self):
        self.window.step_text_size(up=True)
        self.assertEqual(self.manager.state.settings.text_scale, 110)
        self.window.step_text_size(up=False)
        self.assertEqual(self.manager.state.settings.text_scale, 100)

    def test_normal_size_puts_it_back_from_anywhere(self):
        self.window.set_text_size(175)
        self.window.reset_text_size()
        self.assertEqual(self.manager.state.settings.text_scale, 100)

    def test_changing_it_changes_the_stylesheet(self):
        before = self._stylesheet_px()
        self.window.set_text_size(175)
        self.assertGreater(self._stylesheet_px(), before)

    def test_the_details_panel_is_told_too(self):
        # It writes its own HTML, so the stylesheet does not reach inside it.
        self.window.set_text_size(150)
        self.assertEqual(self.window.diff_view.text_scale, 150)

    def test_the_matching_preset_is_ticked(self):
        self.window.set_text_size(125)
        ticked = [a.data() for a in self.window.text_size_actions if a.isChecked()]
        self.assertEqual(ticked, [125])

    def test_a_size_between_two_presets_ticks_none_of_them(self):
        self.window.set_text_size(125)
        self.window.step_text_size(up=True)  # 130, which is not a preset
        self.assertEqual(self.manager.state.settings.text_scale, 130)
        self.assertEqual([a for a in self.window.text_size_actions if a.isChecked()], [])

    def test_it_is_remembered_for_next_time(self):
        from lid_db_manager.manager import Manager

        self.window.set_text_size(150)
        self.assertEqual(Manager(self.paths).state.settings.text_scale, 150)

    def test_switching_dark_mode_does_not_reset_the_size(self):
        from lid_db_manager.ui.theme import font_px

        self.window.set_text_size(150)
        self.window._on_dark_toggled(False)
        try:
            self.assertEqual(self.manager.state.settings.text_scale, 150)
            self.assertEqual(self._stylesheet_px(), font_px(150))
        finally:
            self.window._on_dark_toggled(True)

    def test_the_window_survives_both_extremes(self):
        for scale in (textsize.MIN_SCALE, textsize.MAX_SCALE):
            self.window.set_text_size(scale)
            self.assertEqual(self.manager.state.settings.text_scale, scale)
            self.assertGreater(self.window.mod_list.topLevelItemCount(), 0)

    def test_setting_the_same_size_again_does_nothing_surprising(self):
        before = self._stylesheet_px()
        self.window.set_text_size(100)
        self.assertEqual(self._stylesheet_px(), before)


@unittest.skipUnless(HAVE_QT, "PySide6 is not installed")
class SettingLabelTests(unittest.TestCase):
    """The Configuration tab shows text, not markup.

    It used to build the limits line with `&nbsp;` for spacing. Qt decides
    whether a label is rich text by looking for something tag-shaped, finds
    nothing, and prints the entity as the four characters it is.
    """

    app = None

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def editor(self, **over):
        from lid_db_manager.settings import parse_settings
        from lid_db_manager.ui.setting_editor import SettingEditor

        setting = parse_settings([{**A_SETTING, **over}], "test")[0]
        # Kept on the instance: a widget nothing holds is collected, and its
        # child labels are freed underneath the assertions.
        self._editor = SettingEditor(setting, setting.default, "#888888")
        return self._editor

    def labels(self, editor) -> list[QLabel]:
        return editor.findChildren(QLabel)

    def test_no_html_entity_reaches_the_screen(self):
        for label in self.labels(self.editor()):
            self.assertNotIn("&nbsp;", label.text())
            self.assertNotIn("&amp;", label.text())

    def test_no_markup_reaches_the_screen(self):
        for label in self.labels(self.editor()):
            self.assertNotIn("<b>", label.text())
            self.assertNotIn("</", label.text())

    def test_every_label_is_plain_text(self):
        for label in self.labels(self.editor()):
            self.assertEqual(label.textFormat(), Qt.TextFormat.PlainText, label.text())

    def test_the_limits_line_still_reads_as_a_range_with_a_default(self):
        texts = [label.text() for label in self.labels(self.editor())]
        limits = [t for t in texts if "default" in t]
        self.assertEqual(len(limits), 1)
        self.assertIn("1 slots to 1,000 slots", limits[0])
        self.assertIn("default 100 slots", limits[0])

    def test_a_mod_cannot_put_markup_in_its_own_label(self):
        # Rich text would let a mod folder fetch a remote image into the panel.
        editor = self.editor(label="<img src=http://example.com/x.png>Sneaky</b>")
        shown = [label.text() for label in self.labels(editor)]
        self.assertIn("<img src=http://example.com/x.png>Sneaky</b>", shown)
        for label in self.labels(editor):
            self.assertEqual(label.textFormat(), Qt.TextFormat.PlainText)

    def test_the_help_text_is_shown_when_there_is_one(self):
        texts = [label.text() for label in self.labels(self.editor())]
        self.assertIn(A_SETTING["help"], texts)

    def test_the_shipped_mods_configuration_panels_hold_no_markup(self):
        from lid_db_manager.mod import _filled_in
        from lid_db_manager.mod_loader import scan_mods
        from lid_db_manager.ui.setting_editor import SettingEditor

        checked = 0
        for mod in scan_mods(PROJECT_ROOT / "mods").mods:
            if not mod.settings:
                continue
            filled = _filled_in(mod, {s.id: s.default for s in mod.settings})
            for setting in filled.settings:
                editor = SettingEditor(setting, setting.default, "#888888")
                for label in editor.findChildren(QLabel):
                    self.assertNotIn("&nbsp;", label.text(), f"{mod.id}/{setting.id}")
                    self.assertNotIn("<b>", label.text(), f"{mod.id}/{setting.id}")
                checked += 1
        self.assertGreater(checked, 5, "no shipped settings were checked")


if __name__ == "__main__":
    unittest.main()
