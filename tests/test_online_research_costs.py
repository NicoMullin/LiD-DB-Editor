"""The Online Research Costs mod: research recipes back to their online costs.

Run through the manager on a copy of the real vanilla database: with both
switches on every recipe in S3er0i9ng's profile has its online value, each
switch only touches its own columns, nothing else in master_part_research
changes, and switching the mod off puts every offline value back.
"""

from __future__ import annotations

import re
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from lid_db_manager.manager import Manager
from lid_db_manager.mod_loader import load_mod_folder
from lid_db_manager.paths import AppPaths

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MOD = PROJECT_ROOT / "mods" / "Online Research Costs"
MOD_ID = "Online Research Costs"
VANILLA_DB = PROJECT_ROOT / "LiD Vanilla DB" / "5.0.4.2" / "masters.db"
UPDATE = re.compile(r"^UPDATE master_part_research SET (.+) WHERE ptid = '([^']+)';$")


def wanted(name: str) -> dict:
    """{(ptid, column): online value} from one of the mod's SQL files."""
    out = {}
    for line in (MOD / name).read_text(encoding="utf-8").splitlines():
        match = UPDATE.match(line)
        if match:
            for pair in match.group(1).split(", "):
                column, value = pair.split(" = ")
                out[(match.group(2), column)] = float(value)
    return out


class TheMod(unittest.TestCase):
    def test_it_loads_and_is_sql_only(self) -> None:
        mod = load_mod_folder(MOD)
        self.assertEqual({p.type for p in mod.patches}, {"raw_sql_file"})
        self.assertEqual([s.id for s in mod.settings], ["materials", "splithium"])
        materials, splithium = wanted("materials.sql"), wanted("splithium.sql")
        self.assertEqual((len(materials), len(splithium)), (3685, 1001))
        self.assertEqual({c for _, c in splithium}, {"craft_spirit", "lvup_spirit"})
        self.assertFalse({c for _, c in materials} & {"craft_spirit", "lvup_spirit"})


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
        self.stock = self.table()
        self.materials = wanted("materials.sql")
        self.splithium = wanted("splithium.sql")
        self.manager = Manager(self.paths)
        self.manager.set_db_path(self.db)
        self.manager.set_enabled(MOD_ID, True)

    def tearDown(self) -> None:
        self._run.cleanup()

    def table(self) -> dict:
        con = sqlite3.connect(str(self.db))
        try:
            cur = con.execute("SELECT * FROM master_part_research")
            columns = [d[0] for d in cur.description]
            return {(row[0], c): v for row in cur for c, v in zip(columns, row)}
        finally:
            con.close()

    def save(self, **settings) -> None:
        for name, value in settings.items():
            self.manager.set_mod_setting(MOD_ID, name, value)
        report = self.manager.save_mod_list()
        self.assertTrue(report.ok, report)

    def changed(self) -> dict:
        now = self.table()
        return {k: v for k, v in now.items() if v != self.stock[k]}

    def test_both_on_gives_every_online_value_and_nothing_else(self) -> None:
        self.save()
        self.assertEqual(self.changed(), {**self.materials, **self.splithium})
        self.save()                             # a second save changes nothing more
        self.assertEqual(self.changed(), {**self.materials, **self.splithium})

    def test_each_switch_keeps_to_its_own_columns(self) -> None:
        self.save(splithium=False)
        self.assertEqual(self.changed(), self.materials)
        self.save(splithium=True, materials=False)
        self.assertEqual(self.changed(), self.splithium)

    def test_switching_it_off_puts_the_offline_costs_back(self) -> None:
        self.save()
        self.manager.set_enabled(MOD_ID, False)
        self.assertTrue(self.manager.save_mod_list().ok)
        self.assertEqual(self.table(), self.stock)


if __name__ == "__main__":
    unittest.main()
