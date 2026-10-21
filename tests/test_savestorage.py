"""The Storage Box page: things put in and taken out the way the game stores them (savestorage.py).

What matters: what is in the box is read through its slots into the four record
tables, grouped by thing, level and grilled or not; a thing put in is a record
like the game's own - a weapon or armor at full durability for its level with
full ammo, a beast with the mushroom it carries - in the first free slot, and
the empty {} a new game has for a table becomes a list; what is taken out has
its record removed (the beast's mushroom too), the most worn gear first; the
box grows in the game's steps up to its most and only ever loses empty slots;
upgrade levels read as the game shows them (saved level + is_limitbreak - 1,
no higher than PART_LIMITBREAK_OFFSET_MAX, as the exe works it out), a part and
its uncapped form offered as one; nothing is changed by only looking; and the page writes it all
through Save changes.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_savelibrary import STEAM

from lid_db_manager import savegame, savestorage as S
from lid_db_manager.manager import Manager
from lid_db_manager.paths import AppPaths
from lid_db_manager.saveedit import EditError, SaveDocument

PROJECT = Path(__file__).resolve().parent.parent
VANILLA_DB = PROJECT / "LiD Vanilla DB" / "5.0.4.2" / "masters.db"
TEMPLATE = PROJECT / "lid_db_manager" / "templates" / "new-save-after-tutorial.sav"
REAL_SAVES = Path(os.environ.get("LID_SAVEDATA", r"E:\SteamLibrary\steamapps\common\LET IT DIE\Savedata"))
HAMMER = "PT_ARM_WP011_001"          # Iron Hammer: +0 to +4, 1,400 durability, x1.05 a level
UNCAPPED = "PT_ARM_WP024_005"        # goes on from PT_ARM_WP024_004 (+4): +5 to +24
WHITE_STEEL = "PT_DIY_TOPS_021"      # White Steel Body; its uncapped form PT_DIY_TOPS_021_G has the same name

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtWidgets import QApplication, QMessageBox

    HAVE_QT = True
except ImportError:  # pragma: no cover - depends on the environment
    HAVE_QT = False


def fresh() -> SaveDocument:
    return SaveDocument.read(TEMPLATE)


@unittest.skipUnless(VANILLA_DB.is_file() and TEMPLATE.is_file(), "needs the vanilla database and the template")
class TheBox(unittest.TestCase):
    catalog = None

    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = S.Catalog.read(VANILLA_DB)

    def setUp(self) -> None:
        self.save = fresh()
        self.box = S.Box(self.save, self.catalog)

    def test_a_new_game_has_an_empty_box_of_30(self) -> None:
        self.assertEqual((self.box.capacity, self.box.used(), self.box.groups()), (30, 0, []))
        self.assertEqual(self.save.encoded(), self.save.payload)               # looking changes nothing

    def test_names_and_levels_as_the_game_shows_them(self) -> None:
        cat = self.catalog
        self.assertEqual(cat.part_name(HAMMER), "Iron Hammer")
        self.assertEqual(cat.level_range(HAMMER), (0, 4))
        self.assertEqual(cat.level_range(UNCAPPED), (5, 24))
        self.assertEqual((cat.shown_level(UNCAPPED, 1), cat.raw_level(UNCAPPED, 24)), (5, 20))
        self.assertEqual((cat.shown_level(HAMMER, 5), cat.raw_level(HAMMER, 4)), (4, 5))
        self.assertEqual(cat.full_durability(HAMMER, 1), 1400)
        self.assertEqual(cat.full_durability(HAMMER, 5), 1701)               # 1400 x 1.05^4, rounded down
        self.assertEqual(cat.item_name("ITMP_ARM_WP001_001U"), "Blueprint: Jungle Machete (unrevealed)")
        self.assertEqual((cat.mushroom_name("MSR_001"), cat.mushroom_name("MSR_001", True)),
                         ("Heartshroom", "Grilled Heart Shroom"))
        self.assertEqual(cat.beast_name("BST_BASS", True), "Grilled Fish")
        self.assertEqual((cat.capacity_step(), cat.capacity_max()), (10, 2000))

    def test_a_part_and_its_uncapped_form_are_one_choice(self) -> None:
        # White Steel Body is two rows with one name: +0 to +4, and its uncapped form (+5 to +24)
        cat = self.catalog
        self.assertEqual(cat.uncapped_form(WHITE_STEEL), WHITE_STEEL + "_G")
        self.assertEqual([t.ident for t in cat.things("armor") if t.name == "White Steel Body"], [WHITE_STEEL])
        self.assertEqual(cat.level_range(WHITE_STEEL), (0, 24))
        self.assertEqual(cat.part_at(WHITE_STEEL, 4), (WHITE_STEEL, 5))
        self.assertEqual(cat.part_at(WHITE_STEEL, 24), (WHITE_STEEL + "_G", 20))
        self.assertEqual(cat.part_at(WHITE_STEEL, 5), (WHITE_STEEL + "_G", 1))
        self.assertEqual(cat.level_range(HAMMER), (0, 4))                       # upgrades into another part
        ptid, raw = cat.part_at(WHITE_STEEL, 24)
        self.box.add("armor", ptid, 1, level=raw)
        (group,) = self.box.groups()
        self.assertEqual((group.name, group.detail), ("White Steel Body", "+24"))

    def test_levels_follow_the_games_own_sum_when_a_mod_changes_the_numbers(self) -> None:
        cat = S.Catalog.read(VANILLA_DB)
        cat.parts[UNCAPPED] = {**cat.parts[UNCAPPED], "is_limitbreak": 3, "reflvllmt": 40}
        self.assertEqual(cat.level_range(UNCAPPED), (3, 24))                   # starts at +3, stops at +24
        self.assertEqual(cat.max_level(UNCAPPED), 22)
        cat.consts["PART_LIMITBREAK_OFFSET_MAX"] = 30
        self.assertEqual(cat.level_range(UNCAPPED), (3, 30))
        cat.parts[HAMMER] = {**cat.parts[HAMMER], "reflvllmt": 10}
        self.assertEqual(cat.level_range(HAMMER), (0, 9))

    def test_a_weapon_goes_in_as_the_game_writes_one(self) -> None:
        self.assertEqual(self.box.add("weapon", HAMMER, 2, level=5, now=1_800_000_000), 2)
        rows = self.save.get(("part", "pts", self.save.uid))
        self.assertIsInstance(rows, list)                                      # the game's {} became a list
        new = rows[-1]
        self.assertEqual({k: new[k] for k in ("owner", "ptid", "lvl", "dur", "rest", "spare", "grade", "gettime")},
                         {"owner": "COIN_LOCKER", "ptid": HAMMER, "lvl": 5, "dur": 1701, "rest": 0, "spare": 0,
                          "grade": 0, "gettime": 1_800_000_000})
        self.assertEqual(len(new["eid"]), 36)
        self.assertEqual(self.save.get(("soul", "cl", 1)), {"slot": 1, "type": 0, "eid": new["eid"]})
        (group,) = self.box.groups()
        self.assertEqual((group.name, group.detail, group.count), ("Iron Hammer", "+4", 2))
        self.assertEqual([(c.label, c.after) for c in self.save.changes()],
                         [("Storage Box: added Iron Hammer +4", "2")])
        self.assertTrue(self.save.to_bytes())

    def test_items_blueprints_and_mushrooms(self) -> None:
        self.box.add("item", "ITMT_FIBER_2", 3)
        self.box.add("blueprint", "ITMP_ARM_WP001_001", 1, unrevealed=True)
        self.box.add("mushroom", "MSR_001", 1, grilled=True)
        names = {(g.name, g.count) for g in self.box.groups()}
        self.assertEqual(names, {("Hemp", 3), ("Blueprint: Jungle Machete (unrevealed)", 1),
                                 ("Grilled Heart Shroom", 1)})
        item = self.save.get(("item", "items"))[0]
        self.assertEqual(set(item), {"eid", "gettime", "itemid", "owner"})     # just what the game writes

    def test_a_beast_comes_with_the_mushroom_it_carries(self) -> None:
        self.box.add("beast", "BST_FROG", 2)
        beasts, mushrooms = self.save.get(("beast", "bsts")), self.save.get(("mushroom", "msrs"))
        self.assertEqual([m["msrid"] for m in mushrooms], ["MSR_031", "MSR_031"])
        self.assertEqual({m["owner"] for m in mushrooms}, {"USER"})
        self.assertEqual({b["rwdemsrid"] for b in beasts}, {m["eid"] for m in mushrooms})
        (frogs,) = self.box.groups()
        self.assertEqual(self.box.take(frogs.key, 1), 1)
        self.assertEqual((len(self.save.get(("beast", "bsts"))), len(self.save.get(("mushroom", "msrs")))), (1, 1))

    def test_the_most_worn_goes_first(self) -> None:
        self.box.add("weapon", HAMMER, 2, level=1)
        rows = self.save.get(("part", "pts", self.save.uid))
        rows[0]["dur"] = 10
        worn = rows[0]["eid"]
        (group,) = self.box.groups()
        self.box.take(group.key, 1)
        self.assertNotIn(worn, [r["eid"] for r in self.save.get(("part", "pts", self.save.uid))])
        self.assertEqual(self.box.free_slots()[0], 0)

    def test_repair_and_refill_only_raises(self) -> None:
        self.box.add("weapon", "PT_ARM_WP017_001", 1)                          # a gun: 6 loaded, 36 spare
        row = self.save.get(("part", "pts", self.save.uid))[0]
        row.update(dur=5, rest=1, spare=2)
        self.assertEqual(self.box.repair_all(), 1)
        self.assertEqual((row["rest"], row["spare"]), (6, 36))
        self.assertEqual(row["dur"], self.catalog.full_durability(row["ptid"], 1))
        self.assertEqual(self.box.repair_all(), 0)

    def test_the_box_grows_in_the_games_steps_and_only_loses_empty_slots(self) -> None:
        self.assertEqual(self.box.set_capacity(105), 105)
        self.assertEqual(self.box.set_capacity(5000), 2000)
        self.box.add("item", "ITMT_FIBER_2", 1)
        self.save.doc["soul"]["cl"][42], self.save.doc["soul"]["cl"][0] = (
            {"slot": 42, **{k: v for k, v in self.save.doc["soul"]["cl"][0].items() if k != "slot"}},
            {"slot": 0, "type": -1, "eid": ""})
        self.assertEqual(self.box.set_capacity(30), 50)                        # slot 42 is used: to the next step
        self.assertEqual(self.box.used(), 1)
        self.assertTrue(all(s["slot"] == i for i, s in enumerate(self.save.doc["soul"]["cl"])))

    def test_a_full_box(self) -> None:
        self.assertEqual(self.box.add("item", "ITMT_FIBER_2", 40), 30)
        self.assertEqual(self.box.add("item", "ITMT_FIBER_2", 1), 0)

    def test_only_what_the_game_has(self) -> None:
        with self.assertRaises(EditError):
            self.box.add("weapon", "PT_NOT_A_THING", 1)
        self.box.add("mushroom", "MSR_051", 1, grilled=True)                    # not roastable: raw
        if not self.catalog.mushrooms["MSR_051"].get("enable_roast"):
            self.assertEqual(self.box.groups()[0].detail, "")

    def test_the_lists_offered(self) -> None:
        weapons = self.catalog.things("weapon")
        self.assertTrue(any(t.ident == HAMMER for t in weapons))
        self.assertFalse(any(t.kind != "weapon" for t in weapons))
        offered = {t.ident for t in weapons + self.catalog.things("armor")}
        for never in ("PT_ARM_WP000_001", "PT_NONE_HEAD_001", "PT_GAS_HEAD_001"):   # Fists, None, a gas mask
            self.assertNotIn(never, offered)
        self.assertIn("PT_ARM_WP001_0N3", offered)                 # Beam Katana 2: no blueprint, reached by upgrading
        self.assertTrue(all(t.ident.startswith("ITMP_") and not t.ident.endswith("U")
                            for t in self.catalog.things("blueprint")))
        self.assertTrue(any(t.platform == 1 for t in self.catalog.things("armor")))   # PS-only marked

    @unittest.skipUnless(REAL_SAVES.is_dir(), "needs the real save folder")
    def test_a_real_box_reads_whole(self) -> None:
        for path in sorted(REAL_SAVES.glob(f"*/{savegame.SAVE_NAME}")):
            save = SaveDocument.read(path)
            box = S.Box(save, self.catalog)
            groups = box.groups()
            self.assertEqual(sum(g.count for g in groups), box.used())
            self.assertFalse([g for g in groups if g.ident.startswith("missing:")])
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
        shutil.copy2(TEMPLATE, source)
        self.entry = self.manager.import_save(source, STEAM, "New game")
        self.dialog = SaveEditorDialog(self.manager, self.entry.id)
        self.addCleanup(self.dialog.deleteLater)
        self.page = self.dialog.storage

    def add(self, kind: str, name: str, count: int = 1) -> None:
        page = self.page
        page.add_kind.setCurrentIndex(page.add_kind.findData(kind))
        page.add_thing.setCurrentIndex(page.add_thing.findText(name))
        page.add_count.setValue(count)
        with mock.patch.object(QMessageBox, "information"):
            page.add_picked()

    def test_putting_in_taking_out_and_saving(self) -> None:
        page = self.page
        self.assertEqual(page.used_label.text(), "0 of 30 used")
        self.add("weapon", "Iron Hammer", 2)
        self.assertEqual(page.add_level.maximum(), 4)
        self.add("item", "Hemp", 5)
        self.assertEqual(page.used_label.text(), "7 of 30 used")
        self.assertEqual(self.dialog.changes_label.text(), "2 value(s) changed, not saved yet")
        page.table.selectRow(next(r for r, g in enumerate(page.groups) if g.name == "Hemp"))
        page.take_count.setValue(2)
        page.take_picked()
        self.assertEqual(page.used_label.text(), "5 of 30 used")
        page.undo_last()
        self.assertEqual(page.used_label.text(), "7 of 30 used")
        self.assertEqual(SaveDocument.read(self.manager.library.get(self.entry.id).save).encoded(),
                         fresh().encoded())                                   # nothing written yet
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes) as asked, \
                mock.patch.object(QMessageBox, "information"):
            self.dialog.save()
        self.assertIn("Storage Box: added Iron Hammer +4: ", asked.call_args.args[2])
        saved = S.Box(SaveDocument.read(self.manager.library.get(self.entry.id).save), page.catalog)
        self.assertEqual(sorted((g.name, g.count) for g in saved.groups()), [("Hemp", 5), ("Iron Hammer", 2)])

    def test_plus_24_armor_goes_in_at_plus_24(self) -> None:
        page = self.page
        page.add_kind.setCurrentIndex(page.add_kind.findData("armor"))
        page.add_thing.setCurrentIndex(page.add_thing.findText("White Steel Body"))
        self.assertEqual((page.add_level.minimum(), page.add_level.maximum()), (0, 24))
        page.add_level.setValue(24)
        with mock.patch.object(QMessageBox, "information"):
            page.add_picked()
        (group,) = page.groups
        self.assertEqual((group.ident, group.detail), (WHITE_STEEL + "_G", "+24"))

    def test_playstation_only_things_follow_the_saves_own_data(self) -> None:
        from lid_db_manager.ui.storage_page import AddForm

        ps_part = "PT_ARM_WP017_0A1"                            # PlayStation-only until the Crossover pack
        self.assertEqual(self.page.catalog.parts[ps_part]["platform"], 1)
        form = AddForm(self.page.catalog)
        self.addCleanup(form.deleteLater)
        form.add_kind.setCurrentIndex(form.add_kind.findData("weapon"))
        offered = {form.add_thing.itemData(i) for i in range(form.add_thing.count())}
        self.assertNotIn(ps_part, offered)
        modded = S.Catalog.read(VANILLA_DB)
        modded.parts[ps_part] = {**modded.parts[ps_part], "platform": 0}    # as the pack makes it
        form = AddForm(modded)
        self.addCleanup(form.deleteLater)
        form.add_kind.setCurrentIndex(form.add_kind.findData("weapon"))
        self.assertIn(ps_part, {form.add_thing.itemData(i) for i in range(form.add_thing.count())})

    def test_the_search(self) -> None:
        self.add("item", "Hemp", 1)
        self.add("weapon", "Iron Hammer", 1)
        self.page.search.setText("hammer")
        shown = [self.page.groups[r].name for r in range(self.page.table.rowCount())
                 if not self.page.table.isRowHidden(r)]
        self.assertEqual(shown, ["Iron Hammer"])


if __name__ == "__main__":
    unittest.main()
