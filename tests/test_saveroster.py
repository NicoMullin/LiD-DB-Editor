"""The Fighter Freezer (saveroster.py), moving things between a Death Bag and the Storage
Box, and the editor's freezer buttons and box moves.

What matters: a dead fighter comes back FREE with full HP and its Hater records gone; a
bought fighter goes in an empty hanger at level 1 with bonuses the game can roll and its
gas mask worn; a deleted fighter takes its Death Bag along and gives Splithium back for
its level; the one played, the dead and those on defense are never deleted; the freezer
grows and shrinks only over empty hangers; a move keeps the record and changes its owner,
taking worn gear off first; the change list names the right values after a delete moves
the fighters along; and the editor keeps all of it as steps written at Save changes.
"""

from __future__ import annotations

import os
import random
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_savelibrary import STEAM

from lid_db_manager import savebag as B
from lid_db_manager import savedecals as D
from lid_db_manager import savefighters as F
from lid_db_manager import saveroster as R
from lid_db_manager import savestorage as S
from lid_db_manager.manager import Manager
from lid_db_manager.paths import AppPaths
from lid_db_manager.saveedit import EditError, Limits, SaveDocument

PROJECT = Path(__file__).resolve().parent.parent
VANILLA_DB = PROJECT / "LiD Vanilla DB" / "5.0.4.2" / "masters.db"
TEMPLATE = PROJECT / "lid_db_manager" / "templates" / "new-save-after-tutorial.sav"

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

    HAVE_QT = True
except ImportError:  # pragma: no cover - depends on the environment
    HAVE_QT = False


@unittest.skipUnless(VANILLA_DB.is_file() and TEMPLATE.is_file(), "needs the vanilla database and the template")
class TheFreezer(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules, cls.catalog = F.Rules.read(VANILLA_DB), S.Catalog.read(VANILLA_DB)
        cls.decals, cls.limits, cls.freezer = D.DecalCatalog.read(VANILLA_DB), Limits.read(VANILLA_DB), \
            R.Freezer.read(VANILLA_DB)

    def setUp(self) -> None:
        self.save = SaveDocument.read(TEMPLATE)          # Sibyl died in the tutorial: a real Hater record
        (self.sibyl,) = F.fighters(self.save)

    def buy(self, save: SaveDocument, name: str = "Newbie", cid: str = "new-cid") -> F.Fighter:
        bonus = R.roll_bonus(self.rules, "BAL", 6, random.Random(4))
        R.add_fighter(save, self.rules, self.catalog, name=name, kind="BAL", grade=6, body="BODY_MALE_001",
                      gasmask="ASSET_NM_GAS_HEAD_001", bonus=bonus, room=24, cid=cid, eids=(f"{cid}-mask",), now=5)
        return next(f for f in F.fighters(save) if f.cid == cid)

    def test_bringing_a_dead_fighter_back(self) -> None:
        save, sibyl = self.save, self.sibyl
        self.assertEqual(sibyl.state, "ENEMY")
        self.assertEqual(R.deaths(save, sibyl.cid)[0].price, save.doc["diedchara"]["dchrs"]["1"][0]["rcvrymn"])
        self.assertTrue(R.delete_reason(sibyl))                             # dead: not deleted
        R.recover_fighter(save, self.rules, sibyl.cid)
        chara = save.doc["soul"]["chr"]["chrs"]["1"][0]
        self.assertEqual((chara["state"], chara["hp"]), ("FREE", R.FULL_HP))
        self.assertEqual(save.doc["diedchara"]["dchrs"]["1"], {})
        self.assertEqual(save.doc["playlog"]["fighter"]["recover_fighter"], 1)
        self.assertEqual(len(save.doc["soul"]["deathbag"]["1"][sibyl.cid]), 20)   # kept, already its room
        with self.assertRaises(EditError):
            R.recover_fighter(save, self.rules, sibyl.cid)                  # not dead any more
        SaveDocument(save.to_bytes())

    def test_buying_a_fighter(self) -> None:
        save = self.save
        new = self.buy(save)
        self.assertEqual((new.state, new.kind, new.grade, new.level), ("FREE", "BAL", 6, 1))
        self.assertTrue(all(v in self.rules.bonus_choices("BAL", 6) for v in new.bonus.values()))
        self.assertEqual(save.doc["soul"]["chr"]["slots"]["1"][1]["cid"], "new-cid")
        bag = save.doc["soul"]["deathbag"]["1"]["new-cid"]
        self.assertEqual(len(bag), 24)
        self.assertEqual((bag[0]["eid"], bag[0]["site"]), ("new-cid-mask", "EQSITE_HEAD"))
        mask = next(p for p in save.doc["part"]["pts"]["1"] if p["eid"] == "new-cid-mask")
        self.assertEqual(mask["ptid"], R.starting_gear(self.rules, self.catalog, "BAL", 6)[0])
        self.buy(save, "Second", "cid-2")
        with self.assertRaises(EditError):
            self.buy(save, "Third", "cid-3")                                 # every hanger taken
        R.set_freezer(save, self.freezer, 2)
        self.buy(save, "Third", "cid-3")
        with self.assertRaises(EditError):
            R.set_freezer(save, self.freezer, 1)                            # hanger 4 holds a fighter
        SaveDocument(save.to_bytes())

    def test_deleting_a_fighter(self) -> None:
        save = self.save
        new = self.buy(save)
        R.set_freezer(save, self.freezer, 3)
        save.doc["soul"]["spirit_tank_level"] = 99
        given = R.delete_fighter(save, self.rules, self.decals, self.limits, new.cid)
        discard = self.rules.details[("BAL", 6, 0)]["discard_spirit"]
        self.assertEqual(given, discard * 1)
        self.assertEqual(save.doc["soul"]["spirit"], given)
        self.assertEqual(save.doc["soul"]["deathbag"]["1"]["new-cid"], {})
        self.assertNotIn("new-cid-mask", [p["eid"] for p in save.doc["part"]["pts"]["1"]])
        self.assertEqual([f.cid for f in F.fighters(save)], [self.sibyl.cid])
        self.assertEqual(save.doc["soul"]["chr"]["slots"]["1"][1]["cid"], "")
        self.assertEqual(save.doc["playlog"]["fighter"]["destory_fighter"], 1)
        R.set_freezer(save, self.freezer, 1)                                # the empty hangers can go now
        self.assertEqual(len(save.doc["soul"]["chr"]["slots"]["1"]), 3)
        save.doc["soul"]["chr"]["chrs"]["1"][0]["state"] = "USE"
        with self.assertRaises(EditError):
            R.delete_fighter(save, self.rules, self.decals, self.limits, self.sibyl.cid)

    def test_the_change_list_after_a_delete(self) -> None:
        save = self.save
        R.recover_fighter(save, self.rules, self.sibyl.cid)
        self.buy(save, "Second", "cid-2")
        R.delete_fighter(save, self.rules, self.decals, self.limits, self.sibyl.cid)   # the list moves along
        F.set_fighter(save, self.rules, "cid-2", {**F.fighters(save)[0].values(self.rules), "name": "Renamed"})
        (renamed,) = [c for c in save.changes() if c.label.endswith("Name")]
        self.assertEqual((renamed.before, renamed.after), ("Second", "Renamed"))

    def test_moving_between_a_bag_and_the_box(self) -> None:
        save = self.save
        R.recover_fighter(save, self.rules, self.sibyl.cid)
        box = S.Box(save, self.catalog)
        box.add("weapon", "PT_ARM_WP002_001", 2, level=1, now=1, ids=("hammer-1", "x", "hammer-2", "y"))
        bag = B.Bag(save, self.catalog, B.GearRules.read(VANILLA_DB), self.sibyl.cid, 20, "Sibyl",
                    self.sibyl.chr_path)
        group = box.groups()[0]
        self.assertEqual(box.move(box.picks(group.key, 1), bag, group.name), 1)
        moved = next(s["eid"] for s in bag.slots if s["eid"].startswith("hammer"))
        record = next(p for p in save.doc["part"]["pts"]["1"] if p["eid"] == moved)
        self.assertEqual(record["owner"], "USER")
        bag.set_quick("right", 0, moved)
        bag.set_held("right", 0)
        self.assertEqual(bag.move([moved], box, "hammer"), 1)               # held: put down first
        self.assertEqual(record["owner"], "COIN_LOCKER")
        self.assertEqual(bag.describe(bag.gear()), "nothing")
        self.assertEqual((box.used(), bag.used()), (2, 0))
        self.assertEqual(box.move([], bag), 0)


@unittest.skipUnless(HAVE_QT and VANILLA_DB.is_file() and TEMPLATE.is_file(), "needs PySide6 and the data")
class TheEditor(unittest.TestCase):
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
        shutil.copy2(TEMPLATE, source)
        self.entry = self.manager.import_save(source, STEAM, "New game")
        self.dialog = SaveEditorDialog(self.manager, self.entry.id)
        self.addCleanup(self.dialog.deleteLater)

    def test_the_freezer_and_box_moves_are_written_at_save_changes(self) -> None:
        from lid_db_manager.ui.freezer import BuyDialog

        dialog = self.dialog
        self.assertTrue(dialog.fz_back.isEnabled())                         # Sibyl is dead
        dialog.recover_fighter()
        self.assertEqual(dialog.fighters[dialog.shown_cid].state, "FREE")
        with mock.patch.object(BuyDialog, "exec", return_value=QDialog.DialogCode.Accepted):
            dialog.buy_fighter()
        self.assertEqual(len(dialog.fighters), 2)
        new_cid = dialog.shown_cid
        self.assertEqual(dialog.fighters[new_cid].name, "New Fighter")
        storage, panel = dialog.storage, dialog.bag_panel
        storage.add_kind.setCurrentIndex(storage.add_kind.findData("item"))
        storage.add_thing.setCurrentIndex(storage.add_thing.findText("Hemp"))
        storage.add_count.setValue(3)
        with mock.patch.object(QMessageBox, "information"):
            storage.add_picked()
        panel.refresh()
        panel.from_box.setCurrentIndex(next(i for i in range(panel.from_box.count())
                                            if panel.from_box.itemText(i).startswith("Hemp")))
        panel.from_box_count.setValue(2)
        panel.fetch_picked()                                                # into the new fighter's bag
        self.assertEqual(panel.bag.used(), 3)                               # the mask and two Hemp
        hemp_row = next(r for r in range(panel.table.rowCount()) if panel.table.item(r, 1).text() == "Hemp")
        panel.table.selectRow(hemp_row)
        panel.store_picked()
        self.assertEqual(panel.bag.used(), 2)
        dialog.fz_level.setCurrentIndex(dialog.fz_level.findData(3))
        dialog._freezer_level_picked(0)
        self.assertEqual(dialog.fz_state.text(), "5 hangers, 3 empty")
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes) as asked, \
                mock.patch.object(QMessageBox, "information"):
            dialog.save()
        listed = asked.call_args.args[2]
        for words in ("brought Sibyl back", "bought New Fighter", "Fighter Freezer: hangers",
                      "Storage Box to New Fighter's Death Bag: Hemp"):
            self.assertIn(words, listed)
        saved = SaveDocument.read(self.manager.library.get(self.entry.id).save)
        self.assertEqual(len(saved.doc["soul"]["chr"]["slots"]["1"]), 5)
        self.assertEqual(sorted(f.state for f in F.fighters(saved)), ["FREE", "FREE"])
        self.assertEqual(sum(g.count for g in S.Box(saved, storage.catalog).groups() if g.name == "Hemp"), 2)

    def test_deleting_drops_that_fighters_unsaved_changes(self) -> None:
        from lid_db_manager.ui.freezer import BuyDialog

        dialog = self.dialog
        with mock.patch.object(BuyDialog, "exec", return_value=QDialog.DialogCode.Accepted):
            dialog.buy_fighter()
        cid = dialog.shown_cid
        dialog.f_name.setText("Renamed")
        self.assertIn(cid, dialog.edits)
        self.assertTrue(dialog.fz_delete.isEnabled())
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            dialog.delete_fighter()
        self.assertNotIn(cid, dialog.fighters)
        self.assertNotIn(cid, dialog.edits)
        dialog.undo_freezer()                                               # back again, the delete undone
        self.assertIn(cid, dialog.fighters)
        dialog.undo_freezer()                                               # and the purchase
        self.assertEqual(len(dialog.fighters), 1)
        self.assertEqual(dialog.changes_label.text(), "No changes yet")


if __name__ == "__main__":
    unittest.main()
