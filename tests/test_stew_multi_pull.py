"""The Stew Multi-Pull mod: "Purchase xN" in the Mushroom Club's stew menu.

It is a .PackagePatch that replaces two functions of BrgUIMenu_SkillExchange in
BrgGame.upk with longer copies, one patch for each count the player can choose.
A patch carries both functions whole, so most of what can go wrong - a jump
landing mid-statement, a token the engine cannot read, a table the patch would
have to grow - is checked here without the game.
"""

from __future__ import annotations

import os
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
MOD = PROJECT_ROOT / "mods" / "Stew Multi Pull"
COUNTS = (5, 10, 15, 20, 25)
PATCH_PATH = Path("Game") / "BrgGame" / "CookedPCConsole" / "BrgGame.upk.PackagePatch"
PATCHES = {n: MOD / "tfc" / f"x{n}" / PATCH_PATH for n in COUNTS}
TOOL = PROJECT_ROOT / "tools" / "build_stew_multi_pull.py"
GAME_PACKAGE = Path(os.environ.get(
    "LID_GAME_UPK",
    r"C:\Program Files (x86)\Steam\steamapps\common\LET IT DIE"
    r"\BrgGame\CookedPCConsole\BrgGame.upk"))
FIGHTER_PASSIVES = PROJECT_ROOT / "mods" / "fighter-passives"
DROP_DELAY = b"\x2c\x06\x1f" + b"Item Drop Delay Time" + b"\x00\x28\x2c"
MENU = "BrgUIMenu_SkillExchange\\SetGachaTopMenuType"
TICK = "BrgUIMenu_SkillExchange\\Tick"
COUNT_BYTE = "I:BrgUIMenu_SkillExchange\\mReturnState"
MATINEE = "L:BrgUIMenu_SkillExchange\\Tick\\Matinee"
HEADER = 48


def read_script(data: bytes, refs: dict[int, str] | None = None):
    """A function's export data -> (statements, the reader that read them)."""
    mem, disk = struct.unpack_from("<ii", data, HEADER - 8)
    refs = refs or {}
    reader = Dis(data[HEADER:HEADER + disk], obj_of=lambda r: refs.get(r, f"#{r}"))
    lines = reader.run()
    return lines, reader, mem, disk


def script_text(data: bytes, refs: dict[int, str]) -> str:
    lines, *_ = read_script(data, refs)
    return "\n".join(line[4] for line in lines)


class TheMod(unittest.TestCase):
    def test_it_is_one_package_patch_for_brggame_upk(self) -> None:
        mod = load_mod_folder(MOD)
        self.assertEqual([p.type for p in mod.patches], ["tfc_installer"])
        self.assertEqual(mod.patches[0].transform_targets(), [TARGET])
        self.assertEqual(mod.requires_check_off, ["BrgGame.upk"])

    def test_ten_unless_the_player_chooses(self) -> None:
        mod = load_mod_folder(MOD)
        self.assertEqual(mod.patches[0].source_rel, "tfc/x10")
        self.assertIn("Purchase x10", mod.description)

    def test_each_choice_picks_its_own_patch(self) -> None:
        mod = load_mod_folder(MOD)
        self.assertEqual([o.value for o in mod.setting("count").options], [str(n) for n in COUNTS])
        for n in COUNTS:
            chosen = mod.with_settings({"count": str(n)})
            self.assertEqual(chosen.patches[0].source_rel, f"tfc/x{n}")
            self.assertIn(f"Purchase x{n}", chosen.description)

    def test_there_is_a_patch_for_every_choice_and_no_other(self) -> None:
        folders = sorted(p.name for p in (MOD / "tfc").iterdir())
        self.assertEqual(folders, sorted(f"x{n}" for n in COUNTS))
        for n, path in PATCHES.items():
            self.assertTrue(path.is_file(), n)


class ThePatches(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from lid_db_manager.upk import packagepatch
        cls.patches, cls.refs, cls.functions = {}, {}, {}
        for n, path in PATCHES.items():
            patch = packagepatch.read(path)
            refs = {r.index: r.full_path for r in patch.object_references}
            cls.patches[n], cls.refs[n] = patch, refs
            cls.functions[n] = {refs[o.export_index + 1]: o.data for o in patch.objects}

    def each(self):
        for n in COUNTS:
            with self.subTest(count=n):
                yield n, self.functions[n], self.refs[n]

    def test_they_change_no_table(self) -> None:
        from lid_db_manager.upk import apply
        for n, _, _ in self.each():
            self.assertEqual(apply.unsupported(self.patches[n]), "")

    def test_they_replace_the_two_menu_functions_and_nothing_else(self) -> None:
        for n, functions, _ in self.each():
            self.assertEqual(sorted(functions), [MENU, TICK])

    def test_both_read_back_to_their_end(self) -> None:
        for n, functions, refs in self.each():
            for path, data in functions.items():
                lines, reader, mem, disk = read_script(data, refs)
                self.assertEqual(lines[-1][4], "END", path)
                self.assertEqual((reader.pos, reader.mem), (disk, mem), path)

    def test_every_jump_lands_on_a_statement(self) -> None:
        for n, functions, refs in self.each():
            for path, data in functions.items():
                lines, reader, *_ = read_script(data, refs)
                starts = {line[1] for line in lines}
                for _, kind, to in reader.targets:
                    self.assertIn(to, starts, f"{path}: a {kind} to {to:#x}")

    def test_they_name_everything_the_new_code_relies_on(self) -> None:
        # Otherwise a game update that moved one of them would not be caught,
        # and the function would quietly use the wrong object.
        for n, functions, refs in self.each():
            for path, data in functions.items():
                _, reader, *_ = read_script(data, refs)
                used = {ref for _, ref in reader.objs if ref}
                self.assertEqual(used - set(refs), set(), path)

    def test_the_count_lives_in_the_unused_byte(self) -> None:
        for n, functions, refs in self.each():
            text = script_text(functions[TICK], refs)
            self.assertIn(f"{COUNT_BYTE} = {n}b", text)
            self.assertIn(f"{COUNT_BYTE} = 0b", text)
            # a repeat pull is one where 0 < count < N
            self.assertIn(f"N150(pcast0x3a({COUNT_BYTE}), {n})", text)

    def test_repeat_pulls_skip_the_animation_whatever_the_decal(self) -> None:
        # 0.2.0 kept the super-rare animation for 4- and 5-star decals; in game
        # it zoomed the camera out mid-run, so no pull's stars are looked at
        for n, functions, refs in self.each():
            self.assertNotIn(f"pcast0x3a({MATINEE})", script_text(functions[TICK], refs))

    def test_ten_is_the_patch_proven_in_game(self) -> None:
        import hashlib
        self.assertEqual(hashlib.sha256(PATCHES[10].read_bytes()).hexdigest(),
                         "b48bb3657bda4983deb3d23b5a1cd65bf3b3f231b80f072b67e62050fb67a293")

    def test_the_menu_reads_purchase_xn_in_the_games_own_words(self) -> None:
        for n, functions, refs in self.each():
            text = script_text(functions[MENU], refs)
            self.assertIn(f"'MSHR_KAIKAN.TXT_GACHA_TIMES', pcast0x53({n})", text)
            self.assertIn("I:BrgUIMenu_SkillExchange\\mGachaMenuSelectItemNum = 3", text)

    def test_they_differ_only_in_the_count(self) -> None:
        # The count is written three times: in the menu label, in
        # `mReturnState = N` and in `count < N`.
        base = PATCHES[10].read_bytes()
        for n in COUNTS:
            other = PATCHES[n].read_bytes()
            self.assertEqual(len(other), len(base), n)
            self.assertEqual(sum(a != b for a, b in zip(base, other)), 0 if n == 10 else 3, n)


@unittest.skipUnless(GAME_PACKAGE.is_file(), "needs LET IT DIE installed (set LID_GAME_UPK)")
class AgainstTheRealPackage(unittest.TestCase):
    """LID_GAME_UPK has to be a STOCK BrgGame.upk."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.game = GAME_PACKAGE.read_bytes()

    def test_the_reader_reads_every_function_of_the_stew_menu(self) -> None:
        from lid_db_manager.upk import package as P
        pkg = P.read(self.game)
        count = 0
        for i, e in enumerate(pkg.exports):
            path = pkg.full_path(i + 1)
            if not path.startswith("BrgUIMenu_SkillExchange.") or path.count(".") != 1:
                continue
            if pkg.object_name(e.class_index) != "Function":
                continue
            lines, reader, mem, disk = read_script(pkg.export_data(i))
            self.assertEqual((lines[-1][4], reader.pos, reader.mem), ("END", disk, mem), path)
            count += 1
        self.assertGreater(count, 60)

    def test_the_tool_makes_the_shipped_patches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run([sys.executable, str(TOOL), "--upk", str(GAME_PACKAGE), "--out", tmp],
                           check=True, capture_output=True)
            for n, path in PATCHES.items():
                self.assertEqual((Path(tmp) / f"x{n}" / PATCH_PATH).read_bytes(), path.read_bytes(), n)

    def test_it_lands_beside_instant_drops_and_the_passives_hook(self) -> None:
        from lid_db_manager.upk import package as P
        from lid_db_manager.upk import packagepatch
        stew = load_mod_folder(MOD).with_settings({"count": "25"})
        mods = [stew, load_mod_folder(PROJECT_ROOT / "mods" / "instant-drops")]
        if FIGHTER_PASSIVES.is_dir():
            mods.append(load_mod_folder(FIGHTER_PASSIVES))
        plan, _ = _wanted_transforms(mods)[TARGET]
        out = P.read(plan.transform_target(TARGET, self.game))

        for o in packagepatch.read(PATCHES[25]).objects:
            e = out.exports[o.export_index]
            self.assertEqual(out.data[e.serial_offset:e.serial_offset + e.serial_size], o.data)
        self.assertEqual(out.data[out.data.find(DROP_DELAY) + len(DROP_DELAY)], 0,
                         "the drop wait was lost")
        if FIGHTER_PASSIVES.is_dir():
            self.assertEqual(out.data.count(b"SKL_FTYPE_\x00"), 1, "the passives hook was lost")


if __name__ == "__main__":
    unittest.main()
