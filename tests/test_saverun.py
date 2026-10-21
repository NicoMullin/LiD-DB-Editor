"""The run a fighter is in (saverun.py) and the Run page.

What matters: a save in the Waiting Room has no run; ending a run clears where
the fighter is and the run's floors, removes what the run left on floors and in
chests (never the Death Bag), banks the Kill Coins carried up to the Bank's
limit and adds the Bloodnium; a run that crashed on a normal Heaven floor can go
back to its last boss floor, paused at the arena's arrival point, the floors
after it dropped; a crash right after a boss floor whose arrival point is not
known is not offered; and the page writes through Save changes.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_savelibrary import STEAM

from lid_db_manager import saverun as R
from lid_db_manager.manager import Manager
from lid_db_manager.paths import AppPaths
from lid_db_manager.saveedit import Limits, SaveDocument

PROJECT = Path(__file__).resolve().parent.parent
VANILLA_DB = PROJECT / "LiD Vanilla DB" / "5.0.4.2" / "masters.db"
TEMPLATE = PROJECT / "lid_db_manager" / "templates" / "new-save-after-tutorial.sav"
BOSS_55, NORMAL_56, NORMAL_57 = ("HVN_FLR_0005", "HVN_AREA_017"), ("HVN_FLR_0006", "HVN_AREA_016"), \
    ("HVN_FLR_0007", "HVN_AREA_016")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtWidgets import QApplication, QMessageBox

    HAVE_QT = True
except ImportError:  # pragma: no cover - depends on the environment
    HAVE_QT = False


def _in_a_run(save: SaveDocument, pause: str = "HEAVEN_PAUSE", walked=(BOSS_55, NORMAL_56, NORMAL_57)) -> SaveDocument:
    """The template's fighter quit on the last floor of ``walked`` with things carried and left about."""
    doc, uid = save.doc, save.uid
    fighter = doc["soul"]["chr"]["chrs"][uid][0]
    fighter.update(state="USE", money=5_000, bloodnium=300)
    flrid, areaid = walked[-1]
    doc["soul"].update(pause=pause, stgid="S_HVN", flrid=flrid, areaid=areaid, unitid="A3", relief_point="A3",
                       pause_x=1.5, pause_y=2.5, pause_z=3.5, pause_yaw=90.0, free_money=2_559_000, safe_level=99,
                       replica_money=5_000, hvntrinfo=[{"flrid": "HVN_FLR_0010"}])
    doc["floor"]["rlg"] = {"user": [{"flrid": f, "areaid": a} for f, a in walked],
                           "archive": [{"flrid": f, "areaid": a, "layout": 1} for f, a in walked]}
    doc["floor"]["pop"]["item"] = [{"flrid": f, "n": i} for i, (f, _) in enumerate(walked)]
    doc["floor"]["pop"]["msr"] = {f"{f}-{a}": [1] for f, a in walked}
    doc["floor"]["trbox"] = {"boxes": [1, 2]}
    doc["item"]["items"] = [{"eid": "floor-thing", "owner": "FLOOR", "itemid": "ITMT_WOOD_1"},
                            {"eid": "bag-thing", "owner": "USER", "itemid": "ITMT_WOOD_1"}]
    held = doc["part"]["pts"][uid]
    doc["part"]["pts"][uid] = (held if isinstance(held, list) else []) + [
        {"uid": int(uid), "eid": "jackal-thing", "owner": "JACKAL_01", "ptid": "PT_ARM_WP005_001"}]
    return SaveDocument(save.to_bytes())


@unittest.skipUnless(VANILLA_DB.is_file() and TEMPLATE.is_file(), "needs the vanilla database and the template")
class TheRules(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.limits = Limits.read(VANILLA_DB)
        cls.floors = R.HeavenFloors.read(VANILLA_DB)

    def test_no_run_in_the_waiting_room(self) -> None:
        save = SaveDocument.read(TEMPLATE)
        self.assertIsNone(R.run(save, self.limits))
        self.assertFalse(R.end_run(save, self.limits))
        self.assertIsNone(R.rewind_target(save, self.floors))

    def test_ending_a_run(self) -> None:
        save = _in_a_run(SaveDocument.read(TEMPLATE))
        info = R.run(save, self.limits)
        self.assertEqual((info.money, info.banked, info.lost, info.floors, info.made), (5_000, 1_000, 4_000, 3, 2))
        self.assertEqual(self.floors.label(info.flrid), "57F")
        self.assertTrue(R.end_run(save, self.limits, self.floors))
        soul, doc = save.doc["soul"], save.doc
        self.assertEqual((soul["pause"], soul["flrid"], soul["stgid"], soul["replica_money"]), ("", "", "", -1))
        self.assertEqual((soul["free_money"], soul["bloodnium_point"]), (2_560_000, 300))
        fighter = doc["soul"]["chr"]["chrs"][save.uid][0]
        self.assertEqual((fighter["money"], fighter["bloodnium"]), (0, 0))
        self.assertEqual(doc["floor"]["rlg"], {"user": {}, "archive": {}})
        self.assertEqual((doc["floor"]["pop"]["item"], doc["floor"]["trbox"], soul["hvntrinfo"]), ({}, {}, {}))
        self.assertEqual([i["eid"] for i in doc["item"]["items"]], ["bag-thing"])   # the Death Bag's stays
        self.assertNotIn("jackal-thing", [p.get("eid") for p in doc["part"]["pts"][save.uid]])
        self.assertIn("Run: Kill Coins carried to the Bank", [c.label for c in save.changes()])
        SaveDocument(save.to_bytes())                                       # still a save that reads back

    def test_going_back_to_the_last_boss_floor(self) -> None:
        self.assertIsNone(R.rewind_target(_in_a_run(SaveDocument.read(TEMPLATE)), self.floors))   # paused, not crashed
        save = _in_a_run(SaveDocument.read(TEMPLATE), pause="HEAVEN_CRASH")
        target = R.rewind_target(save, self.floors)
        self.assertEqual((target.flrid, target.dropped), (BOSS_55[0], 2))
        self.assertTrue(R.rewind(save, self.floors))
        soul, floor = save.doc["soul"], save.doc["floor"]
        self.assertEqual((soul["pause"], soul["flrid"], soul["areaid"], soul["unitid"]),
                         ("HEAVEN_PAUSE", BOSS_55[0], BOSS_55[1], "D0"))
        self.assertEqual((soul["pause_x"], soul["pause_yaw"]), (23.341, 23846.0))
        self.assertEqual(floor["rlg"]["user"], [{"flrid": BOSS_55[0], "areaid": BOSS_55[1]}])
        self.assertEqual([a["flrid"] for a in floor["rlg"]["archive"]], [BOSS_55[0]])
        self.assertEqual(floor["pop"]["item"], [{"flrid": BOSS_55[0], "n": 0}])
        self.assertEqual(list(floor["pop"]["msr"]), [f"{BOSS_55[0]}-{BOSS_55[1]}"])
        self.assertEqual([d["unit"] for d in floor["dust"]], ["BOSS_START", "BOSS_GOAL"])
        self.assertEqual(save.doc["soul"]["chr"]["chrs"][save.uid][0]["money"], 5_000)   # carried things stay

    def test_no_known_arrival_point(self) -> None:
        walked = (("HVN_FLR_R00_0010", "HVN_AREA_002_R00"), ("HVN_FLR_R00_0011", "HVN_AREA_000_R00"))
        save = _in_a_run(SaveDocument.read(TEMPLATE), pause="HEAVEN_CRASH", walked=walked)
        target = R.rewind_target(save, self.floors)
        self.assertIsNone(target.arrival)                                    # NEO map B
        self.assertFalse(R.rewind(save, self.floors))


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
        source.write_bytes(_in_a_run(SaveDocument.read(TEMPLATE), pause="HEAVEN_CRASH").to_bytes())
        self.entry = self.manager.import_save(source, STEAM, "In a run")
        self.dialog = SaveEditorDialog(self.manager, self.entry.id)
        self.addCleanup(self.dialog.deleteLater)

    def test_the_page_writes_through_save_changes(self) -> None:
        page = self.dialog.run
        self.assertIn("57F", page.where.text())
        self.assertTrue(page.back.isVisible() or page.back.isVisibleTo(page))
        page.back.setChecked(True)
        page.end.setChecked(True)                                            # one or the other
        self.assertFalse(page.back.isChecked())
        self.assertEqual(self.dialog.changes_label.text(), "1 value(s) changed, not saved yet")
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes) as asked, \
                mock.patch.object(QMessageBox, "information"):
            self.dialog.save()
        self.assertIn("in a run, on 57F -> back in the Waiting Room", asked.call_args.args[2])
        saved = SaveDocument.read(self.manager.library.get(self.entry.id).save)
        self.assertEqual(saved.doc["soul"]["pause"], "")


if __name__ == "__main__":
    unittest.main()
