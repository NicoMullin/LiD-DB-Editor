"""The Hash Patcher only reads the executable when it has to.

It lists every file the game checks, which on a real game is 8,038 rows behind a
45 MB executable. Asking the manager for the status parses that whole file, and
the panel used to do it on every tick and every keystroke - about 26 ms each,
plus 19 ms walking all 8,000 rows to recount the ticks. Typing a seven-letter
search cost nearly a second.

These are not timing tests - a timing test on a shared machine is a flake
waiting to happen. They count the reads and the walks instead, which is the thing
that was wrong.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fixtures import build_db
from test_exe_checksums import an_executable

from lid_db_manager import exe_check_off, validator
from lid_db_manager.manager import Manager
from lid_db_manager.paths import AppPaths

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtWidgets import QApplication, QMessageBox

    HAVE_QT = True
except ImportError:  # pragma: no cover - depends on the environment
    HAVE_QT = False

A = "a" * 40
B = "b" * 40
C = "c" * 40


class Counted:
    """Wraps a method and counts the calls, leaving the behaviour alone."""

    def __init__(self, target, name: str):
        self.target = target
        self.name = name
        self.real = getattr(target, name)
        self.calls = 0

    def __enter__(self):
        def counting(*args, **kwargs):
            self.calls += 1
            return self.real(*args, **kwargs)

        setattr(self.target, self.name, counting)
        return self

    def __exit__(self, *exc):
        setattr(self.target, self.name, self.real)
        return False


class ReadingTheListOnce(unittest.TestCase):
    """The executable is parsed once per question, not once per mod."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.paths = AppPaths(self.root).ensure()
        self.game = self.root / "game"
        exe = self.game / "Binaries" / "Win64" / "BrgGame-Steam.exe"
        exe.parent.mkdir(parents=True)
        exe.write_bytes(an_executable([
            ("BrgGame.upk", A),
            ("UI_ButtonGuide_STM_SF.upk", B),
            ("BrgUIDebugEditParams.ini", C),
        ]))
        self.db = build_db(self.game / "BrgGame" / "masters.db")
        self.manager = Manager(self.paths)
        self.manager.set_db_path(self.db)
        self.manager.set_game_root_override(self.game)
        self._write_mods()
        self.manager.rescan()
        for mod_id in ("needs-a", "needs-b", "needs-c"):
            self.manager.state.set_enabled(mod_id, True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write_mods(self) -> None:
        from fixtures import write_mod

        for mod_id, needs in (("needs-a", "BrgGame.upk"),
                              ("needs-b", "UI_ButtonGuide_STM_SF.upk"),
                              ("needs-c", "BrgUIDebugEditParams.ini")):
            write_mod(self.paths.mods_dir, mod_id, {
                "requires_check_off": [needs],
                "patches": [{"type": "raw_sql", "sql": "SELECT 1;"}],
            })

    def test_listed_names_reads_what_the_game_checks(self):
        listed = validator.listed_names(self.game)
        self.assertEqual(listed, {"brggame.upk", "ui_buttonguide_stm_sf.upk",
                                  "brguidebugeditparams.ini"})

    def test_listed_names_says_nothing_rather_than_guessing(self):
        self.assertIsNone(validator.listed_names(None))
        self.assertIsNone(validator.listed_names(self.root / "not-the-game"))

    def test_three_blocked_mods_read_the_executable_once_between_them(self):
        with Counted(validator, "listed_names") as counted:
            blocked = self.manager.blocked_packages()
        self.assertEqual(counted.calls, 1, "one read for the whole question")
        self.assertEqual(sorted(blocked), ["BrgGame.upk", "BrgUIDebugEditParams.ini",
                                           "UI_ButtonGuide_STM_SF.upk"])

    def test_still_checked_uses_a_list_it_is_handed(self):
        mod = self.manager.scan.get("needs-a")
        with Counted(validator, "listed_names") as counted:
            out = validator.still_checked(mod, self.game, {"brggame.upk"})
        self.assertEqual(out, ["BrgGame.upk"])
        self.assertEqual(counted.calls, 0, "it was given the list already")

    def test_still_checked_reads_for_itself_when_it_is_not_handed_one(self):
        mod = self.manager.scan.get("needs-a")
        self.assertEqual(validator.still_checked(mod, self.game), ["BrgGame.upk"])

    def test_a_handed_list_that_does_not_name_the_file_blocks_nothing(self):
        mod = self.manager.scan.get("needs-a")
        self.assertEqual(validator.still_checked(mod, self.game, set()), [])

    def test_validating_several_mods_reads_the_executable_once(self):
        mods = [self.manager.configured(m) for m in self.manager.scan.mods]
        self.assertGreaterEqual(len(mods), 3)
        with Counted(validator, "listed_names") as counted:
            report = validator.validate(self.db, mods, game_root=self.game)
        self.assertEqual(counted.calls, 1)
        self.assertEqual(len(report.results), len(mods))

    def test_the_blocked_mods_are_still_blocked_with_a_reason(self):
        report = validator.validate(
            self.db, [self.manager.configured(m) for m in self.manager.scan.mods],
            game_root=self.game,
        )
        failed = {r.mod_id for r in report.failed}
        self.assertEqual(failed, {"needs-a", "needs-b", "needs-c"})
        for result in report.failed:
            self.assertTrue(any("still checks" in e for e in result.errors))


@unittest.skipUnless(HAVE_QT, "PySide6 is not installed")
class ThePanelDoesNotReReadIt(unittest.TestCase):
    app = None

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.paths = AppPaths(self.root).ensure()
        self.game = self.root / "game"
        exe = self.game / "Binaries" / "Win64" / "BrgGame-Steam.exe"
        exe.parent.mkdir(parents=True)
        exe.write_bytes(an_executable([("BrgGame.upk", A),
                                       ("UI_ButtonGuide_STM_SF.upk", B)]))
        self.db = build_db(self.game / "BrgGame" / "masters.db")
        self.manager = Manager(self.paths)
        self.manager.set_db_path(self.db)
        self.manager.set_game_root_override(self.game)
        self.dialog = None

    def tearDown(self) -> None:
        if self.dialog is not None:
            self.dialog.close()
            self.dialog.setParent(None)
            self.dialog.deleteLater()
            self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self.dialog = None
        self._tmp.cleanup()

    def _open(self):
        from lid_db_manager.ui.file_check_dialog import FileCheckDialog

        self.dialog = FileCheckDialog(self.manager)
        return self.dialog

    def _item(self, name: str):
        from lid_db_manager.ui.file_check_dialog import NAME_ROLE

        for row in range(self.dialog.list.count()):
            item = self.dialog.list.item(row)
            if item.data(NAME_ROLE) == name:
                return item
        raise AssertionError(f"no row for {name}")

    # -- the reads ----------------------------------------------------------

    def test_opening_it_reads_the_executable_at_most_twice(self):
        # Once for the status, once for what the enabled mods are blocked on.
        with Counted(self.manager, "file_check_status") as status:
            self._open()
        self.assertLessEqual(status.calls, 2)

    def test_ticking_a_box_reads_nothing(self):
        dialog = self._open()
        with Counted(self.manager, "file_check_status") as status:
            self._item("brggame.upk").setCheckState(Qt.CheckState.Checked)
        self.assertEqual(status.calls, 0, "a tick should not re-read 45 MB")
        self.assertEqual(dialog._ticked(), ["brggame.upk"])

    def test_filtering_reads_nothing(self):
        dialog = self._open()
        with Counted(self.manager, "file_check_status") as status:
            dialog.search.setText("button")
            dialog._apply_now()
        self.assertEqual(status.calls, 0)

    def test_refreshing_reads_it_again_because_the_file_may_have_changed(self):
        dialog = self._open()
        with Counted(self.manager, "file_check_status") as status:
            dialog.refresh()
        self.assertGreaterEqual(status.calls, 1)

    # -- the ticked set -----------------------------------------------------

    def test_the_count_on_the_button_follows_the_boxes(self):
        dialog = self._open()
        self.assertNotIn("(", dialog.off_button.text())
        self._item("brggame.upk").setCheckState(Qt.CheckState.Checked)
        self.assertIn("(1)", dialog.off_button.text())
        self._item("ui_buttonguide_stm_sf.upk").setCheckState(Qt.CheckState.Checked)
        self.assertIn("(2)", dialog.off_button.text())
        self._item("brggame.upk").setCheckState(Qt.CheckState.Unchecked)
        self.assertIn("(1)", dialog.off_button.text())

    def test_unticking_the_last_one_turns_the_button_off(self):
        dialog = self._open()
        item = self._item("brggame.upk")
        item.setCheckState(Qt.CheckState.Checked)
        self.assertTrue(dialog.off_button.isEnabled())
        item.setCheckState(Qt.CheckState.Unchecked)
        self.assertFalse(dialog.off_button.isEnabled())
        self.assertEqual(dialog._ticked(), [])

    def test_a_tick_survives_the_search_hiding_its_row(self):
        dialog = self._open()
        self._item("brggame.upk").setCheckState(Qt.CheckState.Checked)
        dialog.search.setText("button")
        dialog._apply_now()
        self.assertTrue(self._item("brggame.upk").isHidden())
        self.assertEqual(dialog._ticked(), ["brggame.upk"])

    def test_a_rebuild_rebuilds_the_ticked_set_with_the_rows(self):
        # The list is cleared and refilled, so a set left over from before would
        # name rows that no longer exist.
        dialog = self._open()
        self._item("brggame.upk").setCheckState(Qt.CheckState.Checked)
        self.assertEqual(dialog._ticked(), ["brggame.upk"])
        dialog.refresh()
        self.assertEqual(dialog._ticked(), [])

    def test_a_file_a_mod_needs_comes_back_ticked_after_a_rebuild(self):
        from fixtures import write_mod

        write_mod(self.paths.mods_dir, "needs-it", {
            "requires_check_off": ["BrgGame.upk"],
            "patches": [{"type": "raw_sql", "sql": "SELECT 1;"}],
        })
        self.manager.rescan()
        self.manager.state.set_enabled("needs-it", True)
        dialog = self._open()
        self.assertEqual(dialog._ticked(), ["brggame.upk"])
        dialog.refresh()
        self.assertEqual(dialog._ticked(), ["brggame.upk"])

    def test_the_rows_are_told_they_are_all_the_same_height(self):
        # Without this the view measures every row to size its scrollbar, which
        # on 8,000 rows costs 120 ms on every build and every cleared search.
        dialog = self._open()
        self.assertTrue(dialog.list.uniformItemSizes())

    def test_switching_everything_off_does_not_read_the_file_again(self):
        dialog = self._open()
        with Counted(self.manager, "file_check_status") as status:
            # The confirmation box is what would block, so stop before it.
            with patch.object(QMessageBox, "question",
                              staticmethod(lambda *a, **k: QMessageBox.StandardButton.No)):
                dialog._switch_off_everything()
        self.assertEqual(status.calls, 0, "refresh() already read it")

    # -- the typing pause ---------------------------------------------------

    def test_typing_does_not_filter_until_it_stops(self):
        dialog = self._open()
        dialog.search.setText("button")
        self.assertFalse(self._item("brggame.upk").isHidden(),
                         "filtering on every keystroke is what was slow")
        self.assertTrue(dialog._typing.isActive())

    def test_the_pause_firing_does_the_filtering(self):
        dialog = self._open()
        dialog.search.setText("button")
        dialog._typing.timeout.emit()  # what the timer does when it runs out
        self.assertTrue(self._item("brggame.upk").isHidden())

    def test_enter_does_not_wait_for_the_pause(self):
        dialog = self._open()
        dialog.search.setText("button")
        dialog.search.returnPressed.emit()
        self.assertTrue(self._item("brggame.upk").isHidden())
        self.assertFalse(dialog._typing.isActive(), "the pending pass was dropped")

    def test_the_shown_line_still_counts_correctly(self):
        dialog = self._open()
        dialog.search.setText("button")
        dialog._apply_now()
        self.assertIn("Showing 1 of 2", dialog.shown.text())
        dialog.search.setText("")
        dialog._apply_now()
        self.assertIn("2 file(s)", dialog.shown.text())


class SwitchingOffDoesNotReParseOncePerFile(unittest.TestCase):
    """Proving the edit must not cost one full parse of the list per name.

    Switching the check off for everything on a real game meant 8,038 names,
    each one asking "is this still in the list?" - and each of those re-read and
    re-parsed the whole 400 KB list. It took 62 seconds with the window frozen.
    The answer for every name is in one parse.
    """

    def setUp(self) -> None:
        self.names = [f"package_{n:04d}.upk" for n in range(300)]
        self.raw = an_executable([(name, f"{n:040x}") for n, name in
                                  enumerate(self.names)])

    def test_the_fake_list_is_the_shape_the_test_needs(self):
        self.assertEqual(len(exe_check_off.checked_names(self.raw)), 300)

    def test_switching_off_300_files_parses_the_list_a_few_times_not_300(self):
        with Counted(exe_check_off, "read_entries") as counted:
            changed, done = exe_check_off.switch_off(self.raw, self.names)
        self.assertEqual(len(done), 300)
        self.assertLessEqual(counted.calls, 2, "one parse answers for every name")

    def test_switching_back_on_parses_the_list_a_few_times_not_300(self):
        changed, _ = exe_check_off.switch_off(self.raw, self.names)
        with Counted(exe_check_off, "read_entries") as counted:
            back, done = exe_check_off.switch_on(changed)
        self.assertEqual(len(done), 300)
        self.assertLessEqual(counted.calls, 2)

    def test_the_proof_step_still_walks_the_list_only_once_per_write(self):
        # listings() is the walk _rewritten uses to check the length held. Twice
        # is the changed copy and the original; per name it would be 600.
        with Counted(exe_check_off, "listings") as counted:
            exe_check_off.switch_off(self.raw, self.names)
        self.assertLessEqual(counted.calls, 4)

    # -- and it still does the same thing --------------------------------

    def test_every_name_really_did_leave_the_list(self):
        changed, done = exe_check_off.switch_off(self.raw, self.names)
        self.assertEqual(exe_check_off.checked_names(changed), [])
        for name in done:
            self.assertFalse(exe_check_off.is_checked(changed, name))

    def test_a_round_trip_gives_the_bytes_back_exactly(self):
        changed, _ = exe_check_off.switch_off(self.raw, self.names)
        self.assertNotEqual(changed, self.raw)
        back, _ = exe_check_off.switch_on(changed)
        self.assertEqual(back, self.raw)

    def test_switching_one_off_leaves_the_other_299_checked(self):
        changed, done = exe_check_off.switch_off(self.raw, ["package_0007.upk"])
        self.assertEqual(done, ["package_0007.upk"])
        self.assertEqual(len(exe_check_off.checked_names(changed)), 299)

    def test_a_name_the_list_never_held_is_not_an_error(self):
        changed, done = exe_check_off.switch_off(self.raw, ["nobody.upk"])
        self.assertEqual(done, [])
        self.assertEqual(changed, self.raw)

    def test_a_failed_proof_still_names_the_file_that_did_not_move(self):
        # The one parse has to be able to say *which* name, the way the loop did.
        with patch.object(exe_check_off, "_rewritten", lambda raw, *a: raw):
            with self.assertRaises(exe_check_off.ExeFormatError) as caught:
                exe_check_off.switch_off(self.raw, ["package_0007.upk"])
        self.assertIn("package_0007.upk", str(caught.exception))
        self.assertIn("still in the file list", str(caught.exception))

    def test_a_failed_put_back_still_names_the_file(self):
        changed, _ = exe_check_off.switch_off(self.raw, ["package_0007.upk"])
        with patch.object(exe_check_off, "_rewritten", lambda raw, *a: raw):
            with self.assertRaises(exe_check_off.ExeFormatError) as caught:
                exe_check_off.switch_on(changed)
        self.assertIn("package_0007.upk", str(caught.exception))
        self.assertIn("did not go back", str(caught.exception))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
