"""A Heaven run, part 2 (saveheaven.py) and the Run page's new sections.

What matters: a fighter paused on a boss floor moves only to boss floors of the
same route and map whose arenas fit, with every reference to the floor renamed,
the boss given the new floor's level and the planned floors shifted; a Screamer
Pit turns into its twin across the 100F line; a roll comes out the same from the
same seed; a lock makes the floors ahead the chosen kind (Dons fall back to a
mid-boss where there are none); I'm feeling lucky plans the whole run and hides
at least one blueprint the save doesn't know, and nothing once all are known;
a Jackal's loot record is swapped for the new one; and the page writes it all
through Save changes.
"""

from __future__ import annotations

import json
import os
import random
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_savelibrary import STEAM

from lid_db_manager import saveheaven as H
from lid_db_manager.manager import Manager
from lid_db_manager.paths import AppPaths
from lid_db_manager.saveedit import SaveDocument

PROJECT = Path(__file__).resolve().parent.parent
VANILLA_DB = PROJECT / "LiD Vanilla DB" / "5.0.4.2" / "masters.db"
TEMPLATE = PROJECT / "lid_db_manager" / "templates" / "new-save-after-tutorial.sav"
F55, F60, F95, F100 = "HVN_FLR_0005", "HVN_FLR_0010", "HVN_FLR_0045", "HVN_FLR_0050"
MBOSS1, BOSS1, RUSH1, RUSH6 = "HVN_AREA_007", "HVN_AREA_003", "HVN_AREA_011", "HVN_AREA_018"

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtWidgets import QApplication, QMessageBox

    HAVE_QT = True
except ImportError:  # pragma: no cover - depends on the environment
    HAVE_QT = False


def _paused_on(heaven: H.Heaven, flrid: str, ref: str, planned: int = 3) -> SaveDocument:
    """The template's fighter quit on boss floor ``flrid`` (arena ``ref``), with chests, a boss and Jackals."""
    save = SaveDocument.read(TEMPLATE)
    doc, uid, rng = save.doc, save.uid, random.Random(7)
    area = heaven.areaid(flrid)
    fighter = doc["soul"]["chr"]["chrs"][uid][0]
    fighter.update(state="USE", bloodnium_result=json.dumps({"max_floor_id": flrid}, separators=(",", ":")))
    doc["soul"].update(pause="HEAVEN_PAUSE", stgid="S_HVN", flrid=flrid, areaid=area, unitid="D0")
    here = heaven.entry(rng, flrid, ref, False)
    ahead = [heaven.entry(rng, f, heaven.arenas[f][0][0], True) for f in heaven.ahead(flrid)[:planned]]
    doc["soul"]["hvntrinfo"] = [here] + ahead
    floor = doc["floor"]
    floor["rlg"] = {"user": [{"flrid": flrid, "areaid": area}],
                    "archive": [{"flrid": flrid, "areaid": area, "ref_areaid": ref,
                                 "units": f'{{"kis":"HEAVEN_BOSS_KIS_RUSH1","p":"HVN_R01_ZAKO_01"}}'}]}
    floor["trbox"] = [{"flrid": flrid, "pntid": "HVN_TB_TGT_00", "type": "TBTP_EXLARGE", "rwdtype": "TBRWD_ITEM",
                       "contentid": "box-xl"},
                      {"flrid": flrid, "pntid": "HVN_TB_TGT_01", "type": "TBTP_LARGE", "rwdtype": "TBRWD_ITEM",
                       "contentid": "box-l"}]
    floor["pop"]["trbox"] = {f"{flrid}-{area}": [{"pntid": "HVN_TB_TGT_01", "type": "TBTP_LARGE",
                                                  "contentid": here["rwds"][-1]["rwdid"]}]}
    floor["pop"]["xzk"] = {f"{flrid}-{area}": [{"pntid": "HVN_R01_ZAKO_01"}]}
    floor["mboss"]["mbss"] = [{"flrid": flrid, "eitemid": "boss-drop", "type": "MBOSS1", "lvl": 50}]
    floor["closed_area_flags"] = {f"{flrid}_A0_{ref}": 1}
    floor["jkls"] = [{"type": "JACKAL_X", "killed": 0, "rwd": json.dumps(
        {"money": 0, "spirit": 0, "tbtp": "", "pts": [{"eptid": "jackal-part"}], "msrs": {}, "items": {}},
        separators=(",", ":"))}, {"type": "JACKAL_Y", "killed": 1, "rwd": "{}"}]
    doc["item"]["items"] = [{"eid": "box-xl", "owner": "TRBOX", "itemid": here["rwdid"]},
                            {"eid": "box-l", "owner": "TRBOX", "itemid": here["rwds"][-1]["rwdid"]},
                            {"eid": "boss-drop", "owner": "MBOSS", "itemid": here["rwds"][0]["rwdid"]}]
    doc["part"]["pts"][uid] = [{"uid": int(uid), "eid": "jackal-part", "owner": "JACKAL_X", "ptid": "PT_ARM_WP051_001"}]
    return SaveDocument(save.to_bytes())


@unittest.skipUnless(VANILLA_DB.is_file() and TEMPLATE.is_file(), "needs the vanilla database and the template")
class TheRules(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.heaven = H.Heaven.read(VANILLA_DB)

    def test_moving_to_another_boss_floor(self) -> None:
        heaven = self.heaven
        save = _paused_on(heaven, F55, MBOSS1)
        targets = H.move_targets(save, heaven)
        self.assertIn(F60, targets)
        self.assertTrue(all(heaven.areaid(f) == "HVN_AREA_017" and heaven.route(f) == "main" for f in targets))
        before = [e["flrid"] for e in H.plan(save)]
        twice = [H.roll_move(save, heaven, F60, 5) for _ in range(2)]
        self.assertEqual(twice[0], twice[1])                                 # the same seed, the same roll
        roll = twice[0]
        self.assertTrue(H.move_floor(save, heaven, F60, 5))
        soul, floor = save.doc["soul"], save.doc["floor"]
        self.assertEqual(soul["flrid"], F60)
        self.assertEqual([e["flrid"] for e in soul["hvntrinfo"]],
                         [heaven.route_boss(F55)[heaven.route_boss(F55).index(f) + 1] for f in before])
        self.assertEqual(floor["rlg"]["user"][0]["flrid"], F60)
        self.assertEqual(list(floor["pop"]["trbox"]), [f"{F60}-HVN_AREA_017"])
        self.assertEqual(list(floor["closed_area_flags"]), [f"{F60}_A0_{MBOSS1}"])
        self.assertEqual(floor["mboss"]["mbss"][0]["lvl"], heaven.levels[F60])
        self.assertIn(f'"{F60}"', soul["chr"]["chrs"][save.uid][0]["bloodnium_result"])
        items = {r["eid"]: r["itemid"] for r in save.doc["item"]["items"]}
        for box in roll.boxes:
            self.assertEqual(items[box["eid"]], box["to"])
        SaveDocument(save.to_bytes())                                        # still a save that reads back

    def test_arenas_decide_where_a_floor_can_go(self) -> None:
        heaven = self.heaven
        don = _paused_on(heaven, F100, BOSS1)
        self.assertTrue(H.move_targets(don, heaven))
        self.assertTrue(all(heaven.no(f) >= 100 for f in H.move_targets(don, heaven)))   # no Dons below 100F
        pit = _paused_on(heaven, F55, RUSH1)
        self.assertIn(F100, H.move_targets(pit, heaven))
        self.assertEqual(H.pit_swap(pit, heaven, F100), RUSH6)
        self.assertTrue(H.move_floor(pit, heaven, F100, 3))
        floor = pit.doc["floor"]
        self.assertEqual(floor["rlg"]["archive"][0]["ref_areaid"], RUSH6)
        self.assertIn("KIS_RUSH6", floor["rlg"]["archive"][0]["units"])
        self.assertIn("HVN_R06_ZAKO_01", floor["rlg"]["archive"][0]["units"])
        self.assertEqual(floor["pop"]["xzk"][f"{F100}-HVN_AREA_017"][0]["pntid"], "HVN_R06_ZAKO_01")
        self.assertEqual(list(floor["closed_area_flags"]), [f"{F100}_A0_{RUSH6}"])
        self.assertEqual(H._plan_entry(pit.doc["soul"]["hvntrinfo"], F100)["refareaid"], RUSH6)

    def test_locking_the_boss_floors_ahead(self) -> None:
        heaven = self.heaven
        save = _paused_on(heaven, F55, MBOSS1)
        ahead = heaven.ahead(F55)
        written = H.lock_floors(save, heaven, "mboss", "mboss:2", extend=True, seed=9)
        self.assertEqual(written, len(ahead))
        planned = {e["flrid"]: e for e in save.doc["soul"]["hvntrinfo"]}
        self.assertTrue(all(heaven.kind(planned[f]["refareaid"]) == ("mboss", 2) for f in ahead))
        self.assertEqual(planned[ahead[0]]["rwds"][0]["gentype"], "PTGENTP_MBOSS2")
        dons = _paused_on(heaven, F55, MBOSS1)
        rows = H.lock_plan(heaven, F55, H.plan(dons), "don", extend=True, seed=1)
        kinds = {r.flrid: heaven.kind((r.entry or r.old)["refareaid"])[0] for r in rows}
        self.assertTrue(all(kinds[f] == "mboss" for f in heaven.ahead(F55) if heaven.no(f) < 100))   # no Dons there
        self.assertTrue(all(kinds[f] == "boss" for f in heaven.ahead(F55) if heaven.no(f) >= 100))
        self.assertEqual(H.extra_planned(heaven, F55, save.doc["soul"]["hvntrinfo"]), H.beyond_game(heaven, F55))
        self.assertEqual(H.clear_extra(save, heaven), len(ahead) - H.GAME_PLAN_AHEAD)
        self.assertEqual(len(save.doc["soul"]["hvntrinfo"]), 1 + H.GAME_PLAN_AHEAD)

    def test_feeling_lucky(self) -> None:
        heaven = self.heaven
        save = _paused_on(heaven, F55, MBOSS1)
        check = H.lucky_check(heaven, F55, H.plan(save), H.owned_blueprints(save), H.researched_parts(save))
        self.assertTrue(check.ok)
        self.assertEqual(check.ceiling, 5)
        again = SaveDocument(save.to_bytes())
        self.assertTrue(H.feeling_lucky(save, heaven, 1234))
        self.assertTrue(H.feeling_lucky(again, heaven, 1234))
        self.assertEqual(save.doc["soul"]["hvntrinfo"], again.doc["soul"]["hvntrinfo"])
        entries = save.doc["soul"]["hvntrinfo"]
        self.assertEqual({e["flrid"] for e in entries}, {F55, *heaven.ahead(F55)})   # the whole run planned
        hidden = H._placed_rewards(heaven, entries[1:]) - H.owned_blueprints(save)
        self.assertTrue(1 <= len({b for b in hidden if H.real_blueprint(heaven, b)}) <= 5)
        self.assertNotIn("ITMP_", " ".join(c.after for c in save.changes()))  # nothing named
        everything = {i for i in heaven.catalog.items if i.startswith("ITMP_")}
        check = H.lucky_check(heaven, F55, H.plan(save), everything, set())
        self.assertFalse(check.ok)

    def test_jackal_loot(self) -> None:
        heaven = self.heaven
        save = _paused_on(heaven, F55, MBOSS1)
        found = H.jackals(save)
        self.assertEqual(H.loot_label(heaven, H.jackal_loot(save, found[0])), "Jackal Sword")
        self.assertTrue(H.set_jackal_loot(save, heaven, "JACKAL_X", H.Loot("item", "ITMP_ARM_WP051_001"), "new-eid", 1))
        self.assertNotIn("jackal-part", [p["eid"] for p in save.doc["part"]["pts"][save.uid]])
        self.assertIn({"eid": "new-eid", "gettime": 0, "itemid": "ITMP_ARM_WP051_001", "owner": "JACKAL_X"},
                      save.doc["item"]["items"])
        self.assertEqual(json.loads(H.jackals(save)[0]["rwd"])["items"][0]["eitemid"], "new-eid")
        self.assertTrue(H.set_jackal_loot(save, heaven, "JACKAL_X", H.Loot("part", "PT_JAC_HEAD_001"), "part-eid", 1))
        self.assertNotIn("new-eid", [r["eid"] for r in save.doc["item"]["items"]])
        made = save.doc["part"]["pts"][save.uid][-1]
        self.assertEqual((made["owner"], made["lvl"], made["dur"] > 0), ("JACKAL_X", 1, True))
        self.assertFalse(H.set_jackal_loot(save, heaven, "JACKAL_Y", H.Loot("money", money=5), "x", 1))   # defeated
        self.assertEqual(H.jackals(SaveDocument.read(TEMPLATE)), [])        # the Waiting Room: no run, no Jackals
        rng = random.Random(3)
        self.assertTrue(all(H.roll_jackal(heaven, "JACKAL_X", rng).kind in ("money", "part", "item") for _ in range(20)))


@unittest.skipUnless(HAVE_QT and VANILLA_DB.is_file() and TEMPLATE.is_file(), "needs PySide6 and the data")
class ThePage(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls.heaven = H.Heaven.read(VANILLA_DB)

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
        source.write_bytes(_paused_on(self.heaven, F55, MBOSS1).to_bytes())
        self.entry = self.manager.import_save(source, STEAM, "On 55F")
        self.dialog = SaveEditorDialog(self.manager, self.entry.id)
        self.addCleanup(self.dialog.deleteLater)

    def test_the_page_writes_through_save_changes(self) -> None:
        page = self.dialog.run
        page.move_pick.setCurrentIndex(page.move_pick.findData(F60))
        page.lock_pick.setCurrentIndex(page.lock_pick.findData("mboss"))
        page.lucky_go.click()
        page._jackal_rolled("JACKAL_X")
        self.assertEqual(page.change_count, 4)
        locked = {r.flrid: r.entry for r in page._lock_rows(page._moved(page._roll())) if r.entry}
        page.spoilers.setChecked(True)
        self.assertGreater(page.move_table.rowCount(), 0)
        page.end.setChecked(True)                                            # ending the run turns the rest off
        self.assertEqual(page.change_count, 1)
        self.assertFalse(page.move_box.isVisibleTo(page))
        page.end.setChecked(False)
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes) as asked, \
                mock.patch.object(QMessageBox, "information"):
            self.dialog.save()
        listed = asked.call_args.args[2]
        self.assertIn("Run: boss floor", listed)
        self.assertIn("I'm feeling lucky", listed)
        saved = SaveDocument.read(self.manager.library.get(self.entry.id).save)
        soul = saved.doc["soul"]
        self.assertEqual(soul["flrid"], F60)
        planned = {e["flrid"]: e for e in soul["hvntrinfo"]}
        self.assertEqual(set(planned), {F60, *self.heaven.ahead(F60)})
        self.assertTrue(all(self.heaven.kind(planned[f]["refareaid"])[0] == "mboss" for f in self.heaven.ahead(F60)))
        self.assertEqual({f: planned[f]["refareaid"] for f in locked}, {f: e["refareaid"] for f, e in locked.items()})
        self.assertNotEqual(json.loads(saved.doc["floor"]["jkls"][0]["rwd"])["pts"], [{"eptid": "jackal-part"}])


if __name__ == "__main__":
    unittest.main()
