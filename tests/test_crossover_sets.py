"""The bundled Crossover mod's optional equipment sets.

S3er0i9ng's own manager splits three sets off the pack's core - DUKE-WoT Sniper
Rifle04, Assault Master G and White Kat - and its safe way of switching one off
never deletes anything: it expires the set's blueprint quests and closes their
research, so gear a player already owns keeps its models and definitions. The
bundled mod offers the same as three toggles, on by default (the mod gave all
three before they were optional). These run the mod's own SQL on a copy of the
real vanilla database, the way the manager applies it.
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from lid_db_manager.mod_loader import load_mod_folder
from lid_db_manager.sqlutil import split_statements

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MOD = PROJECT_ROOT / "mods" / "LET IT DIE Crossover Content v3.79"
VANILLA_DB = PROJECT_ROOT / "LiD Vanilla DB" / "5.0.4.2" / "masters.db"

SETS = {
    "wot_sniper": (["CODEX_WOT_ARM_WP025_0A4"], ["PT_ARM_WP025_0A4"]),
    "assault_master_g": ([f"CODEX_WOT_MIL_{s}_1002" for s in ("HEAD", "TOPS", "BTM")],
                         [f"PT_MIL_{s}_1002" for s in ("HEAD", "TOPS", "BTM")]),
    "white_kat": ([f"CODEX_WHITE_KAT_{s}" for s in ("HEAD", "TOPS", "BTM")],
                  [f"PT_SPE_{s}_{t}" for s in ("HEAD", "TOPS", "BTM")
                   for t in ("063", "163", "064", "164", "164_G")]),
}


class TheSettings(unittest.TestCase):
    def test_three_toggles_on_by_default(self) -> None:
        mod = load_mod_folder(MOD)
        self.assertEqual({s.id: s.default for s in mod.settings}, {name: 1 for name in SETS})
        self.assertEqual({s.group for s in mod.settings}, {"Optional equipment"})
        # with all three on, nothing but the recording and the artwork applies
        self.assertEqual([p.type for p in mod.patches], ["raw_sql_file", "asset_file"])

    def test_each_set_off_adds_only_its_own_part(self) -> None:
        mod = load_mod_folder(MOD)
        for name in SETS:
            with self.subTest(name=name):
                patches = mod.with_settings({name: 0}).patches
                self.assertEqual([p.type for p in patches], ["raw_sql_file", "raw_sql", "asset_file"])
                label = {s.id: s.label for s in mod.settings}[name]
                self.assertTrue(patches[1].description.startswith(label), patches[1].description)


@unittest.skipUnless(VANILLA_DB.is_file(), "needs the vanilla database")
class OnTheVanillaDatabase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "masters.db"
        shutil.copy2(VANILLA_DB, self.db)
        self.addCleanup(self._tmp.cleanup)      # runs last: after the connections close

    def apply(self, **values) -> sqlite3.Connection:
        mod = load_mod_folder(MOD).with_settings(values)
        con = sqlite3.connect(self.db)
        for patch in mod.patches:
            if patch.type == "raw_sql_file":
                sql = (MOD / patch.path).read_text(encoding="utf-8-sig")
            elif patch.type == "raw_sql":
                sql = patch.sql
            else:
                continue
            for statement in split_statements(sql):
                if statement.strip():
                    con.execute(statement)
        con.commit()
        return con

    def state(self, con, name):
        quests, parts = SETS[name]
        q = con.execute(f"SELECT qid, start_date, end_date FROM master_quest WHERE qid IN "
                        f"({','.join('?' * len(quests))})", quests).fetchall()
        r = con.execute(f"SELECT ptid, is_open FROM master_part_research WHERE ptid IN "
                        f"({','.join('?' * len(parts))})", parts).fetchall()
        return q, r

    def test_on_the_quests_run_and_the_research_is_open(self) -> None:
        con = self.apply()
        self.addCleanup(con.close)
        for name, (quests, parts) in SETS.items():
            with self.subTest(name=name):
                q, r = self.state(con, name)
                self.assertEqual(len(q), len(quests))
                self.assertEqual({(s, e) for _, s, e in q}, {(-1, -1)})
                self.assertEqual(len(r), len(parts))
                self.assertEqual({o for _, o in r}, {1})

    def test_off_expires_the_quests_and_closes_the_research_and_keeps_the_rest(self) -> None:
        def everything_else(c):
            return tuple(c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                         for t in ("master_part", "master_item", "master_reward", "master_quest"))

        on = self.apply()
        counts = everything_else(on)
        on.close()
        for name in SETS:
            with self.subTest(name=name):
                shutil.copy2(VANILLA_DB, self.db)
                con = self.apply(**{name: 0})
                self.addCleanup(con.close)      # closing twice is harmless
                q, r = self.state(con, name)
                self.assertEqual({(s, e) for _, s, e in q}, {(0, 1)})
                self.assertEqual({o for _, o in r}, {0})
                # the other two sets are untouched
                for other in set(SETS) - {name}:
                    oq, orr = self.state(con, other)
                    self.assertEqual({(s, e) for _, s, e in oq}, {(-1, -1)})
                    self.assertEqual({o for _, o in orr}, {1})
                # nothing deleted: parts, items, rewards and quests all still there
                self.assertEqual(everything_else(con), counts)
                con.close()             # before the next pass copies over the file


if __name__ == "__main__":
    unittest.main()
