"""Tengoku Enjoyer (ZeroUnderOne): Tengoku to 1000F, in two versions or part by part; and Pre-Nerf Gear.

Run through the manager on a copy of the real vanilla database: the whole mod
takes the tower to 1000F, Pick and choose applies only the parts ticked, switching
the mod off puts every table back, and Pre-Nerf Gear changes its seven values and
nothing else. Ticked with Tengoku Enjoyer, Pre-Nerf Gear gets one note saying the
other already does it, not a clash for every value.
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from lid_db_manager.conflict import KIND_COVERED
from lid_db_manager.manager import Manager
from lid_db_manager.mod_loader import load_mod_folder
from lid_db_manager.paths import AppPaths

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TENGOKU = PROJECT_ROOT / "mods" / "Tengoku Enjoyer"
GEAR = PROJECT_ROOT / "mods" / "Pre-Nerf Gear"
VANILLA_DB = PROJECT_ROOT / "LiD Vanilla DB" / "5.0.4.2" / "masters.db"
GEAR_TABLES = {"master_atk_scale", "master_const_int", "master_part", "master_sklmv"}
PARTS = ["tower.sql", "old-floors.sql", "enemies.sql", "bosses.sql", "gear.sql"]


def files(**values) -> list[str]:
    mod = load_mod_folder(TENGOKU)
    return [p.path.name for p in mod.with_settings(values).patches]


class TheMods(unittest.TestCase):
    def test_the_two_versions_are_the_whole_mod(self) -> None:
        self.assertEqual(files(version="original"), ["haters-original.sql"] + PARTS)
        self.assertEqual(files(version="route_pure"), ["haters-route-pure.sql"] + PARTS)
        # a part switched off by hand earlier does not matter outside Pick and choose
        self.assertEqual(files(version="original", tower=False, gear=False), ["haters-original.sql"] + PARTS)

    def test_pick_and_choose_takes_only_the_parts_ticked(self) -> None:
        self.assertEqual(files(version="pick"), ["haters-original.sql"] + PARTS)
        self.assertEqual(files(version="pick", haters="route_pure", tower=False, old_floors=False, gear=False),
                         ["haters-route-pure.sql", "enemies.sql", "bosses.sql"])
        mod = load_mod_folder(TENGOKU)
        greyed = [s.id for s in mod.settings if not s.used_with({"version": "original"})]
        self.assertEqual(greyed, ["haters", "tower", "old_floors", "enemies", "bosses", "gear"])

    def test_pre_nerf_gear_is_the_same_as_the_gear_part(self) -> None:
        self.assertEqual((GEAR / "gear.sql").read_text(encoding="utf-8"),
                         (TENGOKU / "gear.sql").read_text(encoding="utf-8"))
        self.assertEqual(load_mod_folder(GEAR).covered_by, ["Tengoku Enjoyer"])


@unittest.skipUnless(VANILLA_DB.is_file(), "needs the vanilla database")
class OnTheVanillaDatabase(unittest.TestCase):
    def setUp(self) -> None:
        self._run = tempfile.TemporaryDirectory()
        root = Path(self._run.name)
        self.paths = AppPaths(root).ensure()
        for mod in (TENGOKU, GEAR):
            shutil.copytree(mod, self.paths.mods_dir / mod.name)
        self.db = root / "game" / "masters.db"
        self.db.parent.mkdir()
        shutil.copy2(VANILLA_DB, self.db)
        self.stock = self.tables()
        self.manager = Manager(self.paths)
        self.manager.set_db_path(self.db)

    def tearDown(self) -> None:
        self._run.cleanup()

    def tables(self) -> dict:
        con = sqlite3.connect(str(self.db))
        try:
            names = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table' "
                                               "AND name LIKE 'master_%'")]
            return {n: sorted(con.execute(f'SELECT * FROM "{n}"').fetchall(), key=repr) for n in names}
        finally:
            con.close()

    def one(self, sql: str):
        con = sqlite3.connect(str(self.db))
        try:
            return con.execute(sql).fetchone()
        finally:
            con.close()

    def save(self, **settings) -> None:
        for name, value in settings.items():
            self.manager.set_mod_setting("Tengoku Enjoyer", name, value)
        report = self.manager.save_mod_list()
        self.assertTrue(report.ok, report)

    def changed(self) -> set:
        now = self.tables()
        return {n for n in now if now[n] != self.stock.get(n)}

    def test_the_whole_mod_goes_to_1000f_and_comes_back(self) -> None:
        self.manager.set_enabled("Tengoku Enjoyer", True)
        self.save()
        self.assertEqual(self.one("SELECT value FROM master_const_str WHERE id = 'FLR_END_ID'"), ("HVN_FLR_0951",))
        self.assertEqual(self.one("SELECT COUNT(*) FROM master_floor WHERE id LIKE 'HVN_FLR_0%'")[0], 951)
        # the floor numbers the game shows are 50 above the ids: the last boss
        # floor is 1000F (id 0950), and 1001F is the empty top, as 451F was
        self.assertEqual(self.one("SELECT no, mbsmin FROM master_floor WHERE id = 'HVN_FLR_0950'"), (1000, 1))
        self.assertEqual(self.one("SELECT no, zmbmax FROM master_floor WHERE id = 'HVN_FLR_0951'"), (1001, 0))
        self.assertEqual(self.one("SELECT MAX(no) FROM master_floor WHERE id LIKE 'HVN_FLR_R00_%'"), (250,))
        self.assertEqual(self.one("SELECT gauge_cost FROM master_sklmv WHERE id = 'SKL_31_0'"), (1,))
        self.manager.set_enabled("Tengoku Enjoyer", False)
        self.save()
        self.assertEqual(self.changed(), set())

    def test_one_part_alone(self) -> None:
        self.manager.set_enabled("Tengoku Enjoyer", True)
        self.save(version="pick", tower=False, old_floors=False, bosses=False, gear=False)
        # the tougher enemies, and the Haters' own rows, which nothing uses without the floors
        self.assertEqual(self.changed() - {"master_custom_part", "master_dummy", "master_dummy_chara_equip_gen",
                                           "master_dummy_chara_skill_gen", "master_dummy_group",
                                           "master_dummy_name"},
                         {"master_floor", "master_tmpfloor_stage"})
        self.assertEqual(self.one("SELECT value FROM master_const_str WHERE id = 'FLR_END_ID'"), ("HVN_FLR_0401",))
        self.assertEqual(self.one("SELECT zmbatkup FROM master_floor WHERE id = 'HVN_FLR_0400'"), (7026,))

    def test_pre_nerf_gear_alone_and_beside_tengoku_enjoyer(self) -> None:
        self.manager.set_enabled("Pre-Nerf Gear", True)
        report = self.manager.save_mod_list()
        self.assertTrue(report.ok, report)
        self.assertEqual(self.changed(), GEAR_TABLES)
        self.assertEqual(self.manager.conflicts().conflicts, [])

        self.manager.set_enabled("Tengoku Enjoyer", True)
        found = self.manager.conflicts().conflicts
        self.assertEqual([c.kind for c in found], [KIND_COVERED])
        self.assertEqual(found[0].message(), "Tengoku Enjoyer already does what Pre-Nerf Gear does, "
                                             "so Pre-Nerf Gear does not need to be ticked as well")
        # with Tengoku Enjoyer's gear part off they share nothing, and nothing is said
        self.manager.set_mod_setting("Tengoku Enjoyer", "version", "pick")
        self.manager.set_mod_setting("Tengoku Enjoyer", "gear", False)
        self.assertEqual(self.manager.conflicts().conflicts, [])


if __name__ == "__main__":
    unittest.main()
