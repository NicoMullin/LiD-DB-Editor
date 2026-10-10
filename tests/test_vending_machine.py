"""The Vending Machine mod: the online Hernia stock back on the Buy tab.

The offline game fills the Buy tab from the COMMON list, every row marked
always-in-stock, once each game day. The mod adds its rows there, so the checks
run it through the manager on a copy of the real vanilla database: what each
setting writes, the prices against the 2023 wiki's lists, and the mod switched
off again, which has to leave the table as it was.
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
MOD = PROJECT_ROOT / "mods" / "Vending Machine"
MOD_ID = "Vending Machine"
VANILLA_DB = PROJECT_ROOT / "LiD Vanilla DB" / "5.0.4.2" / "masters.db"
TABLE = "master_automaticshop_lineup"
ARMOURS = (26702, 26703, 26704)
KC, DM = 0, 1


class TheMod(unittest.TestCase):
    def test_it_loads_and_is_sql_only(self) -> None:
        mod = load_mod_folder(MOD)
        self.assertEqual([p.type for p in mod.patches], ["raw_sql"])
        self.assertEqual(mod.asset_targets(), [])

    def test_the_picker_holds_every_good_the_online_machine_sold(self) -> None:
        stock = {s.id: s for s in load_mod_folder(MOD).settings}["stock"]
        groups = [o.group for o in stock.options]
        self.assertEqual({g: groups.count(g) for g in set(groups)},
                         {"Faction metals": 24, "Materials": 37, "Mushrooms": 12, "Blueprints": 40})
        self.assertEqual(stock.default_picks, tuple(o.value for o in stock.options))


class OnTheVanillaDatabase(unittest.TestCase):
    def setUp(self) -> None:
        self._run = tempfile.TemporaryDirectory()
        root = Path(self._run.name)
        self.paths = AppPaths(root).ensure()
        shutil.copytree(MOD, self.paths.mods_dir / MOD.name)
        self.db = root / "game" / "masters.db"
        self.db.parent.mkdir()
        shutil.copy2(VANILLA_DB, self.db)
        self.stock = self.table()
        self.manager = Manager(self.paths)
        self.manager.set_db_path(self.db)
        self.manager.set_enabled(MOD_ID, True)

    def tearDown(self) -> None:
        self._run.cleanup()

    def rows(self, sql: str, *args) -> list:
        con = sqlite3.connect(str(self.db))
        try:
            return con.execute(sql, args).fetchall()
        finally:
            con.close()

    def table(self) -> list:
        return sorted(self.rows(f"SELECT * FROM {TABLE}"))

    def save(self, **settings) -> None:
        for name, value in settings.items():
            self.manager.set_mod_setting(MOD_ID, name, value)
        report = self.manager.save_mod_list()
        self.assertTrue(report.ok, report)

    def offers(self) -> dict:
        """(good, bundle size, currency) -> (KC for the bundle, DM for the bundle) as the row says."""
        return {(t, n, c): (pm, pd) for t, n, c, pm, pd in self.rows(
            f"SELECT type_id, pack_count, currency_type, pack_money, pack_metal FROM {TABLE} "
            "WHERE goods_id >= 90001")}

    def test_as_online(self) -> None:
        self.save()
        offers = self.offers()
        self.assertEqual(len(offers), 194 + 40)
        # every new row on the Buy tab's list, always in stock, nothing blinking
        self.assertEqual(self.rows(f"SELECT DISTINCT lineup_id, is_stable, is_special FROM {TABLE} "
                                   "WHERE goods_id >= 90001"), [("COMMON", 1, 0)])
        # faction metals for Death Metals only: D.O.D. ARMS Blue Metal for 4, or 5 for 16
        metals = {k for k in offers if k[0].startswith("ITMT_STONE_")}
        self.assertEqual(len(metals), 24 * 2)
        self.assertEqual({c for _, _, c in metals}, {DM})
        self.assertEqual(offers[("ITMT_STONE_DIY_1", 5, DM)], (0, 16))
        self.assertEqual(offers[("ITMT_STONE_DIY_1", 1, DM)], (0, 0))
        self.assertEqual(self.rows("SELECT buy_medal FROM master_item WHERE itemid = 'ITMT_STONE_DIY_1'"), [(4,)])
        # a bundle carries its own price: Veneer Plank x5 for 10,000 KC or 2 DM
        self.assertEqual(offers[("ITMT_WOOD_1", 5, KC)], (10000, 0))
        self.assertEqual(offers[("ITMT_WOOD_1", 5, DM)], (0, 2))
        self.assertNotIn(("ITMT_WOOD_1", 1, KC), offers)
        # a single costs what the game says: Walnut, 5,000 KC or 1 DM
        self.assertEqual(offers[("ITMT_WOOD_4", 1, KC)], (0, 0))
        self.assertEqual(self.rows("SELECT buy_money, buy_medal FROM master_item WHERE itemid = 'ITMT_WOOD_4'"),
                         [(5000, 1)])
        # mushrooms as mushrooms; blueprints one a day, for Death Metals
        self.assertEqual(self.rows(f"SELECT DISTINCT entity_type FROM {TABLE} WHERE type_id = 'MSR_053'"),
                         [("MUSHROOM",)])
        blueprints = self.rows(f"SELECT DISTINCT stock, currency_type FROM {TABLE} WHERE type_id LIKE 'ITMP_%' "
                               "AND goods_id >= 90001")
        self.assertEqual(blueprints, [(1, DM)])
        # the three cosmetic armours back to Death Metals
        self.assertEqual(self.rows(f"SELECT currency_type FROM {TABLE} WHERE goods_id IN (26702, 26703, 26704)"),
                         [(DM,)] * 3)
        # each in its place after the always-stocked seven
        priorities = [p for (p,) in self.rows(f"SELECT display_priority FROM {TABLE} WHERE goods_id >= 90001")]
        self.assertEqual(len(set(priorities)), len(priorities))
        self.assertGreater(min(priorities), 15)

    def test_kill_coins_only(self) -> None:
        self.save(pay="kc")
        offers = self.offers()
        self.assertEqual({c for _, _, c in offers}, {KC})
        # a faction metal never sold for Kill Coins online: the game's price, a bundle of 5 for 4
        self.assertEqual(self.rows("SELECT buy_money FROM master_item WHERE itemid = 'ITMT_STONE_DIY_1'"), [(20000,)])
        self.assertEqual(offers[("ITMT_STONE_DIY_1", 5, KC)], (80000, 0))
        self.assertEqual(offers[("ITMT_STONE_DIY_1", 1, KC)], (0, 0))
        # one way to buy each: no Death Metal twins left
        self.assertEqual(len(offers), 161)
        # blueprints for the game's Kill Coin price
        self.assertTrue(any(k[0].startswith("ITMP_") for k in offers))
        self.assertEqual(self.rows(f"SELECT currency_type FROM {TABLE} WHERE goods_id IN (26702, 26703, 26704)"),
                         [(KC,)] * 3)

    def test_death_metals_only(self) -> None:
        self.save(pay="dm")
        offers = self.offers()
        self.assertEqual({c for _, _, c in offers}, {DM})
        self.assertEqual(len(offers), 161)

    def test_picking_what_it_sells(self) -> None:
        self.save(stock="ITMT_WOOD_4,MSR_053", blueprints=False)
        self.assertEqual(sorted({t for t, _, _ in self.offers()}), ["ITMT_WOOD_4", "MSR_053"])

    def test_without_the_blueprints(self) -> None:
        self.save(blueprints=False)
        self.assertFalse([k for k in self.offers() if k[0].startswith("ITMP_")])
        self.assertEqual(len(self.offers()), 194)

    def test_saving_again_with_nothing_changed(self) -> None:
        self.save()
        before = self.table()
        self.save()
        self.assertEqual(self.table(), before)

    def own(self) -> list:
        return self.rows(
            f"SELECT goods_id, entity_type, type_id, currency_type, stock, pack_count, pack_money, pack_metal, "
            f"is_stable, lineup_id FROM {TABLE} WHERE goods_id >= 90001 ORDER BY goods_id")

    def test_your_own_list(self) -> None:
        self.save(mode="custom", custom="ITMT_WOOD_4/3/d/7/2,MSR_053/1/k/0/0,ITBACK/2/k/150000/1,"
                                        "PT_ARM_WP001_001/1/k/0/0,ITMP_ARM_WP038_001/1/d/0/1")
        self.assertEqual(self.own(), [
            # Walnut, 3 for 7 DM, twice a day: on the machine twice, each once a day
            (100033, "ITEM", "ITMT_WOOD_4", DM, 1, 3, 0, 7, 1, "COMMON"),
            (100034, "ITEM", "ITMT_WOOD_4", DM, 1, 3, 0, 7, 1, "COMMON"),
            # Golden Skillshroom at the game's price, no limit
            (100065, "MUSHROOM", "MSR_053", KC, 0, 1, 0, 0, 1, "COMMON"),
            # two Tryptamine D for 150,000 KC, once a day
            (100097, "ITEM", "ITBACK", KC, 1, 2, 150000, 0, 1, "COMMON"),
            # a weapon is not an item or a mushroom, so it is skipped
            (100161, "ITEM", "ITMP_ARM_WP038_001", DM, 1, 1, 0, 0, 1, "COMMON"),
        ])
        # the always-stocked seven as the game has them
        self.assertEqual(self.rows(f"SELECT currency_type FROM {TABLE} WHERE goods_id IN (26702, 26703, 26704)"),
                         [(KC,)] * 3)

    def test_your_own_list_starts_as_the_online_stock(self) -> None:
        self.save(mode="custom")
        own = self.own()
        self.assertEqual(len(own), 234)
        self.assertEqual({(t, n, c, pm, pd) for _, _, t, c, _, n, pm, pd, _, _ in own},
                         {(t, n, c, pm, pd) for (t, n, c), (pm, pd) in self._online_offers().items()})

    def _online_offers(self) -> dict:
        self.save(mode="online")
        offers = self.offers()
        self.save(mode="custom")
        return offers

    def test_switching_between_the_two(self) -> None:
        self.save(mode="custom", custom="ITMT_WOOD_4/1/k/0/0")
        self.assertEqual(len(self.own()), 1)
        self.save(mode="online")
        self.assertEqual(len(self.own()), 234)
        # back to your list, as you left it; saved again with nothing changed
        self.save(mode="custom")
        self.save()
        self.assertEqual([t for _, _, t, *_ in self.own()], ["ITMT_WOOD_4"])

    def test_switching_it_off_puts_the_table_back(self) -> None:
        self.save(pay="dm", blueprints=False)
        self.assertNotEqual(self.table(), self.stock)
        self.manager.set_enabled(MOD_ID, False)
        self.assertTrue(self.manager.save_mod_list().ok)
        self.assertEqual(self.table(), self.stock)


if __name__ == "__main__":
    unittest.main()
