"""The Enemies Left mod: a line on the HUD with the floor's living enemies.

A .PackagePatch that replaces BrgHUD.PostRender with a longer copy, plus SQL
that writes where the line goes and its words. Most of what can go wrong - a
jump landing mid-statement, a token the engine cannot read, a name the patch
does not carry - is checked here without the game.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from lid_db_manager.asset_runner import _wanted_transforms
from lid_db_manager.mod_loader import load_mod_folder

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "tools"))
from ue3dis import Dis  # noqa: E402

TARGET = "BrgGame/CookedPCConsole/BrgGame.upk"
MOD = PROJECT_ROOT / "mods" / "Enemies Left"
PATCH = MOD / "tfc" / "Game" / "BrgGame" / "CookedPCConsole" / "BrgGame.upk.PackagePatch"
# Stew Multi Pull lives in its own repository (LiD-Stew-Multi-Pull), beside
# this checkout or where LID_STEW_MULTI_PULL says
STEW = Path(os.environ.get(
    "LID_STEW_MULTI_PULL",
    PROJECT_ROOT.parent / "LiD Stew Multi Pull" / "mods" / "Stew Multi Pull"))
TOOL = PROJECT_ROOT / "tools" / "build_enemies_left.py"
GAME_PACKAGE = Path(os.environ.get(
    "LID_GAME_UPK",
    r"C:\Program Files (x86)\Steam\steamapps\common\LET IT DIE"
    r"\BrgGame\CookedPCConsole\BrgGame.upk"))
VANILLA_DB = PROJECT_ROOT / "LiD Vanilla DB" / "5.0.4.0" / "masters.db"
POST_RENDER = "BrgHUD\\PostRender"
HEADER = 48


def read_script(data: bytes, refs: dict, names: dict):
    mem, disk = struct.unpack_from("<ii", data, HEADER - 8)
    reader = Dis(data[HEADER:HEADER + disk], obj_of=lambda r: refs.get(r, f"#{r}"),
                 name_of=lambda i: names.get(i, f"name#{i}"))
    return reader.run(), reader, mem, disk


class TheMod(unittest.TestCase):
    def test_it_is_sql_and_one_package_patch(self) -> None:
        mod = load_mod_folder(MOD)
        self.assertEqual([p.type for p in mod.patches], ["raw_sql", "tfc_installer"])
        self.assertEqual(mod.patches[1].transform_targets(), [TARGET])
        self.assertEqual(mod.requires_check_off, ["BrgGame.upk"])

    def test_the_settings_reach_the_sql(self) -> None:
        mod = load_mod_folder(MOD).with_settings(
            {"x": 40, "y": 900, "size": 150, "align": "0", "show_total": False, "colour": "16770560"})
        sql = mod.patches[0].sql
        for part in ("('LID_ENEMY_HUD_X', 40)", "('LID_ENEMY_HUD_Y', 900)",
                     "('LID_ENEMY_HUD_SCALE', 150 / 100.0)", "('LID_ENEMY_HUD_ALIGN', 0)",
                     "('LID_ENEMY_HUD_COLOUR', 16770560)",
                     "CASE WHEN 0 = 1"):
            self.assertIn(part, sql)


class ThePatch(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from lid_db_manager.upk import packagepatch
        cls.patch = packagepatch.read(PATCH)
        cls.refs = {r.index: r.full_path for r in cls.patch.object_references}
        cls.names = {r.index: r.text for r in cls.patch.name_references}
        cls.functions = {cls.refs[o.export_index + 1]: o.data for o in cls.patch.objects}

    def text(self) -> str:
        lines, *_ = read_script(self.functions[POST_RENDER], self.refs, self.names)
        return "\n".join(line[4] for line in lines)

    def test_the_font_is_read_as_a_fixed_size_array(self) -> None:
        # mUIManager.mFont is `Font mFont[N]`: its element is EX_ArrayElement
        # (0x1A), index first. 0.2.0 wrote EX_DynArrayElement (0x10), which
        # crashed the game at random on arriving on a floor.
        code = self.functions[POST_RENDER]
        self.assertIn(b"\x1a\x2c\x02\x19", code)
        self.assertNotIn(b"\x10\x2c\x02\x19", code)

    def test_it_changes_no_table_and_only_post_render(self) -> None:
        from lid_db_manager.upk import apply
        self.assertEqual(apply.unsupported(self.patch), "")
        self.assertEqual(list(self.functions), [POST_RENDER])

    def test_it_reads_back_and_every_jump_lands_on_a_statement(self) -> None:
        lines, reader, mem, disk = read_script(self.functions[POST_RENDER], self.refs, self.names)
        self.assertEqual((lines[-1][4], reader.pos, reader.mem), ("END", disk, mem))
        starts = {line[1] for line in lines}
        for _, kind, to in reader.targets:
            self.assertIn(to, starts, f"a {kind} to {to:#x}")
        self.assertEqual({ref for _, ref in reader.objs if ref} - set(self.refs), set())

    def test_it_draws_only_with_the_hud_on_a_floor_with_enemies(self) -> None:
        text = self.text()
        for part in ("CheckVisibleHUD()", "GetFloorStartEnemyNum(), 0)", "GetAliveEnemyNum(emptyparm)",
                     "'TUB.TXT_LID_ENEMIES_LEFT'", "DrawShadowString(", "SetColor("):
            self.assertIn(part, text)
        # and not in the hub, where the director keeps the last floor's numbers
        self.assertIn(r"N129(L:BrgHUD\PostRender\GInfo.{skip=0x9,BrgGameInfoNative\mStageManager", text)
        self.assertIn("IsInHub()", text)
        # DrawText's UseShadowColor draws the text itself black: not used
        self.assertNotIn("DrawText(", text)
        for const in ("X", "Y", "SCALE", "ALIGN", "COLOUR"):
            self.assertIn(f"GetConstFloat('LID_ENEMY_HUD_{const}'", text)

    def test_the_floats_it_borrows_are_ones_no_script_uses(self) -> None:
        # Checked against the whole stock package by hand when it was written;
        # here, that they are still the four it names.
        text = self.text()
        for field in ("mTestPosition", "mTestPositionAdd", "mTestAngle", "mTestTime", "mTestCounter"):
            self.assertIn(f"BrgUIManager\\{field}", text)


@unittest.skipUnless(VANILLA_DB.is_file(), f"no vanilla database at {VANILLA_DB}")
class TheSqlOnAVanillaDatabase(unittest.TestCase):
    def test_numbers_and_words_in_every_language(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "masters.db"
            shutil.copy2(VANILLA_DB, db)
            con = sqlite3.connect(str(db))
            con.executescript(load_mod_folder(MOD).patches[0].sql)
            self.assertEqual(
                con.execute("SELECT id, value FROM master_const_float WHERE id LIKE 'LID_ENEMY_HUD_%' "
                            "ORDER BY id").fetchall(),
                [("LID_ENEMY_HUD_ALIGN", 2.0), ("LID_ENEMY_HUD_COLOUR", 16777215.0),
                 ("LID_ENEMY_HUD_SCALE", 1.0), ("LID_ENEMY_HUD_X", 1880.0), ("LID_ENEMY_HUD_Y", 160.0)])
            langs = {lang for (lang,) in con.execute("SELECT DISTINCT lang FROM master_text")}
            words = dict(con.execute(
                "SELECT lang, txt FROM master_text WHERE sct = 'TUB' AND id = 'TXT_LID_ENEMIES_LEFT'"))
            self.assertEqual(set(words), langs)
            self.assertEqual(words["int"], "Enemies left: #0 / #1")
            con.close()


@unittest.skipUnless(GAME_PACKAGE.is_file(), "needs LET IT DIE installed (set LID_GAME_UPK)")
class AgainstTheRealPackage(unittest.TestCase):
    """LID_GAME_UPK has to be a STOCK BrgGame.upk."""

    def test_the_tool_makes_the_shipped_patch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run([sys.executable, str(TOOL), "--upk", str(GAME_PACKAGE), "--out", tmp],
                           check=True, capture_output=True)
            made = Path(tmp) / "Game" / "BrgGame" / "CookedPCConsole" / "BrgGame.upk.PackagePatch"
            self.assertEqual(made.read_bytes(), PATCH.read_bytes())

    def test_it_lands_beside_the_stew_and_drops_mods(self) -> None:
        from lid_db_manager.upk import package as P
        from lid_db_manager.upk import packagepatch

        if not STEW.is_dir():
            self.skipTest(f"needs the LiD Stew Multi Pull repository ({STEW})")
        mods = [load_mod_folder(MOD), load_mod_folder(STEW),
                load_mod_folder(PROJECT_ROOT / "mods" / "instant-drops")]
        plan, _ = _wanted_transforms(mods)[TARGET]
        out = P.read(plan.transform_target(TARGET, GAME_PACKAGE.read_bytes()))
        for patch in (PATCH, STEW / "tfc" / "x10" / "Game" / "BrgGame"
                      / "CookedPCConsole" / "BrgGame.upk.PackagePatch"):
            for o in packagepatch.read(patch).objects:
                e = out.exports[o.export_index]
                self.assertEqual(out.data[e.serial_offset:e.serial_offset + e.serial_size], o.data)


if __name__ == "__main__":
    unittest.main()
