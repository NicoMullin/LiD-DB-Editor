"""Dates (savedates.py) and the Dates page.

What matters: every date is found and grouped by kind; a date after the
reference time, past 2038 or wrapped round to negative is a problem, but an
expiry in the future is not; fixing sets wrong dates to the reference time (the
last game day to that day's start), removes login-bonus days not come yet and
leaves good dates alone; a date kept once can be set; a new game day sets the
last one a day back; timers end now when finished, and show only with the Timers
mod (or, with no mods known, when one is running); looking changes nothing; and
the page writes through Save changes, after the other pages.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_savelibrary import STEAM

from lid_db_manager import savedates as T, savegame, savestorage
from lid_db_manager.manager import Manager
from lid_db_manager.paths import AppPaths
from lid_db_manager.saveedit import SaveDocument

PROJECT = Path(__file__).resolve().parent.parent
VANILLA_DB = PROJECT / "LiD Vanilla DB" / "5.0.4.2" / "masters.db"
TEMPLATE = PROJECT / "lid_db_manager" / "templates" / "new-save-after-tutorial.sav"
REAL_SAVES = Path(os.environ.get("LID_SAVEDATA", r"E:\SteamLibrary\steamapps\common\LET IT DIE\Savedata"))
REF = 1_800_000_000             # 2027-01-15, after everything in the template
DAY = savegame.DAY
TIMER = {"wait_type": "BUYPART", "part_id": "PT_ARM_WP005_003", "part_lvl": 3, "facility_type": "", "created": 0,
         "period": REF + 3600}

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtWidgets import QApplication, QMessageBox

    HAVE_QT = True
except ImportError:  # pragma: no cover - depends on the environment
    HAVE_QT = False


def _damaged(save: SaveDocument) -> SaveDocument:
    """The template after some time-jumping: dates ahead of REF, one past 2038, one wrapped."""
    doc = save.doc
    doc["soul"]["modified"] = REF + 10 * DAY
    doc["user"]["last_login_date"] = savegame.game_day(REF + 5 * DAY)
    doc["login_bonus"] = [{"login_date": savegame.game_day(REF) - DAY, "is_vip": 0},
                          {"login_date": savegame.game_day(REF + 3 * DAY), "is_vip": 0}]
    doc["soul"]["vip"]["expired_time"] = REF + 30 * DAY                 # an expiry ahead is fine
    doc["soul"]["abduct_guard_time"] = 2 ** 31 + 5                       # an expiry past 2038 is not
    doc["soul"]["present"][0]["created"] = -500_000_000                  # wrapped round
    doc["soul"]["waiting"] = [dict(TIMER)]
    return SaveDocument(save.to_bytes())


@unittest.skipUnless(TEMPLATE.is_file(), "needs the template")
class TheRules(unittest.TestCase):
    def setUp(self) -> None:
        self.save = _damaged(SaveDocument.read(TEMPLATE))

    def test_finding_problems(self) -> None:
        groups = {g.pattern: g for g in T.scan(self.save.doc)}
        self.assertEqual(groups["soul.modified"].label, "Save data last written")
        self.assertTrue(groups["soul.vip.expired_time"].deadline)
        self.assertEqual(T.problems(list(groups.values()), REF), {"future": 3, "post2038": 1, "wrapped": 1})

    def test_fixing(self) -> None:
        self.assertEqual(T.fix(self.save, REF), 5)
        doc = self.save.doc
        self.assertEqual(doc["soul"]["modified"], REF)
        self.assertEqual(doc["user"]["last_login_date"], savegame.game_day(REF))
        self.assertEqual(len(doc["login_bonus"]), 1)                       # the day not come yet goes
        self.assertEqual(doc["soul"]["vip"]["expired_time"], REF + 30 * DAY)
        self.assertEqual((doc["soul"]["abduct_guard_time"], doc["soul"]["present"][0]["created"]), (REF, REF))
        self.assertEqual(T.problems(T.scan(doc), REF), {"future": 0, "post2038": 0, "wrapped": 0})
        self.assertIn("Dates: Save data last written", [c.label for c in self.save.changes()])
        self.assertEqual(T.fix(self.save, REF), 0)

    def test_one_kind_one_date_and_a_new_day(self) -> None:
        self.assertEqual(T.fix(self.save, REF, "soul.modified"), 1)
        self.assertEqual(self.save.doc["user"]["last_login_date"], savegame.game_day(REF + 5 * DAY))   # not this kind
        self.assertTrue(T.set_one(self.save, "soul.vip.expired_time", REF + 90 * DAY))
        self.assertEqual(self.save.doc["soul"]["vip"]["expired_time"], REF + 90 * DAY)
        self.assertFalse(T.set_one(self.save, "part.pts.*[].created", REF))  # kept many times: not one date
        T.fix(self.save, REF)
        self.assertTrue(T.new_game_day(self.save, REF))
        self.assertEqual(self.save.doc["user"]["last_login_date"], savegame.game_day(REF) - DAY)
        self.assertFalse(T.new_game_day(self.save, REF))                     # already a new day

    def test_timers(self) -> None:
        self.assertTrue(T.timers_shown(["Timers"], self.save))
        self.assertFalse(T.timers_shown(["Death Metals"], self.save))       # mods known, no Timers mod
        self.assertTrue(T.timers_shown([], self.save, now=REF))              # unknown mods, one still running
        self.assertFalse(T.timers_shown([], self.save, now=REF + 7200))
        self.assertEqual(T.timer_label(TIMER, savestorage.Catalog.read(VANILLA_DB)), "Crafting: Metal Bat A +2")
        self.assertEqual(T.finish_timers(self.save, [T.timer_key(TIMER)], now=REF), 1)
        self.assertEqual(self.save.doc["soul"]["waiting"][0]["period"], REF)
        self.assertEqual(T.finish_timers(self.save, [T.timer_key(TIMER)], now=REF), 0)

    @unittest.skipUnless(REAL_SAVES.is_dir(), "needs the real save folder")
    def test_real_saves(self) -> None:
        for path in sorted(REAL_SAVES.glob(f"*/{savegame.SAVE_NAME}")):
            save = SaveDocument.read(path)
            groups = T.scan(save.doc)
            self.assertGreater(sum(len(g.entries) for g in groups), 1000)
            far = 2_000_000_000                                              # 2033: nothing is ahead of it
            self.assertEqual(T.fix(save, far), 0)
            self.assertEqual(save.encoded(), save.payload)


@unittest.skipUnless(HAVE_QT and VANILLA_DB.is_file() and TEMPLATE.is_file(), "needs PySide6 and the data")
class ThePage(unittest.TestCase):
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
        save = SaveDocument.read(TEMPLATE)
        save.doc["soul"]["modified"] = 2_100_000_000                          # 2036: ahead of any test run
        source.write_bytes(save.to_bytes())
        self.entry = self.manager.import_save(source, STEAM, "New game")
        self.dialog = SaveEditorDialog(self.manager, self.entry.id)
        self.addCleanup(self.dialog.deleteLater)

    def test_the_page_writes_through_save_changes(self) -> None:
        page = self.dialog.dates
        self.assertFalse(page.timer_box.isVisibleTo(page))                  # no Timers mod, nothing running
        self.assertIn("1 problem date", page.banner.text())
        page.fix_all()
        self.assertIn("No problem dates", page.banner.text())
        self.assertEqual(page.change_count, 1)
        self.assertEqual(self.dialog.changes_label.text(), "1 value(s) changed, not saved yet")
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes) as asked, \
                mock.patch.object(QMessageBox, "information"):
            self.dialog.save()
        self.assertIn("Dates: Save data last written: 1 wrong -> set to", asked.call_args.args[2])
        saved = SaveDocument.read(self.manager.library.get(self.entry.id).save)
        self.assertLess(saved.doc["soul"]["modified"], 2_100_000_000)


if __name__ == "__main__":
    unittest.main()
