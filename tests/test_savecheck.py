"""Signs of mods in a save whose mod list nobody noted (savecheck.py).

What matters: an id the clean game has no row for is found, and the mod whose
own files add it is named (or, with no such mod here, it is said to need a mod
this PC has not got); more Kill Coins, Splithium or Bloodnium than the clean
game holds points at a mod that raises that limit; a field that only shares a
name with an id field is not taken for one; a save with no mods noted is edited
with the stock limits, never with whatever is applied now; and the list the
player settles on is noted for the save, marked as found or chosen.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fixtures import write_mod
from test_savelibrary import STEAM, document

from lid_db_manager import savecheck, savegame
from lid_db_manager.manager import Manager
from lid_db_manager.paths import AppPaths
from lid_db_manager.saveedit import SaveDocument

PROJECT = Path(__file__).resolve().parent.parent
VANILLA_DB = PROJECT / "LiD Vanilla DB" / "5.0.4.2" / "masters.db"
BANK_LIMIT = PROJECT / "mods" / "bank-limit"
TEMPLATES = PROJECT / "lid_db_manager" / "templates"
NEW_QUEST = "CODEX_COLLAB_TEST_01"
QUEST_MOD = {"patches": [{"type": "raw_sql", "id": "q", "sql": (
    "CREATE TEMP TABLE q AS SELECT * FROM master_quest WHERE qid = 'ATKATTR_001'; "
    f"UPDATE q SET qid = '{NEW_QUEST}', no = (SELECT MAX(no) + 1 FROM master_quest); "
    "INSERT INTO master_quest SELECT * FROM q; DROP TABLE q;")}]}

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog

    HAVE_QT = True
except ImportError:  # pragma: no cover - depends on the environment
    HAVE_QT = False


def a_save(**changes) -> SaveDocument:
    doc = document(kc=1000)
    doc["soul"]["quest"] = {"user": [{"qid": "ATKATTR_001", "ordcnt": 0, "clrcnt": 0}], "dis": {"ATKATTR_001": 1}}
    doc["playlog"]["famous"] = {"body": '{"PT_SPE_TOPS_007":110}'}         # text, not an id
    for path, value in changes.items():
        node = doc
        *parents, last = path.split(".")
        for key in parents:
            node = node.setdefault(key, {})
        node[last] = value
    return SaveDocument(savegame.write_container(2, json.dumps(doc, separators=(",", ":")).encode()))


@unittest.skipUnless(VANILLA_DB.is_file() and BANK_LIMIT.is_dir(), "needs the vanilla database and Bank Limit")
class Checking(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.mods_dir = Path(self._tmp.name) / "mods"
        shutil.copytree(BANK_LIMIT, self.mods_dir / "bank-limit")
        write_mod(self.mods_dir, "Collab Quests", QUEST_MOD)
        write_mod(self.mods_dir, "Unrelated", {"patches": [{"type": "raw_sql", "id": "x",
                                                            "sql": "UPDATE master_skill SET exp = 1;"}]})
        from lid_db_manager.mod_loader import scan_mods
        self.mods = scan_mods(self.mods_dir).mods

    def check(self, save: SaveDocument, mods=None) -> savecheck.SaveCheck:
        return savecheck.check(save, VANILLA_DB, self.mods if mods is None else mods)

    def test_a_stock_save_shows_nothing(self) -> None:
        self.assertTrue(self.check(a_save()).clean)
        for template in TEMPLATES.glob("*.sav"):
            with self.subTest(template=template.name):
                self.assertTrue(self.check(SaveDocument.read(template)).clean)

    def test_something_the_clean_game_has_not_got_names_the_mod_that_adds_it(self) -> None:
        save = a_save(**{"soul.quest.dis": {"ATKATTR_001": 1, NEW_QUEST: 1}})
        found = self.check(save)
        (sign,) = found.signs
        self.assertEqual((sign.mods, sign.examples), (["Collab Quests"], [NEW_QUEST]))
        self.assertEqual(found.found_mods, ["Collab Quests"])
        self.assertIn("1 quests the clean game does not have", found.lines()[0])

        alone = self.check(save, mods=[m for m in self.mods if m.id != "Collab Quests"])
        self.assertEqual(alone.unexplained[0].examples, [NEW_QUEST])
        self.assertIn("a mod not installed here", alone.lines()[0])

    def test_more_than_the_clean_game_holds_points_at_the_mod_that_raises_it(self) -> None:
        found = self.check(a_save(**{"soul.safe_level": 99, "soul.free_money": 9_000_000,
                                     "soul.bloodnium_point": "5000000"}))
        self.assertEqual([s.mods for s in found.signs], [["bank-limit"], []])
        self.assertIn("9,000,000 Kill Coins", found.signs[0].what)
        self.assertIn("5,000,000 Bloodnium", found.signs[1].what)
        self.assertEqual(found.found_mods, ["bank-limit"])


@unittest.skipUnless(VANILLA_DB.is_file() and BANK_LIMIT.is_dir(), "needs the vanilla database and Bank Limit")
class ASaveWithNoModsNoted(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.paths = AppPaths(root / "app").ensure()
        shutil.copytree(BANK_LIMIT, self.paths.mods_dir / "bank-limit")
        write_mod(self.paths.mods_dir, "Collab Quests", QUEST_MOD)
        self.game = root / "game"
        db = self.game / "BrgGame" / "Content" / "masters.db"
        db.parent.mkdir(parents=True)
        shutil.copy2(VANILLA_DB, db)
        (self.game / "Savedata" / STEAM).mkdir(parents=True)
        (self.game / "Savedata" / STEAM / savegame.SAVE_NAME).write_bytes(a_save().raw)
        self.manager = Manager(self.paths)
        self.manager.set_db_path(db)
        self.manager.set_game_root_override(self.game)
        self.manager.rescan()
        self.manager.set_enabled("bank-limit", True)
        self.manager.state.mod_settings["bank-limit"] = {"multiplier": 4}
        self.assertTrue(self.manager.save_mod_list().ok)          # the game has Bank Limit x4 now
        source = root / "from a friend.sav"
        source.write_bytes(a_save(**{"soul.safe_level": 99, "soul.free_money": 9_000_000,
                                     "soul.quest.dis": {NEW_QUEST: 1}}).raw)
        self.entry = self.manager.import_save(source, STEAM, "From a friend")

    def test_it_is_edited_with_the_stock_limits_not_whatever_is_applied(self) -> None:
        data = self.manager.save_game_data(self.entry.id)
        self.assertEqual((data.source, data.db_path), ("stock", self.manager.vanilla_path))
        limits = self.manager.save_limits(data.db_path)
        self.assertEqual(limits.cap(limits.bank, 99), 2_560_000)
        self.assertIn("stock ones", data.describe())

    def test_what_was_found_is_offered_with_its_settings_here_and_noted(self) -> None:
        found = self.manager.check_save(self.entry.id)
        self.assertEqual(found.found_mods, ["Collab Quests", "bank-limit"])
        choice = self.manager.found_mods_choice(found)
        self.assertEqual(choice["enabled_mods"], ["bank-limit", "Collab Quests"])     # the order ticked here
        self.assertEqual(choice["mod_settings"], {"bank-limit": {"multiplier": 4}})
        entry = self.manager.note_save_mods(self.entry.id, choice, "found")
        self.assertEqual(entry.mods["noted"], "found")
        self.assertEqual(self.manager.save_mods(self.entry.id)["enabled_mods"], ["bank-limit", "Collab Quests"])
        data = self.manager.save_game_data(self.entry.id)
        self.assertEqual(data.source, "built")
        limits = self.manager.save_limits(data.db_path)
        self.assertEqual(limits.cap(limits.bank, 99), 10_240_000)                     # x4, as here


@unittest.skipUnless(HAVE_QT and VANILLA_DB.is_file() and BANK_LIMIT.is_dir(), "needs PySide6 and the data")
class TheQuestion(ASaveWithNoModsNoted):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def dialog(self):
        from lid_db_manager.ui.save_mods_dialog import SaveModsDialog

        dialog = SaveModsDialog(self.manager, self.entry.id)
        self.addCleanup(dialog.deleteLater)
        return dialog

    def test_what_was_found_is_picked_and_noted(self) -> None:
        dialog = self.dialog()
        self.assertEqual(dialog.picked(), "found")
        self.assertIn("What was found: Collab Quests, bank-limit", dialog.options["found"].text())
        self.assertIn("The mods applied here now (1)", dialog.options["applied"].text())
        dialog.note()
        self.assertEqual(self.manager.library.get(self.entry.id).mods["noted"], "found")

    def test_no_mods_and_a_mod_pack(self) -> None:
        dialog = self.dialog()
        dialog.options["none"].setChecked(True)
        dialog.note()
        mods = self.manager.library.get(self.entry.id).mods
        self.assertEqual((mods["enabled_mods"], mods["noted"]), ([], "chosen"))
        self.manager.save_modpack("Bank x4")
        dialog = self.dialog()
        dialog.options["pack"].setChecked(True)
        self.assertEqual(dialog.pack_box.currentData(), "Bank x4")
        dialog.note()
        self.assertEqual(self.manager.save_mods(self.entry.id)["mod_settings"], {"bank-limit": {"multiplier": 4}})

    def test_importing_a_sav_asks(self) -> None:
        from lid_db_manager.ui.save_mods_dialog import SaveModsDialog
        from lid_db_manager.ui.saves_tab import SavesTab

        tab = SavesTab(self.manager)
        self.addCleanup(tab.deleteLater)
        other = Path(self._tmp.name) / "other.sav"
        other.write_bytes(a_save().raw)
        with mock.patch.object(QFileDialog, "getOpenFileName", return_value=(str(other), "")), \
                mock.patch.object(QInputDialog, "getText", return_value=("Another", True)), \
                mock.patch.object(SaveModsDialog, "exec", return_value=0) as asked:
            tab.import_file()
        self.assertTrue(asked.called)


if __name__ == "__main__":
    unittest.main()
