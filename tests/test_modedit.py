"""Renaming and re-describing a mod from inside the program.

The rename is the part worth testing hard. A mod's id is its folder name, and
that name also keys its snapshot file, its slot in the load order and its record
of having been applied. Move the folder without the rest and Revert quietly
stops working - the mod loses the only copy of the values it overwrote.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from fixtures import build_db, query, write_mod

from lid_db_manager.manager import Manager
from lid_db_manager.modedit import ModEditError, safe_folder_name
from lid_db_manager.paths import AppPaths

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication

    from lid_db_manager.ui.edit_dialog import EditModDialog

    HAVE_QT = True
except ImportError:  # pragma: no cover - depends on the environment
    HAVE_QT = False

PATCH = {
    "patches": [
        {
            "type": "update_set",
            "table": "master_skill",
            "set": {"val0": 42},
            "where": "id = 'SKL_EXPUP_01'",
        }
    ]
}


class EditingAMod(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.paths = AppPaths(self.root / "app").ensure()
        self.db = build_db(self.root / "game" / "masters.db")
        self.manager = Manager(self.paths)
        self.manager.set_db_path(self.db)
        write_mod(self.paths.mods_dir, "old-name", PATCH)
        self.manager.rescan()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _mod_json(self, mod_id: str) -> dict:
        return json.loads(
            (self.paths.mods_dir / mod_id / "mod.json").read_text(encoding="utf-8")
        )

    # -- details ---------------------------------------------------------

    def test_the_description_can_be_changed_without_renaming(self) -> None:
        mod = self.manager.edit_mod("old-name", "old-name", description="Now explained")
        self.assertEqual(mod.id, "old-name")
        self.assertEqual(mod.description, "Now explained")

    def test_editing_leaves_the_patches_alone(self) -> None:
        """Only the fields on the form are ours to touch."""
        before = self._mod_json("old-name")["patches"]
        self.manager.edit_mod("old-name", "old-name", author="someone")
        self.assertEqual(self._mod_json("old-name")["patches"], before)

    def test_fields_left_out_are_not_disturbed(self) -> None:
        self.manager.edit_mod("old-name", "old-name", description="Kept")
        mod = self.manager.edit_mod("old-name", "old-name", author="someone")
        self.assertEqual(mod.description, "Kept")

    def test_a_readme_can_be_written_and_removed(self) -> None:
        self.manager.edit_mod("old-name", "old-name", readme="# Hello\n\nSome notes.")
        mod = self.manager.scan.get("old-name")
        self.assertIsNotNone(mod.readme)
        self.assertIn("Some notes.", mod.readme.read_text(encoding="utf-8"))

        self.manager.edit_mod("old-name", "old-name", readme="")
        self.assertIsNone(self.manager.scan.get("old-name").readme)

    def test_a_blank_name_is_refused(self) -> None:
        with self.assertRaises(ModEditError):
            self.manager.edit_mod("old-name", "   ")

    # -- renaming --------------------------------------------------------

    def test_renaming_moves_the_folder(self) -> None:
        mod = self.manager.edit_mod("old-name", "New Name")
        self.assertEqual(mod.id, "New Name")
        self.assertTrue((self.paths.mods_dir / "New Name").is_dir())
        self.assertFalse((self.paths.mods_dir / "old-name").exists())
        self.assertEqual(self._mod_json("New Name")["id"], "New Name")

    def test_renaming_keeps_the_load_order_position(self) -> None:
        """Renaming must not change who wins a conflict."""
        write_mod(self.paths.mods_dir, "first", PATCH)
        write_mod(self.paths.mods_dir, "last", PATCH)
        self.manager.rescan()
        for mod_id in ("first", "old-name", "last"):
            self.manager.set_enabled(mod_id, True)

        self.manager.edit_mod("old-name", "Renamed")
        self.assertEqual(
            self.manager.state.enabled_mods, ["first", "Renamed", "last"]
        )

    def test_renaming_an_applied_mod_keeps_revert_working(self) -> None:
        """The whole reason this is not just os.rename."""
        self.manager.set_enabled("old-name", True)
        self.assertTrue(self.manager.save_mod_list().ok)
        self.assertEqual(
            query(self.db, "SELECT val0 FROM master_skill WHERE id = 'SKL_EXPUP_01'"),
            [(42,)],
        )

        self.manager.edit_mod("old-name", "Renamed")
        self.assertIn("Renamed", self.manager.state.applied)
        self.assertNotIn("old-name", self.manager.state.applied)

        results = self.manager.revert(["Renamed"])
        self.assertTrue(results[0].ok, results[0].error)
        self.assertEqual(
            query(self.db, "SELECT val0 FROM master_skill WHERE id = 'SKL_EXPUP_01'"),
            [(10,)],
            "revert should have put the vanilla value back",
        )

    def test_the_snapshot_file_moves_and_names_the_new_id(self) -> None:
        self.manager.set_enabled("old-name", True)
        self.manager.save_mod_list()
        self.manager.edit_mod("old-name", "Renamed")

        old = self.paths.snapshots_dir / "old-name.json"
        new = self.paths.snapshots_dir / "Renamed.json"
        self.assertFalse(old.exists())
        self.assertTrue(new.is_file())
        self.assertEqual(
            json.loads(new.read_text(encoding="utf-8"))["mod_id"], "Renamed"
        )

    def test_renaming_keeps_the_parts_the_player_switched_off(self) -> None:
        """A rename that dropped these would silently switch them all back on."""
        write_mod(
            self.paths.mods_dir,
            "many-parts",
            {
                "patches": [
                    {"type": "update_set", "id": "a", "table": "master_skill",
                     "set": {"val0": 1}, "where": "1=1"},
                    {"type": "update_set", "id": "b", "table": "master_body_detail",
                     "set": {"price": 1}, "where": "1=1"},
                ]
            },
        )
        self.manager.rescan()
        self.manager.set_part_enabled("many-parts", "b", False)

        mod = self.manager.edit_mod("many-parts", "Renamed Parts")
        self.assertEqual(
            self.manager.state.disabled_patches.get("Renamed Parts"), ["b"]
        )
        self.assertNotIn("many-parts", self.manager.state.disabled_patches)
        self.assertEqual([p.key for p in self.manager.active_mod(mod).patches], ["a"])

    def test_renaming_updates_modpacks(self) -> None:
        self.manager.set_enabled("old-name", True)
        self.manager.save_modpack("my pack")
        self.manager.edit_mod("old-name", "Renamed")
        self.assertIn("Renamed", self.manager.state.modpacks["my pack"])
        self.assertNotIn("old-name", self.manager.state.modpacks["my pack"])

    def test_renaming_onto_an_existing_folder_is_refused(self) -> None:
        write_mod(self.paths.mods_dir, "taken", PATCH)
        self.manager.rescan()
        with self.assertRaises(ModEditError):
            self.manager.edit_mod("old-name", "taken")
        self.assertTrue((self.paths.mods_dir / "old-name").is_dir(), "nothing moved")

    def test_a_name_with_path_characters_is_made_safe(self) -> None:
        mod = self.manager.edit_mod("old-name", "../escape/attempt")
        self.assertEqual(mod.id, safe_folder_name("../escape/attempt"))
        self.assertEqual(mod.folder.parent, self.paths.mods_dir)
        self.assertEqual(mod.name, "../escape/attempt")

    def test_renaming_to_the_same_name_is_a_no_op(self) -> None:
        mod = self.manager.edit_mod("old-name", "old-name", description="Changed")
        self.assertEqual(mod.id, "old-name")
        self.assertEqual(mod.description, "Changed")


class EditingASqlOnlyMod(unittest.TestCase):
    """A bare .sql folder has no mod.json - saving details should write one."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.paths = AppPaths(self.root / "app").ensure()
        self.db = build_db(self.root / "game" / "masters.db")
        folder = self.paths.mods_dir / "bare-sql"
        folder.mkdir(parents=True)
        (folder / "mod.sql").write_text(
            "UPDATE master_skill SET val0 = 5;", encoding="utf-8"
        )
        self.manager = Manager(self.paths)
        self.manager.set_db_path(self.db)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_it_starts_without_metadata(self) -> None:
        mod = self.manager.scan.get("bare-sql")
        self.assertEqual(mod.source, "mod.sql")
        self.assertIn("unknown", mod.author)

    def test_editing_gives_it_a_real_mod_json(self) -> None:
        mod = self.manager.edit_mod(
            "bare-sql", "Proper Name", description="Now it has one", author="me"
        )
        self.assertEqual(mod.source, "mod.json")
        self.assertEqual(mod.name, "Proper Name")
        self.assertEqual(mod.author, "me")
        self.assertEqual(len(mod.patches), 1, "the .sql should still be applied")

    def test_the_rewritten_mod_still_applies(self) -> None:
        mod = self.manager.edit_mod("bare-sql", "Proper Name")
        self.manager.set_enabled(mod.id, True)
        self.assertTrue(self.manager.save_mod_list().ok)
        self.assertEqual(
            {r[0] for r in query(self.db, "SELECT val0 FROM master_skill")}, {5}
        )


@unittest.skipUnless(HAVE_QT, "PySide6 is not installed")
class TheEditDialog(unittest.TestCase):
    app = None

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.paths = AppPaths(self.root / "app").ensure()
        self.db = build_db(self.root / "game" / "masters.db")
        self.manager = Manager(self.paths)
        self.manager.set_db_path(self.db)
        write_mod(
            self.paths.mods_dir,
            "old-name",
            {**PATCH, "description": "Original words", "author": "someone"},
        )
        self.manager.rescan()
        self.mod = self.manager.scan.get("old-name")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_it_opens_prefilled(self) -> None:
        dialog = EditModDialog(self.mod)
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(dialog.name_edit.text(), self.mod.name)
        self.assertEqual(dialog.description_edit.toPlainText(), "Original words")
        self.assertEqual(dialog.author_edit.text(), "someone")

    def test_it_warns_that_the_folder_will_move(self) -> None:
        dialog = EditModDialog(self.mod)
        self.addCleanup(dialog.deleteLater)
        self.assertIn("Folder:", dialog.folder_note.text())
        dialog.name_edit.setText("Something Else")
        self.assertIn("renamed", dialog.folder_note.text())
        self.assertIn("load order", dialog.folder_note.text())

    def test_a_blank_name_disables_save(self) -> None:
        dialog = EditModDialog(self.mod)
        self.addCleanup(dialog.deleteLater)
        self.assertTrue(dialog.ok_button.isEnabled())
        dialog.name_edit.setText("   ")
        self.assertFalse(dialog.ok_button.isEnabled())

    def test_details_round_trip_through_the_manager(self) -> None:
        dialog = EditModDialog(self.mod)
        self.addCleanup(dialog.deleteLater)
        dialog.name_edit.setText("Renamed Mod")
        dialog.description_edit.setPlainText("New words")
        dialog.readme_edit.setPlainText("# Notes")
        details = dialog.details()

        updated = self.manager.edit_mod(
            "old-name",
            details.name,
            description=details.description,
            author=details.author,
            version=details.version,
            readme=details.readme,
        )
        self.assertEqual(updated.id, "Renamed Mod")
        self.assertEqual(updated.description, "New words")
        self.assertIn("# Notes", updated.readme.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
