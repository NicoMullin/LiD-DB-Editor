"""Finding the game in whichever Steam library it was installed to."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import fixtures  # noqa: F401 - puts the project on the path

from lid_db_manager import steam_locate


def a_game(library: Path) -> Path:
    db = library / "steamapps" / "common" / "LET IT DIE" / "BrgGame" / "Content" / "masters.db"
    db.parent.mkdir(parents=True)
    db.write_bytes(b"")
    return db


class SteamLocateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.steam = self.root / "C" / "Steam"
        (self.steam / "steamapps").mkdir(parents=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _libraries_file(self, *folders: Path) -> None:
        entries = "".join(
            f'\t"{i}"\n\t{{\n\t\t"path"\t\t"{str(folder).replace(chr(92), chr(92) * 2)}"\n\t}}\n'
            for i, folder in enumerate(folders)
        )
        (self.steam / "steamapps" / "libraryfolders.vdf").write_text(
            f'"libraryfolders"\n{{\n{entries}}}\n', encoding="utf-8"
        )

    def test_a_game_in_a_library_on_another_drive_is_found(self) -> None:
        other = self.root / "E" / "SteamLibrary"
        db = a_game(other)
        self._libraries_file(self.steam, other)
        self.assertEqual(steam_locate.find_game_database([self.steam], []), db)

    def test_a_game_in_steams_own_folder_is_found(self) -> None:
        db = a_game(self.steam)
        self.assertEqual(steam_locate.find_game_database([self.steam], []), db)

    def test_a_usual_folder_name_is_tried_when_steam_is_not_found(self) -> None:
        drive = self.root / "D"
        db = a_game(drive / "SteamLibrary")
        self.assertEqual(steam_locate.find_game_database([self.root / "nowhere"], [drive]), db)

    def test_nothing_found_is_none(self) -> None:
        self.assertIsNone(steam_locate.find_game_database([self.steam], [self.root / "D"]))

    def test_the_libraries_file_is_read_with_escaped_backslashes(self) -> None:
        self._libraries_file(Path(r"E:\SteamLibrary"))
        self.assertIn(Path(r"E:\SteamLibrary"), steam_locate.library_folders(self.steam))


if __name__ == "__main__":
    unittest.main()
