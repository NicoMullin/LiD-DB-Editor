"""Bringing the save library up to date when the game closes, and when the manager starts.

What matters: the game closing is noticed however it was started, the save is
only read once the game has finished writing it, a save played since it was
kept is copied into its slot, a different save in its place never is, and
nothing happens while the game runs.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fixtures import build_db
from test_savelibrary import STEAM, document, save_bytes

from lid_db_manager import asset_runner, savegame
from lid_db_manager.manager import Manager
from lid_db_manager.paths import AppPaths
from lid_db_manager.savesummary import read_summary

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication

    HAVE_QT = True
except ImportError:  # pragma: no cover - depends on the environment
    HAVE_QT = False


def another_save() -> bytes:
    doc = document(kc=31337)
    doc["user"]["created"] = 1600000000          # a different save altogether
    return savegame.write_container(2, json.dumps(doc).encode())


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.game = root / "game"
        self.folder = self.game / "Savedata" / STEAM
        self.folder.mkdir(parents=True)
        self.save = self.folder / savegame.SAVE_NAME
        self.save.write_bytes(save_bytes(kc=1000))
        self.manager = Manager(AppPaths(root / "app").ensure())
        self.manager.set_db_path(build_db(self.game / "BrgGame" / "Content" / "masters.db"))
        self.manager.set_game_root_override(self.game)
        self.entry = self.manager.add_live_save(STEAM, "Main")

    def kc_kept(self) -> int:
        return read_summary(self.manager.library.get(self.entry.id).save).kill_coins


class UpdatingFromTheGame(Base):
    def test_a_save_played_since_is_brought_up_to_date(self) -> None:
        self.save.write_bytes(save_bytes(kc=4242))
        result = self.manager.update_saves_from_game()
        self.assertEqual([e.id for e in result.updated], [self.entry.id])
        self.assertEqual(self.kc_kept(), 4242)
        self.assertEqual(self.manager.update_saves_from_game().updated, [])     # nothing new: nothing done

    def test_never_while_the_game_runs(self) -> None:
        self.save.write_bytes(save_bytes(kc=4242))
        with mock.patch.object(asset_runner, "game_running", return_value=True):
            self.assertEqual(self.manager.update_saves_from_game().updated, [])
        self.assertEqual(self.kc_kept(), 1000)

    def test_a_different_save_in_its_place_is_never_written_over_it(self) -> None:
        self.save.write_bytes(another_save())
        result = self.manager.update_saves_from_game()
        self.assertEqual(result.updated, [])
        (foreign,) = result.foreign
        self.assertEqual(foreign.entry.id, self.entry.id)
        self.assertEqual(self.kc_kept(), 1000)                                  # the slot is untouched
        # switching away refuses too, and says why
        other = Path(self._tmp.name) / "o.sav"
        other.write_bytes(save_bytes(kc=5))
        target = self.manager.import_save(other, STEAM, "Other")
        with self.assertRaises(Exception) as caught:
            self.manager.switch_save(target.id)
        self.assertIn("not \"Main\" any more", str(caught.exception))
        self.assertEqual(read_summary(self.save).kill_coins, 31337)
        # keeping it gives it a slot of its own; "Main" stays as it was
        kept = self.manager.add_live_save(STEAM, "Found in the game")
        self.assertEqual(read_summary(kept.save).kill_coins, 31337)
        self.assertEqual(self.kc_kept(), 1000)
        self.assertTrue(self.manager.library.live_for(self.game, STEAM).known)


class ARebuiltProgram(Base):
    """The program's folder made afresh (a rebuild, a new PC): the game's save folder still has it all."""

    def fresh_install(self) -> Manager:
        manager = Manager(AppPaths(Path(self._tmp.name) / "rebuilt").ensure())
        manager.set_db_path(self.manager.db_path)
        manager.set_game_root_override(self.game)
        return manager

    def test_the_library_comes_back_from_the_game_folder(self) -> None:
        # A save of its own, as a second slot is in real use. Two copies of one
        # save would leave which slot the game's save belongs to down to which
        # was kept in a later second.
        doc = document(kc=5)
        doc["user"]["created"] = 1600000000
        other = Path(self._tmp.name) / "o.sav"
        other.write_bytes(savegame.write_container(2, json.dumps(doc).encode()))
        second = self.manager.import_save(other, STEAM, "Challenge")
        self.manager.library.move_to_slot(second.id, 9)
        (self.folder / "brggame.bak").write_bytes(save_bytes(kc=900))
        self.save.write_bytes(save_bytes(kc=2000))                 # played after, with no manager open

        rebuilt = self.fresh_install()
        self.assertEqual(rebuilt.library.entries(), [])
        result = rebuilt.update_saves_from_game()
        self.assertEqual(sorted((e.slot, e.nickname) for e in result.restored), [(1, "Main"), (9, "Challenge")])
        self.assertEqual([e.nickname for e in result.relinked], ["Main"])
        self.assertEqual([e.nickname for e in result.updated], ["Main"])
        live = rebuilt.library.live_for(self.game, STEAM)
        self.assertTrue(live.known and live.in_sync and live.entry.nickname == "Main")
        challenge = next(e for e in rebuilt.library.entries() if e.nickname == "Challenge")
        self.assertEqual(read_summary(challenge.save).kill_coins, 5)
        self.assertFalse(challenge.bak.exists())                   # the game's .bak is not this save's
        self.assertEqual(rebuilt.update_saves_from_game().restored, [])

    def test_other_saves_in_the_folder_are_offered_slots(self) -> None:
        stray = self.folder / "brggame.sav.before-delevel-2026-10-05"
        stray.write_bytes(save_bytes(kc=77))
        (self.folder / "brggame.sav.notes.txt").write_text("not a save", encoding="utf-8")
        library = self.manager.library
        self.assertEqual(library.stray_saves(self.game), [stray])
        (kept,) = self.manager.import_strays([stray])
        self.assertEqual((kept.nickname, kept.steam_id, kept.slot), ("before-delevel-2026-10-05", STEAM, 2))
        self.assertFalse(kept.cloud)                               # it is in the cloud already
        self.assertEqual(library.stray_saves(self.game), [])
        self.assertTrue(stray.is_file())                           # left where it was


@unittest.skipUnless(HAVE_QT, "needs PySide6")
class TheWatch(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        from lid_db_manager.ui.game_watch import GameWatch

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.file = Path(self._tmp.name) / "brggame.sav"
        self.file.write_bytes(b"one")
        self.running = False
        self.watch = GameWatch(lambda: self.running, lambda: [self.file])
        self.events: list[str] = []
        self.watch.gameStarted.connect(lambda: self.events.append("started"))
        self.watch.gameClosed.connect(lambda: self.events.append("closed"))
        self.watch.start()
        self.watch.timer.stop()               # driven by hand here

    def test_started_then_closed_once_the_save_has_settled(self) -> None:
        self.running = True
        self.watch.look()
        self.assertEqual(self.events, ["started"])
        self.running = False
        self.watch.look()                      # gone - but the save may still be being written
        self.assertEqual(self.events, ["started"])
        self.file.write_bytes(b"two, still writing")
        self.watch.look()
        self.assertEqual(self.events, ["started"])
        self.watch.look()                      # unchanged since the last look
        self.assertEqual(self.events, ["started", "closed"])
        self.watch.look()
        self.assertEqual(self.events, ["started", "closed"])    # once

    def test_a_game_already_running_when_the_manager_starts(self) -> None:
        from lid_db_manager.ui.game_watch import GameWatch

        self.running = True
        watch = GameWatch(lambda: self.running, lambda: [self.file])
        watch.start()
        watch.timer.stop()
        events = []
        watch.gameClosed.connect(lambda: events.append("closed"))
        self.running = False
        watch.look()
        watch.look()
        self.assertEqual(events, ["closed"])


@unittest.skipUnless(HAVE_QT, "needs PySide6")
class TheWindow(Base):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_closing_the_game_updates_the_library_and_says_so(self) -> None:
        from lid_db_manager.ui.main_window import MainWindow

        with mock.patch.object(MainWindow, "_first_run_checks"):
            window = MainWindow(self.manager)
        self.addCleanup(window.deleteLater)
        window.game_watch.timer.stop()
        self.save.write_bytes(save_bytes(kc=777))
        window._on_game_closed()
        self.assertEqual(self.kc_kept(), 777)
        self.assertIn("Main brought up to date", window.statusBar().currentMessage())


if __name__ == "__main__":
    unittest.main()
