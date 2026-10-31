"""The "decal_weights" setting: a set of decals, each with a weight and an
on/off tick, held as text that can never carry SQL - and the window that edits
it. Its default is the mod's own query, run against the player's database."""

from __future__ import annotations

import os
import sqlite3
import unittest
from pathlib import Path

from lid_db_manager.decals import default_weights
from lid_db_manager.errors import ModLoadError
from lid_db_manager.settings import (
    KIND_DECAL_WEIGHTS,
    DecalWeight,
    is_setting_value,
    parse_decal_weights,
    parse_settings,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VANILLA_DB = PROJECT_ROOT / "LiD Vanilla DB" / "5.0.4.2" / "masters.db"
STEW = "SELECT sklid, odds FROM master_skillgacha_odds WHERE id = 'SKLGACH_NORMAL_OFFLINE' AND odds > 0"


def a_setting(**extra):
    entry = {"id": "stew", "label": "Decals in the stew", "type": "decal_weights",
             "default_sql": STEW, "default_label": "The game's own stew"}
    entry.update(extra)
    [setting] = parse_settings([entry], "test")
    return setting


class TheValue(unittest.TestCase):
    def test_entries_read_back(self) -> None:
        self.assertEqual(parse_decal_weights("SKL_A_P/40, SKL_B_P/2/0", "x"),
                         [DecalWeight("SKL_A_P", 40), DecalWeight("SKL_B_P", 2, on=False)])
        self.assertEqual(parse_decal_weights("", "x"), [])
        self.assertEqual(DecalWeight("SKL_B_P", 2, on=False).text(), "SKL_B_P/2/0")

    def test_a_decal_twice_is_kept_once_as_first_given(self) -> None:
        self.assertEqual(parse_decal_weights("A/2,A/5/0,B/1", "x"), [DecalWeight("A", 2), DecalWeight("B", 1)])

    def test_anything_that_could_carry_sql_is_refused(self) -> None:
        for bad in ("A'/1", "A/1;DROP", "A/0", "A/10000", "A/-1", "A B/1", "A/1,", "A", "A/1/1", "A/1/0/0"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_decal_weights(bad, "x")

    def test_weights_past_100_are_fine(self) -> None:
        # The game's stew odds are not percentages; one decal can be in the hundreds.
        self.assertEqual(parse_decal_weights("A/9999", "x"), [DecalWeight("A", 9999)])

    def test_state_json_keeps_it(self) -> None:
        self.assertTrue(is_setting_value("SKL_A_P/40,SKL_B_P/2/0"))
        self.assertFalse(is_setting_value("SKL_A_P/40;"))


class TheSetting(unittest.TestCase):
    def test_it_loads(self) -> None:
        setting = a_setting()
        self.assertEqual(setting.kind, KIND_DECAL_WEIGHTS)
        self.assertTrue(setting.is_list)
        self.assertEqual(setting.default, "")

    def test_it_needs_one_select_for_its_default(self) -> None:
        with self.assertRaises(ModLoadError):
            a_setting(default_sql="")
        with self.assertRaises(ModLoadError):
            a_setting(default_sql="SELECT 1; DROP TABLE master_skill")

    def test_what_it_writes_into_sql(self) -> None:
        setting = a_setting()
        self.assertEqual(setting.sql(""), STEW)
        self.assertEqual(setting.sql("SKL_A_P/40,SKL_B_P/2/0,SKL_C_P/7"), "VALUES ('SKL_A_P', 40), ('SKL_C_P', 7)")
        self.assertEqual(setting.sql("SKL_B_P/2/0"), "SELECT NULL, 0")

    def test_each_form_works_where_a_select_goes(self) -> None:
        con = sqlite3.connect(":memory:")
        self.addCleanup(con.close)
        con.execute("CREATE TABLE master_skillgacha_odds (id TEXT, sklid TEXT, odds INTEGER)")
        con.execute("INSERT INTO master_skillgacha_odds VALUES ('SKLGACH_NORMAL_OFFLINE', 'SKL_X_P', 5)")
        setting = a_setting()
        for value, rows in (("", [("SKL_X_P", 5)]), ("SKL_A_P/40,SKL_B_P/2/0", [("SKL_A_P", 40)]),
                            ("SKL_B_P/2/0", [])):
            with self.subTest(value=value):
                got = con.execute(f"WITH wanted(sklid, weight) AS ({setting.sql(value)}) "
                                  "SELECT sklid, weight FROM wanted WHERE sklid IS NOT NULL").fetchall()
                self.assertEqual(got, rows)

    def test_how_it_reads(self) -> None:
        setting = a_setting()
        self.assertEqual(setting.display(""), "The game's own stew")
        self.assertEqual(setting.display("SKL_A_P/40,SKL_B_P/2/0"), "1 of 2 decals on")

    def test_coerce_tidies_it(self) -> None:
        self.assertEqual(a_setting().coerce(" SKL_A_P/2 , SKL_A_P/9/0"), "SKL_A_P/2")


@unittest.skipUnless(VANILLA_DB.is_file(), "needs the vanilla database")
class TheDefault(unittest.TestCase):
    def test_the_query_gives_the_stew(self) -> None:
        found = default_weights(VANILLA_DB, STEW)
        self.assertEqual(len(found), 208)
        self.assertEqual(dict(found)["SKL_ANTI_MSR_A_P"], 40)

    def test_a_broken_query_gives_nothing(self) -> None:
        self.assertEqual(default_weights(VANILLA_DB, "SELECT nope FROM nowhere"), [])


try:
    from PySide6.QtWidgets import QApplication

    HAVE_QT = True
except ImportError:  # pragma: no cover - the laptop's Python has no PySide6
    HAVE_QT = False


@unittest.skipUnless(HAVE_QT and VANILLA_DB.is_file(), "needs PySide6 and the vanilla database")
class TheWindow(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication([])

    def editor(self, value=""):
        from lid_db_manager.ui.decal_weights_editor import DecalWeightsEditor

        return DecalWeightsEditor(a_setting(), value, VANILLA_DB)

    def test_it_opens_on_the_default(self) -> None:
        editor = self.editor()
        self.assertEqual(len(editor.entries()), 208)
        self.assertEqual(editor.value(), "")
        self.assertIn("208 of 208 decals in", editor.count.text())
        self.assertIn("the default", editor.count.text())

    def test_unticking_keeps_the_weight_and_leaves_the_chance(self) -> None:
        from PySide6.QtCore import Qt

        editor = self.editor("SKL_ANTI_MSR_A_P/40,SKL_ANTI_MSR_B_P/10")
        self.assertEqual([editor.table.item(r, 5).text() for r in (0, 1)], ["80.00%", "20.00%"])
        editor.table.item(0, 0).setCheckState(Qt.CheckState.Unchecked)
        self.assertEqual([editor.table.item(r, 5).text() for r in (0, 1)], ["", "100.00%"])
        self.assertEqual(editor.value(), "SKL_ANTI_MSR_A_P/40/0,SKL_ANTI_MSR_B_P/10")

    def test_adding_none_in_and_back_to_the_default(self) -> None:
        editor = self.editor("SKL_ANTI_MSR_A_P/40")
        editor.add(["SKL_ANTI_MSR_B_P", "SKL_ANTI_MSR_A_P"])       # one already listed: as it is
        self.assertEqual(editor.value(), "SKL_ANTI_MSR_A_P/40,SKL_ANTI_MSR_B_P/1")
        editor._tick_all(False)
        self.assertFalse(editor.ok_button.isEnabled())
        editor.fill(editor.default)
        self.assertEqual(editor.value(), "")

    def test_search_hides_the_rest_and_all_in_ticks_only_what_shows(self) -> None:
        editor = self.editor("SKL_ANTI_MSR_A_P/40/0,SKL_ANTI_MSR_B_P/10/0,SKL_ADVENTURE_01_P/2/0")
        editor.search.setText("ADVENTURE")
        self.assertEqual([editor.table.isRowHidden(r) for r in range(3)], [True, True, False])
        editor._tick_all(True)
        self.assertEqual([e.on for e in editor.entries()], [False, False, True])

    def test_a_decal_no_longer_in_the_database_is_shown(self) -> None:
        editor = self.editor("SKL_NOT_THERE_P/3")
        self.assertIn("not in this database", editor.table.item(0, 1).text())


if __name__ == "__main__":
    unittest.main()
