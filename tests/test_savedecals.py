"""Decals and weapon mastery (savedecals.py) and their pages.

What matters: the stock holds 0 to 99 of each decal, a new game's {} becomes a
list, and a decal set to 0 leaves the list; a fighter wears no more decals than
their slots, within the game's 9 places, never one decal twice (a decal and its
premium version are two), and their entries change in place; putting decals on
leaves the stock alone; a mastery level sets the points the game reads it from,
"never used" stays so at level 1, and nothing goes past the game's table;
looking changes nothing; and the pages write it all through Save changes.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_savelibrary import STEAM

from lid_db_manager import savedecals as D, savefighters, savegame
from lid_db_manager.manager import Manager
from lid_db_manager.paths import AppPaths
from lid_db_manager.saveedit import EditError, SaveDocument

PROJECT = Path(__file__).resolve().parent.parent
VANILLA_DB = PROJECT / "LiD Vanilla DB" / "5.0.4.2" / "masters.db"
TEMPLATE = PROJECT / "lid_db_manager" / "templates" / "new-save-after-tutorial.sav"
REAL_SAVES = Path(os.environ.get("LID_SAVEDATA", r"E:\SteamLibrary\steamapps\common\LET IT DIE\Savedata"))
HP_DISCOUNT, HP_DISCOUNT_P = "SKL_DISCOUNT_HP", "SKL_DISCOUNT_HP_P"
SQUEEZER = "SKL_ABPUP_01"
BAT = "PTARMTP_05"

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QMessageBox

    HAVE_QT = True
except ImportError:  # pragma: no cover - depends on the environment
    HAVE_QT = False


@unittest.skipUnless(VANILLA_DB.is_file() and TEMPLATE.is_file(), "needs the vanilla database and the template")
class TheRules(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = D.DecalCatalog.read(VANILLA_DB)

    def setUp(self) -> None:
        self.save = SaveDocument.read(TEMPLATE)
        (self.fighter,) = savefighters.fighters(self.save)

    def test_the_catalog(self) -> None:
        cat = self.catalog
        self.assertEqual((cat.label(HP_DISCOUNT), cat.label(HP_DISCOUNT_P)),
                         ("Health Discount", "Health Discount (Premium)"))
        self.assertIn("Decreases Dash stamina consumption and fatigue time by 40%",
                      cat.decals["SKL_ADVENTURE_01_P"].description)          # #0 filled, line breaks gone
        self.assertFalse(any(d.platform == 1 for d in cat.offered()))
        self.assertTrue(any(d.platform == 1 for d in cat.offered(include_ps=True)))
        self.assertEqual((len(cat.weapon_types), cat.weapon_types[BAT]), (54, "Bat"))
        self.assertEqual((cat.top_level(BAT), cat.points_for(BAT, 18), cat.level_of(BAT, 3245)), (20, 3100, 18))

    def test_the_stock(self) -> None:
        self.assertEqual(D.stock(self.save), {})                           # a new game's {}
        D.set_stock(self.save, self.catalog, {SQUEEZER: 150, HP_DISCOUNT: 3}, now=1_800_000_000)
        self.assertEqual(D.stock(self.save), {SQUEEZER: 99, HP_DISCOUNT: 3})
        self.assertEqual(self.save.get(("soul", "skl", "psskl"))[0],
                         {"sklid": SQUEEZER, "cnt": 99, "updated": 1_800_000_000, "is_checked": 0})
        D.set_stock(self.save, self.catalog, {HP_DISCOUNT: 0})
        self.assertEqual(D.stock(self.save), {SQUEEZER: 99})                 # 0 leaves the list
        self.assertEqual([(c.label, c.after) for c in self.save.changes()],
                         [("Decals: Squeezer", 99), ("Decals: Health Discount", 3), ("Decals: Health Discount", 0)])

    def test_what_a_fighter_wears(self) -> None:
        cid = self.fighter.cid
        D.set_worn(self.save, self.catalog, cid, {0: HP_DISCOUNT, 1: HP_DISCOUNT_P}, 2, "Sibyl")
        self.assertEqual(D.worn(self.save, cid), {0: HP_DISCOUNT, 1: HP_DISCOUNT_P})
        self.assertEqual(self.save.get(("soul", "skl", "eqskl", self.save.uid)),
                         [{"cid": cid, "sklid": HP_DISCOUNT, "slot": 0}, {"cid": cid, "sklid": HP_DISCOUNT_P, "slot": 1}])
        self.assertEqual(D.stock(self.save), {})                            # the stock is left alone
        with self.assertRaisesRegex(EditError, "twice"):
            D.set_worn(self.save, self.catalog, cid, {0: HP_DISCOUNT, 1: HP_DISCOUNT}, 2, "Sibyl")
        with self.assertRaisesRegex(EditError, "2 decal slot"):
            D.set_worn(self.save, self.catalog, cid, {0: HP_DISCOUNT, 1: HP_DISCOUNT_P, 2: SQUEEZER}, 2, "Sibyl")
        with self.assertRaisesRegex(EditError, "9 places"):
            D.set_worn(self.save, self.catalog, cid, {9: SQUEEZER}, 12, "Sibyl")
        D.set_worn(self.save, self.catalog, cid, {1: SQUEEZER}, 2, "Sibyl")  # in place: slot 0 goes, 1 changes
        self.assertEqual(self.save.get(("soul", "skl", "eqskl", self.save.uid)),
                         [{"cid": cid, "sklid": SQUEEZER, "slot": 1}])

    def test_mastery_follows_the_points(self) -> None:
        mastery = D.mastery(self.save)
        self.assertEqual((mastery[BAT]["abp"], mastery[BAT]["lvl"]), (-1, 1))   # never used
        D.set_mastery(self.save, self.catalog, {BAT: 18, "PTARMTP_02": 1, "PTARMTP_04": 99})
        mastery = D.mastery(self.save)
        self.assertEqual((mastery[BAT]["abp"], mastery[BAT]["lvl"]), (3100, 18))
        self.assertEqual((mastery["PTARMTP_02"]["abp"], mastery["PTARMTP_02"]["lvl"]), (-1, 1))
        self.assertEqual(mastery["PTARMTP_04"]["lvl"], 20)                     # no higher than the table
        D.set_mastery(self.save, self.catalog, {BAT: 1})
        self.assertEqual((D.mastery(self.save)[BAT]["abp"], D.mastery(self.save)[BAT]["lvl"]), (0, 1))

    @unittest.skipUnless(REAL_SAVES.is_dir(), "needs the real save folder")
    def test_real_saves_read_and_keep_to_the_rules(self) -> None:
        rules = savefighters.Rules.read(VANILLA_DB)
        for path in sorted(REAL_SAVES.glob(f"*/{savegame.SAVE_NAME}")):
            save = SaveDocument.read(path)
            self.assertTrue(all(0 < n <= D.COUNT_MAX for n in D.stock(save).values()))
            for f in savefighters.fighters(save):
                self.assertEqual(D.check_worn(D.worn(save, f.cid), f.values(rules)["decal_slots"], self.catalog), "",
                                 f.name)
                D.set_worn(save, self.catalog, f.cid, D.worn(save, f.cid), 9, f.name)
            for ptarmtp, row in D.mastery(save).items():
                if ptarmtp in self.catalog.thresholds:
                    self.assertEqual(self.catalog.level_of(ptarmtp, max(row["abp"], 0)), row["lvl"], ptarmtp)
            D.set_stock(save, self.catalog, D.stock(save))
            self.assertEqual(save.encoded(), save.payload)


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
        save = SaveDocument.read(TEMPLATE)
        save.doc["soul"]["chr"]["chrs"][save.uid][0]["state"] = "FREE"   # the tutorial fighter, alive
        source = root / "new.sav"
        source.write_bytes(save.to_bytes())
        self.entry = self.manager.import_save(source, STEAM, "New game")
        self.dialog = SaveEditorDialog(self.manager, self.entry.id)
        self.addCleanup(self.dialog.deleteLater)

    def test_all_three_pages_write_through_save_changes(self) -> None:
        worn = self.dialog.worn_decals
        slots = self.dialog._now(self.dialog.shown_cid)["decal_slots"]
        self.assertEqual([b.isEnabled() for b in worn.boxes].count(True), slots)
        box = worn.boxes[0]
        box.setCurrentIndex(box.findText("Health Discount (Premium)"))
        worn._picked(0)
        with mock.patch.object(QMessageBox, "information") as said:          # the same decal twice: no
            other = worn.boxes[1]
            other.setCurrentIndex(other.findText("Health Discount (Premium)"))
            worn._picked(1)
        self.assertTrue(said.called)
        self.assertEqual(self.dialog._wearer(self.dialog.shown_cid).decals, [HP_DISCOUNT_P])   # gear sees it
        stock = self.dialog.decal_stock
        stock.table.item(stock.rows.index(SQUEEZER), 2).setData(Qt.ItemDataRole.EditRole, 7)
        self.dialog.mastery.max_all()
        self.assertEqual(self.dialog.changes_label.text(),
                         f"{1 + 1 + self.dialog.mastery.change_count} value(s) changed, not saved yet")
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes) as asked, \
                mock.patch.object(QMessageBox, "information"):
            self.dialog.save()
        text = asked.call_args.args[2]
        self.assertIn("Sibyl's decals: slot 1: (empty) -> Health Discount (Premium)", text)
        self.assertIn("Decals: Squeezer: 0 -> 7", text)
        saved = SaveDocument.read(self.manager.library.get(self.entry.id).save)
        (f,) = savefighters.fighters(saved)
        self.assertEqual(D.worn(saved, f.cid), {0: HP_DISCOUNT_P})
        self.assertEqual(D.stock(saved), {SQUEEZER: 7})
        self.assertEqual(D.mastery(saved)[BAT]["lvl"], 20)


if __name__ == "__main__":
    unittest.main()
