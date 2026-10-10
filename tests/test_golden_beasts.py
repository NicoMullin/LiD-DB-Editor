"""The Golden Beasts mod: more golden beasts, on more floors, or only golden ones.

Which beast turns up on a floor is drawn from a spawn list in master_beast_gen
- each list a few beasts with weights - and every floor area and spawn point
names one. The mod changes those lists, so the checks run it through the
manager on a copy of the real vanilla database: each setting's odds, saving
twice, and the mod switched off again, which has to leave the tables as they
were.
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
import unittest
from collections import defaultdict
from pathlib import Path

from lid_db_manager.manager import Manager
from lid_db_manager.mod_loader import load_mod_folder
from lid_db_manager.paths import AppPaths

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MOD = PROJECT_ROOT / "mods" / "Golden Beasts"
MOD_ID = "Golden Beasts"
VANILLA_DB = PROJECT_ROOT / "LiD Vanilla DB" / "5.0.4.2" / "masters.db"
TWINS = {"BST_SCORPION02": "BST_GSCORPION", "BST_BASS02": "BST_GBASS"}


def golden(beast: str) -> bool:
    return beast.startswith("BST_G")


def twin(beast: str) -> str:
    return TWINS.get(beast, "BST_G" + beast[4:])


class TheMod(unittest.TestCase):
    def test_it_loads_and_is_sql_only(self) -> None:
        mod = load_mod_folder(MOD)
        self.assertEqual({p.type for p in mod.patches}, {"raw_sql"})
        self.assertEqual(mod.asset_targets(), [])
        self.assertEqual(mod.apply_mode, "diff")    # x3 saved twice must stay x3


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
        self.stock = self.lists()
        self.stock_floor = self.rows("SELECT * FROM master_stage_beast_floor")
        self.manager = Manager(self.paths)
        self.manager.set_db_path(self.db)
        self.manager.set_enabled(MOD_ID, True)

    def tearDown(self) -> None:
        self._run.cleanup()

    def rows(self, sql: str, *args) -> list:
        con = sqlite3.connect(str(self.db))
        try:
            return sorted(con.execute(sql, args).fetchall())
        finally:
            con.close()

    def lists(self) -> dict:
        """Spawn list -> {beast: weight}."""
        found: dict = defaultdict(dict)
        for list_id, beast, freq in self.rows("SELECT id, bstid, freq FROM master_beast_gen"):
            found[list_id][beast] = freq
        return dict(found)

    def save(self, **settings) -> None:
        for name, value in settings.items():
            self.manager.set_mod_setting(MOD_ID, name, value)
        report = self.manager.save_mod_list()
        self.assertTrue(report.ok, report)

    def assert_totals_kept(self, after: dict) -> None:
        # The game crashed on the way into a floor - one whose own lists had
        # not changed - when x10 made lists add up to more than they did. So
        # every list keeps its total, and every normal beast stays in.
        for list_id, beasts in self.stock.items():
            with self.subTest(list_id=list_id):
                self.assertEqual(sum(after[list_id].values()), sum(beasts.values()))
                self.assertGreaterEqual(min(after[list_id].values()), 1)
                for beast in beasts:
                    self.assertIn(beast, after[list_id])

    def golden_share(self, beasts: dict) -> float:
        return sum(f for b, f in beasts.items() if golden(b)) / sum(beasts.values())

    def test_x1_leaves_the_games_odds_where_it_has_golden_beasts(self) -> None:
        self.save(chance=1)
        after = self.lists()
        for list_id, beasts in self.stock.items():
            if any(golden(b) for b in beasts):
                self.assertEqual(after[list_id], beasts)

    def test_more_of_them_raises_the_golden_share_inside_the_same_total(self) -> None:
        self.save()
        after = self.lists()
        self.assertEqual(set(after), set(self.stock))
        self.assert_totals_kept(after)
        for list_id, beasts in self.stock.items():
            with self.subTest(list_id=list_id):
                share = self.golden_share(beasts)
                if share == 0:
                    continue        # given golden twins: see the every-floor test
                self.assertEqual(set(after[list_id]), set(beasts))      # nothing added
                if 0 < share < 1:
                    # x3 the share - short of pushing a normal beast below 1 -
                    # give or take rounding to whole weights: up to 1 a beast
                    total, normals = sum(beasts.values()), sum(1 for b in beasts if not golden(b))
                    wanted = min(3 * share, (total - normals) / total)
                    self.assertAlmostEqual(self.golden_share(after[list_id]), wanted,
                                           delta=(normals + 1) / total)
                else:
                    self.assertEqual(after[list_id], beasts)            # all golden already

    def test_x50_never_pushes_a_normal_beast_out(self) -> None:
        self.save(chance=50)
        self.assert_totals_kept(self.lists())

    def test_saving_twice_or_changing_it_does_not_compound(self) -> None:
        self.save(chance=5)
        self.save()
        self.save(chance=10)
        # 7 in 252 x10 = 70, rounded up by what the normal beasts' rounding leaves
        self.assertEqual(self.lists()["BSTGEN_ARC_1_G"]["BST_GFROG"], 74)

    def test_every_floor_gets_golden_beasts(self) -> None:
        # Always on since 0.1.3: the player found the game crashed without it.
        self.save(chance=1)
        after = self.lists()
        self.assert_totals_kept(after)
        without = [g for g, beasts in self.stock.items() if not any(golden(b) for b in beasts)]
        self.assertGreaterEqual(len(without), 12)
        for list_id in without:
            with self.subTest(list_id=list_id):
                added = {b: f for b, f in after[list_id].items() if golden(b)}
                normal = self.stock[list_id]
                self.assertEqual(set(added), {twin(b) for b in normal})
                for gold in added:
                    weight = sum(f for b, f in normal.items() if twin(b) == gold)
                    self.assertGreaterEqual(added[gold], max(1, weight // 20))
                self.assertLess(self.golden_share(after[list_id]), 0.15)   # about a twentieth
        # the lists that already had golden beasts keep theirs as they were
        self.assertEqual(after["BSTGEN_ARC_1_G"], self.stock["BSTGEN_ARC_1_G"])

    def test_nearly_every_beast_golden(self) -> None:
        self.save(mode="only")
        after = self.lists()
        for list_id, beasts in self.stock.items():
            with self.subTest(list_id=list_id):
                # Every normal beast stays in its list at weight 1: a golden
                # beast's gold is in its normal twin's files, which the game
                # only loads while the normal one can turn up. With them gone
                # it crashed on the way into any floor.
                self.assertEqual(set(after[list_id]) - set(beasts),
                                 {twin(b) for b in beasts if not golden(b)} - set(beasts))
                for beast in beasts:
                    if not golden(beast):
                        self.assertEqual(after[list_id][beast], 1)
                # the same total weight, so a floor has as many beasts as before
                self.assertEqual(sum(after[list_id].values()), sum(beasts.values()))
        self.assertEqual(after["BSTGEN_ARC_1_G"]["BST_GFROG"], 46)          # 7 golden + 39 frogs
        self.assertEqual(after["BSTGEN_ARC_1_G"]["BST_GSCORPION"], 73)      # 7 + 66 red ones
        # the fixed spawns are left alone: on 21F and 31F nothing else loads a frog
        self.assertEqual(self.rows("SELECT * FROM master_stage_beast_floor"), self.stock_floor)

    def test_off_restores_the_tables(self) -> None:
        self.save(mode="only")
        self.save(mode="more", chance=7)
        self.manager.set_enabled(MOD_ID, False)
        report = self.manager.save_mod_list()
        self.assertTrue(report.ok, report)
        self.assertEqual(self.lists(), self.stock)
        self.assertEqual(self.rows("SELECT * FROM master_stage_beast_floor"), self.stock_floor)


if __name__ == "__main__":
    unittest.main()
