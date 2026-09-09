"""Parsing mod.json into a Mod."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fixtures import write_mod

from lid_db_manager.errors import ModLoadError
from lid_db_manager.mod import load_mod_json
from lid_db_manager.patch import RawSqlFilePatch, TextReplacePatch, UpdateSetPatch


class ModJsonTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.mods = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_loads_every_field(self) -> None:
        folder = write_mod(
            self.mods,
            "full-mod",
            {
                "name": "Full Mod",
                "description": "does things",
                "version": "2.1.0",
                "author": "someone",
                "homepage": "https://example.invalid",
                "requires": ["other"],
                "conflicts_with": ["enemy"],
                "raw_sql_files_do_not_touch": ["master_text"],
                "patches": [
                    {"type": "update_set", "table": "t", "set": {"c": 1}, "where": "c > 1"},
                    {
                        "type": "text_replace",
                        "table": "master_text",
                        "match": {"id": "X"},
                        "replace": [{"find": "a", "with": "b"}],
                    },
                    {"type": "raw_sql_file", "path": "patch.sql"},
                ],
            },
            patch__sql="UPDATE t SET c = 1;",
        )
        mod = load_mod_json(folder)

        self.assertEqual(mod.id, "full-mod")
        self.assertEqual(mod.version, "2.1.0")
        self.assertEqual(mod.requires, ["other"])
        self.assertEqual(mod.conflicts_with, ["enemy"])
        self.assertEqual(mod.raw_sql_files_do_not_touch, ["master_text"])
        self.assertIsInstance(mod.patches[0], UpdateSetPatch)
        self.assertIsInstance(mod.patches[1], TextReplacePatch)
        self.assertIsInstance(mod.patches[2], RawSqlFilePatch)
        self.assertEqual(mod.load_warnings, [])

    def test_folder_name_wins_over_a_mismatched_id(self) -> None:
        folder = write_mod(
            self.mods,
            "folder-name",
            {"id": "something-else", "patches": [{"type": "raw_sql", "sql": "SELECT 1"}]},
        )
        mod = load_mod_json(folder)
        self.assertEqual(mod.id, "folder-name")
        self.assertTrue(any("does not match its folder name" in w for w in mod.load_warnings))

    def test_missing_required_fields_are_named(self) -> None:
        folder = self.mods / "broken"
        folder.mkdir()
        (folder / "mod.json").write_text(json.dumps({"id": "broken"}), encoding="utf-8")
        with self.assertRaises(ModLoadError) as caught:
            load_mod_json(folder)
        self.assertIn("name", caught.exception.reason)
        self.assertIn("description", caught.exception.reason)

    def test_invalid_json_reports_the_position(self) -> None:
        folder = self.mods / "bad-json"
        folder.mkdir()
        (folder / "mod.json").write_text('{"id": "bad-json",}', encoding="utf-8")
        with self.assertRaises(ModLoadError) as caught:
            load_mod_json(folder)
        self.assertIn("not valid JSON", caught.exception.reason)

    def test_unknown_patch_type_lists_the_valid_ones(self) -> None:
        folder = write_mod(self.mods, "weird", {"patches": [{"type": "teleport"}]})
        with self.assertRaises(ModLoadError) as caught:
            load_mod_json(folder)
        self.assertIn("teleport", caught.exception.reason)
        self.assertIn("update_set", caught.exception.reason)

    def test_empty_patch_list_is_rejected(self) -> None:
        folder = write_mod(self.mods, "empty", {"patches": []})
        with self.assertRaises(ModLoadError):
            load_mod_json(folder)

    def test_raw_sql_file_cannot_escape_the_mod_folder(self) -> None:
        folder = write_mod(
            self.mods, "escapee", {"patches": [{"type": "raw_sql_file", "path": "../evil.sql"}]}
        )
        with self.assertRaises(ModLoadError) as caught:
            load_mod_json(folder)
        self.assertIn("outside the mod folder", caught.exception.reason)

    def test_requires_must_be_a_string_list(self) -> None:
        folder = write_mod(
            self.mods,
            "bad-requires",
            {"requires": [1, 2], "patches": [{"type": "raw_sql", "sql": "SELECT 1"}]},
        )
        with self.assertRaises(ModLoadError):
            load_mod_json(folder)

    def test_affects_label_lists_tables_and_columns(self) -> None:
        folder = write_mod(
            self.mods,
            "affects",
            {
                "patches": [
                    {"type": "update_set", "table": "master_skill", "set": {"buy_money": 1}},
                    {"type": "raw_sql", "sql": "UPDATE master_text SET txt = 'x'"},
                ]
            },
        )
        label = load_mod_json(folder).affects_label()
        self.assertIn("master_skill (buy_money)", label)
        self.assertIn("master_text (whole table)", label)


if __name__ == "__main__":
    unittest.main()
