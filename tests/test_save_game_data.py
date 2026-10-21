"""The save editor works from the game's data as the save's own mods make it.

What matters: a save played with a mod gets that mod's data in its editor
(here Bank Limit's bigger banks) even when the game has other mods applied
right now, with the mod's settings and switched-off parts as the save had
them; a save whose mods are the applied ones uses the game's own masters.db;
the copy is built once and reused; the game's own database is never touched;
and a mod the save used that is not installed is named, not silently dropped;
a save with no mods noted gets the stock data, never whatever is applied now.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from test_savelibrary import STEAM, save_bytes

from lid_db_manager import savegame
from lid_db_manager.manager import Manager
from lid_db_manager.paths import AppPaths
from lid_db_manager.sqlutil import sha256_file

PROJECT = Path(__file__).resolve().parent.parent
VANILLA_DB = PROJECT / "LiD Vanilla DB" / "5.0.4.2" / "masters.db"
BANK_LIMIT = PROJECT / "mods" / "bank-limit"


@unittest.skipUnless(VANILLA_DB.is_file() and BANK_LIMIT.is_dir(), "needs the vanilla database and Bank Limit")
class TheSavesOwnGameData(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.paths = AppPaths(root / "app").ensure()
        shutil.copytree(BANK_LIMIT, self.paths.mods_dir / BANK_LIMIT.name)
        self.game = root / "game"
        db = self.game / "BrgGame" / "Content" / "masters.db"
        db.parent.mkdir(parents=True)
        shutil.copy2(VANILLA_DB, db)
        self.db = db
        folder = self.game / "Savedata" / STEAM
        folder.mkdir(parents=True)
        (folder / savegame.SAVE_NAME).write_bytes(save_bytes(kc=1000))
        self.manager = Manager(self.paths)
        self.manager.set_db_path(db)
        self.manager.set_game_root_override(self.game)
        self.manager.rescan()
        self.assertTrue(self.manager.save_mod_list().ok)      # nothing applied: the game is stock
        self.main = self.manager.add_live_save(STEAM, "Main")
        other = root / "o.sav"
        other.write_bytes(save_bytes(kc=5))
        self.modded = self.manager.import_save(other, STEAM, "Played with Bank Limit")

    def remember(self, entry, mods: dict) -> None:
        self.manager.library.remember_mods(entry.id, mods)

    def bank_cap(self, entry) -> int:
        data = self.manager.save_game_data(entry.id)
        limits = self.manager.save_limits(data.db_path)
        return limits.cap(limits.bank, 99)

    def test_a_save_played_with_a_mod_gets_its_data(self) -> None:
        stock = sha256_file(self.db)
        self.remember(self.modded, {"enabled_mods": ["bank-limit"]})
        data = self.manager.save_game_data(self.modded.id)
        self.assertEqual((data.source, data.mods), ("built", ["bank-limit"]))
        self.assertNotEqual(data.db_path, self.db)
        self.assertEqual(self.bank_cap(self.modded), 25_600_000)          # x10, its default
        self.assertEqual(sha256_file(self.db), stock)                      # the game's own is untouched
        self.assertIn("this save's 1 mod(s)", data.describe())

    def test_with_the_settings_and_parts_it_was_played_with(self) -> None:
        self.remember(self.modded, {"enabled_mods": ["bank-limit"],
                                    "mod_settings": {"bank-limit": {"multiplier": 3}},
                                    "disabled_patches": {"bank-limit": ["sp"]}})
        limits = self.manager.save_limits(self.manager.save_game_data(self.modded.id).db_path)
        self.assertEqual(limits.cap(limits.bank, 99), 7_680_000)
        self.assertEqual(limits.cap(limits.tank, 99), 2_560_000)             # that part was off

    def test_a_save_whose_mods_are_the_applied_ones_uses_the_games_database(self) -> None:
        data = self.manager.save_game_data(self.main.id)                    # kept with nothing applied
        self.assertEqual((data.source, data.db_path), ("applied", self.db))
        self.manager.set_enabled("bank-limit", True)
        self.assertTrue(self.manager.save_mod_list().ok)                     # now the game has Bank Limit
        self.remember(self.modded, {"enabled_mods": []})                     # but this save had no mods
        data = self.manager.save_game_data(self.modded.id)
        self.assertEqual(data.source, "built")
        self.assertEqual(self.bank_cap(self.modded), 2_560_000)             # stock, as it was played

    def test_built_once_and_reused(self) -> None:
        self.remember(self.modded, {"enabled_mods": ["bank-limit"]})
        first = self.manager.save_game_data(self.modded.id).db_path
        made = first.stat().st_mtime_ns
        again = self.manager.save_game_data(self.modded.id).db_path
        self.assertEqual((again, again.stat().st_mtime_ns), (first, made))

    def test_unknown_mods_and_mods_not_installed(self) -> None:
        self.remember(self.modded, {})
        unknown = self.manager.save_game_data(self.modded.id)          # stock: what every game holds
        self.assertEqual((unknown.source, unknown.db_path), ("stock", self.manager.vanilla_path))
        self.remember(self.modded, {"enabled_mods": ["bank-limit", "Gone Mod"]})
        data = self.manager.save_game_data(self.modded.id)
        self.assertEqual(data.missing, ["Gone Mod"])
        self.assertIn("Not installed here, so left out: Gone Mod", data.describe())


if __name__ == "__main__":
    unittest.main()
