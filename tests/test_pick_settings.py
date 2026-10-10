"""Settings that pick any number of entries off the mod's own list.

The same promise as a choice: only values the mod itself lists reach the SQL,
so a pick can sit between quotes and never close them. Until the player ticks
anything the placeholder is the mod's own default list.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from fixtures import build_db, write_mod

from lid_db_manager.errors import ModLoadError
from lid_db_manager.manager import Manager
from lid_db_manager.paths import AppPaths
from lid_db_manager.settings import parse_settings, render_text, render_values, resolve_values

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QCoreApplication, Qt
    from PySide6.QtWidgets import QApplication

    HAVE_QT = True
except ImportError:  # pragma: no cover - the laptop's Python has no PySide6
    HAVE_QT = False

OPTIONS = [
    {"value": "Q_A", "label": "Defeat 50 Haters", "group": "Weekly", "help": "First clear: 100,000 SP"},
    {"value": "Q_B", "label": "Collect 20 mushrooms", "group": "Weekly"},
    {"value": "Q_C", "label": "Defeat 1000 Screamers", "group": "Season", "help": "<b>bold</b>"},
]
PICK = {"id": "quests", "label": "Quests", "type": "pick", "noun": "quest",
        "default_label": "The weekly ones", "default": ["Q_A", "Q_B"], "options": OPTIONS}


def parse(entry):
    [setting] = parse_settings([entry], "m")
    return setting


class DeclaringOne(unittest.TestCase):
    def test_it_loads_with_its_default_list(self) -> None:
        setting = parse(PICK)
        self.assertEqual((setting.kind, setting.default, setting.default_picks, setting.noun),
                         ("pick", "", ("Q_A", "Q_B"), "quest"))
        self.assertEqual(setting.display(""), "The weekly ones")
        self.assertEqual(setting.display("Q_C"), "1 of 3 quests picked")

    def test_no_default_means_every_entry(self) -> None:
        setting = parse({k: v for k, v in PICK.items() if k != "default"})
        self.assertEqual(setting.default_picks, ("Q_A", "Q_B", "Q_C"))

    def test_a_default_must_name_its_own_options(self) -> None:
        for bad in ([], ["Q_A", "Q_Z"], "Q_A", [3]):
            with self.subTest(default=bad), self.assertRaises(ModLoadError):
                parse(dict(PICK, default=bad))

    def test_options_need_safe_values_and_one_word_for_an_entry(self) -> None:
        for bad in ([{"value": "x'y", "label": "X"}], [{"value": "", "label": "None"}], []):
            with self.subTest(options=bad), self.assertRaises(ModLoadError):
                parse({k: v for k, v in dict(PICK, options=bad).items() if k != "default"})
        with self.assertRaises(ModLoadError):
            parse(dict(PICK, noun="a; drop"))


class Choosing(unittest.TestCase):
    def setUp(self) -> None:
        self.setting = parse(PICK)

    def values(self, picked):
        return resolve_values([self.setting], {"quests": picked})

    def test_only_listed_entries_are_kept(self) -> None:
        self.assertEqual(self.values("Q_C,Q_A,Q_C"), ({"quests": "Q_C,Q_A"}, []))
        self.assertEqual(self.values("Q_GONE,Q_B"), ({"quests": "Q_B"}, []))
        self.assertEqual(self.values("Q_GONE"), ({"quests": ""}, []))

    def test_anything_that_could_carry_sql_is_refused(self) -> None:
        for bad in ("Q_A'; DROP TABLE x; --", ["Q_A", "a b"], 5, None):
            with self.subTest(picked=bad):
                values, problems = self.values(bad)
                self.assertEqual((values, len(problems)), ({"quests": ""}, 1))

    def test_in_sql_the_ticks_or_the_default_quoted(self) -> None:
        picked = render_values([self.setting], {"quests": "Q_C"})
        self.assertEqual(render_text("IN ({{quests}})", picked), "IN ('Q_C')")
        self.assertEqual(render_text("{{quests:label}}", picked), "1 of 3 quests picked")
        default = render_values([self.setting], {"quests": ""})
        self.assertEqual(render_text("IN ({{quests}})", default), "IN ('Q_A','Q_B')")


class ThroughTheManager(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.paths = AppPaths(root).ensure()
        self.db = build_db(root / "game" / "masters.db")
        write_mod(self.paths.mods_dir, "pick-mod", {
            "apply": "diff",
            "settings": [PICK],
            "patches": [{"type": "raw_sql", "id": "q",
                         "sql": "UPDATE master_text SET txt = 'picked' WHERE id IN ({{quests}});"}],
        })
        self.manager = Manager(self.paths)
        self.manager.set_db_path(self.db)
        self.manager.set_enabled("pick-mod", True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_a_pick_is_stored_checked_and_survives_a_restart(self) -> None:
        self.manager.set_mod_setting("pick-mod", "quests", "Q_C,Q_A")
        self.assertTrue(self.manager.save_mod_list().ok)
        again = Manager(self.paths)
        again.set_db_path(self.db)
        self.assertEqual(again.configured_mod("pick-mod").values["quests"], "Q_C,Q_A")
        with self.assertRaises(ValueError):
            self.manager.set_mod_setting("pick-mod", "quests", "Q_A,x'; --")


@unittest.skipUnless(HAVE_QT, "PySide6 is not installed")
class TheWindow(unittest.TestCase):
    app = None

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.setting = parse(PICK)

    def picker(self, value=""):
        from lid_db_manager.ui.list_picker import OptionPicker

        return OptionPicker(self.setting, value)

    def test_it_lists_the_mods_entries_with_the_default_ticked(self) -> None:
        picker = self.picker()
        self.assertEqual(picker.table.rowCount(), 3)
        self.assertEqual(sorted(picker.ticked()), ["Q_A", "Q_B"])
        self.assertEqual(picker.value(), "")
        self.assertIn("2 of 3 quests ticked - the default (The weekly ones)", picker.count.text())
        self.assertEqual(picker.windowTitle(), "Choose quests - Quests")

    def test_a_change_and_back(self) -> None:
        picker = self.picker()
        picker._tick_shown(True)
        self.assertEqual(picker.value(), "Q_A,Q_B,Q_C")
        picker._back_to_default()
        self.assertEqual(picker.value(), "")
        picker._tick_shown(False)
        self.assertFalse(picker.ok_button.isEnabled())

    def test_search_by_group(self) -> None:
        picker = self.picker()
        picker.search.setText("season")
        picker._filter()
        shown = [picker.table.item(r, 0).data(Qt.ItemDataRole.UserRole + 1)
                 for r in range(picker.table.rowCount()) if not picker.table.isRowHidden(r)]
        self.assertEqual(shown, ["Q_C"])

    def test_the_panel_never_takes_help_as_markup(self) -> None:
        picker = self.picker()
        self.assertNotIn("<b>bold</b>", picker.by_id["Q_C"].about)
        self.assertIn("&lt;b&gt;bold&lt;/b&gt;", picker.by_id["Q_C"].about)

    def test_the_setting_box(self) -> None:
        from lid_db_manager.ui.setting_editor import SettingEditor

        editor = SettingEditor(self.setting, "")
        self.assertEqual((editor.decals_button.text(), editor._decals_summary.text()),
                         ("Choose quests...", "The weekly ones"))
        seen = []
        editor.committed.connect(seen.append)
        editor.set_value("Q_C")
        editor._commit()
        self.assertEqual((seen, editor._decals_summary.text()), (["Q_C"], "1 of 3 quests picked"))
        editor.reset_to_default()
        self.assertEqual(seen[-1], "")
        QCoreApplication.processEvents()


if __name__ == "__main__":
    unittest.main()
