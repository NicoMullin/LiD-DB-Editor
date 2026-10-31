"""The Tengoku Blueprints mod: more Tengoku (H4) blueprints from Tengoku's medium chests.

Each Tengoku floor has a list of what its medium chests give, all blueprints.
The checks run the mod through the manager on a copy of the real vanilla
database: the Tengoku groups get the share asked for, every list keeps its
total weight and every row (none drops to 0), nothing outside those lists
changes, the chest-count toggle only touches Tengoku floors that can have
medium chests, a second save does not stack, and switching it off puts the
stock lists back.
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
MOD = PROJECT_ROOT / "mods" / "Tengoku Blueprints"
MOD_ID = "Tengoku Blueprints"
VANILLA_DB = PROJECT_ROOT / "LiD Vanilla DB" / "5.0.4.2" / "masters.db"
H4_GROUPS = "SELECT DISTINCT grp FROM master_item WHERE itemtype = 'ITTP_RMAP' AND itemid LIKE '%H4'"
LISTS = ("SELECT flrid, areaid, refareaid, grp, freq FROM master_floor_drop_gen "
         "WHERE flrid LIKE 'HVN%' AND type = 'PTGENTP_TRBOX_M'")
OTHER_DROPS = ("SELECT * FROM master_floor_drop_gen "
               "WHERE NOT (flrid LIKE 'HVN%' AND type = 'PTGENTP_TRBOX_M')")


class TheMod(unittest.TestCase):
    def test_it_loads_and_is_sql_only(self) -> None:
        mod = load_mod_folder(MOD)
        self.assertEqual({p.type for p in mod.patches}, {"raw_sql"})
        self.assertEqual(mod.asset_targets(), [])
        self.assertEqual(mod.apply_mode, "diff")    # 50% saved twice must stay 50%
        self.assertEqual([s.id for s in mod.settings], ["share", "most_chests"])


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
        self.stock_lists = self.rows(LISTS)
        self.stock_other = self.rows(OTHER_DROPS)
        self.stock_floors = self.rows("SELECT * FROM master_floor")
        self.stock_stages = self.rows("SELECT * FROM master_tmpfloor_stage")
        self.h4 = {row[0] for row in self.rows(H4_GROUPS)}
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

    def save(self, **settings) -> None:
        for name, value in settings.items():
            self.manager.set_mod_setting(MOD_ID, name, value)
        report = self.manager.save_mod_list()
        self.assertTrue(report.ok, report)

    def by_list(self, rows: list) -> dict:
        lists: dict = {}
        for flrid, areaid, refareaid, grp, freq in rows:
            lists.setdefault((flrid, areaid, refareaid), {})[grp] = freq
        return lists

    def test_the_stock_lists_are_what_the_mod_expects(self) -> None:
        self.assertEqual(len(self.h4), 28)
        lists = self.by_list(self.stock_lists)
        self.assertEqual(len(lists), 757)
        total = sum(sum(g.values()) for g in lists.values())
        h4 = sum(f for g in lists.values() for grp, f in g.items() if grp in self.h4)
        self.assertEqual((total, h4), (7570000, 18060))       # about 0.24%
        blueprints = {row[0] for row in self.rows(
            "SELECT DISTINCT grp FROM master_item WHERE itemtype = 'ITTP_RMAP'")}
        self.assertTrue({grp for g in lists.values() for grp in g} <= blueprints)

    def check_share(self, share: int) -> None:
        before = self.by_list(self.stock_lists)
        after = self.by_list(self.rows(LISTS))
        self.assertEqual(after.keys(), before.keys())
        mixed = 0
        for key, stock in before.items():
            now = after[key]
            self.assertEqual(now.keys(), stock.keys(), key)            # no row added or lost
            self.assertEqual(sum(now.values()), sum(stock.values()), key)
            self.assertGreaterEqual(min(now.values()), 1, key)
            has_h4 = any(grp in self.h4 for grp in stock)
            if not has_h4 or all(grp in self.h4 for grp in stock):
                self.assertEqual(now, stock, key)                       # nothing to trade
                continue
            mixed += 1
            got = 100 * sum(f for grp, f in now.items() if grp in self.h4) / sum(now.values())
            self.assertAlmostEqual(got, share, delta=1, msg=key)
        self.assertGreater(mixed, 0)
        self.assertEqual(self.rows(OTHER_DROPS), self.stock_other)

    def test_25_percent_by_default(self) -> None:
        self.save()
        self.check_share(25)
        self.assertEqual(self.rows("SELECT * FROM master_floor"), self.stock_floors)
        self.assertEqual(self.rows("SELECT * FROM master_tmpfloor_stage"), self.stock_stages)

    def test_50_and_90_percent(self) -> None:
        self.save(share=50)
        self.check_share(50)
        self.save(share=90)
        self.check_share(90)

    def test_the_tengoku_groups_keep_their_proportions(self) -> None:
        self.save(share=50)
        before = self.by_list(self.stock_lists)
        after = self.by_list(self.rows(LISTS))
        for key, stock in before.items():
            mine = {grp: f for grp, f in stock.items() if grp in self.h4}
            if len(mine) < 2 or len(mine) == len(stock):
                continue
            now = {grp: after[key][grp] for grp in mine}
            stock_total, now_total = sum(mine.values()), sum(now.values())
            for grp in mine:
                self.assertAlmostEqual(now[grp] / now_total, mine[grp] / stock_total, delta=0.01, msg=(key, grp))

    def test_0_percent_leaves_the_lists_alone(self) -> None:
        self.save(share=0)
        self.assertEqual(self.rows(LISTS), self.stock_lists)

    def test_the_most_chests_only_on_tengoku_floors_that_have_them(self) -> None:
        self.save(most_chests=True)
        for table, stock in (("master_floor", self.stock_floors), ("master_tmpfloor_stage", self.stock_stages)):
            rows = dict(self.rows(f"SELECT id, mtbmin || '/' || mtbmax FROM {table} WHERE id LIKE 'HVN%'"))
            for floor, counts in rows.items():
                low, high = map(int, counts.split("/"))
                self.assertEqual(low, high, (table, floor))
            self.assertEqual(self.rows(f"SELECT * FROM {table} WHERE id NOT LIKE 'HVN%'"),
                             [r for r in stock if not r[0].startswith("HVN")], table)
            none = self.rows(f"SELECT id FROM {table} WHERE id LIKE 'HVN%' AND mtbmax = 0")
            self.assertEqual(len(none), 152, table)                     # floors with none stay at none
        self.check_share(25)

    def test_saving_again_does_not_stack(self) -> None:
        self.save(share=50)
        first = self.rows(LISTS)
        self.save(share=50)
        self.assertEqual(self.rows(LISTS), first)
        self.save(share=25)
        self.check_share(25)

    def test_switching_it_off_puts_the_stock_lists_back(self) -> None:
        self.save(share=75, most_chests=True)
        self.manager.set_enabled(MOD_ID, False)
        report = self.manager.save_mod_list()
        self.assertTrue(report.ok, report)
        self.assertEqual(self.rows(LISTS), self.stock_lists)
        self.assertEqual(self.rows("SELECT * FROM master_floor"), self.stock_floors)
        self.assertEqual(self.rows("SELECT * FROM master_tmpfloor_stage"), self.stock_stages)


if __name__ == "__main__":
    unittest.main()
