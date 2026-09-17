"""Game files the executable would refuse are never installed.

The game keeps a SHA-1 for most of its packages inside BrgGame-Steam.exe and
stops with an error naming any package that does not match. A game update can
change those hashes under a mod that installed fine the day before - which is
exactly what 1.88 did to 33 of the Crossover pack's icons - so every copy is
checked against the table of the executable actually in the game folder.
"""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from test_asset_file import COOKED, build_game_tree, write_asset_mod
from test_exe_checksums import an_executable

from lid_db_manager import asset_runner
from lid_db_manager.mod_loader import scan_mods


def sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


STOCK_OLD = b"icon as 1.87 shipped it"
STOCK_NEW = b"icon as 1.88 ships it"


class ChecksumGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.game = build_game_tree(self.root)
        self.cooked = self.game / "BrgGame" / "CookedPCConsole"
        self.backups = self.root / "backups"
        self.backups.mkdir()
        self.mods = self.root / "mods"
        self.mods.mkdir()
        self.log_lines: list[str] = []

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _exe(self, **hashes: str) -> None:
        exe = self.game / "Binaries" / "Win64" / "BrgGame-Steam.exe"
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.write_bytes(an_executable(list(hashes.items())))

    def _apply(self, *mod_ids):
        by_id = scan_mods(self.mods).by_id
        return asset_runner.apply_asset_patches(
            [by_id[m] for m in mod_ids], self.game, self.backups
        )

    def test_a_file_the_game_expects_is_installed(self) -> None:
        write_asset_mod(self.mods, "pack", {"UI_A_SF.upk": b"pack icon"})
        self._exe(**{"UI_A_SF.upk": sha1(b"pack icon")})
        report = self._apply("pack")
        self.assertTrue(report.ok, report.error)
        self.assertEqual(report.copied, [f"{COOKED}/UI_A_SF.upk"])
        self.assertEqual(report.warnings, [])

    def test_a_file_the_game_does_not_list_is_installed(self) -> None:
        write_asset_mod(self.mods, "pack", {"NEW_SF.upk": b"brand new"})
        self._exe(**{"OTHER_SF.upk": sha1(b"x")})
        report = self._apply("pack")
        self.assertTrue(report.ok, report.error)
        self.assertEqual((self.cooked / "NEW_SF.upk").read_bytes(), b"brand new")

    def test_a_file_the_game_would_refuse_is_left_out(self) -> None:
        (self.cooked / "UI_A_SF.upk").write_bytes(STOCK_NEW)
        write_asset_mod(self.mods, "pack", {"UI_A_SF.upk": b"pack icon", "NEW_SF.upk": b"new"})
        self._exe(**{"UI_A_SF.upk": sha1(STOCK_NEW)})
        report = self._apply("pack")
        self.assertTrue(report.ok, report.error)
        self.assertEqual((self.cooked / "UI_A_SF.upk").read_bytes(), STOCK_NEW)
        self.assertEqual(report.copied, [f"{COOKED}/NEW_SF.upk"], "the rest still goes in")
        self.assertEqual(len(report.warnings), 1)
        self.assertIn("UI_A_SF.upk", report.warnings[0])
        self.assertIn("different game build", report.warnings[0])

    def test_after_an_update_the_old_file_is_taken_back_out(self) -> None:
        # Installed while the executable accepted it, and the stock icon kept.
        (self.cooked / "UI_A_SF.upk").write_bytes(STOCK_OLD)
        write_asset_mod(self.mods, "pack", {"UI_A_SF.upk": b"pack icon"})
        self._exe(**{"UI_A_SF.upk": sha1(b"pack icon")})
        self.assertTrue(self._apply("pack").ok)
        # The update: a new executable, and a kept copy that is now wrong too.
        self._exe(**{"UI_A_SF.upk": sha1(STOCK_OLD)})
        report = self._apply("pack")
        self.assertTrue(report.ok, report.error)
        self.assertEqual((self.cooked / "UI_A_SF.upk").read_bytes(), STOCK_OLD)
        self.assertEqual(asset_runner.list_asset_backups(self.backups), [])

    def test_when_steam_put_the_right_file_back_it_is_kept_and_the_old_copy_forgotten(self) -> None:
        (self.cooked / "UI_A_SF.upk").write_bytes(STOCK_OLD)
        write_asset_mod(self.mods, "pack", {"UI_A_SF.upk": b"pack icon"})
        self._exe(**{"UI_A_SF.upk": sha1(b"pack icon")})
        self.assertTrue(self._apply("pack").ok)
        # The update replaces the icon and the executable together.
        (self.cooked / "UI_A_SF.upk").write_bytes(STOCK_NEW)
        self._exe(**{"UI_A_SF.upk": sha1(STOCK_NEW)})
        report = self._apply("pack")
        self.assertTrue(report.ok, report.error)
        self.assertEqual((self.cooked / "UI_A_SF.upk").read_bytes(), STOCK_NEW)
        self.assertEqual(asset_runner.list_asset_backups(self.backups), [],
                         "the 1.87 icon would come back if the pack were switched off")

    def test_a_wrong_file_with_nothing_right_to_put_back_is_reported(self) -> None:
        (self.cooked / "UI_A_SF.upk").write_bytes(STOCK_OLD)
        write_asset_mod(self.mods, "pack", {"UI_A_SF.upk": b"pack icon"})
        self._exe(**{"UI_A_SF.upk": sha1(b"pack icon")})
        self.assertTrue(self._apply("pack").ok)
        self._exe(**{"UI_A_SF.upk": sha1(STOCK_NEW)})
        report = self._apply("pack")
        self.assertTrue(report.ok, report.error)
        self.assertTrue(any("Verify integrity" in w for w in report.warnings), report.warnings)

    def test_a_file_a_new_mod_version_dropped_is_put_right_too(self) -> None:
        # Version 1 installed the icon; version 2 no longer ships it.
        (self.cooked / "UI_A_SF.upk").write_bytes(STOCK_OLD)
        write_asset_mod(self.mods, "pack", {"UI_A_SF.upk": b"pack icon", "NEW_SF.upk": b"new"})
        self._exe(**{"UI_A_SF.upk": sha1(b"pack icon")})
        self.assertTrue(self._apply("pack").ok)
        (self.mods / "pack" / "assets" / "UI_A_SF.upk").unlink()
        self._exe(**{"UI_A_SF.upk": sha1(STOCK_OLD)})
        report = self._apply("pack")
        self.assertTrue(report.ok, report.error)
        self.assertEqual((self.cooked / "UI_A_SF.upk").read_bytes(), STOCK_OLD)
        self.assertNotIn(f"{COOKED}/UI_A_SF.upk",
                         [entry.target for entry in asset_runner.list_asset_backups(self.backups)])

    def test_no_executable_means_nothing_is_checked(self) -> None:
        write_asset_mod(self.mods, "pack", {"UI_A_SF.upk": b"pack icon"})
        report = self._apply("pack")
        self.assertTrue(report.ok, report.error)
        self.assertEqual((self.cooked / "UI_A_SF.upk").read_bytes(), b"pack icon")


if __name__ == "__main__":
    unittest.main()
