"""The "goods" setting: a shop's list, with each good's bundle, currency, price
and daily limit, held as text that can never carry SQL."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from lid_db_manager.errors import ModLoadError
from lid_db_manager.goods import read_goods
from lid_db_manager.settings import (
    KIND_GOODS,
    GoodsEntry,
    is_setting_value,
    parse_goods,
    parse_settings,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VANILLA_DB = PROJECT_ROOT / "LiD Vanilla DB" / "5.0.4.2" / "masters.db"


def a_setting(**extra):
    entry = {"id": "goods", "label": "Your list", "type": "goods",
             "default": "ITMT_WOOD_4/1/k/0/0,MSR_053/5/d/8/1", "default_label": "Two things"}
    entry.update(extra)
    [setting] = parse_settings([entry], "test")
    return setting


class TheValue(unittest.TestCase):
    def test_entries_read_back(self) -> None:
        self.assertEqual(parse_goods("ITMT_WOOD_4/3/d/7/2, MSR_053/1/k/0/0", "x"), [
            GoodsEntry("ITMT_WOOD_4", 3, "d", 7, 2), GoodsEntry("MSR_053", 1, "k", 0, 0)])
        self.assertEqual(parse_goods("", "x"), [])

    def test_an_entry_twice_is_kept_once(self) -> None:
        self.assertEqual(len(parse_goods("A/1/k/0/0,A/1/k/0/0,A/5/k/0/0", "x")), 2)

    def test_anything_that_could_carry_sql_is_refused(self) -> None:
        for bad in ("A'/1/k/0/0", "A/1/k/0/0;DROP", "A/1/x/0/0", "A/1/k/0", "A/1/k/-5/0", "A B/1/k/0/0",
                    "A/1/k/0/0,", "A/0/k/0/0", "A/1/k/0/21", "A/1000/k/0/0", "A/1/k/9999999999/0"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_goods(bad, "x")

    def test_too_many(self) -> None:
        with self.assertRaises(ValueError):
            parse_goods(",".join(f"G{n}/1/k/0/0" for n in range(501)), "x")

    def test_state_json_keeps_it(self) -> None:
        self.assertTrue(is_setting_value("ITMT_WOOD_4/3/d/7/2,MSR_053/1/k/0/0"))
        self.assertFalse(is_setting_value("ITMT_WOOD_4/3/d/7/2;"))


class TheSetting(unittest.TestCase):
    def test_it_loads(self) -> None:
        setting = a_setting()
        self.assertEqual(setting.kind, KIND_GOODS)
        self.assertTrue(setting.is_list)
        self.assertEqual(setting.default, "")
        self.assertEqual(len(setting.goods("")), 2)

    def test_a_bad_default_is_refused_when_the_mod_loads(self) -> None:
        with self.assertRaises(ModLoadError):
            a_setting(default="ITMT_WOOD_4/1/k/0/0;")

    def test_what_it_writes_into_sql(self) -> None:
        setting = a_setting()
        self.assertEqual(setting.sql(""), "(1, 'ITMT_WOOD_4', 1, 0, 0, 0), (2, 'MSR_053', 5, 1, 8, 1)")
        self.assertEqual(setting.sql("ITBACK/2/k/150000/1"), "(1, 'ITBACK', 2, 0, 150000, 1)")
        self.assertEqual(a_setting(default="").sql(""), "(0, NULL, 1, 0, 0, 0)")

    def test_how_it_reads(self) -> None:
        setting = a_setting()
        self.assertEqual(setting.display(""), "Two things")
        self.assertEqual(setting.display("ITBACK/2/k/150000/1"), "1 good")
        self.assertEqual(setting.display("ITBACK/2/k/0/1,ITBIV/1/k/0/0"), "2 goods")

    def test_coerce_tidies_it(self) -> None:
        self.assertEqual(a_setting().coerce(" ITBACK/2/k/0/1 , ITBACK/2/k/0/1"), "ITBACK/2/k/0/1")


@unittest.skipUnless(VANILLA_DB.is_file(), "needs the vanilla database")
class WhatItCanSell(unittest.TestCase):
    def test_items_and_mushrooms_but_no_gear(self) -> None:
        goods = {good.id: good for good in read_goods(VANILLA_DB)}
        self.assertEqual(goods["ITMP_ARM_WP038_001"].name, "Tactical Shovel blueprint")
        self.assertEqual((goods["MSR_053"].name, goods["MSR_053"].category), ("Golden Skillshroom", "Mushrooms"))
        self.assertEqual((goods["ITBACK"].money, goods["ITBACK"].medal), (98000, 5))
        self.assertNotIn("ITMP_ARM_WP038_001U", goods)          # the unidentified copy
        self.assertFalse([g for g in goods if g.startswith("PT_")])
        self.assertNotIn("ITWD_01", goods)                      # nothing to call it


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
        from lid_db_manager.ui.goods_editor import GoodsEditor

        return GoodsEditor(a_setting(), value, VANILLA_DB)

    def test_it_opens_on_the_list(self) -> None:
        editor = self.editor()
        self.assertEqual(editor.entries(), a_setting().goods(""))
        self.assertEqual(editor.value(), "")                    # still the default
        self.assertEqual(editor.table.item(0, 0).text(), "Walnut")
        self.assertEqual(editor.table.item(1, 4).text(), "10 DM")   # 5 Golden Skillshrooms at 2 each

    def test_changing_adding_removing_and_moving(self) -> None:
        editor = self.editor()
        editor.table.cellWidget(0, 1).setValue(5)               # Walnut in fives
        editor.table.cellWidget(0, 3).setValue(20000)
        editor.add(["ITBACK"])
        self.assertEqual(editor.value(), "ITMT_WOOD_4/5/k/20000/0,MSR_053/5/d/8/1,ITBACK/1/k/0/0")
        editor.table.selectRow(2)
        editor.move(-1)
        self.assertEqual([e.id for e in editor.entries()], ["ITMT_WOOD_4", "ITBACK", "MSR_053"])
        editor.table.selectRow(0)
        editor.remove_highlighted()
        self.assertEqual([e.id for e in editor.entries()], ["ITBACK", "MSR_053"])

    def test_an_empty_list_cannot_be_used(self) -> None:
        editor = self.editor()
        editor.fill([])
        self.assertFalse(editor.ok_button.isEnabled())

    def test_a_good_no_longer_in_the_database_is_shown(self) -> None:
        editor = self.editor("NOT_A_THING/1/k/0/0")
        self.assertIn("not in your database", editor.table.item(0, 0).text())


if __name__ == "__main__":
    unittest.main()
