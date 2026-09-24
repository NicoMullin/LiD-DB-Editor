"""The manager switching the game's own file check off, and the panel for it.

The manager has always been able to say that a mod is blocked - the executable
still carries a hash for a file the mod replaces, so the game would refuse it at
startup. These cover being able to do something about it without leaving for
another program.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from fixtures import build_db, write_mod
from test_exe_checksums import an_executable

from lid_db_manager import exe_check_off as OFF
from lid_db_manager.manager import Manager
from lid_db_manager.paths import AppPaths
from lid_db_manager.state import Settings

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication

    HAVE_QT = True
except ImportError:  # pragma: no cover - depends on the environment
    HAVE_QT = False

A = "a" * 40
B = "b" * 40

A_MOD = {
    "requires_check_off": ["UI_ButtonGuide_STM_SF.upk"],
    "patches": [
        {
            "type": "update_set",
            "table": "master_skill",
            "set": {"buy_money": 1},
            "where": "buy_money > 1",
        }
    ],
}


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.paths = AppPaths(self.root).ensure()
        self.game = self.root / "game"
        self.exe = self.game / "Binaries" / "Win64" / "BrgGame-Steam.exe"
        self.exe.parent.mkdir(parents=True)
        self.exe.write_bytes(
            an_executable([("BrgGame.upk", A), ("UI_ButtonGuide_STM_SF.upk", B)])
        )
        self.stock = self.exe.read_bytes()
        self.db = build_db(self.game / "BrgGame" / "masters.db")
        self.manager = Manager(self.paths)
        self.manager.set_db_path(self.db)
        self.manager.set_game_root_override(self.game)

    def tearDown(self) -> None:
        self._tmp.cleanup()


class ReadingWhatTheGameChecks(Base):
    def test_a_stock_executable_checks_everything(self) -> None:
        status = self.manager.file_check_status()
        self.assertTrue(status.readable)
        self.assertEqual(status.checked, ["brggame.upk", "ui_buttonguide_stm_sf.upk"])
        self.assertEqual(status.switched_off, [])
        self.assertFalse(status.all_off)

    def test_no_game_folder_says_so_rather_than_guessing(self) -> None:
        self.manager.set_game_root_override(None)
        self.manager.set_db_path(self.root / "loose" / "masters.db")
        status = self.manager.file_check_status()
        self.assertFalse(status.readable)
        self.assertTrue(status.reason)
        self.assertEqual(status.checked, [])

    def test_a_file_that_is_not_an_executable_says_so(self) -> None:
        self.exe.write_bytes(b"not a PE at all")
        status = self.manager.file_check_status()
        self.assertFalse(status.readable)
        self.assertIn("could not be read", status.reason)

    def test_the_files_enabled_mods_are_blocked_on(self) -> None:
        write_mod(self.paths.mods_dir, "reskin", A_MOD)
        self.manager.rescan()
        self.assertEqual(self.manager.blocked_packages(), [])  # not enabled yet
        self.manager.set_enabled("reskin", True)
        self.assertEqual(
            self.manager.blocked_packages(), ["UI_ButtonGuide_STM_SF.upk"]
        )


class SwitchingItOff(Base):
    def test_one_file_goes_and_the_others_stay(self) -> None:
        done = self.manager.switch_file_check_off(["UI_ButtonGuide_STM_SF.upk"])
        self.assertEqual(done, ["ui_buttonguide_stm_sf.upk"])
        status = self.manager.file_check_status()
        self.assertEqual(status.checked, ["brggame.upk"])
        self.assertEqual(status.switched_off, ["ui_buttonguide_stm_sf.upx"])

    def test_the_executable_keeps_its_length_and_its_code(self) -> None:
        before = self.exe.read_bytes()
        self.manager.switch_file_check_off(["BrgGame.upk"])
        after = self.exe.read_bytes()
        self.assertEqual(len(after), len(before))
        self.assertEqual(sum(1 for a, b in zip(before, after) if a != b), 1)

    def test_a_copy_of_the_stock_executable_is_kept_first(self) -> None:
        self.manager.switch_file_check_off(["BrgGame.upk"])
        kept = [entry for entry in self.manager.asset_backups()
                if entry.target.endswith("BrgGame-Steam.exe")]
        self.assertEqual(len(kept), 1)
        self.assertEqual(Path(kept[0].backup_path).read_bytes(), self.stock)

    def test_the_kept_copy_is_never_taken_from_a_changed_executable(self) -> None:
        """The trap: back up second and a switched-off file becomes 'stock'."""
        changed, _ = OFF.switch_off(self.stock, ["BrgGame.upk"])
        self.exe.write_bytes(changed)
        self.manager.switch_file_check_off(["UI_ButtonGuide_STM_SF.upk"])
        self.assertEqual(
            [e for e in self.manager.asset_backups()
             if e.target.endswith("BrgGame-Steam.exe")],
            [],
        )

    def test_a_name_the_game_never_listed_changes_nothing(self) -> None:
        before = self.exe.read_bytes()
        self.assertEqual(self.manager.switch_file_check_off(["nope.upk"]), [])
        self.assertEqual(self.exe.read_bytes(), before)

    def test_it_is_written_down(self) -> None:
        self.manager.switch_file_check_off(["BrgGame.upk"])
        self.assertTrue(
            any("file check off" in line.message for line in self.manager.log.lines),
            [line.message for line in self.manager.log.lines],
        )


class SwitchingItBackOn(Base):
    def test_the_executable_comes_back_byte_for_byte(self) -> None:
        self.manager.switch_file_check_off(["BrgGame.upk"])
        self.assertNotEqual(self.exe.read_bytes(), self.stock)
        done = self.manager.switch_file_check_on()
        self.assertEqual(done, ["brggame.upk"])
        self.assertEqual(self.exe.read_bytes(), self.stock)

    def test_it_needs_no_kept_copy_to_do_it(self) -> None:
        """What each name was is read off the name, so a lost or wrong backup
        cannot put the wrong executable back - there is nothing to put back."""
        self.manager.switch_file_check_off(["BrgGame.upk"])
        shutil.rmtree(self.paths.backups_dir)
        self.manager.switch_file_check_on()
        self.assertEqual(self.exe.read_bytes(), self.stock)

    def test_everything_off_and_everything_back(self) -> None:
        checked = self.manager.file_check_status().checked
        self.manager.switch_file_check_off(checked)
        self.assertTrue(self.manager.file_check_status().all_off)
        self.manager.switch_file_check_on()
        self.assertEqual(self.exe.read_bytes(), self.stock)

    def test_a_mod_stops_being_blocked_once_its_file_is_off(self) -> None:
        write_mod(self.paths.mods_dir, "reskin", A_MOD)
        self.manager.rescan()
        self.manager.set_enabled("reskin", True)
        self.assertTrue(self.manager.blocked_packages())
        self.manager.switch_file_check_off(self.manager.blocked_packages())
        self.assertEqual(self.manager.blocked_packages(), [])


class AVettedExeRecipeMeetsASwitchedOffFile(unittest.TestCase):
    """The one place the two routes past the check run into each other.

    A recipe writes the correct hash for the file it installs. Switching the
    check off takes the name out of the list altogether - so the recipe has
    nothing to look up, and used to fail with "this executable carries no hash
    for ...". The replacement works perfectly well in that state, so there is
    nothing to fail about: the executable is handed back as it came.
    """

    def setUp(self) -> None:
        from lid_db_manager import vetted
        from lid_db_manager.patch import ExeChecksumPatch

        self.recipe_name, self.recipe = next(iter(vetted.known_exe_recipes().items()))
        self.patch = ExeChecksumPatch(
            {"type": "exe_checksum_entry", "recipe": self.recipe_name}, Path("."), 0
        )
        self.stock = an_executable(
            [(self.recipe.package, self.recipe.checksum_before), ("Other.upk", A)]
        )

    def test_on_a_stock_executable_it_writes_the_hash_as_before(self) -> None:
        from lid_db_manager import exe_checksums

        changed = self.patch.transform(self.stock)
        self.assertEqual(len(changed), len(self.stock))
        entry = exe_checksums.read_entries(changed)[self.recipe.package.lower()]
        self.assertEqual(entry.sha1, self.recipe.checksum_after)
        self.assertEqual(self.patch.to_pristine(changed), self.stock)

    def test_with_that_file_switched_off_the_executable_is_left_alone(self) -> None:
        off, _ = OFF.switch_off(self.stock, [self.recipe.package])
        self.assertEqual(self.patch.transform(off), off)

    def test_the_way_back_is_left_alone_too(self) -> None:
        off, _ = OFF.switch_off(self.stock, [self.recipe.package])
        self.assertEqual(self.patch.to_pristine(off), off)

    def test_with_everything_switched_off_as_well(self) -> None:
        off, _ = OFF.switch_off(self.stock, OFF.checked_names(self.stock))
        self.assertEqual(self.patch.transform(off), off)

    def test_putting_the_check_back_lets_the_recipe_work_again(self) -> None:
        off, _ = OFF.switch_off(self.stock, [self.recipe.package])
        back, _ = OFF.switch_on(off)
        self.assertEqual(back, self.stock)
        self.assertNotEqual(self.patch.transform(back), back)


class BeingToldHowToFixIt(Base):
    """A blocked mod has to say where the switch is, not just that it is off."""

    def setUp(self) -> None:
        super().setUp()
        write_mod(self.paths.mods_dir, "reskin", A_MOD)
        self.manager.rescan()
        self.manager.set_enabled("reskin", True)

    def _error(self) -> str:
        report = self.manager.validate()
        self.assertFalse(report.ok)
        return report.for_mod("reskin").errors[0]

    def test_the_message_names_the_panel_and_the_button(self) -> None:
        message = self._error()
        self.assertIn("Tools > Hash Patcher", message)
        self.assertIn("Switch off for the ticked files", message)
        self.assertIn("UI_ButtonGuide_STM_SF.upk", message)

    def test_it_says_what_the_change_actually_is(self) -> None:
        """Somebody is being asked to let a program edit their game's .exe.
        Saying what that means is the least it can do."""
        message = self._error()
        self.assertIn("one byte per file", message)
        self.assertIn("no program code", message)
        self.assertIn("puts it back", message)

    def test_an_explicit_game_folder_is_honoured_by_validation(self) -> None:
        """It used to derive the folder from the database's own place and
        nothing else - so somebody who had pointed at their game by hand was
        never told their mod was blocked, and the save just failed later.
        """
        from lid_db_manager.asset_runner import game_root_for

        self.assertIsNone(game_root_for(self.db), "this layout must not derive")
        self.assertFalse(self.manager.validate().ok)

    def test_it_is_not_reported_once_the_file_is_switched_off(self) -> None:
        self.manager.switch_file_check_off(["UI_ButtonGuide_STM_SF.upk"])
        self.assertTrue(self.manager.validate().ok)


@unittest.skipUnless(HAVE_QT, "PySide6 is not installed")
class DoingItWhenTheSaveIsBlocked(Base):
    """The offer made at the moment a save is refused for this reason."""

    app = None

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        super().setUp()
        from lid_db_manager.ui.main_window import MainWindow

        write_mod(self.paths.mods_dir, "reskin", A_MOD)
        self.manager.rescan()
        self.manager.set_enabled("reskin", True)
        self.window = MainWindow(self.manager)

    def tearDown(self) -> None:
        self.window.close()
        QApplication.processEvents()
        super().tearDown()

    def test_with_the_setting_on_it_is_done_without_asking(self) -> None:
        self.assertTrue(self.manager.state.settings.auto_switch_file_check_off)
        report = self.manager.save_mod_list()
        self.assertFalse(report.ok)
        self.assertTrue(self.window._offer_file_check(report))
        self.assertEqual(self.manager.blocked_packages(), [])
        self.assertEqual(
            sum(1 for a, b in zip(self.stock, self.exe.read_bytes()) if a != b), 1
        )

    def test_the_setting_is_on_to_begin_with(self) -> None:
        """The user's decision. Without it a mod that replaces a checked file
        just refuses to apply, and the only way on is a panel somebody has to
        be told about first - for a change that is one byte per file, backed
        up, and undone from the same panel."""
        self.assertTrue(self.manager.state.settings.auto_switch_file_check_off)

    def test_it_can_still_be_turned_off(self) -> None:
        """Unticked, nothing is written until the person says so."""
        self.manager.state.settings.auto_switch_file_check_off = False
        self.assertFalse(
            Settings.from_dict({"auto_switch_file_check_off": False})
            .auto_switch_file_check_off
        )

    def test_it_is_never_done_quietly(self) -> None:
        report = self.manager.save_mod_list()
        self.window._offer_file_check(report)
        self.assertTrue(
            any("file check off" in line.message for line in self.manager.log.lines),
            [line.message for line in self.manager.log.lines],
        )
        self.assertIn("no longer checks", self.window.statusBar().currentMessage())

    def test_a_failure_that_is_not_this_one_is_left_alone(self) -> None:
        self.manager.set_enabled("reskin", False)
        write_mod(
            self.paths.mods_dir,
            "broken",
            {"patches": [{"type": "raw_sql", "sql": "UPDATE ghost SET a = 1"}]},
        )
        self.manager.rescan()
        self.manager.set_enabled("broken", True)
        report = self.manager.save_mod_list()
        self.assertFalse(report.ok)
        self.assertFalse(self.window._offer_file_check(report))
        self.assertEqual(self.exe.read_bytes(), self.stock)

    def test_the_header_carries_the_switch_for_it(self) -> None:
        box = self.window.auto_check_off_box
        self.assertTrue(box.isChecked())
        box.setChecked(False)
        self.assertFalse(self.manager.state.settings.auto_switch_file_check_off)
        box.setChecked(True)
        self.assertTrue(self.manager.state.settings.auto_switch_file_check_off)


@unittest.skipUnless(HAVE_QT, "PySide6 is not installed")
class ThePanel(Base):
    app = None

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _dialog(self):
        from lid_db_manager.ui.file_check_dialog import FileCheckDialog

        return FileCheckDialog(self.manager)

    def _rows(self, dialog) -> list[str]:
        return [dialog.list.item(i).text() for i in range(dialog.list.count())]

    def _names(self, dialog) -> list[str]:
        from lid_db_manager.ui.file_check_dialog import NAME_ROLE

        return [
            dialog.list.item(i).data(NAME_ROLE) for i in range(dialog.list.count())
        ]

    def _visible(self, dialog) -> list[str]:
        from lid_db_manager.ui.file_check_dialog import NAME_ROLE

        return [
            dialog.list.item(i).data(NAME_ROLE)
            for i in range(dialog.list.count())
            if not dialog.list.item(i).isHidden()
        ]

    def _search(self, dialog, text: str) -> None:
        """Type into the search box and let the filter run.

        Typing does not filter on every keystroke any more - each pass re-hides
        every row, and on a real game there are eight thousand of them - so it
        waits for typing to stop. Enter applies it at once, which is what this
        does rather than sleeping out the pause.
        """
        dialog.search.setText(text)
        dialog._apply_now()

    def _tick(self, dialog, name: str) -> None:
        from PySide6.QtCore import Qt

        from lid_db_manager.ui.file_check_dialog import NAME_ROLE

        for i in range(dialog.list.count()):
            item = dialog.list.item(i)
            if item.data(NAME_ROLE) == name:
                item.setCheckState(Qt.CheckState.Checked)
                return
        raise AssertionError(f"no row for {name}")

    def test_every_file_the_game_checks_is_listed(self) -> None:
        """All of them, so any can be found and switched off - not only the
        ones a mod happens to be waiting on."""
        dialog = self._dialog()
        self.assertEqual(
            self._names(dialog), ["brggame.upk", "ui_buttonguide_stm_sf.upk"]
        )
        self.assertEqual(dialog._ticked(), [])
        self.assertFalse(dialog.off_button.isEnabled())
        self.assertFalse(dialog.on_button.isEnabled())

    def test_the_search_hides_what_does_not_match(self) -> None:
        dialog = self._dialog()
        self._search(dialog, "button")
        self.assertEqual(self._visible(dialog), ["ui_buttonguide_stm_sf.upk"])
        self.assertIn("Showing 1 of 2", dialog.shown.text())
        self._search(dialog, "")
        self.assertEqual(len(self._visible(dialog)), 2)

    def test_a_search_that_matches_nothing_shows_nothing(self) -> None:
        dialog = self._dialog()
        self._search(dialog, "no such package anywhere")
        self.assertEqual(self._visible(dialog), [])
        self.assertIn("Showing 0 of 2", dialog.shown.text())

    def test_a_tick_survives_searching_away_from_it(self) -> None:
        """Filtering hides rows rather than rebuilding them, because a tick is
        a choice and rebuilding would quietly throw it away."""
        dialog = self._dialog()
        self._tick(dialog, "brggame.upk")
        self._search(dialog, "button")
        self.assertEqual(self._visible(dialog), ["ui_buttonguide_stm_sf.upk"])
        self.assertEqual(dialog._ticked(), ["brggame.upk"])

    def test_the_button_says_how_many_are_ticked(self) -> None:
        dialog = self._dialog()
        self._tick(dialog, "brggame.upk")
        self.assertIn("(1)", dialog.off_button.text())
        self._tick(dialog, "ui_buttonguide_stm_sf.upk")
        self.assertIn("(2)", dialog.off_button.text())

    def test_a_blocked_file_is_offered_ticked(self) -> None:
        write_mod(self.paths.mods_dir, "reskin", A_MOD)
        self.manager.rescan()
        self.manager.set_enabled("reskin", True)
        dialog = self._dialog()
        self.assertEqual(dialog._ticked(), ["ui_buttonguide_stm_sf.upk"])
        self.assertTrue(dialog.off_button.isEnabled())
        # ... and it is put first, so the reason for opening this is not
        # somewhere down a list of eight thousand.
        self.assertEqual(self._names(dialog)[0], "ui_buttonguide_stm_sf.upk")

    def test_switching_off_from_the_panel(self) -> None:
        write_mod(self.paths.mods_dir, "reskin", A_MOD)
        self.manager.rescan()
        self.manager.set_enabled("reskin", True)
        dialog = self._dialog()
        done, problem = dialog.apply_change(
            lambda: self.manager.switch_file_check_off(dialog._ticked())
        )
        self.assertEqual(problem, "")
        self.assertEqual(done, ["ui_buttonguide_stm_sf.upk"])
        self.assertIn("already switched off", " ".join(self._rows(dialog)))
        self.assertTrue(dialog.on_button.isEnabled())

    def test_the_quiet_wording_uses_the_theme_rather_than_qts_own_grey(self) -> None:
        """It was set to palette(mid), which the theme never fills in.

        Qt's own mid grey came out at about 2.4:1 against the light window -
        grey lettering on a grey background, which is what it looked like. The
        theme's "dim" is 5.5:1 there and 6.2:1 in the dark one.
        """
        dialog = self._dialog()
        dim = [
            label
            for label in dialog.findChildren(type(dialog.summary))
            if label.objectName() == "dim"
        ]
        self.assertTrue(dim, "the quieter labels are not themed")
        for label in dialog.findChildren(type(dialog.summary)):
            self.assertNotIn("palette(", label.styleSheet())

    def test_a_problem_is_reported_rather_than_raised(self) -> None:
        self.exe.write_bytes(b"not a PE at all")
        dialog = self._dialog()
        done, problem = dialog.apply_change(
            lambda: self.manager.switch_file_check_off(["BrgGame.upk"])
        )
        self.assertEqual(done, [])
        self.assertTrue(problem)

    def test_an_unreadable_executable_disables_every_button(self) -> None:
        self.exe.write_bytes(b"not a PE at all")
        dialog = self._dialog()
        self.assertFalse(dialog.off_button.isEnabled())
        self.assertFalse(dialog.all_button.isEnabled())
        self.assertFalse(dialog.on_button.isEnabled())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
