"""The loading bar behind Save Mod List and Re-apply All."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fixtures import build_db, write_mod
from test_asset_file import build_game_tree, write_asset_mod

from lid_db_manager.manager import Manager
from lid_db_manager.paths import AppPaths
from lid_db_manager.progress import Progress

COST_MOD = {
    "patches": [
        {
            "type": "update_set",
            "table": "master_skill",
            "set": {"buy_money": 1},
            "where": "buy_money > 1",
        }
    ]
}


class Recorder:
    def __init__(self) -> None:
        self.seen: list[tuple[str, int]] = []

    def __call__(self, text: str, value: int) -> None:
        self.seen.append((text, value))

    @property
    def values(self) -> list[int]:
        return [value for _, value in self.seen]


class ProgressTests(unittest.TestCase):
    def test_a_step_fills_only_its_own_slice(self) -> None:
        seen = Recorder()
        progress = Progress(seen)
        progress.stage("Database", 20, 60)
        progress.step(1, 2)
        progress.step(2, 2)
        self.assertEqual(seen.seen, [("Database", 20), ("Database", 40), ("Database", 60)])

    def test_a_part_covers_a_share_of_the_step(self) -> None:
        seen = Recorder()
        progress = Progress(seen)
        progress.stage("Files", 40, 100)
        rebuilding = progress.part(0.0, 0.8, "Rebuilding")
        rebuilding.step(1, 2)
        copying = progress.part(0.8, 1.0, "Copying")
        copying.step(1, 1)
        self.assertEqual(seen.values, [40, 40, 64, 88, 100])
        self.assertEqual(seen.seen[-1][0], "Copying")

    def test_nobody_listening_is_fine(self) -> None:
        progress = Progress()
        progress.stage("x", 0, 100)
        progress.step(1, 0)  # no parts: ignored rather than dividing by zero
        progress.part(0, 1).step(1, 1)

    def test_a_broken_window_never_breaks_the_work(self) -> None:
        def broken(text, value):
            raise RuntimeError("window gone")
        Progress(broken).stage("x", 0, 100)


class SaveReportsProgressTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _check(self, seen: Recorder) -> None:
        self.assertTrue(seen.values, "nothing was reported")
        self.assertEqual(seen.values, sorted(seen.values), "the bar must never go backwards")
        self.assertEqual(seen.values[-1], 100)

    def test_a_database_only_save(self) -> None:
        paths = AppPaths(self.root).ensure()
        db = build_db(self.root / "game" / "masters.db")
        write_mod(paths.mods_dir, "cost", COST_MOD)
        manager = Manager(paths)
        manager.set_db_path(db)
        manager.set_enabled("cost", True)

        seen = Recorder()
        self.assertTrue(manager.save_mod_list(Progress(seen)).ok)
        self._check(seen)
        texts = [text for text, _ in seen.seen]
        self.assertIn("Backing up your database...", texts)
        self.assertTrue(any("to the database" in text for text in texts))

        again = Recorder()
        self.assertTrue(manager.reapply_all(Progress(again)).ok)
        self._check(again)

    def test_a_save_with_game_files(self) -> None:
        game = build_game_tree(self.root)
        paths = AppPaths(self.root / "app").ensure()
        write_asset_mod(paths.mods_dir, "pack", {"A_SF.upk": b"aaa", "B_SF.upk": b"bbb"})
        manager = Manager(paths)
        manager.set_db_path(game / "BrgGame" / "Content" / "masters.db")
        manager.set_enabled("pack", True)

        seen = Recorder()
        report = manager.save_mod_list(Progress(seen))
        self.assertTrue(report.ok, report.error)
        self._check(seen)
        self.assertTrue(any(text.startswith("Copying A_SF.upk") for text, _ in seen.seen))


if __name__ == "__main__":
    unittest.main()
