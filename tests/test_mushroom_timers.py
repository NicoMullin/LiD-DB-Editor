"""The Mushroom Timers mod: how long mushroom effects last.

An effect's time is tmmin/tmmax in master_mushroom_efc. The checks run the mod
through the manager on a copy of the real vanilla database, in both modes:
one percentage for all, and seconds for each mushroom (grilled and thrown
following by the same share). Saving twice must not add up, and switching the
mod off must put the game's own times back.
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
MOD = PROJECT_ROOT / "mods" / "Mushroom Timers"
MOD_ID = "Mushroom Timers"
VANILLA_DB = PROJECT_ROOT / "LiD Vanilla DB" / "5.0.4.2" / "masters.db"


class TheMod(unittest.TestCase):
    def test_it_loads_with_a_setting_for_each_timed_mushroom(self) -> None:
        mod = load_mod_folder(MOD)
        self.assertEqual({p.type for p in mod.patches}, {"raw_sql"})
        self.assertEqual(mod.apply_mode, "diff")    # 200% saved twice must stay 200%
        self.assertEqual(len([s for s in mod.settings if s.id.startswith("msr_")]), 33)


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
        self.stock = self.times()
        self.stock_all = self.rows("SELECT * FROM master_mushroom_efc")
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

    def times(self) -> dict:
        return {i: (lo, hi) for i, lo, hi in self.rows("SELECT id, tmmin, tmmax FROM master_mushroom_efc")}

    def save(self, **settings) -> None:
        for name, value in settings.items():
            self.manager.set_mod_setting(MOD_ID, name, value)
        report = self.manager.save_mod_list()
        self.assertTrue(report.ok, report)

    def test_the_stock_times_are_what_the_mod_expects(self) -> None:
        self.assertEqual(self.stock["MSREFC_ATK_UP_01"], (30, 30))
        self.assertEqual(self.stock["MSREFC_ATK_UP_02"], (40, 40))
        self.assertEqual(self.stock["MSREFC_THROUGH_SLEEP_02"], (40, 40))

    def test_same_for_every_mushroom_doubles_their_effects_only(self) -> None:
        self.save(percent=200)
        after = self.times()
        self.assertEqual(after["MSREFC_ATK_UP_01"], (60, 60))
        self.assertEqual(after["MSREFC_SLEEP_02"], (80, 80))
        self.assertEqual(after["MSREFC_THROUGH_SLEEP_01"], (40, 40))
        # not given by a mushroom: a trap's poison and the convulsion stay
        self.assertEqual(after["MSREFC_POISON_03"], self.stock["MSREFC_POISON_03"])
        self.assertEqual(after["MSREFC_ALL_CONVLUSION_01"], self.stock["MSREFC_ALL_CONVLUSION_01"])
        # untimed effects stay untimed
        for effect, (lo, hi) in self.stock.items():
            if hi == 0:
                self.assertEqual(after[effect], (lo, hi), effect)

    def test_thrown_can_be_left_alone(self) -> None:
        self.save(percent=300, thrown=0)
        after = self.times()
        self.assertEqual(after["MSREFC_SLEEP_01"], (60, 60))
        self.assertEqual(after["MSREFC_THROUGH_SLEEP_01"], self.stock["MSREFC_THROUGH_SLEEP_01"])

    def test_each_mushroom_on_its_own_carries_grilled_and_thrown_along(self) -> None:
        self.save(mode="each", msr_024=60, msr_010=10)
        after = self.times()
        self.assertEqual(after["MSREFC_ATK_UP_01"], (60, 60))     # Crushroom eaten
        self.assertEqual(after["MSREFC_ATK_UP_02"], (80, 80))     # grilled, the same share
        self.assertEqual(after["MSREFC_SLEEP_01"], (10, 10))      # Fluffy Fungus halved
        self.assertEqual(after["MSREFC_SLEEP_02"], (20, 20))
        self.assertEqual(after["MSREFC_THROUGH_SLEEP_01"], (10, 10))
        self.assertEqual(after["MSREFC_DEF_UP_01"], self.stock["MSREFC_DEF_UP_01"])    # left at its own

    def test_each_mushroom_at_its_defaults_changes_nothing(self) -> None:
        self.save(mode="each")
        self.assertEqual(self.rows("SELECT * FROM master_mushroom_efc"), self.stock_all)

    def test_saving_again_does_not_add_up(self) -> None:
        self.save(percent=200)
        self.save(percent=200)
        self.assertEqual(self.times()["MSREFC_ATK_UP_01"], (60, 60))

    def test_switching_it_off_puts_the_games_times_back(self) -> None:
        self.save(percent=500)
        self.manager.set_enabled(MOD_ID, False)
        report = self.manager.save_mod_list()
        self.assertTrue(report.ok, report)
        self.assertEqual(self.rows("SELECT * FROM master_mushroom_efc"), self.stock_all)


if __name__ == "__main__":
    unittest.main()
