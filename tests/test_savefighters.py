"""The Fighters page: a save's fighters changed by the game's own rules (savefighters.py).

What matters - each rule read out of the game's scripts and checked against
a real save: the limit break is worked out from the stats (and the save's own
set to match), never set on its own; a stat's bonus raises its cap at limit
break 0 and is never more than the step to limit break 1; decal slots, Death
Bag and rage bars are bought only past limit break 0; the level is worked out
again as the game counts it; a value not changed is never cut, while a new
class or grade fits everything to it; the dead, kidnapped, a fighter out on a
run, and (beyond name and look) one on defense are left alone; a model of the
other gender gets a gas mask that fits it; Max takes a fighter to the top; and
an untouched save still writes back byte for byte.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_savelibrary import STEAM, UID

from lid_db_manager import savecheck, savefighters as F, savegame
from lid_db_manager.manager import Manager
from lid_db_manager.paths import AppPaths
from lid_db_manager.saveedit import EditError, SaveDocument

PROJECT = Path(__file__).resolve().parent.parent
VANILLA_DB = PROJECT / "LiD Vanilla DB" / "5.0.4.2" / "masters.db"

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtWidgets import QApplication, QMessageBox

    HAVE_QT = True
except ImportError:  # pragma: no cover - depends on the environment
    HAVE_QT = False


def chara(cid: str, name: str, kind: str, grade: int, lb: int = 0, state: str = "FREE", **more) -> dict:
    return {"uid": UID, "cid": cid, "body": more.pop("body", "BODY_FEMALE_003"),
            "gasmask": more.pop("gasmask", "ASSET_NF_GAS_HEAD_003"), "type": kind, "grade": grade,
            "limit_break": lb, "hp": 999999, "pause": "", "abid": more.pop("abid", ""), "name": name,
            "state": state, **more}


def levels(cid: str, stats, skill=0, bag=0, rage=0, bonus=(0,) * 6) -> dict:
    row = {"uid": UID, "cid": cid}
    row.update(dict(zip(("hp", "str", "dex", "vit", "stm", "luk"), stats)))
    row.update({"skill": skill, "bag": bag, "rage": rage})
    row.update({f"{k}_bonus": b for k, b in zip(("hp", "str", "dex", "vit", "stm", "luk"), bonus)})
    row["lvl"] = F.level_of(row, skill, bag, rage)
    return row


def a_save(in_run: bool = False) -> SaveDocument:
    roster = [
        chara("c-new", "Rookie", "BAL", 1),
        chara("c-max", "Ace", "BAL", 6, 4, state="USE"),
        chara("c-over", "Donna", "SHT", 3),
        chara("c-dead", "Erica", "BRE", 2, state="ENEMY"),
        chara("c-guard", "Helen", "BRE", 5, state="GUARD"),
        chara("c-taken", "Kim", "TEC", 4, abid="someone"),
    ]
    bodies = [
        levels("c-new", [1] * 6),
        levels("c-max", [45] * 6, skill=4, bag=12, bonus=(0, 0, 3, 0, 2, 0)),
        levels("c-over", [14, 13, 13, 13, 13, 13], bonus=(1, 0, 0, 0, 0, 0)),   # 14 = cap 13 + its bonus
        levels("c-dead", [9] * 6),
        levels("c-guard", [1] * 6),
        levels("c-taken", [5] * 6),
    ]
    doc = {"user": {"uid": UID, "nm": "Fighter"},
           "soul": {"uid": UID, "pause": "{\"x\":1}" if in_run else "",
                    "chr": {"chrs": {str(UID): roster}}},
           "bodyuser": {str(UID): bodies},
           "fortzmbsetting": [{"wave": 0, "order": 1, "cid": "c-guard", "body": "BODY_FEMALE_003", "grade": 5,
                               "type": "BRE", "lvl": 1, "max_limit_break": 0, "is_equip_whistle": 0}]}
    return SaveDocument(savegame.write_container(2, json.dumps(doc, separators=(",", ":")).encode()))


def by_name(save: SaveDocument, name: str) -> F.Fighter:
    return next(f for f in F.fighters(save) if f.name == name)


@unittest.skipUnless(VANILLA_DB.is_file(), "needs the vanilla database")
class TheRules(unittest.TestCase):
    rules = None

    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = F.Rules.read(VANILLA_DB)

    def test_the_limit_break_comes_from_the_stats(self) -> None:
        r, zero = self.rules, {k: 0 for k in F.STAT_LABELS}
        self.assertEqual([r.limit_break_of("BAL", 6, {k: n for k in F.STAT_LABELS}, zero)
                          for n in (25, 26, 30, 31, 41, 45)], [0, 1, 1, 2, 4, 4])
        one_high = {**{k: 1 for k in F.STAT_LABELS}, "str": 36}
        self.assertEqual(r.limit_break_of("BAL", 6, one_high, zero), 3)        # the highest stat decides
        donna = {"hp": 14, "str": 13, "dex": 13, "vit": 13, "stm": 13, "luk": 13}
        self.assertEqual(r.limit_break_of("SHT", 3, donna, {**zero, "hp": 1}), 0)   # 14 is her bonus
        self.assertEqual(r.limit_break_of("SHT", 3, donna, zero), 1)
        # what was bought counts as levels too: a decal slot past what grade 4 limit break 1 gives
        self.assertEqual(r.limit_break_of("COL", 4, {k: 20 for k in F.STAT_LABELS}, zero, skill=1), 1)
        self.assertEqual(r.limit_break_of("COL", 4, {k: 20 for k in F.STAT_LABELS}, zero, skill=2), 0)

    def test_the_limits_at_a_limit_break(self) -> None:
        stock = self.rules.limits("BAL", 6, {k: 25 for k in F.STAT_LABELS}, {k: 0 for k in F.STAT_LABELS})
        self.assertEqual((stock.limit_break, stock.stat_top, stock.bonus_max), (0, 45, 5))
        self.assertEqual([stock.total_range(w) for w in F.BOUGHT], [(5, 5), (24, 24), (5, 5)])   # none to buy
        top = self.rules.limits("BAL", 6, {k: 45 for k in F.STAT_LABELS}, {k: 0 for k in F.STAT_LABELS})
        self.assertEqual([top.total_range(w) for w in F.BOUGHT], [(5, 9), (24, 36), (5, 5)])
        ski = self.rules.limits("SKI", 6, {k: 45 for k in F.STAT_LABELS}, {k: 0 for k in F.STAT_LABELS})
        self.assertEqual(ski.total_range("rage"), (5, 8))
        self.assertEqual(self.rules.stat_cap("SHT", 3, 0, 1), 14)              # cap 13 + a bonus of 1
        self.assertEqual(self.rules.stat_cap("SHT", 3, 1, 1), 15)              # uncapped: the bonus is gone

    def test_classes_grades_bodies_and_masks(self) -> None:
        self.assertNotIn("OLD", self.rules.classes())
        self.assertEqual(len(self.rules.classes()), 8)
        self.assertEqual((self.rules.max_grade("BAL"), self.rules.max_limit_break("BAL", 6),
                          self.rules.max_limit_break("BAL", 1)), (6, 4, 1))
        self.assertEqual(len(self.rules.bodies), 16)
        self.assertTrue(all("NM" in m for m in self.rules.masks_for("BODY_MALE_002")))
        self.assertEqual(F.body_label("BODY_MALE_002", self.rules), "Male 2")

    def test_the_fighters_and_their_level(self) -> None:
        save = a_save()
        ace = by_name(save, "Ace")
        self.assertEqual((ace.level, ace.skill, ace.bag), (281, 4, 12))
        self.assertEqual(ace.values(self.rules)["decal_slots"], 9)             # 5 free + 4 bought
        self.assertEqual(ace.bonus["dex"], 3)


@unittest.skipUnless(VANILLA_DB.is_file(), "needs the vanilla database")
class Changing(unittest.TestCase):
    rules = None

    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = F.Rules.read(VANILLA_DB)

    def setUp(self) -> None:
        self.save = a_save()

    def change(self, who: str, **values) -> F.Fighter:
        F.set_fighter(self.save, self.rules, by_name(self.save, who).cid, values)
        return by_name(self.save, values.get("name", who))

    def test_a_stat_and_the_level_follows(self) -> None:
        rookie = self.change("Rookie", str=1)                                  # the same: nothing
        self.assertEqual(self.save.changes(), [])
        rookie = self.change("Rookie", grade=6, str=25)
        self.assertEqual((rookie.grade, rookie.stats["str"], rookie.level), (6, 25, 25))
        self.assertIn("Rookie: Level", [c.label for c in self.save.changes()])

    def test_held_to_the_range(self) -> None:
        rookie = self.change("Rookie", str=99, luk=-3)
        self.assertEqual((rookie.stats["str"], rookie.stats["luk"]), (6, 1))   # BAL G1 tops out at 6 (LB 1)

    def test_raising_a_stat_past_the_cap_uncaps(self) -> None:
        rookie = self.change("Rookie", grade=6, str=31)
        self.assertEqual((rookie.limit_break, rookie.level), (2, 31))
        self.assertIn("Rookie: Limit Break", [c.label for c in self.save.changes()])
        rookie = self.change("Rookie", str=20)
        self.assertEqual(rookie.limit_break, 0)                                 # and back

    def test_a_bonus_counts_only_at_limit_break_0(self) -> None:
        donna = self.change("Donna", str=12)
        self.assertEqual((donna.stats["hp"], donna.stats["str"], donna.limit_break), (14, 12, 0))
        donna = self.change("Donna", hp_bonus=0)                                # 14 is now past the cap
        self.assertEqual(donna.limit_break, 1)
        donna = self.change("Donna", grade=1)
        self.assertEqual(donna.stats["hp"], 6)                                 # a new grade fits it

    def test_a_bonus_is_never_more_than_the_step_to_limit_break_1(self) -> None:
        rookie = self.change("Rookie", grade=6, hp_bonus=3, str_bonus=50)
        self.assertEqual((rookie.bonus["hp"], rookie.bonus["str"]), (3, 5))    # BAL G6: 25 -> 30
        self.assertEqual(rookie.level, 1)                                      # a bonus is not a level

    def test_only_a_bonus_the_purchase_roll_can_give(self) -> None:
        # 0/5/10/15/20 percent of the grade's limit break 0 cap, rounded down - the class plays no part
        self.assertEqual([self.rules.bonus_choices("BAL", g) for g in (1, 3, 5, 6)],
                         [(0, 1), (0, 1, 2), (0, 1, 2, 3, 4), (0, 1, 2, 3, 5)])
        self.assertEqual(self.rules.bonus_choices("COL", 6), self.rules.bonus_choices("BAL", 6))
        self.assertEqual(self.rules.bonus_odds, {0: 50, 5: 20, 10: 15, 15: 10, 20: 5})
        rookie = self.change("Rookie", grade=6, dex_bonus=4)
        self.assertEqual(rookie.bonus["dex"], 3)                               # 4 cannot be rolled at grade 6

    def test_bought_only_past_limit_break_0(self) -> None:
        rookie = self.change("Rookie", grade=6, decal_slots=9, death_bag=36)
        self.assertEqual((rookie.skill, rookie.bag), (0, 0))                   # at limit break 0: none
        maxed = {k: 45 for k in F.STAT_LABELS}
        rookie = self.change("Rookie", decal_slots=9, death_bag=36, **maxed)
        self.assertEqual((rookie.skill, rookie.bag, rookie.limit_break, rookie.level), (4, 12, 4, 281))

    def test_the_ones_left_alone(self) -> None:
        for name, why in (("Erica", "dead"), ("Kim", "kidnapped")):
            with self.subTest(name=name), self.assertRaises(EditError) as caught:
                self.change(name, str=2)
            self.assertIn(why, str(caught.exception))
        in_run = a_save(in_run=True)
        with self.assertRaises(EditError) as caught:
            F.set_fighter(in_run, self.rules, "c-max", {"str": 40})
        self.assertIn("on a run", str(caught.exception))

    def test_on_defense_only_the_name_and_look(self) -> None:
        with self.assertRaises(EditError) as caught:
            self.change("Helen", grade=6)
        self.assertIn("on defense", str(caught.exception))
        helen = self.change("Helen", name="Helena", body="BODY_MALE_004")
        self.assertEqual((helen.name, helen.body, helen.gasmask), ("Helena", "BODY_MALE_004",
                                                                    "ASSET_NM_GAS_HEAD_003"))
        self.assertEqual(self.save.get(("fortzmbsetting", 0, "body")), "BODY_MALE_004")
        self.assertEqual(self.save.get(("fortzmbsetting", 0, "grade")), 5)

    def test_a_name_must_fit(self) -> None:
        for bad in ("", "x" * 17, "tab\there"):
            with self.subTest(name=bad), self.assertRaises(EditError):
                self.change("Rookie", name=bad)

    def test_max_all(self) -> None:
        done, skipped = F.max_all(self.save, self.rules)
        self.assertEqual(done, ["Rookie", "Ace", "Donna"])
        self.assertEqual(len(skipped), 3)
        for name in done:
            f = by_name(self.save, name)
            self.assertEqual((f.grade, f.limit_break, f.stats["luk"]), (6, 4, 45))
        self.assertEqual(by_name(self.save, "Rookie").level, 281)
        self.assertEqual([c for c in self.save.changes() if c.label.startswith("Ace")], [])   # already there
        self.assertEqual(by_name(self.save, "Ace").bonus["dex"], 3)                            # bonuses kept
        self.assertTrue(self.save.to_bytes())

    def test_an_untouched_save_still_writes_back_byte_for_byte(self) -> None:
        for f in F.fighters(self.save):
            if not F.lock_reason(f, self.save):
                F.set_fighter(self.save, self.rules, f.cid, f.values(self.rules))
        self.assertEqual(self.save.encoded(), self.save.payload)

    def test_the_check_sees_fighters_past_the_clean_game(self) -> None:
        self.save.set(("soul", "chr", "chrs", str(UID), 0, "grade"), 9)
        found = savecheck.check(self.save, VANILLA_DB, [])
        self.assertIn("1 fighter(s) past the clean game's grades", found.lines()[0])
        self.assertTrue(savecheck.check(a_save(), VANILLA_DB, []).clean)       # a bonus over a cap is not


@unittest.skipUnless(HAVE_QT and VANILLA_DB.is_file(), "needs PySide6 and the vanilla database")
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
        source = root / "fighters.sav"
        source.write_bytes(a_save().raw)
        self.entry = self.manager.import_save(source, STEAM, "Fighters")
        self.dialog = SaveEditorDialog(self.manager, self.entry.id)
        self.addCleanup(self.dialog.deleteLater)

    def pick(self, name: str) -> None:
        for row in range(self.dialog.fighter_list.count()):
            if self.dialog.fighter_list.item(row).text().startswith(name + " "):
                self.dialog.fighter_list.setCurrentRow(row)
                return
        self.fail(name)

    def test_changing_a_fighter_and_saving(self) -> None:
        d = self.dialog
        self.pick("Rookie")
        d.f_grade.setValue(6)
        d.f_stats["str"].setValue(25)
        self.assertIn("<b>25</b> (was 1)", d.f_level.text())
        self.assertIn("Rookie", d.fighter_list.currentItem().text())
        self.assertTrue(d.fighter_list.currentItem().text().endswith("*"))
        self.pick("Donna")
        self.assertEqual((d.f_stats["hp"].value(), d.f_stats["hp"].maximum()), (14, 15))
        self.assertEqual(d.f_bonus["hp"].maximum(), 2)
        self.assertIn("<b>0</b>", d.f_lb.text())
        self.pick("Rookie")
        self.assertEqual(d.f_stats["str"].value(), 25)                          # kept while away
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes) as asked, \
                mock.patch.object(QMessageBox, "information"):
            d.save()
        self.assertIn("Rookie: STR: 1 -> 25", asked.call_args.args[2])
        saved = SaveDocument.read(self.manager.library.get(self.entry.id).save)
        self.assertEqual(by_name(saved, "Rookie").stats["str"], 25)

    def test_the_bonus_box_steps_through_what_can_be_rolled(self) -> None:
        d = self.dialog
        self.pick("Rookie")
        d.f_grade.setValue(6)
        box = d.f_bonus["dex"]
        box.setValue(3)
        box.stepUp()
        self.assertEqual(box.value(), 5)                                       # past 4, which cannot be rolled
        box.stepDown()
        self.assertEqual(box.value(), 3)

    def test_locked_and_defending_fighters(self) -> None:
        d = self.dialog
        self.pick("Erica")
        self.assertFalse(d.f_name.isEnabled())
        self.assertIn("dead", d.f_lock.text())
        self.pick("Helen")
        self.assertTrue(d.f_name.isEnabled())
        self.assertFalse(d.f_grade.isEnabled())

    def test_max_all(self) -> None:
        d = self.dialog
        d.max_all_fighters()
        self.assertIn("Left as they are: Erica", d.f_ranges.text())
        self.pick("Donna")
        self.assertEqual((d.f_grade.value(), d.f_stats["vit"].value()), (6, 45))
        self.assertIn("value(s) changed", d.changes_label.text())


if __name__ == "__main__":
    unittest.main()
