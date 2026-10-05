"""Keeping the game's own masters.db as the clean copy for a new build."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from fixtures import build_db, write_mod

from lid_db_manager import vanilla_capture, vanilla_library
from lid_db_manager.manager import Manager
from lid_db_manager.paths import AppPaths

COOKED = "BrgGame/CookedPCConsole"
NEW_BUILD = "9.9.9.9.0 - 2.00"


def set_version(path: Path, title: str, steam: str = "") -> None:
    con = sqlite3.connect(str(path))
    try:
        con.execute(
            'CREATE TABLE IF NOT EXISTS master_const_str ("id" CHARACTER(64) NOT NULL, '
            '"value" TEXT NOT NULL, PRIMARY KEY ("id"))'
        )
        con.execute("DELETE FROM master_const_str WHERE id LIKE 'TITLE_VERSION%'")
        con.execute("INSERT INTO master_const_str VALUES ('TITLE_VERSION', ?)", (title,))
        if steam:
            con.execute("INSERT INTO master_const_str VALUES ('TITLE_VERSION_STEAM', ?)", (steam,))
        con.commit()
    finally:
        con.close()


class CaptureTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.paths = AppPaths(self.root / "app").ensure()

        # A game folder: a database, and packages carrying Steam's timestamp.
        self.game = self.root / "game"
        (self.game / "BrgGame" / "Content").mkdir(parents=True)
        self.cooked = self.game / COOKED
        self.cooked.mkdir(parents=True)
        self.db = build_db(self.game / "BrgGame" / "Content" / "masters.db")
        set_version(self.db, NEW_BUILD, "9.9.9.9.0")
        self.written_at = time.time() - 3600
        for number in range(4):
            package = self.cooked / f"Package_{number}.upk"
            package.write_bytes(b"x")
            os.utime(package, (self.written_at, self.written_at))
        os.utime(self.db, (self.written_at, self.written_at))

        # A clean copy of the previous build, to compare the shape against.
        previous = self.paths.vanilla_dir / "9.9.9.8"
        previous.mkdir(parents=True)
        self.previous = build_db(previous / "masters.db")
        set_version(self.previous, "9.9.9.8.0 - 1.99", "9.9.9.8.0")

        self.manager = Manager(self.paths)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _capture(self):
        """Point the manager at the game's database without capturing yet."""
        self.manager.state.db_path = str(self.db)
        return self.manager.capture_clean_copy()

    def _touch_db_later(self) -> None:
        later = self.written_at + 4 * 3600
        os.utime(self.db, (later, later))

    def test_it_keeps_the_file_after_a_game_update(self) -> None:
        result = self._capture()
        self.assertTrue(result.ok, result.reason)
        self.assertEqual(result.kept.parent.name, "9.9.9.9")
        # And it is now the clean copy this database is measured against.
        build = vanilla_library.best_for(self.db, self.paths.root)
        self.assertIsNotNone(build)
        self.assertEqual(build.version, NEW_BUILD)

    def test_nothing_happens_when_the_build_is_already_known(self) -> None:
        self.assertTrue(self._capture().ok)
        again = self.manager.capture_clean_copy()
        self.assertFalse(again.ok)
        self.assertIn("already", again.reason)

    def test_choosing_the_database_captures_on_its_own(self) -> None:
        """The whole point: the player does nothing at all."""
        self.manager.set_db_path(self.db)
        self.assertTrue((self.paths.vanilla_dir / "9.9.9.9" / "masters.db").is_file())

    def test_a_file_changed_after_the_update_is_not_kept(self) -> None:
        self._touch_db_later()
        result = self._capture()
        self.assertFalse(result.ok)
        self.assertIn("same write", result.reason)
        self.assertFalse(any(self.paths.vanilla_dir.glob("9.9.9.9*/masters.db")))

    def test_a_database_carrying_the_managers_note_is_not_kept(self) -> None:
        from lid_db_manager import db_record

        con = sqlite3.connect(str(self.db))
        try:
            db_record.write(con, [db_record.RecordedMod(mod_id="some-mod", name="Some mod")])
            con.commit()
        finally:
            con.close()
        os.utime(self.db, (self.written_at, self.written_at))  # even with the timestamp
        result = self._capture()
        self.assertFalse(result.ok)
        self.assertIn("lists 1 mod", result.reason)

    def test_a_database_with_mod_shaped_changes_is_not_kept(self) -> None:
        con = sqlite3.connect(str(self.db))
        try:
            con.execute("UPDATE master_skill SET buy_money = 1")
            con.commit()
        finally:
            con.close()
        os.utime(self.db, (self.written_at, self.written_at))
        result = self._capture()
        self.assertFalse(result.ok)
        self.assertIn("master_skill", result.reason)
        self.assertEqual(result.unexpected_tables, ["master_skill"])

    def test_patch_shaped_changes_are_allowed(self) -> None:
        con = sqlite3.connect(str(self.db))
        try:
            con.execute("UPDATE master_text SET txt = 'new wording' WHERE rowid = 1")
            con.commit()
        finally:
            con.close()
        os.utime(self.db, (self.written_at, self.written_at))
        self.assertTrue(self._capture().ok)

    def test_the_timestamp_check_ignores_files_a_mod_replaced(self) -> None:
        # One package replaced long after the update must not move the answer.
        odd = self.cooked / "Package_1.upk"
        later = self.written_at + 50 * 3600
        os.utime(odd, (later, later))
        self.assertEqual(vanilla_capture.steam_wrote_at(self.game), float(round(self.written_at)))

    def test_a_folder_name_is_made_from_the_build(self) -> None:
        self.assertEqual(vanilla_capture._folder_name("5.0.4.2.0"), "5.0.4.2.0")
        self.assertEqual(vanilla_capture._folder_name("5.0.4.1.0 - 1.89"), "5.0.4.1.0_-_1.89")
        self.assertEqual(vanilla_capture._folder_name("  "), "unknown")


class LastKnownModListTests(unittest.TestCase):
    """A game update wipes the note; the backups still have it."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.paths = AppPaths(self.root / "app").ensure()
        self.db = build_db(self.root / "game" / "masters.db")
        write_mod(
            self.paths.mods_dir,
            "some-mod",
            {"patches": [{"type": "update_set", "table": "master_skill",
                          "set": {"buy_money": 1}, "where": "buy_money > 1"}]},
        )
        self.manager = Manager(self.paths)
        self.manager.set_db_path(self.db)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_it_reads_the_note_from_the_database_itself(self) -> None:
        self.manager.set_enabled("some-mod", True)
        self.assertTrue(self.manager.save_mod_list().ok)
        record, where = self.manager.last_known_record()
        self.assertIsNotNone(record)
        self.assertEqual([m.mod_id for m in record.mods], ["some-mod"])
        self.assertEqual(where, self.db.name)

    def test_it_falls_back_to_the_backups_when_the_game_replaced_the_file(self) -> None:
        self.manager.set_enabled("some-mod", True)
        self.assertTrue(self.manager.save_mod_list().ok)
        self.assertTrue(self.manager.save_mod_list().ok)  # now a backup carries the note
        build_db(self.db)  # the game update: a fresh database, note and all gone

        record, where = self.manager.last_known_record()
        self.assertIsNotNone(record, "the mod list was lost with the update")
        self.assertEqual([m.mod_id for m in record.mods], ["some-mod"])
        self.assertNotEqual(where, self.db.name)

    def test_nothing_saved_yet_is_not_an_error(self) -> None:
        record, where = self.manager.last_known_record()
        self.assertIsNone(record)
        self.assertEqual(where, "")



try:
    from PySide6.QtWidgets import QApplication, QMessageBox

    HAVE_QT = True
except ImportError:  # pragma: no cover - same guard as the other UI tests
    HAVE_QT = False


@unittest.skipUnless(HAVE_QT, "PySide6 is not installed")
class AskingAboutANewBuildTests(unittest.TestCase):
    """When the checks cannot vouch for the file, the player is asked."""

    app = None

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        from lid_db_manager.ui.main_window import MainWindow

        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.paths = AppPaths(self.root / "app").ensure()
        self.db = build_db(self.root / "game" / "masters.db")
        set_version(self.db, NEW_BUILD, "9.9.9.9.0")
        self.manager = Manager(self.paths)
        self.manager.set_db_path(self.db)  # no game folder, so nothing is captured
        self.window = MainWindow(self.manager)

    def tearDown(self) -> None:
        self.window.close()
        self._tmp.cleanup()

    def _offer(self, button: str):
        """Run the question, pressing the button labelled ``button``.

        Returns how many times it was asked (0 or 1) and what it returned.
        """
        from unittest import mock

        from lid_db_manager.ui import main_window as window_module

        asked = []

        def press(box):
            asked.append(box.text())
            for candidate in box.buttons():
                if candidate.text() == button:
                    candidate.click()
                    return
            raise AssertionError(f"no {button!r} button")

        with mock.patch.object(window_module.QMessageBox, "exec", press), \
                mock.patch.object(window_module.QMessageBox, "information"), \
                mock.patch.object(window_module.QMessageBox, "warning"):
            started = self.window._offer_clean_copy()
        return len(asked), started

    def test_mine_is_clean_keeps_it_as_the_clean_copy_for_that_build(self) -> None:
        self.assertIsNone(self.manager.chosen_vanilla())
        self.assertEqual(self._offer("Mine is clean"), (1, False))
        build = self.manager.chosen_vanilla()
        self.assertIsNotNone(build, "it was not kept")
        self.assertEqual(build.version, NEW_BUILD)
        self.assertEqual(build.label, "9.9.9.9")

    def test_not_now_keeps_nothing_and_is_not_asked_again(self) -> None:
        self.assertEqual(self._offer("Not now"), (1, False))
        self.assertIsNone(self.manager.chosen_vanilla())
        self.assertIn(NEW_BUILD, self.manager.state.clean_copy_asked)
        self.assertEqual(self._offer("Not now"), (0, False))

    def test_download_it_fetches_that_build_then_scans_for_mods(self) -> None:
        wanted = []
        self.window.download_clean_copy = lambda version, then=None: wanted.append((version, then))
        self.assertEqual(self._offer("Download it"), (1, True))
        self.assertEqual(wanted, [(NEW_BUILD, self.window._offer_adoption)])

    def test_a_build_already_covered_is_never_asked_about(self) -> None:
        folder = self.paths.vanilla_dir / "9.9.9.9"
        folder.mkdir(parents=True)
        clean = build_db(folder / "masters.db")
        set_version(clean, NEW_BUILD, "9.9.9.9.0")
        self.assertEqual(self._offer("Not now"), (0, False))



@unittest.skipUnless(HAVE_QT, "PySide6 is not installed")
class NewPlayerChoosesTests(unittest.TestCase):
    """A new player says up front where the clean copy comes from."""

    app = None
    setUpClass = AskingAboutANewBuildTests.setUpClass
    setUp = AskingAboutANewBuildTests.setUp
    tearDown = AskingAboutANewBuildTests.tearDown

    def _answer(self, *presses: str):
        """Run the welcome question, pressing each button in turn; returns the
        titles of the boxes shown."""
        from unittest import mock

        from lid_db_manager.ui import main_window as window_module

        shown, queue = [], list(presses)

        def press(box):
            shown.append(box.windowTitle())
            wanted = queue.pop(0)
            for candidate in box.buttons():
                if candidate.text() == wanted:
                    candidate.click()
                    return
            raise AssertionError(f"no {wanted!r} button")

        with mock.patch.object(window_module.QMessageBox, "exec", press):
            self.assertFalse(self.window._offer_carry_over())
        return shown

    def test_new_asks_where_the_clean_copy_comes_from(self) -> None:
        shown = self._answer("I'm new", "Download from GitHub")
        self.assertEqual(len(shown), 2)
        self.assertEqual(self.window._clean_source, "download")
        self._answer("I'm new", "Use my game files")
        self.assertEqual(self.window._clean_source, "mine")

    def test_download_fetches_that_build_then_scans_for_mods(self) -> None:
        wanted = []
        self.window.download_clean_copy = lambda version, then=None: wanted.append((version, then))
        self.assertTrue(self.window._clean_copy_from("download"))
        self.assertEqual(wanted, [(NEW_BUILD, self.window._offer_adoption)])
        self.assertIn(NEW_BUILD, self.manager.state.clean_copy_asked, "not asked again")

    def test_use_mine_keeps_the_game_file_as_the_clean_copy(self) -> None:
        self.assertTrue(self.window._clean_copy_from("mine"))
        build = self.manager.chosen_vanilla()
        self.assertIsNotNone(build)
        self.assertEqual(build.version, NEW_BUILD)
        self.assertTrue(self.manager.state.adoption_offered,
                        "a file compared with itself has no mods to find")

    def test_nothing_happens_when_a_clean_copy_is_already_here(self) -> None:
        folder = self.paths.vanilla_dir / "9.9.9.9"
        folder.mkdir(parents=True)
        set_version(build_db(folder / "masters.db"), NEW_BUILD, "9.9.9.9.0")
        self.window.download_clean_copy = lambda *a, **k: self.fail("downloaded")
        self.assertFalse(self.window._clean_copy_from("download"))
        self.assertFalse(self.window._clean_copy_from("mine"))


if __name__ == "__main__":
    unittest.main()
