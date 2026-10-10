"""Settings that are a set of decals, picked in a window of their own.

The same promise as every other setting: only ids made of letters, digits and
underscores reach the SQL, so a pick can sit between quotes and never close
them. Until the player picks anything, the placeholder is the mod's own query,
so the default follows the database - and any mod that adds decals to it -
instead of freezing a list.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fixtures import build_db, query, write_mod

from lid_db_manager.db_record import _clean_values
from lid_db_manager.decals import DecalReadError, default_ids, game_text, read_decals
from lid_db_manager.errors import ModLoadError
from lid_db_manager.manager import Manager
from lid_db_manager.paths import AppPaths
from lid_db_manager.settings import parse_settings, render_text, render_values, resolve_values
from lid_db_manager.state import AppliedRecord, State

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QCoreApplication, Qt
    from PySide6.QtWidgets import QApplication

    HAVE_QT = True
except ImportError:  # pragma: no cover - the laptop's Python has no PySide6
    HAVE_QT = False

DEFAULT_SQL = "SELECT sklid FROM pool WHERE id = 'NORMAL' AND odds > 0"
POOL = {"id": "pool", "label": "Decals in the stew", "type": "decals",
        "default_label": "The normal stew's", "default_sql": DEFAULT_SQL, "group": "Stew"}
SQL = """
INSERT OR REPLACE INTO pool (id, sklid, odds)
SELECT 'DM', s.id,
       COALESCE((SELECT n.odds FROM pool n WHERE n.id = 'NORMAL' AND n.sklid = s.id),
                CASE s.rarity WHEN 5 THEN 2 ELSE 1 END)
FROM master_skill s WHERE s.id IN ({{pool}});
"""


def a_decal_db(path: Path) -> Path:
    """The fixture database, plus decals with stars and categories, their
    English names, and a stew's pool to copy from."""
    build_db(path)
    con = sqlite3.connect(str(path))
    try:
        con.executescript(
            """
            ALTER TABLE master_skill ADD COLUMN rarity INTEGER;
            ALTER TABLE master_skill ADD COLUMN category TEXT;
            ALTER TABLE master_skill ADD COLUMN "desc" TEXT;
            ALTER TABLE master_skill ADD COLUMN getroute TEXT;
            ALTER TABLE master_skill ADD COLUMN val1 INTEGER;
            ALTER TABLE master_skill ADD COLUMN val2 INTEGER;
            ALTER TABLE master_skill ADD COLUMN val3 INTEGER;
            ALTER TABLE master_skill ADD COLUMN val4 INTEGER;
            ALTER TABLE master_skill ADD COLUMN val5 INTEGER;
            ALTER TABLE master_skill ADD COLUMN type TEXT;
            CREATE TABLE master_skill_type (id TEXT PRIMARY KEY, name TEXT, "group" TEXT);
            CREATE TABLE master_skill_group (id TEXT PRIMARY KEY, max_value INTEGER, "desc" TEXT);
            CREATE TABLE master_skill_category (id TEXT PRIMARY KEY, name TEXT);
            CREATE TABLE pool (id TEXT, sklid TEXT, odds INTEGER, PRIMARY KEY (id, sklid));
            """
        )
        con.executemany(
            "INSERT INTO master_skill (id, name, buy_money, val0, rarity, category) VALUES (?, ?, 0, 0, ?, ?)",
            [
                ("SKL_A_P", "SKILL_NAME.TXT_A", 3, "SKCTG_X"),
                ("SKL_B_P", "SKILL_NAME.TXT_B", 4, "SKCTG_Y"),
                ("SKL_C_P", "SKILL_NAME.TXT_C", 5, "SKCTG_X"),
                ("SKL_D_P", "SKILL_NAME.TXT_NONE", 1, "SKCTG_Y"),
                ("SKL_A", "SKILL_NAME.TXT_A", 3, "SKCTG_X"),     # the effect, not a decal
            ],
        )
        con.execute("UPDATE master_skill SET \"desc\" = 'SKILL_DESCRIPTION.TXT_A', "
                    "getroute = 'SKILL_GETROUTE.TXT_HALL', val0 = 40, val1 = 15, type = 'SKLTP_A' "
                    "WHERE id = 'SKL_A_P'")
        con.execute("INSERT INTO master_skill_type VALUES ('SKLTP_A', '', 'SKLGP_A')")
        con.execute("INSERT INTO master_skill_group VALUES ('SKLGP_A', 50, 'TUB.TXT_LIMIT_A')")
        con.executemany("INSERT INTO master_skill_category VALUES (?, ?)",
                        [("SKCTG_X", "TUB.TXT_X"), ("SKCTG_Y", "TUB.TXT_Y")])
        con.executemany(
            "INSERT INTO master_text (sct, id, snd, lang, txt, type) VALUES (?, ?, '', ?, ?, 4)",
            [
                ("SKILL_NAME", "TXT_A", "int", "Alpha"),
                ("SKILL_NAME", "TXT_A", "jpn", "Arufa"),
                ("SKILL_NAME", "TXT_B", "int", "Bravo"),
                ("SKILL_NAME", "TXT_C", "int", "Charlie"),
                ("TUB", "TXT_X", "int", "Exes"),
                ("TUB", "TXT_Y", "int", "Whys"),
                ("SKILL_DESCRIPTION", "TXT_A", "int", "Attack up\nby #0%.//Defence up by\n#1%.\n "),
                ("SKILL_GETROUTE", "TXT_HALL", "int", "From Mushroom Club"),
                ("TUB", "TXT_LIMIT_A", "int", "Stacks up to\n#0%.\n "),
            ],
        )
        con.executemany("INSERT INTO pool VALUES (?, ?, ?)",
                        [("NORMAL", "SKL_A_P", 40), ("NORMAL", "SKL_B_P", 4), ("NORMAL", "SKL_D_P", 0)])
        con.commit()
    finally:
        con.close()
    return path


def a_pool_mod(mods_dir: Path, mod_id: str = "dm-pool") -> Path:
    return write_mod(
        mods_dir, mod_id,
        {
            "description": "The stew holds {{pool:label}}.",
            "apply": "diff",
            "settings": [POOL],
            "patches": [{"type": "raw_sql", "id": "pool", "sql": SQL}],
        },
    )


class DeclaringOne(unittest.TestCase):
    def test_it_loads(self) -> None:
        [setting] = parse_settings([POOL], "m")
        self.assertEqual((setting.kind, setting.default, setting.default_sql), ("decals", "", DEFAULT_SQL))
        self.assertEqual(setting.display(""), "The normal stew's")

    def test_it_needs_one_select_as_its_default(self) -> None:
        for bad in (None, "", "DELETE FROM pool", "SELECT 1; DROP TABLE pool", 3):
            with self.subTest(default_sql=bad), self.assertRaises(ModLoadError):
                parse_settings([dict(POOL, default_sql=bad)], "m")

    def test_a_default_list_is_checked_too(self) -> None:
        [setting] = parse_settings([dict(POOL, default=["SKL_A_P", "SKL_A_P", "SKL_B_P"])], "m")
        self.assertEqual(setting.default, "SKL_A_P,SKL_B_P")
        with self.assertRaises(ModLoadError):
            parse_settings([dict(POOL, default=["SKL_A_P", "x'y"])], "m")

    def test_a_number_style_will_not_load(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = write_mod(Path(tmp), "m", {
                "settings": [POOL],
                "patches": [{"type": "raw_sql", "sql": "SELECT {{pool:comma}};"}],
            })
            from lid_db_manager.mod_loader import load_mod_folder

            with self.assertRaises(ModLoadError):
                load_mod_folder(folder)


class ChoosingDecals(unittest.TestCase):
    def setUp(self) -> None:
        [self.setting] = parse_settings([POOL], "m")

    def values(self, picked):
        return resolve_values([self.setting], {"pool": picked})

    def test_a_list_or_text_is_taken_once_each(self) -> None:
        self.assertEqual(self.values(["SKL_A_P", "SKL_B_P", "SKL_A_P"]), ({"pool": "SKL_A_P,SKL_B_P"}, []))
        self.assertEqual(self.values(" SKL_A_P , SKL_C_P "), ({"pool": "SKL_A_P,SKL_C_P"}, []))
        self.assertEqual(self.values(""), ({"pool": ""}, []))

    def test_anything_that_could_carry_sql_is_refused(self) -> None:
        for bad in ("SKL_A'; DROP TABLE pool; --", ["SKL_A_P", "a b"], ["SKL_A_P", 3], 5, None):
            with self.subTest(picked=bad):
                values, problems = self.values(bad)
                self.assertEqual(values, {"pool": ""})
                self.assertEqual(len(problems), 1)

    def test_more_than_the_limit_is_refused(self) -> None:
        values, problems = self.values([f"SKL_{i}" for i in range(5001)])
        self.assertEqual((values, len(problems)), ({"pool": ""}, 1))

    def test_in_sql_quoted_ids_or_the_mods_own_query(self) -> None:
        picked = render_values([self.setting], {"pool": "SKL_A_P,SKL_C_P"})
        self.assertEqual(render_text("IN ({{pool}})", picked), "IN ('SKL_A_P','SKL_C_P')")
        self.assertEqual(render_text("{{pool:label}}", picked), "2 decals picked")
        default = render_values([self.setting], {"pool": ""})
        self.assertEqual(render_text("IN ({{pool}})", default), f"IN ({DEFAULT_SQL})")
        self.assertEqual(render_text("{{pool:label}}", default), "The normal stew's")


class ReadingTheDecals(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = a_decal_db(Path(self._tmp.name) / "masters.db")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_every_decal_with_its_english_name_stars_and_category(self) -> None:
        decals = read_decals(self.db)
        self.assertEqual(
            [(d.id, d.name, d.stars, d.category) for d in decals],
            [("SKL_A_P", "Alpha", 3, "Exes"), ("SKL_B_P", "Bravo", 4, "Whys"),
             ("SKL_C_P", "Charlie", 5, "Exes"), ("SKL_D_P", "SKL_D_P", 1, "Whys")],
        )

    def test_what_a_decal_does_as_the_game_words_it(self) -> None:
        alpha = read_decals(self.db)[0]
        self.assertEqual(
            (alpha.description, alpha.limit, alpha.how_to_get),
            ("Attack up by 40%. Defence up by 15%.", "Stacks up to 50%.", "From Mushroom Club"),
        )
        self.assertEqual(read_decals(self.db)[1].description, "")

    def test_a_database_without_descriptions_still_lists_its_decals(self) -> None:
        bare = Path(self._tmp.name) / "bare.db"
        con = sqlite3.connect(str(bare))
        con.executescript(
            "CREATE TABLE master_skill (id TEXT, name TEXT, rarity INTEGER, category TEXT);"
            "CREATE TABLE master_skill_category (id TEXT, name TEXT);"
            "CREATE TABLE master_text (sct TEXT, id TEXT, snd TEXT, lang TEXT, txt TEXT, type INTEGER);"
            "INSERT INTO master_skill VALUES ('SKL_Z_P', 'SKILL_NAME.TXT_Z', 2, 'SKCTG_X');"
        )
        con.commit()
        con.close()
        [only] = read_decals(bare)
        self.assertEqual((only.id, only.stars, only.description), ("SKL_Z_P", 2, ""))

    def test_game_text(self) -> None:
        self.assertEqual(game_text("Up\nby #0%//and #2.\n ", (5.0, 1, None)), "Up by 5% and #2.")
        self.assertEqual(game_text("", (1,)), "")

    def test_the_default_is_the_mods_query_run_here(self) -> None:
        self.assertEqual(sorted(default_ids(self.db, DEFAULT_SQL)), ["SKL_A_P", "SKL_B_P"])
        self.assertEqual(default_ids(self.db, "SELECT nothing FROM nowhere"), [])

    def test_it_only_reads(self) -> None:
        before = self.db.read_bytes()
        read_decals(self.db)
        default_ids(self.db, DEFAULT_SQL)
        self.assertEqual(self.db.read_bytes(), before)

    def test_a_database_without_decals_says_so(self) -> None:
        empty = Path(self._tmp.name) / "empty.db"
        sqlite3.connect(str(empty)).close()
        with self.assertRaises(DecalReadError):
            read_decals(empty)


class ThroughTheManager(unittest.TestCase):
    """The default, a pick, a decal since gone, back to the default and off."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.paths = AppPaths(self.root).ensure()
        self.db = a_decal_db(self.root / "game" / "masters.db")
        self.stock = self.pool()
        a_pool_mod(self.paths.mods_dir)
        self.manager = Manager(self.paths)
        self.manager.set_db_path(self.db)
        self.manager.set_enabled("dm-pool", True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def pool(self):
        return sorted(query(self.db, "SELECT id, sklid, odds FROM pool"))

    def stew(self):
        return sorted((sklid, odds) for stew, sklid, odds in self.pool() if stew == "DM")

    def choose(self, picked) -> None:
        self.manager.set_mod_setting("dm-pool", "pool", picked)
        self.assertTrue(self.manager.save_mod_list().ok)

    def test_by_default_the_mods_own_choice(self) -> None:
        self.assertTrue(self.manager.save_mod_list().ok)
        self.assertEqual(self.stew(), [("SKL_A_P", 40), ("SKL_B_P", 4)])

    def test_a_pick_replaces_it(self) -> None:
        self.choose("SKL_C_P,SKL_A_P")
        self.assertEqual(self.stew(), [("SKL_A_P", 40), ("SKL_C_P", 2)])
        self.assertEqual(self.manager.configured_mod("dm-pool").description, "The stew holds 2 decals picked.")

    def test_a_decal_no_longer_there_is_skipped(self) -> None:
        self.choose("SKL_GONE_P,SKL_D_P")
        self.assertEqual(self.stew(), [("SKL_D_P", 0)])

    def test_back_to_the_default_and_off(self) -> None:
        self.choose("SKL_C_P")
        self.choose("")
        self.assertEqual(self.stew(), [("SKL_A_P", 40), ("SKL_B_P", 4)])
        self.manager.set_enabled("dm-pool", False)
        self.assertTrue(self.manager.save_mod_list().ok)
        self.assertEqual(self.pool(), self.stock)

    def test_the_pick_survives_a_restart(self) -> None:
        self.choose("SKL_C_P,SKL_B_P")
        again = Manager(self.paths)
        again.set_db_path(self.db)
        self.assertEqual(again.configured_mod("dm-pool").values["pool"], "SKL_C_P,SKL_B_P")
        self.assertEqual(again.state.applied["dm-pool"].values["pool"], "SKL_C_P,SKL_B_P")
        self.assertTrue(again.save_mod_list().ok)
        self.assertEqual(self.stew(), [("SKL_B_P", 4), ("SKL_C_P", 2)])

    def test_a_refused_pick_is_not_stored(self) -> None:
        with self.assertRaises(ValueError):
            self.manager.set_mod_setting("dm-pool", "pool", "SKL_A_P,x'; DROP TABLE pool; --")
        self.assertNotIn("dm-pool", self.manager.state.mod_settings)


class WhatIsReadBackFromFiles(unittest.TestCase):
    def test_state_keeps_a_pick(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text(json.dumps({"mod_settings": {"m": {
                "pool": "SKL_A_P,SKL_B_P", "bad": "SKL_A_P,a'b", "worse": "SKL_A_P,,SKL_B_P"}}}),
                encoding="utf-8")
            self.assertEqual(State.load(path).mod_settings, {"m": {"pool": "SKL_A_P,SKL_B_P"}})

    def test_the_applied_record_and_the_database_note_keep_it(self) -> None:
        record = AppliedRecord.from_dict({"values": {"pool": "SKL_A_P,SKL_B_P", "bad": "a;b"}})
        self.assertEqual(record.values, {"pool": "SKL_A_P,SKL_B_P"})
        self.assertEqual(_clean_values(json.dumps({"pool": "SKL_A_P,SKL_C_P", "bad": "x y"})),
                         {"pool": "SKL_A_P,SKL_C_P"})


@unittest.skipUnless(HAVE_QT, "PySide6 is not installed")
class TheWindow(unittest.TestCase):
    app = None

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = a_decal_db(Path(self._tmp.name) / "masters.db")
        [self.setting] = parse_settings([POOL], "m")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def picker(self, value=""):
        from lid_db_manager.ui.decal_picker import DecalPicker

        return DecalPicker(self.setting, value, self.db)

    def rows(self, picker):
        """(id, ticked, shown) per row, in the table's current order."""
        found = []
        for row in range(picker.table.rowCount()):
            item = picker.table.item(row, 0)
            found.append((item.data(Qt.ItemDataRole.UserRole + 1),
                          item.checkState() == Qt.CheckState.Checked,
                          not picker.table.isRowHidden(row)))
        return found

    def test_it_starts_with_the_default_ticked_and_keeps_it_as_the_default(self) -> None:
        picker = self.picker()
        self.assertEqual(sorted(picker.ticked()), ["SKL_A_P", "SKL_B_P"])
        self.assertEqual(picker.value(), "")
        self.assertIn("the default", picker.count.text())

    def test_a_change_becomes_a_list_in_the_databases_order(self) -> None:
        picker = self.picker()
        for row in range(picker.table.rowCount()):
            item = picker.table.item(row, 0)
            if item.data(Qt.ItemDataRole.UserRole + 1) == "SKL_C_P":
                item.setCheckState(Qt.CheckState.Checked)
        self.assertEqual(picker.value(), "SKL_A_P,SKL_B_P,SKL_C_P")

    def test_search_and_tick_all_shown(self) -> None:
        picker = self.picker("SKL_A_P")
        picker.search.setText("exes")
        picker._filter()
        shown = [i for i, _, visible in self.rows(picker) if visible]
        self.assertEqual(sorted(shown), ["SKL_A_P", "SKL_C_P"])
        picker._tick_shown(True)
        self.assertEqual(picker.value(), "SKL_A_P,SKL_C_P")
        picker._tick_shown(False)
        self.assertEqual(picker.value(), "")      # nothing ticked
        self.assertFalse(picker.ok_button.isEnabled())

    def test_sorting_by_stars(self) -> None:
        picker = self.picker()
        picker.table.sortByColumn(1, Qt.SortOrder.DescendingOrder)
        self.assertEqual([i for i, _, _ in self.rows(picker)], ["SKL_C_P", "SKL_B_P", "SKL_A_P", "SKL_D_P"])

    def test_a_picked_decal_since_gone_is_listed_and_can_go(self) -> None:
        picker = self.picker("SKL_GONE_P,SKL_C_P")
        self.assertIn(("SKL_GONE_P", True, True), self.rows(picker))
        self.assertEqual(picker.value(), "SKL_C_P,SKL_GONE_P")
        picker._back_to_default()
        self.assertEqual(picker.value(), "")

    def highlight(self, picker, ids):
        from PySide6.QtCore import QItemSelectionModel

        model = picker.table.selectionModel()
        model.clearSelection()
        for row in range(picker.table.rowCount()):
            if picker.table.item(row, 0).data(Qt.ItemDataRole.UserRole + 1) in ids:
                picker.table.setCurrentCell(row, 0, QItemSelectionModel.SelectionFlag.NoUpdate)
                model.select(picker.table.model().index(row, 0),
                             QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
        QCoreApplication.processEvents()

    def test_one_button_ticks_or_unticks_every_highlighted_decal(self) -> None:
        picker = self.picker()           # A and B ticked
        self.assertFalse(picker.toggle_button.isEnabled())
        self.highlight(picker, {"SKL_B_P", "SKL_C_P", "SKL_D_P"})
        self.assertEqual(picker.toggle_button.text(), "Tick highlighted (3)")
        picker.toggle_highlighted()
        self.assertEqual(picker.value(), "SKL_A_P,SKL_B_P,SKL_C_P,SKL_D_P")
        self.assertEqual(picker.toggle_button.text(), "Untick highlighted (3)")
        picker.toggle_highlighted()
        self.assertEqual(picker.value(), "SKL_A_P")

    def test_a_highlighted_row_the_search_hides_is_left_alone(self) -> None:
        picker = self.picker()
        self.highlight(picker, {"SKL_C_P", "SKL_D_P"})
        picker.search.setText("whys")
        picker._filter()
        self.assertEqual(picker.toggle_button.text(), "Tick highlighted (1)")
        picker.toggle_highlighted()
        self.assertEqual(sorted(picker.ticked()), ["SKL_A_P", "SKL_B_P", "SKL_D_P"])

    def test_the_panel_says_what_the_decal_does(self) -> None:
        picker = self.picker()
        self.highlight(picker, {"SKL_A_P"})
        text = picker.about.toPlainText()
        for part in ("Alpha", "Attack up by 40%. Defence up by 15%.", "Stacks up to 50%.",
                     "How to get it: From Mushroom Club"):
            self.assertIn(part, text)

    def test_the_panel_never_takes_a_name_as_markup(self) -> None:
        from lid_db_manager.decals import Decal
        from lid_db_manager.ui.decal_picker import describe

        shown = describe(Decal("SKL_X_P", "<img src=x>Bold", 3, "Cat", "Does <b>this</b>."))
        self.assertNotIn("<img", shown)
        self.assertIn("&lt;img src=x&gt;Bold", shown)
        self.assertIn("Does &lt;b&gt;this&lt;/b&gt;.", shown)

    def test_the_setting_box(self) -> None:
        from lid_db_manager.ui.setting_editor import SettingEditor

        editor = SettingEditor(self.setting, "", db_path=self.db)
        self.assertEqual(editor._decals_summary.text(), "The normal stew's")
        seen = []
        editor.committed.connect(seen.append)
        editor.set_value("SKL_A_P,SKL_C_P")
        editor._commit()
        self.assertEqual((seen, editor._decals_summary.text()), (["SKL_A_P,SKL_C_P"], "2 decals picked"))
        self.assertTrue(editor.reset_button.isEnabled())
        editor.reset_to_default()
        self.assertEqual(seen[-1], "")
        QCoreApplication.processEvents()


if __name__ == "__main__":
    unittest.main()
