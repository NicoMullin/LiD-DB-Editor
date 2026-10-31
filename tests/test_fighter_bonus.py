"""The Fighter Bonus Points mod: the odds of each bonus size a bought fighter rolls.

The game (FUN_1411b1d40 in 1.92) reads BODY_BONUS_N from master_const_int for
the sizes built into the exe, 0/5/10/15/20, and picks one by weight; a weight
of 0 is skipped and all five at 0 gives no bonus. Run through the manager on a
copy of the real vanilla database: each choice writes the five odds it should,
nothing else changes, and switching it off puts the stock odds back.
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
MOD = PROJECT_ROOT / "mods" / "fighter-bonus"
MOD_ID = "fighter-bonus"
VANILLA_DB = PROJECT_ROOT / "LiD Vanilla DB" / "5.0.4.2" / "masters.db"
SIZES = (0, 5, 10, 15, 20)
STOCK = {0: 50, 5: 20, 10: 15, 15: 10, 20: 5}


class TheMod(unittest.TestCase):
    def test_one_part_per_choice(self) -> None:
        mod = load_mod_folder(MOD)
        self.assertEqual([s.id for s in mod.settings], ["mode", "w0", "w5", "w10", "w15", "w20"])
        for mode, part in (("no_zero", "no-zero"), ("best", "best"), ("own", "own")):
            self.assertEqual([p.key for p in mod.with_settings({"mode": mode}).patches], [part])

    def test_own_odds_start_at_stock(self) -> None:
        mod = load_mod_folder(MOD)
        self.assertEqual({n: mod.setting(f"w{n}").default for n in SIZES}, STOCK)


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
        self.stock_rows = self.rows()
        self.assertEqual(self.odds(), STOCK)
        self.manager = Manager(self.paths)
        self.manager.set_db_path(self.db)
        self.manager.set_enabled(MOD_ID, True)

    def tearDown(self) -> None:
        self._run.cleanup()

    def rows(self) -> list:
        con = sqlite3.connect(str(self.db))
        try:
            return con.execute("SELECT * FROM master_const_int ORDER BY id").fetchall()
        finally:
            con.close()

    def odds(self) -> dict:
        found = dict(row for row in self.rows() if row[0].startswith("BODY_BONUS_"))
        return {n: found[f"BODY_BONUS_{n}"] for n in SIZES}

    def save(self, **settings) -> None:
        for name, value in settings.items():
            self.manager.set_mod_setting(MOD_ID, name, value)
        report = self.manager.save_mod_list()
        self.assertTrue(report.ok, report)

    def test_never_nothing_takes_only_the_zero_out(self) -> None:
        self.save()
        self.assertEqual(self.odds(), {**STOCK, 0: 0})

    def test_always_the_biggest(self) -> None:
        self.save(mode="best")
        self.assertEqual(self.odds(), {0: 0, 5: 0, 10: 0, 15: 0, 20: 100})

    def test_own_odds_and_changing_them(self) -> None:
        self.save(mode="own", w0=0, w5=10, w10=20, w15=30, w20=40)
        self.assertEqual(self.odds(), {0: 0, 5: 10, 10: 20, 15: 30, 20: 40})
        self.save(w0=5)
        self.assertEqual(self.odds(), {0: 5, 5: 10, 10: 20, 15: 30, 20: 40})

    def test_only_the_odds_change_and_switching_off_puts_them_back(self) -> None:
        self.save(mode="best")
        others = lambda rows: [r for r in rows if not r[0].startswith("BODY_BONUS_")]  # noqa: E731
        self.assertEqual(others(self.rows()), others(self.stock_rows))
        self.save(mode="own")                       # switching choice, then off
        self.manager.set_enabled(MOD_ID, False)
        self.assertTrue(self.manager.save_mod_list().ok)
        self.assertEqual(self.rows(), self.stock_rows)


if __name__ == "__main__":
    unittest.main()
