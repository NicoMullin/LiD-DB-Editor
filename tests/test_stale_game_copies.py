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


if __name__ == "__main__":
    unittest.main()
