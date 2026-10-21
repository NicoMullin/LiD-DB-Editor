"""Research (saveresearch.py) and its page.

What matters, from the game's own code: a part's levels run 1 to its top, all
FINISHED and the top one CHARGE; a part under its top has a LEVELUP marker at the
next level and a part at its top opens the next part of its chain with a REMODEL
marker; level 1 points back at the part before it (or at nothing for a chain's
first part), and the starting weapons cannot be taken away; researching a part
maxes the parts before it and taking one below its top takes away the parts after
it; an uncapped part shows on one line, +0 to +24; a blueprint given is a MAP
entry, and researching it turns that entry into the finished one; each part's
first level moves its Funshot, and on a save without the game's first count the
Funshots are set to the total and the count marked done; a skin whose research
drops below +4 comes off; and leaving things as they are changes nothing.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_savelibrary import STEAM

from lid_db_manager import saveresearch as R, savegame
from lid_db_manager.manager import Manager
from lid_db_manager.paths import AppPaths
from lid_db_manager.saveedit import SaveDocument

PROJECT = Path(__file__).resolve().parent.parent
VANILLA_DB = PROJECT / "LiD Vanilla DB" / "5.0.4.2" / "masters.db"
TEMPLATE = PROJECT / "lid_db_manager" / "templates" / "new-save-after-tutorial.sav"
REAL_SAVES = Path(os.environ.get("LID_SAVEDATA", r"E:\SteamLibrary\steamapps\common\LET IT DIE\Savedata"))
METAL_BAT, METAL_BAT_A, SPIKED_BAT, SPIKED_BAT_A, SPIKED_BAT_A_G = (
    "PT_ARM_WP005_001", "PT_ARM_WP005_003", "PT_ARM_WP005_002", "PT_ARM_WP005_004", "PT_ARM_WP005_005")
JACKAL, JACKAL_G = "PT_JAC_TOPS_203", "PT_JAC_TOPS_203_G"
MACHETE = "PT_ARM_WP001_001"
LANDMINE = "PT_ARM_WP023_001"

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QMessageBox

    HAVE_QT = True
except ImportError:  # pragma: no cover - depends on the environment
    HAVE_QT = False


def _of(save: SaveDocument, ptid: str) -> list[dict]:
    return [e for e in save.doc["soul"]["partresearch"]["user"] if e["ptid"] == ptid]


def _kinds(save: SaveDocument, ptid: str) -> list[tuple]:
    return sorted((e["lvl"], e["research_type"], e["receive_type"], e["before_ptid"], e["before_lvl"])
                  for e in _of(save, ptid))


@unittest.skipUnless(VANILLA_DB.is_file() and TEMPLATE.is_file(), "needs the vanilla database and the template")
class TheRules(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = R.ResearchRules.read(VANILLA_DB)

    def setUp(self) -> None:
        self.save = SaveDocument.read(TEMPLATE)

    def test_the_list(self) -> None:
        rows = {r.ptid: r for r in self.rules.rows(self.save)}
        self.assertNotIn(LANDMINE, rows)                                  # not open for research
        self.assertNotIn(JACKAL_G, rows)                                  # shown on its capped part's line
        self.assertEqual((rows[JACKAL].name, self.rules.level_range(JACKAL)), ("Jackal Z Body D", (0, 24)))
        self.assertEqual(self.rules.level_range(METAL_BAT_A), (0, 4))
        self.assertEqual([r.slot for r in self.rules.rows(self.save)][0], "Weapon")
        self.assertEqual((self.rules.stamp_type(METAL_BAT_A), self.rules.stamp_type(JACKAL)), ("HIT", "BODY"))
        self.assertEqual(R.tops(self.save), {METAL_BAT: 1, "PT_ARM_WP006_001": 1})

    def test_a_chain_is_researched_as_the_game_does(self) -> None:
        R.set_research(self.save, self.rules, {METAL_BAT_A: 2}, now=1_800_000_000)
        self.assertEqual(_kinds(self.save, METAL_BAT), [
            (1, "FINISHED", "FINISHED", "", 0), (2, "FINISHED", "FINISHED", "", 0),    # the starter's own entries
            (3, "FINISHED", "FINISHED", METAL_BAT, 2), (4, "FINISHED", "FINISHED", METAL_BAT, 3),
            (5, "FINISHED", "CHARGE", METAL_BAT, 4)])
        self.assertEqual(_kinds(self.save, METAL_BAT_A), [                 # +2 is level 3
            (1, "FINISHED", "FINISHED", METAL_BAT, 5), (2, "FINISHED", "FINISHED", METAL_BAT_A, 1),
            (3, "FINISHED", "CHARGE", METAL_BAT_A, 2), (4, "LEVELUP", "UNKNOWN", METAL_BAT_A, 3)])
        self.assertEqual(_of(self.save, SPIKED_BAT), [])                   # Metal Bat A is not at its top
        self.assertEqual(R.stamps(self.save)["HIT"], 0.8)
        self.assertTrue(R.stamp_flag(self.save))                           # the first count marked done
        labels = [(c.label, c.before, c.after) for c in self.save.changes()]
        self.assertIn(("Research: Metal Bat", "+0", "+4"), labels)
        self.assertIn(("Research: Metal Bat A", "not researched", "+2"), labels)
        self.assertIn(("Funshots: Hit", "0.0", "0.8"), labels)
        self.assertEqual(SaveDocument(self.save.to_bytes()).doc["soul"]["researchstamp"][2], {"type": "HIT", "rate": 0.8})

        R.set_research(self.save, self.rules, {METAL_BAT_A: 4})            # at its top: Spiked Bat opens
        self.assertEqual(_kinds(self.save, SPIKED_BAT), [(1, "REMODEL", "UNKNOWN", METAL_BAT_A, 5)])
        R.set_research(self.save, self.rules, {SPIKED_BAT: 0})              # the REMODEL turns finished
        self.assertEqual(_kinds(self.save, SPIKED_BAT)[:2], [
            (1, "FINISHED", "CHARGE", METAL_BAT_A, 5), (2, "LEVELUP", "UNKNOWN", SPIKED_BAT, 1)])
        R.set_research(self.save, self.rules, {METAL_BAT: None})            # the starter goes to +0 ...
        self.assertEqual(_kinds(self.save, METAL_BAT), [(1, "FINISHED", "CHARGE", "", 0), (2, "LEVELUP", "UNKNOWN", "", 0)])
        self.assertEqual((_of(self.save, METAL_BAT_A), _of(self.save, SPIKED_BAT)), ([], []))   # ... the rest goes
        self.assertEqual(R.stamps(self.save)["HIT"], 0.0)

    def test_an_uncapped_part_is_one_line(self) -> None:
        R.set_research(self.save, self.rules, {JACKAL: 20})
        self.assertEqual((R.tops(self.save)[JACKAL], R.tops(self.save)[JACKAL_G]), (5, 16))
        self.assertEqual(_kinds(self.save, JACKAL_G)[0], (1, "FINISHED", "FINISHED", JACKAL, 5))
        self.assertEqual(_kinds(self.save, JACKAL_G)[-1], (17, "LEVELUP", "UNKNOWN", JACKAL_G, 16))
        self.assertEqual(self.rules.shown(JACKAL, R.tops(self.save)), 20)
        R.set_research(self.save, self.rules, {JACKAL: 4})
        self.assertEqual(_kinds(self.save, JACKAL_G), [(1, "REMODEL", "UNKNOWN", JACKAL, 5)])
        R.set_research(self.save, self.rules, {JACKAL: 24})
        self.assertEqual(_kinds(self.save, JACKAL_G)[-1], (20, "FINISHED", "CHARGE", JACKAL_G, 19))   # +24: no marker

    def test_blueprints(self) -> None:
        self.assertTrue(R.blueprint_wanted(self.save, self.rules, MACHETE))
        self.assertFalse(R.blueprint_wanted(self.save, self.rules, SPIKED_BAT))   # not a chain's first part
        R.set_research(self.save, self.rules, {}, blueprints=[MACHETE])
        self.assertEqual(_kinds(self.save, MACHETE), [(1, "MAP", "UNKNOWN", "", 0)])
        self.assertEqual(R.known(self.save), {MACHETE: "MAP"})
        R.set_research(self.save, self.rules, {MACHETE: 0})
        self.assertEqual(_kinds(self.save, MACHETE), [(1, "FINISHED", "CHARGE", "", 0), (2, "LEVELUP", "UNKNOWN", MACHETE, 1)])
        self.assertEqual(_of(self.save, MACHETE)[0]["is_announced"], 1)

    def test_everything_at_its_top(self) -> None:
        wanted = {r.ptid: self.rules.level_range(r.ptid)[1] for r in self.rules.rows(self.save) if r.offered}
        R.set_research(self.save, self.rules, wanted)
        levels = R.tops(self.save)
        self.assertTrue(all(levels[p] == self.rules.max_level(p) for p in levels))
        entries = self.save.doc["soul"]["partresearch"]["user"]
        self.assertFalse([e for e in entries if e["research_type"] in R.MARKERS])
        self.assertEqual(R.stamps(self.save)["SLASH"], 53.2)               # half the 106.4 a double count gives
        again = SaveDocument(self.save.to_bytes())
        self.assertEqual(R.tops(again), levels)

    @unittest.skipUnless(REAL_SAVES.is_dir(), "needs the real save folder")
    def test_real_saves(self) -> None:
        for path in sorted(REAL_SAVES.glob(f"*/{savegame.SAVE_NAME}")):
            save = SaveDocument.read(path)
            levels = R.tops(save)
            as_is = {r.ptid: self.rules.shown(r.ptid, levels) for r in self.rules.rows(save)}
            self.assertEqual(R.resolve(save, self.rules, as_is), {})
            R.set_research(save, self.rules, as_is)
            self.assertEqual(save.encoded(), save.payload)
            skins = [s["ptid"] for s in save.doc["soul"].get("armorskin") or [] if isinstance(s, dict)]
            line = next((self.rules.row_of(p) for p in skins if R.skin_unlocked(self.rules, p, levels)), None)
            if line is None or not R.stamp_flag(save):
                continue
            before = R.stamps(save)
            self.assertEqual(R.skins_lost(save, self.rules, R.resolve(save, self.rules, {line: 2})),
                             [self.rules.name(line)])
            expected = R.stamps_after(save, self.rules, R.resolve(save, self.rules, {line: None}))
            R.set_research(save, self.rules, {line: None})
            self.assertNotIn(line, [s["ptid"] for s in save.doc["soul"]["armorskin"]])
            self.assertEqual(R.stamps(save), expected)
            kind = self.rules.stamp_type(line)
            if kind:
                self.assertLess(R.stamps(save)[kind], before[kind])          # moved by what went, not reset
                self.assertGreater(R.stamps(save)[kind], 0)


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
        source.write_bytes(TEMPLATE.read_bytes())
        self.entry = self.manager.import_save(source, STEAM, "New game")
        self.dialog = SaveEditorDialog(self.manager, self.entry.id)
        self.addCleanup(self.dialog.deleteLater)

    def test_the_page_writes_through_save_changes(self) -> None:
        page = self.dialog.research
        row = page.index[JACKAL]
        page.table.item(row, 2).setData(Qt.ItemDataRole.EditRole, 10)
        self.assertEqual(page.change_count, 1)
        self.assertEqual(page.table.item(row, 2).data(Qt.ItemDataRole.EditRole), 10)
        page.search.setText("Jungle Machete")
        page.give_shown()
        self.assertEqual(page.table.item(page.index[MACHETE], 3).text(), "given (ready to develop)")
        page.search.setText("Metal Bat")
        page.raise_to.setCurrentIndex(0)
        page.raise_shown()                                                  # Metal Bat and Metal Bat A to +4
        self.assertEqual(page.shown_now(METAL_BAT_A), 4)
        self.assertEqual(page.table.item(page.index[SPIKED_BAT], 3).text(), "ready to remodel")
        self.assertIn("Hit 0.0 &rarr; <b>0.8</b>", page.stamp_label.text())
        self.assertEqual(self.dialog.changes_label.text(), f"{page.change_count} value(s) changed, not saved yet")
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes) as asked, \
                mock.patch.object(QMessageBox, "information"):
            self.dialog.save()
        text = asked.call_args.args[2]
        self.assertIn("Research: Jackal Z Body D: not researched -> +10", text)
        self.assertIn("Blueprint: Jungle Machete: unknown -> known", text)
        saved = SaveDocument.read(self.manager.library.get(self.entry.id).save)
        self.assertEqual(R.tops(saved)[JACKAL_G], 6)
        self.assertEqual(R.tops(saved)[METAL_BAT_A], 5)
        self.assertEqual(R.known(saved), {MACHETE: "MAP"})


if __name__ == "__main__":
    unittest.main()
