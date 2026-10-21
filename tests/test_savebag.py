"""A fighter's Death Bag and gear (savebag.py), and the Fighters page's Death Bag panel.

What matters: the bag is read through its slots like the Storage Box, its new
records are the game's own (owner USER) and it never holds more than the
fighter's Death Bag space plus the Express Pass's extra; what is worn and held
is written as the game writes it (site, arm_slot, select_arm_slots), a weapon
sits in one quick slot only, and a two-handed weapon leaves the other hand
empty - whichever hand was set last keeps its weapon; a piece is only worn
where it goes, unbroken, and with the stat it needs (the game's own sum, less
the worn decals' discounts); a part's numbers grow by the game's whole-number
formula; rewriting unchanged gear changes nothing; and the page shows locked
fighters' bags without letting them change.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_savelibrary import STEAM

from lid_db_manager import savebag as B, savefighters, savegame, savestorage as S
from lid_db_manager.manager import Manager
from lid_db_manager.paths import AppPaths
from lid_db_manager.saveedit import EditError, SaveDocument

PROJECT = Path(__file__).resolve().parent.parent
VANILLA_DB = PROJECT / "LiD Vanilla DB" / "5.0.4.2" / "masters.db"
TEMPLATE = PROJECT / "lid_db_manager" / "templates" / "new-save-after-tutorial.sav"
REAL_SAVES = Path(os.environ.get("LID_SAVEDATA", r"E:\SteamLibrary\steamapps\common\LET IT DIE\Savedata"))
HAMMER = "PT_ARM_WP011_001"          # Iron Hammer: one hand
RIFLE = "PT_ARM_WP031_001"           # KAMAS-A1 Assault Rifle: two hands
WHITE_STEEL = "PT_DIY_TOPS_021"      # White Steel Body (its +5 to +24 form is PT_DIY_TOPS_021_G)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtWidgets import QApplication, QMessageBox

    HAVE_QT = True
except ImportError:  # pragma: no cover - depends on the environment
    HAVE_QT = False


@unittest.skipUnless(VANILLA_DB.is_file() and TEMPLATE.is_file(), "needs the vanilla database and the template")
class TheBag(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = S.Catalog.read(VANILLA_DB)
        cls.rules = B.GearRules.read(VANILLA_DB)

    def setUp(self) -> None:
        self.save = SaveDocument.read(TEMPLATE)
        (self.fighter,) = savefighters.fighters(self.save)
        self.bag = self.make(room=20)

    def make(self, room: int) -> B.Bag:
        return B.Bag(self.save, self.catalog, self.rules, self.fighter.cid, room, self.fighter.name,
                     self.fighter.chr_path)

    def eid_of(self, ptid: str) -> str:
        return next(r["eid"] for r in self.save.get(("part", "pts", self.save.uid)) if r["ptid"] == ptid)

    def wearer(self, **stats) -> B.Wearer:
        return B.Wearer("Sibyl", "BAL", 6, {k: stats.get(k, 1) for k in ("hp", "str", "dex", "vit", "stm", "luk")})

    def test_things_go_in_as_the_game_writes_a_bag(self) -> None:
        self.assertEqual(self.bag.add("weapon", HAMMER, 1, level=1, now=1_800_000_000), 1)
        record = self.save.get(("part", "pts", self.save.uid))[-1]
        self.assertEqual(record["owner"], "USER")
        slot = self.bag.slots[0]
        self.assertEqual(slot, {"uid": int(self.save.uid), "cid": self.fighter.cid, "slot": 0, "type": 0,
                                "eid": record["eid"], "site": "", "arm_slot": -1})
        self.assertEqual([c.label for c in self.save.changes()], ["Sibyl's Death Bag: added Iron Hammer +0"])

    def test_never_more_than_its_room(self) -> None:
        self.assertEqual(self.bag.add("item", "ITMT_FIBER_2", 25), 20)
        self.assertEqual(self.bag.add("item", "ITMT_FIBER_2", 1), 0)
        roomier = self.make(room=30)                               # more space: the list grows to it
        self.assertEqual(roomier.add("item", "ITMT_FIBER_2", 15), 10)
        self.assertEqual(len(roomier.slots), 30)
        self.assertTrue(all(s["slot"] == i for i, s in enumerate(roomier.slots)))

    def test_quick_slots_hands_and_a_two_handed_weapon(self) -> None:
        self.bag.add("weapon", HAMMER, 1)
        self.bag.add("weapon", RIFLE, 1)
        hammer, rifle = self.eid_of(HAMMER), self.eid_of(RIFLE)
        self.bag.set_quick("right", 0, hammer)
        self.bag.set_held("right", 0)
        self.bag.set_quick("left", 1, rifle)
        self.bag.set_held("left", 1)                               # two hands: the hammer goes down
        gear = self.bag.gear()
        self.assertEqual((gear.held_eid("right"), gear.held_eid("left")), (None, rifle))
        self.assertEqual(self.save.get(self.fighter.chr_path)["select_arm_slots"], "0,1")
        slots = {s["eid"]: (s["site"], s["arm_slot"]) for s in self.bag.slots if s["eid"]}
        self.assertEqual(slots, {hammer: ("", 0), rifle: ("EQSITE_ARML", 4)})
        self.bag.set_held("right", 0)                              # the hammer back up: the rifle goes down
        gear = self.bag.gear()
        self.assertEqual((gear.held_eid("right"), gear.held_eid("left")), (hammer, None))
        self.bag.set_quick("left", 2, hammer)                      # one quick slot each: it moves
        self.assertEqual(self.bag.gear().quick, {4: rifle, 5: hammer})
        self.assertIsNone(self.bag.gear().held_eid("right"))

    def test_armor_and_what_may_be_worn(self) -> None:
        self.bag.add("armor", WHITE_STEEL + "_G", 1, level=20)    # +24
        steel = self.eid_of(WHITE_STEEL + "_G")
        self.assertEqual(self.catalog.requirement(WHITE_STEEL + "_G", 20), ("HP", 9614))
        weak = self.wearer(hp=22)                                  # an All-rounder G6 at HP level 22 has 9,310
        self.assertEqual(self.bag.problem(steel, "EQSITE_BODY", weak), "it needs HP 9,614 and Sibyl has 9,310")
        self.assertEqual(self.bag.problem(steel, "EQSITE_HEAD"), "it does not go there")
        with self.assertRaises(EditError):
            self.bag.set_armor("EQSITE_BODY", steel, weak)
        strong = self.wearer(hp=23)                                # 9,620
        self.assertEqual(self.bag.problem(steel, "EQSITE_BODY", strong), "")
        self.assertEqual(self.bag.problem(steel, "EQSITE_BODY", B.Wearer("Sibyl", "BAL", 6, weak.stats,
                                                                         ["SKL_DISCOUNT_HP"])), "")  # half: 4,807
        self.bag.set_armor("EQSITE_BODY", steel, strong)
        self.assertEqual(self.bag.gear().armor, {"EQSITE_BODY": steel})
        self.save.get(("part", "pts", self.save.uid))[-1]["dur"] = 0
        self.assertEqual(self.bag.problem(steel, "EQSITE_BODY", strong), "it is broken")
        self.assertEqual(self.bag.take_off({steel}), 1)
        self.assertEqual(self.bag.gear().armor, {})

    def test_decal_discounts_add_up(self) -> None:
        self.assertAlmostEqual(self.rules.rate("HP", []), 1.0)
        self.assertAlmostEqual(self.rules.rate("HP", ["SKL_DISCOUNT_HP"]), 0.5)
        self.assertAlmostEqual(self.rules.rate("HP", ["SKL_DISCOUNT_HP", "SKL_DISCOUNT_ALL_P"]), 0.3, places=5)
        self.assertAlmostEqual(self.rules.rate("DEX", ["SKL_DISCOUNT_HP"]), 1.0)
        self.assertAlmostEqual(self.rules.rate("STR", ["SKL_DISCOUNT_STR", "SKL_DISCOUNT_STR_P"]), 0.0)
        # oberlinx's in-game numbers: a 27 DEX gun is 21 with Everything's On Sale (80%), 2 at 90% off
        self.assertEqual(int(27 * self.rules.rate("DEX", ["SKL_DISCOUNT_ALL_P"])), 21)
        self.assertEqual(int(27 * self.rules.rate("DEX", ["SKL_DISCOUNT_DEX", "SKL_DISCOUNT_ALL_P",
                                                          "SKL_DISALL_ATKUP_HPDWN_P"])), 2)

    def test_taking_out_a_worn_piece_and_looking_changes_nothing(self) -> None:
        untouched = SaveDocument.read(TEMPLATE)
        bag = B.Bag(untouched, self.catalog, self.rules, self.fighter.cid, 20, "Sibyl", self.fighter.chr_path)
        bag._put(bag.gear())
        self.assertEqual(untouched.encoded(), untouched.payload)
        self.bag.add("weapon", HAMMER, 1)
        hammer = self.eid_of(HAMMER)
        self.bag.set_quick("right", 0, hammer)
        self.bag.set_held("right", 0)
        self.assertEqual(self.bag.remove([0], "the hammer"), 1)
        self.assertEqual(self.bag.gear().quick, {})
        self.assertNotIn(hammer, [r["eid"] for r in self.save.get(("part", "pts", self.save.uid))])
        self.assertTrue(self.save.to_bytes())

    def test_the_express_pass_adds_room_while_it_runs(self) -> None:
        self.assertEqual(self.rules.vip_extra(self.save), 0)
        self.save.doc["soul"]["vip"].update(flag=1, expired_time=2_000_000_000)
        self.assertEqual(self.rules.vip_extra(self.save, now=1_900_000_000), 10)
        self.assertEqual(self.rules.vip_extra(self.save, now=2_100_000_000), 0)

    def test_the_games_whole_number_growth(self) -> None:
        self.assertEqual(S.calc_part_param(1, 1400, 105), 1400)
        self.assertEqual(S.calc_part_param(5, 1400, 105), 1701)
        self.assertEqual(S.calc_part_param(20, 6000, 102), 8740)              # 6000 x 1.02^19 = 8740.9
        for ptid, row in list(self.catalog.parts.items())[::7]:
            for level in (1, 2, 5, 12, 20):
                self.assertEqual(self.catalog.full_durability(ptid, level),
                                 int(float(row["dur"] or 0) * float(row["dur_c"] or 1) ** (level - 1) + 1e-6)
                                 if float(row["dur"] or 0) > 0 else 0, (ptid, level))

    @unittest.skipUnless(REAL_SAVES.is_dir(), "needs the real save folder")
    def test_every_real_bag_reads_and_its_gear_passes(self) -> None:
        rules = savefighters.Rules.read(VANILLA_DB)
        for path in sorted(REAL_SAVES.glob(f"*/{savegame.SAVE_NAME}")):
            save = SaveDocument.read(path)
            for f in savefighters.fighters(save):
                if not isinstance(save.get(("soul", "deathbag", save.uid, f.cid)), list):
                    continue
                bag = B.Bag(save, self.catalog, self.rules, f.cid,
                            f.values(rules)["death_bag"] + self.rules.vip_extra(save), f.name, f.chr_path)
                wearer = B.Wearer(f.name, f.kind, f.grade, f.stats, B.worn_decals(save, f.cid))
                self.assertEqual(bag.problems(wearer), [], f.name)
                self.assertFalse([g for g in bag.groups() if g.ident.startswith("missing:")])
                bag._put(bag.gear())
            self.assertEqual(save.encoded(), save.payload)


@unittest.skipUnless(HAVE_QT and VANILLA_DB.is_file() and TEMPLATE.is_file(), "needs PySide6 and the data")
class ThePanel(unittest.TestCase):
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
        self.panel = self.dialog.bag_panel

    def add(self, kind: str, name: str, level: int | None = None) -> None:
        form = self.panel.form
        form.add_kind.setCurrentIndex(form.add_kind.findData(kind))
        form.add_thing.setCurrentIndex(form.add_thing.findText(name))
        if level is not None:
            form.add_level.setValue(level)
        with mock.patch.object(QMessageBox, "information"):
            self.panel.add_picked()

    def test_putting_in_holding_and_saving(self) -> None:
        panel = self.panel
        self.assertTrue(panel.body.isEnabled())
        self.add("weapon", "Iron Hammer", 0)
        self.assertEqual(panel.table.rowCount(), 1)
        combo = panel.quick[("right", 0)]
        combo.setCurrentIndex(combo.findText("Iron Hammer +0 (100%)"))
        panel.held["right"].button(0).click()
        self.assertEqual(panel.table.item(0, 3).text(), "in the right hand")
        self.assertEqual(self.dialog.changes_label.text(), "3 value(s) changed, not saved yet")
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes) as asked, \
                mock.patch.object(QMessageBox, "information"):
            self.dialog.save()
        self.assertIn("Sibyl's Death Bag: right hand: (empty) -> Iron Hammer +0", asked.call_args.args[2])
        saved = SaveDocument.read(self.manager.library.get(self.entry.id).save)
        (f,) = savefighters.fighters(saved)
        bag = B.Bag(saved, self.panel.catalog, self.panel.rules, f.cid, 20, f.name, f.chr_path)
        self.assertEqual(bag.describe(bag.gear()), "Right hand Iron Hammer +0")

    def test_gear_follows_the_stats_on_the_page(self) -> None:
        self.add("armor", "White Steel Body", 24)
        body = self.panel.armor["EQSITE_BODY"]
        steel = next(i for i in range(body.count()) if "White Steel" in body.itemText(i))
        self.assertIn("it needs HP", body.itemText(steel))
        self.assertFalse(body.model().item(steel).isEnabled())
        self.dialog.max_fighter()                                   # stats up on the Stats tab
        steel = next(i for i in range(body.count()) if "White Steel" in body.itemText(i))
        self.assertTrue(body.model().item(steel).isEnabled())

    def test_a_dead_fighters_bag_is_only_looked_at(self) -> None:
        self.dialog.fighters[next(iter(self.dialog.fighters))].state = "ENEMY"
        self.dialog._show_fighter()
        self.assertFalse(self.panel.body.isEnabled())
        self.assertIn("Hater", self.panel.lock.text())


if __name__ == "__main__":
    unittest.main()
