"""Copies kept from before a game update are never put back over the new game.

The copy kept as a game file's way back is taken the first time a mod claims
the file. A game update then replaces the executable and some packages, and
that copy describes a build that no longer exists: putting the old executable
back over the new game, or an old icon the new executable refuses, breaks the
game. Found for real when 1.88 arrived under Colored PlayStation Buttons v1.2.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from test_asset_file import COOKED, build_game_tree, write_asset_mod
from test_exe_checksums import an_executable

from lid_db_manager import asset_runner
from lid_db_manager.mod_loader import scan_mods

EXE = "Binaries/Win64/BrgGame-Steam.exe"


def sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


class StaleCopyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.game = build_game_tree(self.root)
        self.cooked = self.game / "BrgGame" / "CookedPCConsole"
        self.backups = self.root / "backups"
        self.store = self.backups / "game_files"
        self.store.mkdir(parents=True)
        self.mods = self.root / "mods"
        self.mods.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _manifest(self, **entries) -> None:
        (self.store / "manifest.json").write_text(json.dumps(entries), encoding="utf-8")

    def _kept(self, name: str, data: bytes) -> str:
        (self.store / name).write_bytes(data)
        return name

    def _write_exe(self, entries, code: bytes) -> bytes:
        raw = an_executable(entries, code=code)
        path = self.game / EXE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return raw

    def test_an_executable_kept_from_an_older_build_is_not_put_back(self) -> None:
        old = an_executable([("UI_A_SF.upk", sha1(b"old icon"))], code=b"\x90" * 512)
        new = self._write_exe([("UI_A_SF.upk", sha1(b"new icon"))], code=b"\xcc" * 512)
        self._manifest(**{EXE: {"backup": self._kept("exe.original", old)}})
        report = asset_runner.restore_targets([EXE], self.game, self.backups)
        self.assertTrue(report.ok, report.warnings)
        self.assertEqual((self.game / EXE).read_bytes(), new, "the old build's exe was put back")
        self.assertEqual(asset_runner.list_asset_backups(self.backups), [])

    def test_an_executable_from_the_same_build_is_still_put_back(self) -> None:
        code = b"\x90" * 512
        stock = an_executable([("UI_A_SF.upk", sha1(b"icon"))], code=code)
        self._write_exe([("UI_A_SF.upk", sha1(b"modded icon"))], code=code)
        self._manifest(**{EXE: {"backup": self._kept("exe.original", stock)}})
        asset_runner.restore_targets([EXE], self.game, self.backups)
        self.assertEqual((self.game / EXE).read_bytes(), stock)

    def test_a_package_kept_from_an_older_build_is_not_put_back(self) -> None:
        self._write_exe([("UI_A_SF.upk", sha1(b"new icon"))], code=b"\xcc" * 512)
        (self.cooked / "UI_A_SF.upk").write_bytes(b"new icon")
        self._manifest(**{f"{COOKED}/UI_A_SF.upk": {"backup": self._kept("a.original", b"old icon")}})
        report = asset_runner.restore_targets([f"{COOKED}/UI_A_SF.upk"], self.game, self.backups)
        self.assertEqual((self.cooked / "UI_A_SF.upk").read_bytes(), b"new icon")
        self.assertEqual(report.warnings, [])
        self.assertEqual(asset_runner.list_asset_backups(self.backups), [])

    def test_an_old_package_copy_with_the_wrong_file_in_the_game_says_to_verify(self) -> None:
        self._write_exe([("UI_A_SF.upk", sha1(b"new icon"))], code=b"\xcc" * 512)
        (self.cooked / "UI_A_SF.upk").write_bytes(b"a mod's icon")
        self._manifest(**{f"{COOKED}/UI_A_SF.upk": {"backup": self._kept("a.original", b"old icon")}})
        report = asset_runner.restore_targets([f"{COOKED}/UI_A_SF.upk"], self.game, self.backups)
        self.assertTrue(any("Verify integrity" in w for w in report.warnings), report.warnings)
        self.assertNotEqual((self.cooked / "UI_A_SF.upk").read_bytes(), b"old icon")

    def test_applying_replaces_an_old_kept_copy_with_this_builds_file(self) -> None:
        # The mod ships an unchecked file, so it installs; the kept copy of the
        # checked icon it also claims is from the last build.
        self._write_exe([("UI_A_SF.upk", sha1(b"new icon"))], code=b"\xcc" * 512)
        (self.cooked / "UI_A_SF.upk").write_bytes(b"new icon")
        write_asset_mod(self.mods, "pack", {"UI_A_SF.upk": b"new icon"})
        self._manifest(**{f"{COOKED}/UI_A_SF.upk": {"backup": self._kept("a.original", b"old icon")}})
        mods = [scan_mods(self.mods).by_id["pack"]]
        report = asset_runner.apply_asset_patches(mods, self.game, self.backups)
        self.assertTrue(report.ok, report.error)
        self.assertEqual((self.store / "a.original").read_bytes(), b"new icon")


class AddsAMark:
    """A package patch reduced to what the rebuild asks of it."""

    def to_pristine(self, raw: bytes) -> bytes:
        return raw[:-5] if raw.endswith(b" mark") else raw

    def transform(self, raw: bytes) -> bytes:
        return raw + b" mark"


class StalePackageRebuildTests(unittest.TestCase):
    """A package a mod rebuilds is rebuilt from this build's copy.

    Found for real when 1.91 arrived: BrgGame.upk was rebuilt from the copy
    kept under 1.89, so the new executable ran the old build's scripts.
    """

    TARGET = f"{COOKED}/BrgGame.upk"
    setUp = StaleCopyTests.setUp
    tearDown = StaleCopyTests.tearDown
    _manifest = StaleCopyTests._manifest
    _kept = StaleCopyTests._kept
    _write_exe = StaleCopyTests._write_exe

    def _stage(self) -> tuple[list, dict]:
        manifest = json.loads((self.store / "manifest.json").read_text(encoding="utf-8"))
        plan, _stock = asset_runner._stage_transforms(
            {self.TARGET: (AddsAMark(), "scripts")}, self.game, self.store, manifest,
            self.root / "staging")
        return plan, manifest

    def test_an_old_builds_kept_copy_gives_way_to_this_builds_file(self) -> None:
        self._write_exe([("BrgGame.upk", sha1(b"new scripts"))], code=b"\xcc" * 512)
        (self.cooked / "BrgGame.upk").write_bytes(b"new scripts")
        self._manifest(**{self.TARGET: {"backup": self._kept("brg.original", b"old scripts")}})
        plan, manifest = self._stage()
        [(_target, staged, _dest)] = plan
        self.assertEqual(staged.read_bytes(), b"new scripts mark")
        self.assertNotIn(self.TARGET, manifest)
        self.assertFalse((self.store / "brg.original").exists())

    def test_with_no_copy_of_this_build_it_says_to_verify(self) -> None:
        self._write_exe([("BrgGame.upk", sha1(b"new scripts"))], code=b"\xcc" * 512)
        (self.cooked / "BrgGame.upk").write_bytes(b"old scripts mark")
        self._manifest(**{self.TARGET: {"backup": self._kept("brg.original", b"old scripts")}})
        with self.assertRaisesRegex(OSError, "Verify integrity"):
            self._stage()
        self.assertTrue((self.store / "brg.original").exists())

    def test_a_kept_copy_of_this_build_is_still_used(self) -> None:
        self._write_exe([("BrgGame.upk", sha1(b"scripts"))], code=b"\xcc" * 512)
        (self.cooked / "BrgGame.upk").write_bytes(b"scripts mark")
        self._manifest(**{self.TARGET: {"backup": self._kept("brg.original", b"scripts")}})
        plan, manifest = self._stage()
        self.assertEqual(plan[0][1].read_bytes(), b"scripts mark")
        self.assertIn(self.TARGET, manifest)

    def _on_disk(self) -> dict:
        return json.loads((self.store / "manifest.json").read_text(encoding="utf-8"))

    def test_a_save_that_fails_after_dropping_an_old_copy_leaves_no_dangling_record(self) -> None:
        # Found for real when 1.92 arrived: the save dropped 1.91's copy, then a
        # mod's change no longer fitted, and the record of the deleted copy stayed.
        class NoLongerFits(AddsAMark):
            def transform(self, raw: bytes) -> bytes:
                raise ValueError("those bytes are not in this package")

        self._write_exe([("BrgGame.upk", sha1(b"new scripts"))], code=b"\xcc" * 512)
        (self.cooked / "BrgGame.upk").write_bytes(b"new scripts")
        self._manifest(**{self.TARGET: {"backup": self._kept("brg.original", b"old scripts")}})
        manifest = self._on_disk()
        with self.assertRaises(OSError):
            asset_runner._stage_transforms({self.TARGET: (NoLongerFits(), "scripts")}, self.game,
                                           self.store, manifest, self.root / "staging")
        self.assertFalse((self.store / "brg.original").exists())
        self.assertNotIn(self.TARGET, self._on_disk())

    def test_a_missing_kept_copy_is_rebuilt_from_the_games_own_file(self) -> None:
        self._write_exe([("BrgGame.upk", sha1(b"scripts"))], code=b"\xcc" * 512)
        (self.cooked / "BrgGame.upk").write_bytes(b"scripts")
        self._manifest(**{self.TARGET: {"backup": "brg.original"}})     # the file is not there
        plan, manifest = self._stage()
        self.assertEqual(plan[0][1].read_bytes(), b"scripts mark")
        self.assertNotIn(self.TARGET, manifest)
        self.assertNotIn(self.TARGET, self._on_disk())

    def test_a_missing_kept_copy_with_a_changed_game_file_is_still_refused(self) -> None:
        self._write_exe([("BrgGame.upk", sha1(b"scripts"))], code=b"\xcc" * 512)
        (self.cooked / "BrgGame.upk").write_bytes(b"someone else's scripts")
        self._manifest(**{self.TARGET: {"backup": "brg.original"}})

        class PackageMod(AddsAMark):          # rebuilds one package, as the real ones do
            def transform_target(self, target: str, raw: bytes) -> bytes:
                return self.transform(raw)

        with self.assertRaisesRegex(OSError, "already been changed"):
            asset_runner._stage_transforms({self.TARGET: (PackageMod(), "scripts")}, self.game,
                                           self.store, self._on_disk(), self.root / "staging")

    def test_with_the_file_check_switched_off(self) -> None:
        # Switched off, the exe lists brggame.upy: the hash beside it still counts.
        self._write_exe([("BrgGame.upy", sha1(b"new scripts"))], code=b"\xcc" * 512)
        (self.cooked / "BrgGame.upk").write_bytes(b"new scripts")
        self._manifest(**{self.TARGET: {"backup": self._kept("brg.original", b"old scripts")}})
        plan, _manifest = self._stage()
        self.assertEqual(plan[0][1].read_bytes(), b"new scripts mark")


if __name__ == "__main__":
    unittest.main()
