"""Start a new game day: the save's last game day set back one, nothing else.

A made-up save stands in for the game's - the same container, and JSON with
the two things a careless rewrite would change (the game's "\\/" and its
four-place floats) - so the tests can say the contents differ by that one
number and no other byte.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from lid_db_manager import savegame
from lid_db_manager.manager import Manager
from lid_db_manager.paths import AppPaths

DAY = 86400
TODAY = 1791331200                     # 2026-10-07, a game day's start
NOON = TODAY + 12 * 3600               # 2026-10-07 12:00 UTC: game day 2026-10-07
EARLY = TODAY + 5 * 3600               # 2026-10-07 05:00 UTC: still game day 2026-10-06
REAL_SAVES = Path(os.environ.get(
    "LID_SAVEDATA", r"C:\Program Files (x86)\Steam\steamapps\common\LET IT DIE\Savedata"))


def payload(last_login: int = TODAY) -> bytes:
    text = ('{"next_update_cid":3,"user":{"name":"Fighter","modified":1791439969,'
            f'"last_login_date":{last_login},"login_count":60}},'
            '"soul":{"icon":"UI\\/Icon\\/A","ratio":1.5000,"free_continue_count":0},'
            '"login_bonus":[{"login_date":1791331200,"is_vip":1}]}')
    return text.encode("utf-8")


def a_save(folder: Path, last_login: int = TODAY) -> Path:
    path = folder / "Savedata" / "76561190000000000" / savegame.SAVE_NAME
    path.parent.mkdir(parents=True)
    path.write_bytes(savegame.write_container(2, payload(last_login)))
    return path


class TheContainer(unittest.TestCase):
    def test_it_reads_back_what_it_wrote(self) -> None:
        data = payload() * 50
        self.assertEqual(savegame.read_container(savegame.write_container(2, data)), (2, data))

    def test_anything_else_is_refused(self) -> None:
        good = savegame.write_container(2, payload())
        for bad in (b"", b"PNG\0" + good[4:], good[:-30], good[:20] + b"\xff" * 40 + good[60:]):
            with self.subTest(bad=bad[:8]), self.assertRaises(savegame.SaveError):
                savegame.read_container(bad)

    def test_the_game_day_turns_at_ten_utc(self) -> None:
        self.assertEqual(savegame.game_day(NOON), TODAY)
        self.assertEqual(savegame.game_day(EARLY), TODAY - DAY)
        self.assertEqual(savegame.game_day(TODAY + 10 * 3600), TODAY)


class StartingANewDay(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.backups = self.root / "backups"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_only_that_number_changes_and_the_save_is_backed_up(self) -> None:
        save = a_save(self.root)
        before = save.read_bytes()
        result = savegame.start_new_game_day(save, self.backups, now=NOON)
        self.assertEqual((result.before, result.after, result.changed), (TODAY, TODAY - DAY, True))
        _, after = savegame.read_container(save.read_bytes())
        self.assertEqual(after, payload(TODAY - DAY))          # byte for byte, "\/" and 1.5000 kept
        self.assertEqual(result.backup.read_bytes(), before)
        self.assertEqual(result.backup.parent, self.backups / "saves" / "76561190000000000")
        self.assertFalse(save.with_name(save.name + ".new").exists())

    def test_nothing_is_written_when_the_next_start_is_a_new_day_already(self) -> None:
        save = a_save(self.root, TODAY - DAY)
        before = save.read_bytes()
        result = savegame.start_new_game_day(save, self.backups, now=NOON)
        self.assertEqual((result.changed, result.backup), (False, None))
        self.assertEqual(save.read_bytes(), before)
        self.assertFalse(self.backups.exists())

    def test_early_in_the_utc_day_it_is_still_the_day_before(self) -> None:
        save = a_save(self.root, TODAY - DAY)
        result = savegame.start_new_game_day(save, self.backups, now=EARLY)
        self.assertEqual(result.after, TODAY - 2 * DAY)

    def test_twice_in_a_row_backs_up_each_time(self) -> None:
        save = a_save(self.root)
        first = savegame.start_new_game_day(save, self.backups, now=NOON)
        # the game plays, and its next start writes today's day again
        version, data = savegame.read_container(save.read_bytes())
        save.write_bytes(savegame.write_container(version, data.replace(
            b'"last_login_date":%d' % (TODAY - DAY), b'"last_login_date":%d' % TODAY)))
        second = savegame.start_new_game_day(save, self.backups, now=NOON + 60)
        self.assertNotEqual(first.backup, second.backup)
        self.assertEqual(len(list((self.backups / "saves").rglob("*.sav"))), 2)

    def test_a_save_it_cannot_be_sure_of_is_left_alone(self) -> None:
        for broken in (payload().replace(b'"last_login_date"', b'"last_logout_date"'),
                       payload().replace(b'"login_count":60', b'"login_count":60,"x":{"last_login_date":5}'),
                       payload().replace(b'"last_login_date":1791331200', b'"last_login_date":"soon"')):
            with self.subTest(broken=broken[40:90]):
                folder = Path(tempfile.mkdtemp(dir=self.root))
                save = a_save(folder)
                save.write_bytes(savegame.write_container(2, broken))
                before = save.read_bytes()
                with self.assertRaises(savegame.SaveError):
                    savegame.start_new_game_day(save, folder / "backups", now=NOON)
                self.assertEqual(save.read_bytes(), before)

    def test_finding_the_saves(self) -> None:
        save = a_save(self.root)
        self.assertEqual(savegame.find_saves(self.root), [save])
        self.assertEqual(savegame.find_saves(self.root / "nowhere"), [])
        self.assertEqual(savegame.find_saves(None), [])


class ThroughTheManager(unittest.TestCase):
    def test_it_uses_the_game_folder_and_the_managers_backups(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            game = root / "LET IT DIE"
            (game / "BrgGame" / "CookedPCConsole").mkdir(parents=True)
            save = a_save(game, savegame.game_day(time.time()))
            paths = AppPaths(root / "manager").ensure()
            manager = Manager(paths)
            manager.set_game_root_override(game)
            self.assertEqual(manager.saves(), [save])
            result = manager.start_new_game_day(save)
            self.assertTrue(result.changed)
            self.assertTrue(str(result.backup).startswith(str(paths.backups_dir)))


try:
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtWidgets import QApplication

    HAVE_QT = True
except ImportError:  # pragma: no cover - the laptop's Python has no PySide6
    HAVE_QT = False


@unittest.skipUnless(HAVE_QT, "PySide6 is not installed")
class FromTheToolsMenu(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        from lid_db_manager.ui import main_window as mw

        self.mw = mw
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        game = root / "LET IT DIE"
        (game / "BrgGame" / "CookedPCConsole").mkdir(parents=True)
        self.save = a_save(game, savegame.game_day(time.time()))
        self.manager = Manager(AppPaths(root / "manager").ensure())
        self.manager.set_game_root_override(game)
        self._real_first_run = mw.MainWindow._first_run_checks
        mw.MainWindow._first_run_checks = lambda self, **kwargs: None
        self._real_box = mw.QMessageBox
        self.messages = messages = []
        self.answer = QMessageBox.StandardButton.Yes
        test = self

        class RecordingBox:
            StandardButton = QMessageBox.StandardButton
            Icon = QMessageBox.Icon

            @staticmethod
            def question(parent, title, text, *args, **kwargs):
                messages.append(("question", text))
                return test.answer

            @staticmethod
            def warning(parent, title, text, *args, **kwargs):
                messages.append(("warning", text))

            @staticmethod
            def information(parent, title, text, *args, **kwargs):
                messages.append(("information", text))

        mw.QMessageBox = RecordingBox
        self.window = mw.MainWindow(self.manager)

    def tearDown(self) -> None:
        from test_ui import destroy

        self.mw.QMessageBox = self._real_box
        self.mw.MainWindow._first_run_checks = self._real_first_run
        destroy(self.window)
        for _ in range(4):
            QCoreApplication.processEvents()
        self._tmp.cleanup()

    def test_it_is_on_the_tools_menu(self) -> None:
        texts = [a.text() for menu in self.window.menuBar().actions() for a in menu.menu().actions()]
        self.assertIn("Start a new game day...", texts)

    def test_it_asks_backs_up_and_says_where(self) -> None:
        before = self.save.read_bytes()
        self.window.start_new_game_day()
        kinds = [kind for kind, _ in self.messages]
        self.assertEqual(kinds, ["question", "information"])
        self.assertIn("backed up first", self.messages[0][1])
        self.assertNotEqual(self.save.read_bytes(), before)
        backups = list((self.manager.paths.backups_dir / "saves").rglob("*.sav"))
        self.assertEqual([b.read_bytes() for b in backups], [before])
        self.assertIn(str(backups[0]), self.messages[1][1])

    def test_no_is_no(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        self.answer = QMessageBox.StandardButton.No
        before = self.save.read_bytes()
        self.window.start_new_game_day()
        self.assertEqual(self.save.read_bytes(), before)
        self.assertFalse((self.manager.paths.backups_dir / "saves").exists())


@unittest.skipUnless(any(REAL_SAVES.glob(f"*/{savegame.SAVE_NAME}")) if REAL_SAVES.is_dir() else False,
                     "needs the game's Savedata folder (set LID_SAVEDATA)")
class OnACopyOfARealSave(unittest.TestCase):
    """Only ever a copy: the player's own save is never opened for writing."""

    def test_a_real_save_changes_by_that_one_number(self) -> None:
        real = next(REAL_SAVES.glob(f"*/{savegame.SAVE_NAME}"))
        with tempfile.TemporaryDirectory() as tmp:
            save = Path(tmp) / "Savedata" / real.parent.name / savegame.SAVE_NAME
            save.parent.mkdir(parents=True)
            shutil.copy2(real, save)
            version, before = savegame.read_container(save.read_bytes())
            marker = json.loads(before)["user"]["last_login_date"]
            result = savegame.start_new_game_day(save, Path(tmp) / "backups", now=marker + DAY + 12 * 3600)
            self.assertEqual(result.after, marker)       # tomorrow's start: back to today's day
            result = savegame.start_new_game_day(save, Path(tmp) / "backups", now=marker + 12 * 3600)
            self.assertEqual(result.after, marker - DAY)
            again_version, after = savegame.read_container(save.read_bytes())
            self.assertEqual(again_version, version)
            self.assertEqual(after, before.replace(b'"last_login_date":%d' % marker,
                                                   b'"last_login_date":%d' % (marker - DAY)))


if __name__ == "__main__":
    unittest.main()
