"""A mod that replaces a file the game still checks is refused, with a reason.

The executable keeps a SHA-1 for most of its packages. Replace one that is still
listed and the game stops at startup with an error box naming the package and
nothing else - no mention of which mod, or what to do. A mod that knows it
replaces such a file says so in ``requires_check_off``, and the manager turns
that into an explanation before anything is written.

Nothing is claimed when the executable cannot be read: refusing a mod over a
guess would be worse than the error box.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from test_asset_file import COOKED, build_game_tree, write_asset_mod
from test_exe_checksums import an_executable

from lid_db_manager import validator
from lid_db_manager.mod_loader import scan_mods

PACKAGE = "UI_ButtonGuide_STM_SF.upk"


def sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


class RequiresCheckOffTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.game = build_game_tree(self.root)
        self.mods = self.root / "mods"
        self.mods.mkdir()
        write_asset_mod(self.mods, "buttons", {PACKAGE: b"coloured buttons"})
        self.declare(["UI_ButtonGuide_STM_SF.upk"])

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def declare(self, files) -> None:
        """Say which files this mod needs the game to have stopped checking."""
        path = self.mods / "buttons" / "mod.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["requires_check_off"] = files
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def exe(self, entries) -> None:
        exe = self.game / "Binaries" / "Win64" / "BrgGame-Steam.exe"
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.write_bytes(an_executable(entries))

    def mod(self):
        return scan_mods(self.mods).by_id["buttons"]

    # -- the declaration itself ------------------------------------------

    def test_the_field_is_read_from_mod_json(self) -> None:
        self.assertEqual(["UI_ButtonGuide_STM_SF.upk"], self.mod().requires_check_off)

    def test_a_mod_that_says_nothing_is_never_blocked(self) -> None:
        self.declare([])
        self.exe([(PACKAGE, sha1(b"stock"))])
        self.assertEqual([], validator.still_checked(self.mod(), self.game))
        self.assertEqual("", validator.check_off_error(self.mod(), self.game))

    # -- with the game in front of us -------------------------------------

    def test_it_is_blocked_while_the_game_still_checks_the_file(self) -> None:
        self.exe([(PACKAGE, sha1(b"stock"))])
        self.assertEqual([PACKAGE], validator.still_checked(self.mod(), self.game))
        reason = validator.check_off_error(self.mod(), self.game)
        self.assertIn(PACKAGE, reason)
        # It has to say where the switch is, not only that it is off.
        self.assertIn("tools > hash patcher", reason.lower())
        self.assertIn("switch off for the ticked files", reason.lower())

    def test_it_is_allowed_once_the_check_is_off_for_that_file(self) -> None:
        # The name is gone from the list, which is what switching it off does.
        self.exe([("UI_ButtonGuide_STM_SF.upX", sha1(b"stock"))])
        self.assertEqual([], validator.still_checked(self.mod(), self.game))
        self.assertEqual("", validator.check_off_error(self.mod(), self.game))

    def test_only_the_named_files_matter(self) -> None:
        self.exe([("SOMETHING_ELSE_SF.upk", sha1(b"stock"))])
        self.assertEqual([], validator.still_checked(self.mod(), self.game))

    def test_the_name_is_matched_whatever_the_case(self) -> None:
        self.declare(["ui_buttonguide_stm_sf.UPK"])
        self.exe([(PACKAGE, sha1(b"stock"))])
        self.assertEqual(1, len(validator.still_checked(self.mod(), self.game)))

    def test_several_files_read_as_a_list(self) -> None:
        self.declare([PACKAGE, "OTHER_SF.upk"])
        self.exe([(PACKAGE, sha1(b"a")), ("OTHER_SF.upk", sha1(b"b"))])
        reason = validator.check_off_error(self.mod(), self.game)
        self.assertIn("those files", reason)
        self.assertIn("OTHER_SF.upk", reason)

    # -- when we cannot tell ----------------------------------------------

    def test_no_executable_means_no_claim(self) -> None:
        self.assertEqual([], validator.still_checked(self.mod(), self.game))

    def test_an_unreadable_executable_means_no_claim(self) -> None:
        exe = self.game / "Binaries" / "Win64" / "BrgGame-Steam.exe"
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.write_bytes(b"not a program")
        self.assertEqual([], validator.still_checked(self.mod(), self.game))

    def test_no_game_folder_means_no_claim(self) -> None:
        self.assertEqual([], validator.still_checked(self.mod(), None))

    # -- through validation proper ----------------------------------------

    def test_validation_fails_for_the_whole_mod(self) -> None:
        self.exe([(PACKAGE, sha1(b"stock"))])
        db = self.game / "BrgGame" / "Content" / "masters.db"
        db.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE master_const_str (id TEXT, value TEXT)")
        con.commit()
        con.close()
        report = validator.validate(db, [self.mod()])
        result = report.for_mod("buttons")
        self.assertFalse(result.ok, "a mod the game would refuse must not validate")
        self.assertTrue(any(PACKAGE in e for e in result.errors), result.errors)


class ShippedButtonsModTests(unittest.TestCase):
    """The mod that ships with the manager says what it needs."""

    def test_it_declares_the_package_and_no_longer_edits_the_executable(self) -> None:
        here = Path(__file__).resolve().parent.parent
        path = here / "mods" / "Colored PlayStation Buttons v1.4" / "mod.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual([PACKAGE], data["requires_check_off"])
        kinds = [p["type"] for p in data["patches"]]
        self.assertEqual(["asset_file"], kinds,
                         "the hash edit is gone; the file check is switched off instead")


if __name__ == "__main__":
    unittest.main()
