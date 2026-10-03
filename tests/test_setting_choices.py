"""Settings that are a pick from a list, or on/off, rather than a number.

The same promise as the number settings: whatever a player does in the
Configuration tab, only a value the mod itself declared reaches the SQL. A
choice's values are letters, digits and underscores, so one can sit between
quotes and never close them; a toggle is 1 or 0.

A choice can also decide which rows a mod writes at all - "None" writes no row
where a decal writes one - so changing it has to take the old rows away.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from fixtures import build_db, query, write_mod

from lid_db_manager.db_record import _clean_values
from lid_db_manager.errors import ModLoadError
from lid_db_manager.manager import Manager
from lid_db_manager.mod import load_mod_json
from lid_db_manager.paths import AppPaths
from lid_db_manager.settings import parse_settings, render_text, render_values
from lid_db_manager.state import AppliedRecord, State

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QCoreApplication, Qt
    from PySide6.QtWidgets import QApplication, QLabel

    HAVE_QT = True
except ImportError:  # pragma: no cover - the laptop's Python has no PySide6
    HAVE_QT = False

SKILLS = [
    {"value": "", "label": "None"},
    {"value": "SKL_EXPUP_01", "label": "Exp Up", "group": "Growth", "help": "More EXP."},
    {"value": "SKL_POWER_01", "label": "Power Up", "group": "Fighting", "help": "Hits harder."},
]
SKILL = {"id": "skill", "label": "Passive", "type": "choice", "default": "",
         "options_from": "skills.json", "group": "Class A"}
EXTRA = {"id": "extra", "label": "Extra row", "type": "toggle", "default": False, "group": "Class A"}

SQL = """
INSERT OR REPLACE INTO master_skill (id, name, buy_money, val0)
SELECT 'SKL_CLASS_A', name, 0, val0 FROM master_skill WHERE id = '{{skill}}';
INSERT OR REPLACE INTO master_skill (id, name, buy_money, val0)
SELECT 'SKL_CLASS_A_EXTRA', 'Extra', 0, 1 WHERE {{extra}} = 1;
"""


def a_class_mod(mods_dir: Path, mod_id: str = "class-bonus", skills=None) -> Path:
    return write_mod(
        mods_dir, mod_id,
        {
            "description": "Class A gets {{skill:label}}; extra row {{extra:label}}.",
            "apply": "diff",
            "settings": [SKILL, EXTRA],
            "patches": [{"type": "raw_sql_file", "path": "bonus.sql"}],
        },
        skills__json=json.dumps(skills if skills is not None else SKILLS),
        bonus__sql=SQL,
    )


class DeclaringChoices(unittest.TestCase):
    def test_a_choice_and_a_toggle(self) -> None:
        inline = {k: v for k, v in SKILL.items() if k != "options_from"} | {"options": SKILLS}
        choice, toggle = parse_settings([inline, EXTRA], "t")
        self.assertFalse(choice.is_number)
        self.assertEqual(choice.default, "")
        self.assertEqual(choice.display("SKL_POWER_01"), "Power Up")
        self.assertEqual(choice.option("SKL_EXPUP_01").group, "Growth")
        self.assertEqual(choice.group, "Class A")
        self.assertEqual(toggle.default, 0)
        self.assertEqual(toggle.display(1), "On")
        self.assertEqual(toggle.display(0), "Off")

    def test_bad_declarations_are_refused(self) -> None:
        inline = {k: v for k, v in SKILL.items() if k != "options_from"}
        cases = {
            "a quote in a value": [{**inline, "options": [{"value": "A'B", "label": "x"}]}],
            "a space in a value": [{**inline, "options": [{"value": "A B", "label": "x"}]}],
            "a dash in a value": [{**inline, "options": [{"value": "A-B", "label": "x"}]}],
            "a value that is not text": [{**inline, "default": 1, "options": [{"value": 1, "label": "x"}]}],
            "no label": [{**inline, "options": [{"value": "A"}]}],
            "the same value twice": [{**inline, "options": [{"value": "", "label": "x"},
                                                            {"value": "", "label": "y"}]}],
            "two labels only case apart": [{**inline, "options": [{"value": "", "label": "Tank"},
                                                                  {"value": "B", "label": "TANK"}]}],
            "no options": [{**inline, "options": []}],
            "default not listed": [{**inline, "default": "SKL_MISSING", "options": SKILLS}],
            "both kinds of list": [{**SKILL, "options": SKILLS}],
            "a toggle that is maybe": [{**EXTRA, "default": "maybe"}],
            "too many options": [{**inline, "options": [
                {"value": f"V{i}", "label": f"L{i}"} for i in range(5001)]}],
        }
        for name, raw in cases.items():
            with self.subTest(case=name):
                with self.assertRaises(ModLoadError):
                    parse_settings(raw, "t")

    def test_a_list_file_outside_the_mod_folder_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "mod"
            folder.mkdir()
            (Path(tmp) / "elsewhere.json").write_text(json.dumps(SKILLS), encoding="utf-8")
            with self.assertRaises(ModLoadError):
                parse_settings([{**SKILL, "options_from": "../elsewhere.json"}], "t", folder)
            with self.assertRaises(ModLoadError):
                parse_settings([{**SKILL, "options_from": "missing.json"}], "t", folder)

    def test_settings_sharing_a_list_file_share_one_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "skills.json").write_text(json.dumps(SKILLS), encoding="utf-8")
            first, second = parse_settings([SKILL, {**SKILL, "id": "other"}], "t", folder)
            self.assertIs(first.options, second.options)


class ChoosingValues(unittest.TestCase):
    def setUp(self) -> None:
        inline = {k: v for k, v in SKILL.items() if k != "options_from"} | {"options": SKILLS}
        self.choice, self.toggle = parse_settings([inline, EXTRA], "t")

    def test_only_a_listed_value_is_taken(self) -> None:
        self.assertEqual(self.choice.coerce("SKL_POWER_01"), "SKL_POWER_01")
        for bad in ("x'; DROP TABLE master_skill; --", "SKL_NOT_LISTED", "skl_power_01", 3, None):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    self.choice.coerce(bad)

    def test_a_toggle_is_one_or_zero(self) -> None:
        for given, wanted in ((True, 1), (False, 0), (1, 1), (0, 0), ("on", 1), ("Off", 0), ("true", 1)):
            with self.subTest(value=given):
                self.assertEqual(self.toggle.coerce(given), wanted)
        for bad in (2, -1, "maybe", 0.5, None):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    self.toggle.coerce(bad)

    def test_filling_in_sql_and_text(self) -> None:
        values = render_values([self.choice, self.toggle], {"skill": "SKL_POWER_01", "extra": 1})
        self.assertEqual(render_text("id = '{{skill}}' AND {{extra}} = 1", values),
                         "id = 'SKL_POWER_01' AND 1 = 1")
        self.assertEqual(render_text("{{skill:label}} / {{extra:label}}", values), "Power Up / On")


class LoadingAChoiceMod(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.mods = Path(self._tmp.name) / "mods"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_it_loads_and_fills_in(self) -> None:
        mod = load_mod_json(a_class_mod(self.mods))
        self.assertEqual(mod.load_warnings, [])
        self.assertEqual(mod.values, {"skill": "", "extra": 0})
        self.assertEqual(mod.description, "Class A gets None; extra row Off.")
        chosen = mod.with_settings({"skill": "SKL_EXPUP_01", "extra": 1})
        self.assertIn("WHERE id = 'SKL_EXPUP_01'", chosen.patches[0].sql_text())
        self.assertIn("WHERE 1 = 1", chosen.patches[0].sql_text())
        self.assertEqual(chosen.description, "Class A gets Exp Up; extra row On.")

    def test_a_number_style_on_a_choice_will_not_load(self) -> None:
        folder = write_mod(self.mods, "wrong-style", {
            "settings": [{k: v for k, v in SKILL.items() if k != "options_from"} | {"options": SKILLS}],
            "patches": [{"type": "raw_sql", "sql": "SELECT '{{skill:comma}}';"}],
        })
        with self.assertRaises(ModLoadError):
            load_mod_json(folder)

    def test_a_label_on_a_number_will_not_load(self) -> None:
        folder = write_mod(self.mods, "wrong-label", {
            "settings": [{"id": "n", "type": "integer", "default": 1, "min": 0, "max": 5}],
            "patches": [{"type": "raw_sql", "sql": "SELECT {{n:label}};"}],
        })
        with self.assertRaises(ModLoadError):
            load_mod_json(folder)


class ThroughTheManager(unittest.TestCase):
    """Chosen, changed, set back to None and switched off - leaving nothing."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.paths = AppPaths(self.root).ensure()
        self.db = build_db(self.root / "game" / "masters.db")
        self.stock = self.skills()
        a_class_mod(self.paths.mods_dir)
        self.manager = Manager(self.paths)
        self.manager.set_db_path(self.db)
        self.manager.set_enabled("class-bonus", True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def skills(self):
        return sorted(query(self.db, "SELECT id, name, buy_money, val0 FROM master_skill"))

    def added(self):
        return sorted(set(self.skills()) - set(self.stock))

    def choose(self, **values) -> None:
        for setting_id, value in values.items():
            self.manager.set_mod_setting("class-bonus", setting_id, value)
        self.assertTrue(self.manager.save_mod_list().ok)

    def test_the_chosen_decal_is_copied(self) -> None:
        self.choose(skill="SKL_POWER_01")
        self.assertEqual(self.added(), [("SKL_CLASS_A", "Power Up", 0, 5)])

    def test_changing_the_choice_replaces_the_row(self) -> None:
        self.choose(skill="SKL_POWER_01")
        self.choose(skill="SKL_EXPUP_01")
        self.assertEqual(self.added(), [("SKL_CLASS_A", "Exp Up", 0, 10)])

    def test_back_to_none_takes_the_row_away(self) -> None:
        self.choose(skill="SKL_POWER_01", extra=True)
        self.assertEqual(len(self.added()), 2)
        self.choose(skill="")
        self.assertEqual(self.added(), [("SKL_CLASS_A_EXTRA", "Extra", 0, 1)])
        self.choose(extra=False)
        self.assertEqual(self.skills(), self.stock)

    def test_switching_it_off_leaves_stock(self) -> None:
        self.choose(skill="SKL_POWER_01", extra=True)
        self.manager.set_enabled("class-bonus", False)
        self.assertTrue(self.manager.save_mod_list().ok)
        self.assertEqual(self.skills(), self.stock)

    def test_the_choice_survives_a_restart(self) -> None:
        self.choose(skill="SKL_POWER_01")
        again = Manager(self.paths)
        again.set_db_path(self.db)
        self.assertEqual(again.configured_mod("class-bonus").values["skill"], "SKL_POWER_01")
        self.assertEqual(again.state.applied["class-bonus"].values["skill"], "SKL_POWER_01")
        # Nothing changed, so saving again leaves the row exactly as it is.
        self.assertTrue(again.save_mod_list().ok)
        self.assertEqual(self.added(), [("SKL_CLASS_A", "Power Up", 0, 5)])

    def test_a_refused_value_is_not_stored(self) -> None:
        with self.assertRaises(ValueError):
            self.manager.set_mod_setting("class-bonus", "skill", "x'; DROP TABLE master_skill; --")
        self.assertNotIn("class-bonus", self.manager.state.mod_settings)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
VANILLA_5042 = PROJECT_ROOT / "LiD Vanilla DB" / "5.0.4.2" / "masters.db"
# Released on its own rather than with the manager, so it may not be here.
FIGHTER_PASSIVES = PROJECT_ROOT / "mods" / "fighter-passives"


@unittest.skipUnless(VANILLA_5042.is_file(), f"no vanilla database at {VANILLA_5042}")
@unittest.skipUnless(FIGHTER_PASSIVES.is_dir(), "Fighter Class Passives is not in mods/")
class TheFighterPassivesMod(unittest.TestCase):
    """The shipped mod, on the real game's database."""

    def setUp(self) -> None:
        import shutil

        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.paths = AppPaths(self.root).ensure()
        folder = self.paths.mods_dir / "fighter-passives"
        shutil.copytree(PROJECT_ROOT / "mods" / "fighter-passives", folder)
        # The database half only: the BrgGame.upk hook needs a real game folder,
        # and is tested on the real package in test_package_stacking.
        mod_json = json.loads((folder / "mod.json").read_text(encoding="utf-8"))
        mod_json["patches"] = [p for p in mod_json["patches"] if p["type"] != "tfc_installer"]
        (folder / "mod.json").write_text(json.dumps(mod_json), encoding="utf-8")
        self.db = self.root / "game" / "BrgGame" / "Content" / "masters.db"
        self.db.parent.mkdir(parents=True)
        shutil.copy2(VANILLA_5042, self.db)
        self.manager = Manager(self.paths)
        self.manager.set_db_path(self.db)
        self.manager.set_enabled("fighter-passives", True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def passives(self):
        return query(
            self.db,
            "SELECT id, type, is_nosale, is_display, is_display_list, buy_money, platform, "
            "no_steam > 0 FROM master_skill WHERE id GLOB 'SKL_FTYPE_*' OR id GLOB 'SKL_FMAP_*' ORDER BY id",
        )

    def shop_text(self, text_id: str) -> str:
        return query(self.db, "SELECT txt FROM master_text WHERE sct = 'FTYPE_DSC' AND id = ? "
                              "AND lang = 'int'", (text_id,))[0][0]

    def test_it_loads_with_a_decal_and_a_map_switch_per_class(self) -> None:
        mod = self.manager.scan.get("fighter-passives")
        self.assertEqual(mod.load_warnings, [])
        self.assertEqual(len(mod.settings), 16)
        self.assertGreater(len(mod.setting("col_skill").options), 300)

    def test_by_default_it_changes_nothing(self) -> None:
        self.assertTrue(self.manager.save_mod_list().ok)
        self.assertEqual(self.passives(), [])

    def test_hidden_copies_and_the_shop_text(self) -> None:
        self.manager.set_mod_setting("fighter-passives", "col_map", True)
        self.manager.set_mod_setting("fighter-passives", "bal_skill", "SKL_PATROL_WOT_P")  # PS4-only
        self.manager.set_mod_setting("fighter-passives", "luk_skill", "SKL_ADVENTURE_01_P")
        self.assertTrue(self.manager.save_mod_list().ok)
        self.assertEqual(self.passives(), [
            ("SKL_FMAP_COL", "SKLTP_SEARCHUP_ITEM", 1, 0, 0, 0, 0, 1),
            ("SKL_FTYPE_BAL", "SKLTP_PATROL_WOT", 1, 0, 0, 0, 0, 1),
            ("SKL_FTYPE_LUK", "SKLTP_ADVENTURE", 1, 0, 0, 0, 0, 1),
        ])
        self.assertTrue(self.shop_text("TXT_COL").endswith(" Passive: map reveal."))
        self.assertTrue(self.shop_text("TXT_BAL").endswith(" Passive: PATROL DUTY."))
        self.assertTrue(self.shop_text("TXT_LUC").endswith(" Passive: Spy."))

    def test_switching_it_off_leaves_stock(self) -> None:
        before = query(self.db, "SELECT count(*) FROM master_skill")
        self.manager.set_mod_setting("fighter-passives", "tec_skill", "SKL_STRENGTHEN_BODY_01_P")
        self.manager.set_mod_setting("fighter-passives", "tec_map", True)
        self.assertTrue(self.manager.save_mod_list().ok)
        self.manager.set_enabled("fighter-passives", False)
        self.assertTrue(self.manager.save_mod_list().ok)
        self.assertEqual(query(self.db, "SELECT count(*) FROM master_skill"), before)
        self.assertNotIn("Passive", self.shop_text("TXT_TEC"))


class WhatIsReadBackFromFiles(unittest.TestCase):
    def test_state_keeps_choices_and_drops_anything_else(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text(json.dumps({"mod_settings": {"m": {
                "skill": "SKL_POWER_01", "n": 3, "bad": "a'b", "flag": True}}}), encoding="utf-8")
            state = State.load(path)
            self.assertEqual(state.mod_settings, {"m": {"skill": "SKL_POWER_01", "n": 3}})

    def test_the_applied_record_keeps_choices(self) -> None:
        record = AppliedRecord.from_dict({"values": {"skill": "SKL_POWER_01", "bad": "x y", "n": 2}})
        self.assertEqual(record.values, {"skill": "SKL_POWER_01", "n": 2})

    def test_the_database_note_keeps_choices(self) -> None:
        self.assertEqual(
            _clean_values(json.dumps({"skill": "SKL_POWER_01", "n": 2, "bad": "a;b", "t": True})),
            {"skill": "SKL_POWER_01", "n": 2},
        )


@unittest.skipUnless(HAVE_QT, "PySide6 is not installed")
class TheConfigurationTab(unittest.TestCase):
    app = None

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        from lid_db_manager.ui.main_window import MainWindow

        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.paths = AppPaths(self.root).ensure()
        self.db = build_db(self.root / "game" / "masters.db")
        a_class_mod(self.paths.mods_dir)
        # A long list, which gets a box to type into.
        many = [{"value": "", "label": "None"}] + [
            {"value": f"SKL_{i:02d}", "label": f"Decal {i:02d}", "group": "Odd" if i % 2 else "Even"}
            for i in range(20)
        ]
        a_class_mod(self.paths.mods_dir, "long-list", skills=many)
        self.manager = Manager(self.paths)
        self.manager.set_db_path(self.db)
        self.window = MainWindow(self.manager)
        self.window.show()
        self._settle()

    def tearDown(self) -> None:
        from test_ui import destroy

        destroy(self.window)
        self._settle()
        self._tmp.cleanup()

    def _settle(self) -> None:
        for _ in range(5):
            QCoreApplication.processEvents()

    def _open(self, mod_id: str):
        self.window.mod_list.select_mods([mod_id])
        self._settle()
        return self.window.diff_view

    def test_a_heading_per_group(self) -> None:
        view = self._open("class-bonus")
        headings = [label.text() for label in view.configuration.widget().findChildren(QLabel)
                    if label.objectName() == "settingGroup"]
        self.assertEqual(headings, ["Class A"])

    def test_picking_from_the_list_chooses_it(self) -> None:
        editor = self._open("class-bonus").editor_for("skill")
        index = editor.combo.findData("SKL_POWER_01")
        editor.combo.setCurrentIndex(index)
        editor.combo.activated.emit(index)
        self._settle()
        self.assertEqual(self.manager.state.mod_settings, {"class-bonus": {"skill": "SKL_POWER_01"}})

    def test_group_headings_in_the_list_cannot_be_chosen(self) -> None:
        editor = self._open("class-bonus").editor_for("skill")
        heading = editor.combo.findText("Growth")
        self.assertGreaterEqual(heading, 0)
        self.assertIsNone(editor.combo.itemData(heading))
        self.assertEqual(editor.combo.model().item(heading).flags(), Qt.ItemFlag.NoItemFlags)

    def test_a_short_list_is_not_typed_into(self) -> None:
        self.assertFalse(self._open("class-bonus").editor_for("skill").combo.isEditable())

    def test_typing_a_name_in_a_long_list_chooses_it(self) -> None:
        editor = self._open("long-list").editor_for("skill")
        self.assertTrue(editor.combo.isEditable())
        editor.combo.lineEdit().setText("decal 07")
        editor.combo.lineEdit().editingFinished.emit()
        self._settle()
        self.assertEqual(self.manager.state.mod_settings, {"long-list": {"skill": "SKL_07"}})

    def test_typing_nonsense_changes_nothing(self) -> None:
        editor = self._open("long-list").editor_for("skill")
        editor.combo.lineEdit().setText("'; DROP TABLE")
        editor.combo.lineEdit().editingFinished.emit()
        self._settle()
        self.assertNotIn("long-list", self.manager.state.mod_settings)
        self.assertEqual(editor.combo.currentText(), "None")

    def test_the_tick_box(self) -> None:
        editor = self._open("class-bonus").editor_for("extra")
        editor.check.setChecked(True)
        self._settle()
        self.assertEqual(self.manager.state.mod_settings, {"class-bonus": {"extra": 1}})
        self.window.diff_view.editor_for("extra").reset_button.click()
        self._settle()
        self.assertNotIn("class-bonus", self.manager.state.mod_settings)

    def test_a_change_stays_on_the_tab_with_the_same_boxes(self) -> None:
        """It used to flash, rebuild every box and land back on Details."""
        self.window._open_configuration("class-bonus")
        self._settle()
        view = self.window.diff_view
        editor, tick = view.editor_for("skill"), view.editor_for("extra")
        index = editor.combo.findData("SKL_POWER_01")
        editor.combo.setCurrentIndex(index)
        editor.combo.activated.emit(index)
        self._settle()
        tick.check.setChecked(True)
        self._settle()
        self.assertIs(view.tabs.currentWidget(), view.configuration)
        self.assertIs(view.editor_for("skill"), editor)
        self.assertIs(view.editor_for("extra"), tick)
        self.assertEqual(editor.value(), "SKL_POWER_01")
        self.assertTrue(editor.reset_button.isEnabled())
        self.assertEqual(self.manager.state.mod_settings,
                         {"class-bonus": {"skill": "SKL_POWER_01", "extra": 1}})

    def test_values_changed_elsewhere_show_in_the_same_boxes(self) -> None:
        view = self._open("class-bonus")
        editor = view.editor_for("skill")
        self.manager.set_mod_setting("class-bonus", "skill", "SKL_EXPUP_01")
        view.refresh()
        self.assertIs(view.editor_for("skill"), editor)
        self.assertEqual(editor.combo.currentText(), "Exp Up")
        self.assertFalse(editor.pending())

    def test_rebuilding_the_list_does_not_report_the_same_selection(self) -> None:
        self._open("class-bonus")
        seen = []
        self.window.mod_list.selectionChangedTo.connect(seen.append)
        self.window.mod_list.refresh()
        self._settle()
        self.assertEqual(seen, [])
        self.assertEqual(self.window.mod_list.selected_mod_ids(), ["class-bonus"])

    def test_put_all_back_to_defaults(self) -> None:
        self.manager.set_mod_setting("class-bonus", "skill", "SKL_EXPUP_01")
        self.manager.set_mod_setting("class-bonus", "extra", True)
        from PySide6.QtWidgets import QPushButton

        view = self._open("class-bonus")
        buttons = [b for b in view.configuration.widget().findChildren(QPushButton)
                   if b.text() == "Put all back to defaults"]
        buttons[0].click()
        self._settle()
        self.assertNotIn("class-bonus", self.manager.state.mod_settings)


if __name__ == "__main__":
    unittest.main()
