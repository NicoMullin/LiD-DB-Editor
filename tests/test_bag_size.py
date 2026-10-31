"""The Bag Size mod: bigger fighter bags, by a floor or by an amount over stock.

Run through the manager on a copy of the real vanilla database: 'at least'
raises every smaller bag to the number and leaves bigger ones alone, 'more than
stock' adds to every bag and stops at 60, nothing outside bag_capacity changes,
a second save does not stack, and switching it off puts every bag back.
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from lid_db_manager.manager import Manager
from lid_db_manager.mod_loader import load_mod_folder
from lid_db_manager.paths import AppPaths

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MOD = PROJECT_ROOT / "mods" / "bag-size"
MOD_ID = "bag-size"
VANILLA_DB = PROJECT_ROOT / "LiD Vanilla DB" / "5.0.4.2" / "masters.db"
BAGS = "SELECT type, grade, limit_break, bag_capacity FROM master_body_detail"


class TheMod(unittest.TestCase):
    def test_one_part_per_way(self) -> None:
        mod = load_mod_folder(MOD)
        self.assertEqual([s.id for s in mod.settings], ["mode", "slots", "extra"])
        self.assertEqual([p.key for p in mod.with_settings({"mode": "at_least"}).patches], ["at-least"])
        self.assertEqual([p.key for p in mod.with_settings({"mode": "more"}).patches], ["more"])
        self.assertEqual(mod.apply_mode, "diff")


@unittest.skipUnless(VANILLA_DB.is_file(), "needs the vanilla database")
class OnTheVanillaDatabase(unittest.TestCase):
    def setUp(self) -> None:
        self._run = tempfile.TemporaryDirectory()
        root = Path(self._run.name)
        self.paths = AppPaths(root).ensure()
        shutil.copytree(MOD, self.paths.mods_dir / MOD.name)
        self.db = root / "game" / "masters.db"
        self.db.parent.mkdir()
        shutil.copy2(VANILLA_DB, self.db)
        self.stock = self.bags()
        self.stock_rows = self.rows("SELECT * FROM master_body_detail")
        self.manager = Manager(self.paths)
        self.manager.set_db_path(self.db)
        self.manager.set_enabled(MOD_ID, True)

    def tearDown(self) -> None:
        self._run.cleanup()

    def rows(self, sql: str) -> list:
        con = sqlite3.connect(str(self.db))
        try:
            return sorted(con.execute(sql).fetchall(), key=repr)
        finally:
            con.close()

    def bags(self) -> dict:
        return {row[:3]: row[3] for row in self.rows(BAGS)}

    def save(self, **settings) -> None:
        for name, value in settings.items():
            self.manager.set_mod_setting(MOD_ID, name, value)
        report = self.manager.save_mod_list()
        self.assertTrue(report.ok, report)

    def test_at_least_raises_small_bags_and_leaves_big_ones(self) -> None:
        self.save(mode="at_least", slots=40)
        now = self.bags()
        for key, stock in self.stock.items():
            self.assertEqual(now[key], max(stock, 40), key)
        self.assertIn(54, now.values())             # a Collector's top bag stays as it was
        self.save()                                 # saving again changes nothing
        self.assertEqual(self.bags(), now)

    def test_more_adds_to_every_bag_up_to_60(self) -> None:
        self.save(mode="more", extra=40)
        now = self.bags()
        for key, stock in self.stock.items():
            self.assertEqual(now[key], min(stock + 40, 60), key)
        self.assertEqual(max(now.values()), 60)
        self.save()
        self.assertEqual(self.bags(), now)

    def test_no_bag_can_be_set_past_60(self) -> None:
        """Past 60 the game's bag goes wrong (and the Express Pass adds 10)."""
        mod = load_mod_folder(MOD)
        self.assertEqual(mod.setting("slots").maximum, 60)
        stock_smallest = min(self.stock.values())
        self.assertEqual(stock_smallest + mod.setting("extra").maximum, 60)

    def test_only_bags_change_and_switching_off_puts_them_back(self) -> None:
        self.save(mode="at_least", slots=60)
        self.assertEqual(set(self.bags().values()), {60})
        strip = lambda rows: [tuple(v for i, v in enumerate(r) if i != self.bag_column()) for r in rows]  # noqa: E731
        self.assertEqual(strip(self.rows("SELECT * FROM master_body_detail")), strip(self.stock_rows))
        self.manager.set_enabled(MOD_ID, False)
        self.assertTrue(self.manager.save_mod_list().ok)
        self.assertEqual(self.rows("SELECT * FROM master_body_detail"), self.stock_rows)

    def bag_column(self) -> int:
        con = sqlite3.connect(str(self.db))
        try:
            return [r[1] for r in con.execute("PRAGMA table_info(master_body_detail)")].index("bag_capacity")
        finally:
            con.close()


if __name__ == "__main__":
    unittest.main()
