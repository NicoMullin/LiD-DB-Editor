"""Several mods changing the same package.

The Fighter Class Passives hook is a package patch (a TFC Installer mod's
.PackagePatch) for BrgGame.upk, and Instant Drops changes bytes in the same
file. The runner kept only the last of them in load order, so whichever mod
came first was silently lost from the game. After that was fixed it still kept
only one package patch per file - a player running Instant Drops beside two
other BrgGame.upk mods lost all but one of the three.

Mods may share a package. Only two of them changing the same object in it is
a clash.
"""

from __future__ import annotations

import os
import struct
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from lid_db_manager import asset_runner
from lid_db_manager.asset_runner import _wanted_transforms
from lid_db_manager.mod_loader import load_mod_folder

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TARGET = "BrgGame/CookedPCConsole/BrgGame.upk"
# Released on its own rather than with the manager, so it may not be here.
FIGHTER_PASSIVES = PROJECT_ROOT / "mods" / "fighter-passives"
GAME_PACKAGE = Path(os.environ.get(
    "LID_GAME_UPK",
    r"C:\Program Files (x86)\Steam\steamapps\common\LET IT DIE"
    r"\BrgGame\CookedPCConsole\BrgGame.upk"))
DROP_DELAY = b"\x2c\x06\x1f" + b"Item Drop Delay Time" + b"\x00\x28\x2c"


class FakePatch:
    """Stands in for a patch that rewrites a package; says when it ran."""

    def __init__(self, kind: str, label: str, ran: list[str]) -> None:
        self.type, self.label, self.ran = kind, label, ran

    def transform_targets(self) -> list[str]:
        return [TARGET]

    def transform_target(self, target: str, data: bytes) -> bytes:
        self.ran.append(self.label)
        return data + b"|" + self.label.encode()

    def to_pristine(self, raw: bytes) -> bytes:
        return raw


def a_mod(mod_id: str, *patches) -> SimpleNamespace:
    return SimpleNamespace(id=mod_id, patches=list(patches))


class StackingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ran: list[str] = []

    def patch(self, kind: str, label: str) -> FakePatch:
        return FakePatch(kind, label, self.ran)

    def run_plan(self, mods) -> bytes:
        patch, _mod_id = _wanted_transforms(mods)[TARGET]
        return patch.transform_target(TARGET, b"stock")

    def test_a_package_patch_and_byte_edits_all_land(self) -> None:
        out = self.run_plan([a_mod("drops", self.patch("package_bytes", "drop")),
                             a_mod("hook", self.patch("tfc_installer", "hook")),
                             a_mod("coins", self.patch("package_bytes", "coins"))])
        self.assertEqual(out, b"stock|hook|drop|coins")

    def test_the_package_patch_goes_first_whatever_the_load_order(self) -> None:
        # It was made against the stock package, so it gets the stock package;
        # the byte edits find their bytes by what surrounds them, either way.
        self.run_plan([a_mod("drops", self.patch("package_bytes", "drop")),
                       a_mod("hook", self.patch("tfc_installer", "hook"))])
        self.assertEqual(self.ran, ["hook", "drop"])

    def test_two_package_patches_on_one_file_both_land_in_load_order(self) -> None:
        out = self.run_plan([a_mod("one", self.patch("tfc_installer", "one")),
                             a_mod("drops", self.patch("package_bytes", "drop")),
                             a_mod("two", self.patch("tfc_installer", "two"))])
        self.assertEqual(out, b"stock|one|two|drop")

    def test_a_failure_names_the_mod_it_came_from(self) -> None:
        broken = self.patch("tfc_installer", "broken")
        broken.transform_target = mock.Mock(side_effect=ValueError("it does not fit"))
        patch, _ = _wanted_transforms([a_mod("broken-mod", broken),
                                       a_mod("fine", self.patch("package_bytes", "x"))])[TARGET]
        with self.assertRaisesRegex(ValueError, "broken-mod"):
            patch.transform_target(TARGET, b"stock")

    def test_stacking_one_package_leaves_a_patchs_other_packages_alone(self) -> None:
        """The loop used to put the stack in place of the patch itself, so a
        patch for two packages carried the first one's stack into the second."""
        other = "BrgGame/CookedPCConsole/Other.upk"
        both = self.patch("tfc_installer", "both")
        both.transform_targets = lambda: [TARGET, other]
        plan = _wanted_transforms([a_mod("drops", self.patch("package_bytes", "drop")),
                                   a_mod("both", both)])
        self.assertEqual(plan[other][0].transform_target(other, b"stock"), b"stock|both")


def an_empty_package_patch() -> bytes:
    """A .PackagePatch that changes nothing - enough for a mod to load."""
    tables = struct.pack("<ii", 0, 0) * 3
    return struct.pack("<i", 2) + tables + struct.pack("<i", 0) + struct.pack("<iii", 12, 0, 0)


def a_package_patch(objects: list[tuple[int, bytes]]) -> bytes:
    """A .PackagePatch replacing each (export index, data), and nothing else."""
    out = struct.pack("<i", 2) + struct.pack("<ii", 0, 0) * 3
    out += struct.pack("<i", len(objects))
    for index, data in objects:
        out += struct.pack("<ii", index, 0) + struct.pack("<i", len(data)) + data
    return out + struct.pack("<iii", 12, 0, 0)


def a_package_patch_mod(mods_dir: Path, mod_id: str, patch: bytes | None = None):
    """A mod with one .PackagePatch for BrgGame.upk and nothing else."""
    folder = mods_dir / mod_id
    cooked = folder / "tfc" / "Game" / "BrgGame" / "CookedPCConsole"
    cooked.mkdir(parents=True)
    (cooked / "BrgGame.upk.PackagePatch").write_bytes(
        an_empty_package_patch() if patch is None else patch)
    (folder / "mod.json").write_text(
        '{"id": "%s", "name": "%s", "description": "test mod", "version": "1.0.0",'
        ' "author": "tests", "patches": [{"type": "tfc_installer", "source": "tfc"}]}'
        % (mod_id, mod_id), encoding="utf-8")
    return load_mod_folder(folder)


class ConflictWarnings(unittest.TestCase):
    """The mod list warned "instant-drops and fighter-passives both write game
    file BrgGame.upk - fighter-passives wins" when both in fact land."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.mods = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def asset_conflicts(self, mods) -> list:
        from lid_db_manager.conflict import KIND_ASSET, KIND_OBJECT, analyze
        return [c for c in analyze(mods).conflicts if c.kind in (KIND_ASSET, KIND_OBJECT)]

    def test_a_package_patch_and_byte_edits_are_not_a_conflict(self) -> None:
        drops = load_mod_folder(PROJECT_ROOT / "mods" / "instant-drops")
        hook = a_package_patch_mod(self.mods, "hook")
        self.assertEqual(self.asset_conflicts([drops, hook]), [])
        self.assertEqual(self.asset_conflicts([hook, drops]), [])

    def test_two_package_patches_changing_different_objects_are_not(self) -> None:
        quests = a_package_patch_mod(self.mods, "quests", a_package_patch([(10, b"a")]))
        enemy = a_package_patch_mod(self.mods, "enemy", a_package_patch([(20, b"b")]))
        drops = load_mod_folder(PROJECT_ROOT / "mods" / "instant-drops")
        self.assertEqual(self.asset_conflicts([drops, quests, enemy]), [])

    def test_two_changing_the_same_object_are(self) -> None:
        one = a_package_patch_mod(self.mods, "one", a_package_patch([(10, b"a"), (11, b"x")]))
        two = a_package_patch_mod(self.mods, "two", a_package_patch([(10, b"b")]))
        found = self.asset_conflicts([one, two])
        self.assertEqual(len(found), 1)
        self.assertIn("BrgGame.upk", found[0].detail)
        self.assertIn("object #10", found[0].detail)
        self.assertNotIn("#11", found[0].detail)
        self.assertEqual("serious", found[0].severity)

    def test_two_byte_edits_on_the_same_spot_are(self) -> None:
        import shutil
        shutil.copytree(PROJECT_ROOT / "mods" / "instant-drops", self.mods / "copy")
        manifest = self.mods / "copy" / "mod.json"
        manifest.write_text(manifest.read_text(encoding="utf-8").replace(
            '"id": "instant-drops"', '"id": "drops-copy"'), encoding="utf-8")
        drops = load_mod_folder(PROJECT_ROOT / "mods" / "instant-drops")
        found = self.asset_conflicts([drops, load_mod_folder(self.mods / "copy")])
        self.assertEqual(len(found), 1)
        self.assertIn("Item Drop Delay Time", found[0].detail)

    def test_a_whole_copied_package_still_is(self) -> None:
        """A replacement file cannot be merged - it wins over everything."""
        from fixtures import write_mod
        folder = write_mod(self.mods, "whole", {"patches": [
            {"type": "asset_file", "source": "assets", "target": "BrgGame/CookedPCConsole"}]})
        (folder / "assets").mkdir(exist_ok=True)
        (folder / "assets" / "BrgGame.upk").write_bytes(b"whole package")
        drops = load_mod_folder(PROJECT_ROOT / "mods" / "instant-drops")
        found = self.asset_conflicts([drops, load_mod_folder(folder)])
        self.assertEqual(len(found), 1)


class PackagePatchesWithoutTextures(unittest.TestCase):
    """Only a texture pack needs the index of which package holds which
    texture, and building it the first time reads every package in the game -
    a few minutes, for a mod that names its one package itself."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.game = root / "game"
        (self.game / "BrgGame" / "CookedPCConsole").mkdir(parents=True)
        self.backups = root / "backups"
        self.backups.mkdir()
        self.mod = a_package_patch_mod(root / "mods", "hook")
        self.no_index = mock.patch("lid_db_manager.upk.texture_index.build",
                                   side_effect=AssertionError("the texture index was built"))
        self.no_index.start()

    def tearDown(self) -> None:
        self.no_index.stop()
        self._tmp.cleanup()

    def test_saving_does_not_build_the_texture_index(self) -> None:
        asset_runner._bind_tfc_patches([self.mod], self.game, self.backups, None)
        self.assertEqual(self.mod.patches[0].transform_targets(), [TARGET])

    def test_nor_does_switching_it_off(self) -> None:
        asset_runner.bind_tfc_patches_for_revert([self.mod], self.game, self.backups)
        self.assertEqual(self.mod.patches[0].transform_targets(), [TARGET])

    def test_nor_does_checking_it(self) -> None:
        asset_runner.bind_tfc_patches_for_validation([self.mod], self.game)
        self.assertEqual(self.mod.patches[0].transform_targets(), [TARGET])


@unittest.skipUnless(GAME_PACKAGE.is_file(), "needs LET IT DIE installed (set LID_GAME_UPK)")
class ThreeModsInTheRealPackage(unittest.TestCase):
    """What the player who reported it ran: Instant Drops and two package
    patches for other parts of BrgGame.upk. All three have to land."""

    def test_all_three_land(self) -> None:
        from lid_db_manager.upk import package as P

        game = GAME_PACKAGE.read_bytes()
        stock = P.read(game)
        # Two objects nobody else touches, nowhere near the drop wait.
        wait_at = stock.data.find(DROP_DELAY)
        picks = [i for i, e in enumerate(stock.exports)
                 if 64 <= e.serial_size <= 4096
                 and not e.serial_offset <= wait_at < e.serial_offset + e.serial_size][:2]
        changed = {}
        for i in picks:
            e = stock.exports[i]
            body = bytearray(stock.data[e.serial_offset:e.serial_offset + e.serial_size])
            body[-1] ^= 0xFF
            changed[i] = bytes(body)

        with tempfile.TemporaryDirectory() as tmp:
            mods = Path(tmp)
            first = a_package_patch_mod(
                mods, "quests", a_package_patch([(picks[0], changed[picks[0]])]))
            second = a_package_patch_mod(
                mods, "enemy", a_package_patch([(picks[1], changed[picks[1]])]))
            drops = load_mod_folder(PROJECT_ROOT / "mods" / "instant-drops")
            plan, _ = _wanted_transforms([drops, first, second])[TARGET]
            out = P.read(plan.transform_target(TARGET, game))

        for i in picks:
            e = out.exports[i]
            self.assertEqual(out.data[e.serial_offset:e.serial_offset + e.serial_size],
                             changed[i], f"object #{i} was lost")
        self.assertEqual(out.data[out.data.find(DROP_DELAY) + len(DROP_DELAY)], 0,
                         "the drop wait was lost")


@unittest.skipUnless(FIGHTER_PASSIVES.is_dir(), "Fighter Class Passives is not in mods/")
class TheFighterPassivesHook(unittest.TestCase):
    PATCH = (FIGHTER_PASSIVES / "tfc" / "Game" / "BrgGame" / "CookedPCConsole"
             / "BrgGame.upk.PackagePatch")

    def test_the_mod_carries_it(self) -> None:
        mod = load_mod_folder(FIGHTER_PASSIVES)
        self.assertEqual([p.type for p in mod.patches], ["raw_sql_file", "tfc_installer"])
        self.assertEqual(mod.patches[1].transform_targets(), [TARGET])

    def test_it_replaces_get_skill_array_and_nothing_else(self) -> None:
        from lid_db_manager.upk import apply, packagepatch
        patch = packagepatch.read(self.PATCH)
        self.assertEqual(apply.unsupported(patch), "")
        self.assertEqual([o.export_index for o in patch.objects], [39980])
        self.assertIn("BrgCommonPawn_CustomChara\\GetSkillArray",
                      [r.full_path for r in patch.object_references])

    @unittest.skipUnless(GAME_PACKAGE.is_file(), "needs LET IT DIE installed (set LID_GAME_UPK)")
    def test_it_and_instant_drops_both_land_in_the_real_package(self) -> None:
        from lid_db_manager.upk import package as P

        hook = load_mod_folder(FIGHTER_PASSIVES)
        drops = load_mod_folder(PROJECT_ROOT / "mods" / "instant-drops")
        game = GAME_PACKAGE.read_bytes()
        alone, _ = _wanted_transforms([drops])[TARGET]
        _, want = P.decompressed(alone.transform_target(TARGET, game))
        wanted_wait = want[want.find(DROP_DELAY) + len(DROP_DELAY)]

        both, _ = _wanted_transforms([hook, drops])[TARGET]
        _, flat = P.decompressed(both.transform_target(TARGET, game))
        self.assertEqual(flat.count(b"SKL_FTYPE_\x00"), 1, "the hook was lost")
        self.assertEqual(flat.count(b"SKL_FMAP_\x00"), 1, "the hook was lost")
        self.assertEqual(flat[flat.find(DROP_DELAY) + len(DROP_DELAY)], wanted_wait,
                         "the drop wait was lost")


if __name__ == "__main__":
    unittest.main()
