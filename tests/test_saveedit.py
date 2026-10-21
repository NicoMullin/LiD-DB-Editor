"""The save editor: changing a save and writing it back the way the game does.

What matters: an untouched save writes back byte for byte (so a written save
differs only by what was edited); a value keeps the type the save stores it as;
every number stays within what the game holds, the banks' caps following their
levels; nothing is written while the game runs, and the save is backed up
first; and an edit to the save in the game lands in the game and in its slot.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fixtures import build_db
from test_savelibrary import STEAM, document, save_bytes

from lid_db_manager import savegame, savelink
from lid_db_manager.manager import Manager
from lid_db_manager.paths import AppPaths
from lid_db_manager.saveedit import (
    EditError, Limits, SaveDocument, account_bounds, encode, set_account, write_save,
)
from lid_db_manager.savesummary import read_summary

REAL_DB = Path(__file__).resolve().parent.parent / "LiD Vanilla DB" / "5.0.4.2" / "masters.db"
REAL_SAVES = Path(os.environ.get(
    "LID_SAVEDATA", r"C:\Program Files (x86)\Steam\steamapps\common\LET IT DIE\Savedata"))
LIMITS = Limits(bank={1: 100, 2: 5000, 3: 9000}, tank={1: 50, 2: 700}, bloodnium=999)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtWidgets import QApplication, QMessageBox

    HAVE_QT = True
except ImportError:  # pragma: no cover - depends on the environment
    HAVE_QT = False


def game_text() -> bytes:
    """JSON with the game's quirks: "\\/" in strings, floats to four places, a number kept as text."""
    return ('{"user":{"uid":5,"nm":"A\\/B","skey":"37564","free_medal":10,"paid_medal":2},'
            '"soul":{"uid":5,"free_money":90,"safe_level":1,"spirit":10,"spirit_tank_level":1,'
            '"bloodnium_point":"40","recycle_point":3,"pause_x":0.0000,"ratio":1.5000}}').encode()


class TheGamesJson(unittest.TestCase):
    def test_an_untouched_save_writes_back_byte_for_byte(self) -> None:
        text = game_text()
        save = SaveDocument(savegame.write_container(2, text))
        self.assertEqual(save.encoded(), text)
        self.assertFalse(save.changed)

    def test_the_real_saves_too(self) -> None:
        found = sorted(REAL_SAVES.glob(f"*/{savegame.SAVE_NAME}")) if REAL_SAVES.is_dir() else []
        templates = sorted((Path(__file__).resolve().parent.parent / "lid_db_manager" / "templates").glob("*.sav"))
        for path in found + templates:
            with self.subTest(save=path.name):
                save = SaveDocument.read(path)
                self.assertEqual(save.encoded(), save.payload)

    def test_strings_and_floats_as_the_game_writes_them(self) -> None:
        self.assertEqual(encode({"a": "x/y", "f": 2.0, "n": 3, "t": True, "z": None, "l": [1.5]}),
                         '{"a":"x\\/y","f":2.0000,"n":3,"t":true,"z":null,"l":[1.5000]}')


class Changing(unittest.TestCase):
    def setUp(self) -> None:
        self.save = SaveDocument(savegame.write_container(2, game_text()))

    def test_a_value_keeps_its_type(self) -> None:
        self.save.set(("soul", "bloodnium_point"), 77, "Bloodnium")
        self.save.set(("soul", "free_money"), "120", "Kill Coins")
        self.save.set(("soul", "pause_x"), 2, "x")
        self.assertEqual(self.save.get(("soul", "bloodnium_point")), "77")      # text stays text
        self.assertEqual(self.save.get(("soul", "free_money")), 120)            # a number stays a number
        self.assertEqual(self.save.get(("soul", "pause_x")), 2.0)
        self.assertIn(b'"bloodnium_point":"77"', self.save.encoded())

    def test_the_changes_are_listed(self) -> None:
        self.save.set(("soul", "free_money"), 95, "Kill Coins")
        self.save.set(("soul", "spirit"), 10, "Splithium")                     # the same: not a change
        (change,) = self.save.changes()
        self.assertEqual((change.label, change.before, change.after), ("Kill Coins", 90, 95))

    def test_a_missing_section_is_refused(self) -> None:
        with self.assertRaises(EditError):
            self.save.set(("nowhere", "x"), 1)


class TheAccountPage(unittest.TestCase):
    def setUp(self) -> None:
        self.save = SaveDocument(savegame.write_container(2, game_text()))

    def test_the_banks_cap_follows_its_level(self) -> None:
        self.assertEqual(account_bounds(self.save, LIMITS)["kill_coins"], (0, 100))
        self.assertEqual(account_bounds(self.save, LIMITS, {"bank_level": 3})["kill_coins"], (0, 9000))
        set_account(self.save, {"bank_level": 2, "kill_coins": 999999}, LIMITS)
        self.assertEqual((self.save.number(("soul", "safe_level")), self.save.number(("soul", "free_money"))),
                         (2, 5000))
        set_account(self.save, {"tank_level": 9, "splithium": 10 ** 9}, LIMITS)
        self.assertEqual(self.save.number(("soul", "spirit_tank_level")), 2)     # the top level there is
        self.assertEqual(self.save.number(("soul", "spirit")), 700)

    def test_a_value_past_the_limit_is_left_alone_unless_changed(self) -> None:
        self.save.set(("soul", "spirit_tank_level"), 8)             # more levels than this database has
        set_account(self.save, {"tank_level": 8, "splithium": 10}, LIMITS)
        self.assertEqual(self.save.number(("soul", "spirit_tank_level")), 8)
        set_account(self.save, {"tank_level": 7}, LIMITS)          # changed: held to what there is
        self.assertEqual(self.save.number(("soul", "spirit_tank_level")), 2)

    def test_the_other_numbers(self) -> None:
        set_account(self.save, {"bloodnium": 5000, "re_points": -4, "death_metals": 123,
                                "free_continues": 25}, LIMITS)
        self.assertEqual(self.save.get(("soul", "bloodnium_point")), "999")
        self.assertEqual(self.save.number(("soul", "recycle_point")), 0)
        self.assertEqual((self.save.number(("user", "free_medal")), self.save.number(("user", "paid_medal"))),
                         (123, 2))                                              # bought ones left alone
        self.assertEqual(self.save.number(("soul", "free_continue_max_count")), 25)
        self.assertEqual(self.save.number(("soul", "free_continue_count")), 25)   # all there to use today
        self.assertEqual(len(self.save.changes()), 5)

    @unittest.skipUnless(REAL_DB.is_file(), "needs the vanilla database")
    def test_the_limits_come_from_the_database(self) -> None:
        limits = Limits.read(REAL_DB)
        self.assertEqual(limits.top_level(limits.bank), 99)
        self.assertEqual(limits.cap(limits.bank, 99), 2_560_000)
        self.assertEqual(limits.bloodnium, 999_999)
        with tempfile.TemporaryDirectory() as tmp:                        # a mod raising the Bloodnium cap counts
            raised = Path(tmp) / "masters.db"
            shutil.copy2(REAL_DB, raised)
            con = sqlite3.connect(raised)
            con.execute("UPDATE master_const_int SET value = 5000000 WHERE id = 'BLOODNIUM_POINT_MAX'")
            con.commit()
            con.close()
            self.assertEqual(Limits.read(raised).bloodnium, 5_000_000)


class Writing(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.target = Path(self._tmp.name) / STEAM / "brggame.sav"
        self.target.parent.mkdir()
        self.target.write_bytes(save_bytes(kc=10))
        self.backups = Path(self._tmp.name) / "backups"

    def test_backed_up_first_then_replaced(self) -> None:
        save = SaveDocument.read(self.target)
        save.set(("soul", "free_money"), 500, "Kill Coins")
        backup = write_save(self.target, save.to_bytes(), self.backups, lock_reason=lambda: "")
        self.assertEqual(backup.read_bytes(), save_bytes(kc=10))
        self.assertEqual(read_summary(self.target).kill_coins, 500)

    def test_never_while_the_game_runs(self) -> None:
        save = SaveDocument.read(self.target)
        save.set(("soul", "free_money"), 500, "Kill Coins")
        with self.assertRaises(EditError):
            write_save(self.target, save.to_bytes(), self.backups, lock_reason=lambda: "LET IT DIE is running.")
        self.assertEqual(read_summary(self.target).kill_coins, 10)


class ThroughTheLibrary(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.game = root / "game"
        self.folder = self.game / "Savedata" / STEAM
        self.folder.mkdir(parents=True)
        self.live = self.folder / savegame.SAVE_NAME
        self.live.write_bytes(save_bytes(kc=1000))
        self.manager = Manager(AppPaths(root / "app").ensure())
        self.manager.set_db_path(build_db(self.game / "BrgGame" / "Content" / "masters.db"))
        self.manager.set_game_root_override(self.game)
        self.main = self.manager.add_live_save(STEAM, "Main")
        other = root / "o.sav"
        other.write_bytes(save_bytes(kc=5))
        self.other = self.manager.import_save(other, STEAM, "Other")
        self.assertTrue(self.manager.save_mod_list().ok)            # a mod note beside the save

    def edit(self, entry, kc: int):
        save = self.manager.open_save(entry.id)
        set_account(save, {"kill_coins": kc}, Limits())
        return self.manager.write_edited_save(entry.id, save)

    def test_the_save_in_the_game_is_changed_there_and_in_its_slot(self) -> None:
        self.live.write_bytes(save_bytes(kc=1500))                  # played since it was kept
        save = self.manager.open_save(self.main.id)
        self.assertEqual(save.number(("soul", "free_money")), 1500)  # the game's own, newest file
        set_account(save, {"kill_coins": 4242}, Limits())
        self.manager.write_edited_save(self.main.id, save)
        self.assertEqual(read_summary(self.live).kill_coins, 4242)
        live = self.manager.library.live_for(self.game, STEAM)
        self.assertTrue(live.known and live.in_sync)
        self.assertEqual(read_summary(self.folder / ("brggame.sav.lidmm-" + self.main.id)).kill_coins, 4242)
        self.assertEqual(self.manager.save_mod_checks()[0].state, "matches")    # still the same save
        self.assertTrue(any((self.manager.paths.backups_dir / "saves" / STEAM).glob("*before-edit*")))

    def test_a_save_only_in_the_library_is_changed_in_its_slot(self) -> None:
        entry = self.edit(self.other, 777)
        self.assertEqual(read_summary(self.manager.library.get(self.other.id).save).kill_coins, 777)
        self.assertEqual(read_summary(self.live).kill_coins, 1000)                # the game's is untouched
        self.assertEqual(entry.sha256, self.manager.library.get(self.other.id).sha256)


@unittest.skipUnless(HAVE_QT, "needs PySide6")
class TheWindow(ThroughTheLibrary):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_changing_kill_coins_and_saving(self) -> None:
        from lid_db_manager.ui.save_editor import SaveEditorDialog

        dialog = SaveEditorDialog(self.manager, self.main.id)
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(dialog.changes_label.text(), "No changes yet")
        dialog.boxes["kill_coins"].setValue(3333)
        self.assertEqual(dialog.changes_label.text(), "1 value(s) changed, not saved yet")
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes) as asked, \
                mock.patch.object(QMessageBox, "information"):
            dialog.save()
        self.assertIn("Kill Coins: 1,000 -> 3,333", asked.call_args.args[2])
        self.assertEqual(read_summary(self.live).kill_coins, 3333)
        self.assertIsNotNone(dialog.written)


if __name__ == "__main__":
    unittest.main()
