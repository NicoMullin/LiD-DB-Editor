"""A TFC Installer mod, installed and removed through the manager's own path.

The mod format and install logic are FCH823's, from TFC Installer, ported with
their permission. These tests run the whole thing the way a player would: tick
the mod, and the packages it changes are rebuilt from stock and put in, with
the pack's texture cache beside them; untick it, and every one goes back.

They need the reference copy of the Tommygun mod and the stock packages it
changes, kept outside the repository, and skip without them.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from test_upk import HAVE_REAL_FILES, REFERENCE

from lid_db_manager import asset_runner
from lid_db_manager.mod_loader import scan_mods
from lid_db_manager.upk import package as P

COOKED = "BrgGame/CookedPCConsole"
WEAPON = "WP_AssaultRifle3102_SF.upk"
ICON = "UI_Icon_PT_ARM_WP031_0B4_SF.upk"


def same_but_guid(mine: bytes, theirs: bytes) -> bool:
    """Equal once decompressed, apart from the GUID TFC Installer writes into."""
    a, b = P.read(mine), P.read(theirs)
    at = a.summary.offsets_at["thumbnail_table_offset"] + 4
    x, y = bytearray(a.data), bytearray(b.data)
    x[at:at + 16] = y[at:at + 16]
    return x == y


@unittest.skipUnless(HAVE_REAL_FILES, "needs the reference copy of the Tommygun mod")
class TfcInstallerModTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.game = root / "game"
        self.cooked = self.game / COOKED
        self.cooked.mkdir(parents=True)
        for name in (WEAPON, ICON):
            shutil.copy2(REFERENCE / "stock" / name, self.cooked / name)
        self.backups = root / "manager" / "backups"
        self.backups.mkdir(parents=True)
        self.mods = root / "mods"
        mod = self.mods / "Tommygun"
        shutil.copytree(REFERENCE / "tommygun", mod / "tfc")
        (mod / "mod.json").write_text(json.dumps({
            "id": "Tommygun", "name": "Tommygun", "version": "1.0",
            "description": "a TFC Installer mod", "author": "someone",
            "patches": [{"type": "tfc_installer", "source": "tfc"}],
        }), encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def mod(self):
        return scan_mods(self.mods).by_id["Tommygun"]

    def apply(self, mods):
        report = asset_runner.apply_asset_patches(mods, self.game, self.backups)
        self.assertTrue(report.ok, report.error)
        return report

    def switch_off(self) -> None:
        """What Manager.revert does: a freshly loaded mod, bound, then restored."""
        mod = self.mod()                      # a new object, as after a restart
        asset_runner.bind_tfc_patches_for_revert([mod], self.game, self.backups)
        report = asset_runner.restore_targets(mod.asset_targets(), self.game, self.backups)
        self.assertTrue(report.ok, report.error)

    def test_it_loads_as_a_mod(self) -> None:
        summary = self.mod().patches[0].summary()
        self.assertIn("1 package patch(es)", summary)
        self.assertIn("6 texture(s)", summary)

    def test_installing_it_matches_tfc_installer(self) -> None:
        self.apply([self.mod()])
        for name in (WEAPON, ICON):
            self.assertTrue(same_but_guid((self.cooked / name).read_bytes(),
                                          (REFERENCE / "tfc-output" / name).read_bytes()),
                            f"{name} differs from TFC Installer's")
        cache = self.cooked / "Texture2D_0.tfc"
        self.assertEqual((REFERENCE / "tommygun" / "TexturePack" / "Texture2D_0.tfc").read_bytes(),
                         cache.read_bytes())

    def test_switching_it_off_puts_the_game_back(self) -> None:
        self.apply([self.mod()])
        self.switch_off()
        for name in (WEAPON, ICON):
            self.assertEqual((REFERENCE / "stock" / name).read_bytes(),
                             (self.cooked / name).read_bytes(), f"{name} was not put back")
        self.assertFalse((self.cooked / "Texture2D_0.tfc").exists(),
                         "the installed texture cache was left behind")

    def test_saving_twice_changes_nothing(self) -> None:
        self.apply([self.mod()])
        first = {p.name: p.read_bytes() for p in self.cooked.iterdir()}
        self.apply([self.mod()])
        second = {p.name: p.read_bytes() for p in self.cooked.iterdir()}
        self.assertEqual(first, second)

    def exe_listing(self, *names: str) -> None:
        from test_exe_checksums import an_executable
        exe = self.game / "Binaries" / "Win64" / "BrgGame-Steam.exe"
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.write_bytes(an_executable([(name, "0" * 40) for name in names]))

    def test_it_is_refused_while_the_game_still_checks_what_it_rebuilds(self) -> None:
        from lid_db_manager import validator
        self.exe_listing(WEAPON, "Something_Else_SF.upk")
        mod = self.mod()
        asset_runner._bind_tfc_patches([mod], self.game, self.backups, None)
        # The weapon has a patch; the icon only holds a texture from the pack -
        # both count, though the mod names neither.
        self.assertEqual([WEAPON], validator.still_checked(mod, self.game))
        self.exe_listing(WEAPON, ICON)
        self.assertEqual(sorted([WEAPON, ICON]),
                         sorted(validator.still_checked(mod, self.game)))

    def test_it_is_allowed_once_the_check_is_off(self) -> None:
        from lid_db_manager import validator
        self.exe_listing(WEAPON[:-1] + "X", ICON[:-1] + "X")
        mod = self.mod()
        asset_runner._bind_tfc_patches([mod], self.game, self.backups, None)
        self.assertEqual([], validator.still_checked(mod, self.game))

    def exe_with_stock_hashes(self, mangled: bool) -> None:
        """An executable listing both packages with their real stock SHA-1s."""
        import hashlib
        from test_exe_checksums import an_executable
        entries = []
        for name in (WEAPON, ICON):
            sha1 = hashlib.sha1((REFERENCE / "stock" / name).read_bytes()).hexdigest()
            entries.append((name[:-1] + "X" if mangled else name, sha1))
        exe = self.game / "Binaries" / "Win64" / "BrgGame-Steam.exe"
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.write_bytes(an_executable(entries))

    def test_a_package_another_tool_already_changed_is_refused(self) -> None:
        # The check is off (names mangled) - but the stock hashes are still there.
        self.exe_with_stock_hashes(mangled=True)
        shutil.copy2(REFERENCE / "tfc-output" / WEAPON, self.cooked / WEAPON)
        report = asset_runner.apply_asset_patches([self.mod()], self.game, self.backups)
        self.assertFalse(report.ok)
        self.assertIn("TFC Installer", report.error)
        self.assertEqual((REFERENCE / "tfc-output" / WEAPON).read_bytes(),
                         (self.cooked / WEAPON).read_bytes(), "nothing should have changed")

    def test_stock_packages_pass_that_check(self) -> None:
        self.exe_with_stock_hashes(mangled=True)
        self.apply([self.mod()])
        self.switch_off()
        self.assertEqual((REFERENCE / "stock" / WEAPON).read_bytes(),
                         (self.cooked / WEAPON).read_bytes())

    def test_a_cache_number_already_taken_is_skipped(self) -> None:
        # Another tool's texture cache is already Texture2D_0.
        (self.cooked / "Texture2D_0.tfc").write_bytes(b"someone else's")
        self.apply([self.mod()])
        self.assertEqual(b"someone else's", (self.cooked / "Texture2D_0.tfc").read_bytes())
        self.assertTrue((self.cooked / "Texture2D_1.tfc").is_file())
        weapon = P.read(self.cooked / WEAPON)
        from lid_db_manager.upk import texture2d as T2
        import struct
        texture = T2.read(weapon, T2.textures_by_path(weapon)[
            "wp_assaultrifle3102\\textures\\tx_wp_assaultrifle3102_d"])
        tag = next(t for t in texture.tags if t.name == "TextureFileCacheName")
        self.assertEqual(2, struct.unpack("<ii", tag.value)[1], "should point at Texture2D_1")
        self.switch_off()
        self.assertEqual(b"someone else's", (self.cooked / "Texture2D_0.tfc").read_bytes(),
                         "switching off must not touch another tool's cache")
        self.assertFalse((self.cooked / "Texture2D_1.tfc").exists())


@unittest.skipUnless(HAVE_REAL_FILES, "needs the reference copy of the Tommygun mod")
class DroppingATfcModTests(unittest.TestCase):
    """What happens when a player drops the mod on the Install dialog."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.mods = self.root / "mods"
        self.mods.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def check_installed(self, folder: Path) -> None:
        from lid_db_manager.mod_loader import scan_mods
        mod = scan_mods(self.mods).by_id[folder.name]
        self.assertEqual(["tfc_installer"], [p.type for p in mod.patches])
        self.assertTrue((folder / "tfc" / "TexturePack" / "Texture2D_0.tfc").is_file())

    def test_the_folder_is_recognised_and_installed(self) -> None:
        from lid_db_manager import install
        candidate = install.inspect(REFERENCE / "tommygun")
        self.assertEqual(install.KIND_TFC_MOD, candidate.kind)
        self.assertIn("1 package patch(es), 6 texture(s)", candidate.note)
        folder = install.install(candidate, self.mods, "Tommygun")
        self.check_installed(folder)

    def test_a_zip_of_it_installs_the_same_way(self) -> None:
        from lid_db_manager import install
        archive = self.root / "Tommygun.zip"
        shutil.make_archive(str(archive.with_suffix("")), "zip",
                            root_dir=REFERENCE, base_dir="tommygun")
        folder = install.install(install.inspect(archive), self.mods, "Tommygun")
        self.check_installed(folder)

    def test_one_carrying_things_not_supported_is_refused_whole(self) -> None:
        from lid_db_manager import install
        for extra, expect in (("Game/BrgGame/Config/SteamPCRelease-BrgEngine.IniPatch", "ini patches"),
                              ("Game/BrgGame/Movies/Intro.bik", "game files"),
                              ("System/MyDocuments/My Games/thing.ini", "system folders")):
            folder = self.root / f"mod-{expect.replace(' ', '-')}"
            shutil.copytree(REFERENCE / "tommygun", folder)
            (folder / extra).parent.mkdir(parents=True, exist_ok=True)
            (folder / extra).write_text("x", encoding="utf-8")
            with self.assertRaises(install.InstallError) as caught:
                install.inspect(folder)
            self.assertIn(expect, str(caught.exception))
            self.assertIn("TFC Installer", str(caught.exception))

    def test_a_broken_one_is_refused_with_a_reason(self) -> None:
        from lid_db_manager import install
        broken = self.root / "broken"
        shutil.copytree(REFERENCE / "tommygun", broken)
        (broken / "TexturePack" / "Texture2D_0.tfc").unlink()
        with self.assertRaises(install.InstallError) as caught:
            install.inspect(broken)
        self.assertIn("Texture2D_0.tfc", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
