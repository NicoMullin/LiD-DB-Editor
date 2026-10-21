"""The Mushroom Stew list (savestew.py), quests (savequests.py) and their pages.

What matters: clearing the stew list leaves it as a new game has it ({}), so the
next stew rolls a new one, and the list read shows what comes next; resetting
quests puts every count back to never taken and never cleared, except a quest
taken now (taken once) and the game's one-time fighter quests unless asked;
a quest is never cleared more often than taken; progress stays within the goal;
dropping the last taken quest leaves the game's {}; looking changes nothing; and
the pages write through Save changes.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_savelibrary import STEAM

from lid_db_manager import savedecals, savegame, savequests as Q, savestew as S
from lid_db_manager.manager import Manager
from lid_db_manager.paths import AppPaths
from lid_db_manager.saveedit import SaveDocument

PROJECT = Path(__file__).resolve().parent.parent
VANILLA_DB = PROJECT / "LiD Vanilla DB" / "5.0.4.2" / "masters.db"
TEMPLATE = PROJECT / "lid_db_manager" / "templates" / "new-save-after-tutorial.sav"
REAL_SAVES = Path(os.environ.get("LID_SAVEDATA", r"E:\SteamLibrary\steamapps\common\LET IT DIE\Savedata"))
SCREAMER, SCREAMER_31, FIGHTER_QUEST = "KILL_SCREAMER_030", "KILL_SCREAMER_031", "BODY_001"
NEXT_PULL, HP_DISCOUNT = "SKL_ADVENTURE_01_P", "SKL_DISCOUNT_HP_P"   # the stew gives Premium decals

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QMessageBox

    HAVE_QT = True
except ImportError:  # pragma: no cover - depends on the environment
    HAVE_QT = False


def _with_history(save: SaveDocument) -> SaveDocument:
    """The template with a rolled stew list, some quest history and a quest taken."""
    save.doc["soul"]["skl"]["gacha"]["normal"]["sklids"] = [HP_DISCOUNT, "SKL_NOT_IN_THE_STEW", NEXT_PULL]
    save.doc["soul"]["quest"]["user"] = [
        {"qid": SCREAMER, "ordcnt": 3, "clrcnt": 2},
        {"qid": SCREAMER_31, "ordcnt": 1, "clrcnt": 0},
        {"qid": FIGHTER_QUEST, "ordcnt": 1, "clrcnt": 1},
    ]
    save.doc["soul"]["quest"]["ord"] = [{"qid": SCREAMER_31, "prgnow": 5, "prgmax": 80, "timer": 0, "expire": 0,
                                         "term": 0, "rwd": "RWD_ITEM_MAT_411", "order": 1_800_000_000}]
    return SaveDocument(save.to_bytes())


@unittest.skipUnless(VANILLA_DB.is_file() and TEMPLATE.is_file(), "needs the vanilla database and the template")
class TheRules(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.odds = S.StewOdds.read(VANILLA_DB)
        cls.quests = Q.QuestCatalog.read(VANILLA_DB)

    def setUp(self) -> None:
        self.save = _with_history(SaveDocument.read(TEMPLATE))

    def test_the_stew_list(self) -> None:
        self.assertIn(NEXT_PULL, self.odds.odds)
        self.assertEqual(S.coming(self.save, 2), [NEXT_PULL, "SKL_NOT_IN_THE_STEW"])    # taken from the end
        self.assertNotIn(NEXT_PULL, S.missing(self.save, self.odds))
        self.assertGreater(len(S.missing(self.save, self.odds)), 100)
        self.assertTrue(S.clear(self.save))
        self.assertIn(b'"normal":{"sklids":{}}', self.save.encoded())                             # a new game's form
        self.assertEqual(S.queue(self.save), [])
        self.assertFalse(S.clear(self.save))                                 # nothing left to clear
        self.assertEqual([(c.label, c.before) for c in self.save.changes()], [("Mushroom Stew: decals rolled ahead", "3")])

    def test_quest_names_and_one_time_quests(self) -> None:
        self.assertEqual(self.quests.name(SCREAMER), "Hunting SCREAMER #030")
        self.assertTrue(self.quests.one_time(FIGHTER_QUEST))
        self.assertFalse(self.quests.one_time(SCREAMER))
        self.assertEqual(sorted(q for q, r in self.quests.quests.items() if r.get("event_type") == Q.ONE_TIME),
                         ["BODY_001", "BODY_002", "BODY_003", "BODY_004"])

    def test_reset_all(self) -> None:
        wanted = Q.reset_counts(self.save, self.quests)
        self.assertEqual(wanted, {SCREAMER: (0, 0)})                         # 031 is taken: stays taken once
        self.assertIn(FIGHTER_QUEST, Q.reset_counts(self.save, self.quests, one_time=True))
        Q.set_counts(self.save, self.quests, wanted, summary="Reset all")
        self.assertEqual(Q.counts(self.save)[SCREAMER], (0, 0))
        self.assertEqual(Q.counts(self.save)[FIGHTER_QUEST], (1, 1))
        self.assertEqual([(c.label, c.before, c.after) for c in self.save.changes()],
                         [("Quests", "Reset all", "1 quest(s) changed")])

    def test_counts_progress_and_dropping(self) -> None:
        Q.set_counts(self.save, self.quests, {SCREAMER: (1, 4)})            # cleared 4 times: taken at least 4
        self.assertEqual(Q.counts(self.save)[SCREAMER], (4, 4))
        Q.set_progress(self.save, self.quests, {SCREAMER_31: 500})
        self.assertEqual(Q.taken(self.save)[0]["prgnow"], 80)
        Q.drop(self.save, self.quests, [SCREAMER_31])
        self.assertEqual(self.save.doc["soul"]["quest"]["ord"], {})          # the game's empty form
        hash_before = SaveDocument.read(TEMPLATE).doc["soul"]["quest"]["hash"]
        self.assertEqual(self.save.doc["soul"]["quest"]["hash"], hash_before)

    @unittest.skipUnless(REAL_SAVES.is_dir(), "needs the real save folder")
    def test_real_saves(self) -> None:
        for path in sorted(REAL_SAVES.glob(f"*/{savegame.SAVE_NAME}")):
            save = SaveDocument.read(path)
            Q.set_counts(save, self.quests, Q.counts(save))
            Q.set_progress(save, self.quests, {str(o["qid"]): o["prgnow"] for o in Q.taken(save)})
            self.assertEqual(save.encoded(), save.payload)                 # looking changes nothing
            self.assertTrue(all(self.quests.name(q) for q in Q.counts(save)))
            if S.queue(save):
                S.clear(save)
                self.assertEqual(S.queue(save), [])
                self.assertEqual(SaveDocument(save.to_bytes()).get(S.PATH), {})
            wanted = Q.reset_counts(save, self.quests)
            self.assertFalse([q for q in wanted if self.quests.one_time(q)])


@unittest.skipUnless(HAVE_QT and VANILLA_DB.is_file() and TEMPLATE.is_file(), "needs PySide6 and the data")
class ThePages(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        from lid_db_manager.ui.save_editor import SaveEditorDialog

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        game = root / "game"
        db = game / "BrgGame" / "Content" / "masters.db"
        db.parent.mkdir(parents=True)
        shutil.copy2(VANILLA_DB, db)
        (game / "Savedata" / STEAM).mkdir(parents=True)
        self.manager = Manager(AppPaths(root / "app").ensure())
        self.manager.set_db_path(db)
        self.manager.set_game_root_override(game)
        source = root / "new.sav"
        source.write_bytes(_with_history(SaveDocument.read(TEMPLATE)).to_bytes())
        self.entry = self.manager.import_save(source, STEAM, "New game")
        self.dialog = SaveEditorDialog(self.manager, self.entry.id)
        self.addCleanup(self.dialog.deleteLater)

    def test_the_pages_write_through_save_changes(self) -> None:
        stew = self.dialog.stew
        self.assertEqual(stew.coming.item(0).text(), savedecals.DecalCatalog.read(VANILLA_DB).label(NEXT_PULL))
        self.assertIn("3 decals are rolled ahead", stew.state.text())
        stew.clear()
        quests = self.dialog.quests
        quests.reset_all()
        self.assertEqual(quests.counts_now(SCREAMER), (0, 0))
        quests.taken_table.selectRow(0)
        quests.complete_selected()
        self.assertEqual(quests.taken_table.item(0, 1).data(Qt.ItemDataRole.EditRole), 80)
        row = quests.rows.index(FIGHTER_QUEST)
        quests.table.item(row, 3).setData(Qt.ItemDataRole.EditRole, 0)
        self.assertEqual(quests.counts_now(FIGHTER_QUEST), (1, 0))
        self.assertEqual(self.dialog.changes_label.text(), "4 value(s) changed, not saved yet")
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes) as asked, \
                mock.patch.object(QMessageBox, "information"):
            self.dialog.save()
        text = asked.call_args.args[2]
        self.assertIn("Mushroom Stew: decals rolled ahead: 3 -> none (the next stew rolls a new list)", text)
        self.assertIn("Quests: Reset all -> 2 quest(s) changed", text)
        saved = SaveDocument.read(self.manager.library.get(self.entry.id).save)
        self.assertEqual(S.queue(saved), [])
        self.assertEqual(Q.counts(saved)[SCREAMER], (0, 0))
        self.assertEqual(Q.taken(saved)[0]["prgnow"], 80)


if __name__ == "__main__":
    unittest.main()
