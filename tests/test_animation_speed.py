"""The Animation Speed mod: a speed per action, and one change to BrgGame.upk.

The settings write one master_const_float row per animation, LID_ANIM_ and its
name, holding the speed as a fraction (150% is 1.5). The .PackagePatch makes the
two functions that start an animation look that row up; it is the same for any
settings. The SQL is run on a copy of the real vanilla database.
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from lid_db_manager.mod_loader import load_mod_folder
from lid_db_manager.upk import packagepatch as PP

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MOD = PROJECT_ROOT / "mods" / "Animation Speed"
VANILLA_DB = PROJECT_ROOT / "LiD Vanilla DB" / "5.0.4.2" / "masters.db"
PATCH = MOD / "tfc" / "Game" / "BrgGame" / "CookedPCConsole" / "BrgGame.upk.PackagePatch"
SETTINGS = ["eat", "chest", "pickup", "equip", "catch", "cook", "machine"]


def sql_of(mod) -> str:
    return next(p for p in mod.patches if p.type == "raw_sql").sql


class TheMod(unittest.TestCase):
    def test_a_speed_for_each_action(self) -> None:
        mod = load_mod_folder(MOD)
        self.assertEqual([s.id for s in mod.settings], SETTINGS)
        for s in mod.settings:
            self.assertEqual((s.default, s.minimum, s.maximum), (150, 50, 300), s.id)
        self.assertEqual([p.type for p in mod.patches], ["raw_sql", "tfc_installer"])

    def test_the_package_change_is_the_two_animation_starters(self) -> None:
        patch = PP.read(PATCH.read_bytes())
        self.assertEqual(len(patch.objects), 2)
        paths = {ref.object_id for ref in patch.object_references} | {ref.full_path for ref in patch.object_references}
        self.assertIn("BrgPawn_Base\\PlayAnimation", paths)
        self.assertIn("BrgPawn_Base\\PlayAnimationUpperPart", paths)
        self.assertIn("BrgDatabase\\GetConstFloat\\ReturnValue", paths)


@unittest.skipUnless(VANILLA_DB.is_file(), "needs the vanilla database")
class OnTheVanillaDatabase(unittest.TestCase):
    def setUp(self) -> None:
        self._run = tempfile.TemporaryDirectory()
        self.db = Path(self._run.name) / "masters.db"
        shutil.copy2(VANILLA_DB, self.db)
        self.addCleanup(self._run.cleanup)
        self.con = sqlite3.connect(str(self.db))
        self.addCleanup(self.con.close)

    def speeds(self) -> dict[str, float]:
        return dict(self.con.execute("SELECT id, value FROM master_const_float WHERE id LIKE 'LID_ANIM_%'"))

    def test_no_row_is_there_before(self) -> None:
        self.assertEqual(self.speeds(), {})

    def test_each_setting_sets_its_own_animations(self) -> None:
        mod = load_mod_folder(MOD).with_settings(
            {"eat": 200, "chest": 300, "pickup": 50, "equip": 100, "catch": 120, "cook": 250,
             "machine": 180})
        self.con.executescript(sql_of(mod))
        self.con.executescript(sql_of(mod))          # saving again changes nothing
        speeds = self.speeds()
        self.assertEqual(len(speeds), 19)
        self.assertEqual(speeds["LID_ANIM_ANI_CH_IDL_Eat"], 2.0)
        self.assertEqual(speeds["LID_ANIM_ANI_CH_IDL_Eat_Stand"], 2.0)
        self.assertEqual(speeds["LID_ANIM_ANI_CH_EVE_TreasureOpen"], 3.0)
        self.assertEqual(speeds["LID_ANIM_ANI_CH_IDL_Gather"], 0.5)
        self.assertEqual(speeds["LID_ANIM_ANI_CH_EVE_Equip_Leg"], 1.0)
        self.assertEqual(speeds["LID_ANIM_ANI_CH_EVE_Catch"], 1.2)
        self.assertEqual(speeds["LID_ANIM_ANI_CH_ATK_Burn"], 2.5)
        self.assertEqual(speeds["LID_ANIM_ANI_CH_EVE_VendingMachine02"], 1.8)


if __name__ == "__main__":
    unittest.main()
