"""The Saves tab, driven against a made-up game folder (see test_savelibrary.py).

What matters at the window: the save in the game can be kept with a
nickname, the list and details show what is in each save, saving the mod
list notes it for the save in the game, and switching puts the other save in
and hands its mod list over to be applied.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fixtures import build_db, write_mod
from test_savelibrary import STEAM, save_bytes

from lid_db_manager import savegame
from lid_db_manager.manager import Manager
from lid_db_manager.paths import AppPaths
from lid_db_manager.savesummary import read_summary

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog, QMessageBox

    HAVE_QT = True
except ImportError:  # pragma: no cover - depends on the environment
    HAVE_QT = False

SKILL_PATCH = [{"type": "update_set", "table": "master_skill",
                "set": {"buy_money": 5}, "where": "id = 'SKL_EXPUP_01'"}]
YES = QMessageBox.StandardButton.Yes if HAVE_QT else None


@unittest.skipUnless(HAVE_QT, "needs PySide6")
class TheSavesTab(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        from lid_db_manager.ui.saves_tab import SavesTab

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.paths = AppPaths(root / "app").ensure()
        self.game = root / "game"
        self.folder = self.game / "Savedata" / STEAM
        self.folder.mkdir(parents=True)
        (self.folder / savegame.SAVE_NAME).write_bytes(save_bytes(kc=1000))
        write_mod(self.paths.mods_dir, "Cheap EXP", {"patches": SKILL_PATCH})
        self.manager = Manager(self.paths)
        self.manager.set_db_path(build_db(self.game / "BrgGame" / "Content" / "masters.db"))
        self.manager.set_game_root_override(self.game)
        self.manager.rescan()
        self.tab = SavesTab(self.manager)
        self.addCleanup(self.tab.deleteLater)
        self.chosen: list = []
        self.tab.modListChosen.connect(self.chosen.append)
        self.tab.refresh()

    def keep(self, nickname: str) -> None:
        with mock.patch.object(QInputDialog, "getText", return_value=(nickname, True)):
            self.tab.keep_live()

    def test_the_game_save_is_offered_kept_and_shown(self) -> None:
        self.assertIn("not in the library yet", self.tab.live_label.text())
        self.assertTrue(self.tab.keep_button.isEnabled())
        self.keep("Main run")
        self.assertEqual(self.tab.list.count(), 30)                 # every slot, empty ones too
        self.assertIn("Slot  1", self.tab.list.item(0).text())
        self.assertIn("Main run", self.tab.list.item(0).text())
        self.assertIn("2 h 01 min", self.tab.list.item(0).text())
        self.assertIn("- empty -", self.tab.list.item(29).text())
        self.assertEqual(self.tab.slots_label.text(), "Save slots: 1 of 30 used")
        self.assertEqual(self.tab.fighters.rowCount(), 1)
        self.assertEqual(self.tab.fighters.item(0, 0).text(), "Leila")
        self.assertFalse(self.tab.switch_button.isEnabled())       # it is the one in the game
        self.assertFalse(self.tab.keep_button.isEnabled())

    def test_saving_the_mod_list_notes_it_for_the_save_in_the_game(self) -> None:
        self.keep("Main run")
        self.manager.set_enabled("Cheap EXP", True)
        self.assertTrue(self.manager.save_mod_list().ok)
        (entry,) = self.manager.library.entries()
        self.assertEqual(entry.mods["enabled_mods"], ["Cheap EXP"])

    def test_switching_puts_the_other_save_in_and_offers_its_mods(self) -> None:
        self.keep("Main run")
        other = Path(self._tmp.name) / "other.sav"
        other.write_bytes(save_bytes(kc=5))
        from lid_db_manager.ui.save_mods_dialog import SaveModsDialog

        with mock.patch.object(QInputDialog, "getText", return_value=("No-mods run", True)), \
                mock.patch.object(QFileDialog, "getOpenFileName", return_value=(str(other), "")), \
                mock.patch.object(SaveModsDialog, "exec", return_value=0):      # "Not now"
            self.tab.import_file()
        entry = next(e for e in self.manager.library.entries() if e.nickname == "No-mods run")
        self.manager.library.remember_mods(entry.id, {"enabled_mods": ["Cheap EXP", "Gone Mod"]})
        self.tab.refresh()
        self.assertIn("(not installed)", self.tab.mods_label.text())
        with mock.patch.object(QMessageBox, "question", return_value=YES):
            self.tab.switch()
        self.assertEqual(read_summary(self.folder / savegame.SAVE_NAME).kill_coins, 5)
        self.assertEqual(self.chosen, [["Gone Mod"]])
        self.assertEqual(self.manager.state.enabled_mods, ["Cheap EXP"])

    def test_a_refused_switch_says_so_and_changes_nothing(self) -> None:
        other = Path(self._tmp.name) / "other.sav"
        other.write_bytes(save_bytes(kc=5))
        self.manager.import_save(other, STEAM, "Other")
        self.tab.refresh()
        with mock.patch.object(QMessageBox, "question", return_value=YES), \
                mock.patch.object(QMessageBox, "warning") as warned:
            self.tab.switch()
        self.assertIn("not in the library yet", warned.call_args.args[2])
        self.assertEqual(read_summary(self.folder / savegame.SAVE_NAME).kill_coins, 1000)

    def test_a_new_save_after_the_tutorial(self) -> None:
        from lid_db_manager import newsave

        self.keep("Main run")
        label = newsave.START_LABELS[newsave.AFTER_TUTORIAL]
        with mock.patch.object(QInputDialog, "getItem", return_value=(label, True)), \
                mock.patch.object(QInputDialog, "getText", side_effect=[("Rookie", True), ("Second run", True)]), \
                mock.patch.object(QMessageBox, "question", return_value=YES), \
                mock.patch.object(QMessageBox, "information"):
            self.tab.new_save()
        s = read_summary(self.folder / savegame.SAVE_NAME)
        self.assertEqual((s.player, s.highest_floor), ("Rookie", 1))
        self.assertEqual(sorted(e.nickname for e in self.manager.library.entries()), ["Main run", "Second run"])
        self.assertEqual(read_summary(next(e for e in self.manager.library.entries()
                                           if e.nickname == "Main run").save).kill_coins, 1000)

    def test_steam_cloud_is_on_for_a_new_save_and_switched_by_its_button(self) -> None:
        from lid_db_manager.savelibrary import CLOUD_PREFIX

        self.assertFalse(self.tab.cloud_button.isEnabled())          # nothing picked yet
        self.keep("Main run")
        (entry,) = self.manager.library.entries()
        copy = self.folder / (CLOUD_PREFIX + entry.id)
        self.assertTrue(entry.cloud and copy.is_file())               # on by default
        self.assertTrue(self.tab.cloud_button.isChecked())
        self.assertEqual(self.tab.cloud_button.text(), "Steam Cloud backup: on")
        self.assertIn("Steam Cloud:", self.tab.cloud_meter.text())
        self.tab.cloud_button.click()
        self.assertFalse(self.manager.library.get(entry.id).cloud or copy.exists())
        self.assertEqual(self.tab.cloud_button.text(), "Steam Cloud backup: off")
        self.tab.cloud_button.click()
        self.assertTrue(copy.is_file())
        self.assertFalse(self.tab.found_box.isVisibleTo(self.tab))
        # the library is lost (a new PC): the tab offers the cloud's copy back
        import shutil
        shutil.rmtree(entry.folder)
        self.tab.refresh()
        self.assertTrue(self.tab.found_box.isVisibleTo(self.tab))
        self.assertIn("Main run", self.tab.cloud_found.text())
        self.tab.restore_cloud()
        self.assertEqual([e.nickname for e in self.manager.library.entries()], ["Main run"])
        self.assertFalse(self.tab.found_box.isVisibleTo(self.tab))

    def test_a_save_that_does_not_fit_the_cloud_is_kept_all_the_same(self) -> None:
        from lid_db_manager import savelibrary

        with mock.patch.object(savelibrary, "CLOUD_BUDGET", 10), \
                mock.patch.object(QMessageBox, "information") as told:
            self.keep("Main run")
        (entry,) = self.manager.library.entries()
        self.assertFalse(entry.cloud)
        self.assertIn("not backed up to Steam Cloud", told.call_args.args[2])
        self.assertEqual(self.tab.cloud_button.text(), "Steam Cloud backup: off")

    def test_rename(self) -> None:
        self.keep("Main run")
        self.tab.nickname.setText("Speedrun")
        self.tab.rename()
        self.assertEqual(self.manager.library.entries()[0].nickname, "Speedrun")


if __name__ == "__main__":
    unittest.main()
