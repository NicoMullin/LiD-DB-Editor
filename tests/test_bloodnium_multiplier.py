"""The Bloodnium Multiplier mod: every kill's Bloodnium points times a setting.

The points are six rows of master_const_int. The checks run the mod through
the manager on a copy of the real vanilla database: the six rows multiplied,
nothing else touched (the Bloodnium a fighter needs to level up is a cost, not
an earning), a second save not multiplying again, and switching it off putting
the stock numbers back.
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
MOD = PROJECT_ROOT / "mods" / "Bloodnium Multiplier"
MOD_ID = "Bloodnium Multiplier"
VANILLA_DB = PROJECT_ROOT / "LiD Vanilla DB" / "5.0.4.2" / "masters.db"
POINTS = {
    "SCREAMER_BLOODNIUM_POINT": 1,
    "ENEMY_BLOODNIUM_POINT": 3,
    "HARD_HATER_BLOODNIUM_POINT": 9,
    "TREASURE_TUBER_BLOODNIUM_POINT": 10,
    "MBOSS_BLOODNIUM_POINT": 100,
    "BOSS_BLOODNIUM_POINT": 150,
}


class TheMod(unittest.TestCase):
    def test_it_loads_and_is_sql_only(self) -> None:
        mod = load_mod_folder(MOD)
        self.assertEqual({p.type for p in mod.patches}, {"raw_sql"})
        self.assertEqual(mod.asset_targets(), [])
        self.assertEqual(mod.apply_mode, "diff")    # x2 saved twice must stay x2


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
        self.stock_consts = self.rows("SELECT * FROM master_const_int")
        self.stock_levels = self.rows("SELECT * FROM master_bodylvl_exp")
        self.manager = Manager(self.paths)
        self.manager.set_db_path(self.db)
        self.manager.set_enabled(MOD_ID, True)

    def tearDown(self) -> None:
        self._run.cleanup()

    def rows(self, sql: str) -> list:
        con = sqlite3.connect(str(self.db))
        try:
            return sorted(con.execute(sql).fetchall())
        finally:
            con.close()

    def points(self) -> dict:
        return dict(self.rows("SELECT id, value FROM master_const_int WHERE id LIKE '%BLOODNIUM_POINT'"))

    def save(self, **settings) -> None:
        for name, value in settings.items():
            self.manager.set_mod_setting(MOD_ID, name, value)
        report = self.manager.save_mod_list()
        self.assertTrue(report.ok, report)

    def test_the_stock_points_are_what_the_mod_expects(self) -> None:
        stock = dict(self.stock_consts)
        for name, value in POINTS.items():
            self.assertEqual(stock[name], value, name)

    def test_x5_multiplies_every_kill_and_nothing_else(self) -> None:
        self.save(times=5)
        after = self.points()
        for name, value in POINTS.items():
            self.assertEqual(after[name], value * 5, name)
        self.assertEqual(dict(self.rows("SELECT id, value FROM master_const_int"))["BLOODNIUM_POINT_MAX"],
                         999999)      # the cap stays
        changed = set(self.rows("SELECT * FROM master_const_int")) ^ set(self.stock_consts)
        self.assertEqual({row[0] for row in changed}, set(POINTS))
        self.assertEqual(self.rows("SELECT * FROM master_bodylvl_exp"), self.stock_levels)

    def test_saving_again_does_not_multiply_twice(self) -> None:
        self.save(times=3)
        self.save(times=3)
        self.assertEqual(self.points()["ENEMY_BLOODNIUM_POINT"], 9)
        self.save(times=2)
        self.assertEqual(self.points()["BOSS_BLOODNIUM_POINT"], 300)

    def test_switching_it_off_puts_the_stock_numbers_back(self) -> None:
        self.save(times=10)
        self.manager.set_enabled(MOD_ID, False)
        report = self.manager.save_mod_list()
        self.assertTrue(report.ok, report)
        self.assertEqual(self.rows("SELECT * FROM master_const_int"), self.stock_consts)


if __name__ == "__main__":
    unittest.main()
