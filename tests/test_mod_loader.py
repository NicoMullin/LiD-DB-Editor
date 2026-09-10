"""Scanning mods/ and ordering the result."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fixtures import write_mod, write_sql_mod

from lid_db_manager.mod import SOURCE_JSON, SOURCE_SQL, SOURCE_SQL_PAIR, Mod
from lid_db_manager.mod_loader import out_of_order_requirements, scan_mods

SIMPLE_PATCH = {"patches": [{"type": "raw_sql", "sql": "UPDATE t SET c = 1"}]}


def stub(mod_id: str, requires: list[str] | None = None) -> Mod:
    return Mod(
        id=mod_id,
        name=mod_id,
        description="",
        version="1.0.0",
        author="tests",
        folder=Path("."),
        requires=requires or [],
    )


class ScanTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.mods = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_layout_1_mod_json(self) -> None:
        write_mod(self.mods, "json-mod", SIMPLE_PATCH)
        result = scan_mods(self.mods)
        self.assertEqual([m.id for m in result.mods], ["json-mod"])
        self.assertEqual(result.mods[0].source, SOURCE_JSON)

    def test_layout_2_single_sql_file(self) -> None:
        write_sql_mod(self.mods, "sql-mod", "UPDATE t SET c = 1;")
        mod = scan_mods(self.mods).mods[0]
        self.assertEqual(mod.source, SOURCE_SQL)
        self.assertEqual(mod.name, "Sql Mod")
        self.assertIn("unknown", mod.author)
        self.assertFalse(mod.has_inverse_sql)

    def test_layout_3_sql_pair_supports_revert(self) -> None:
        write_sql_mod(self.mods, "paired", "UPDATE t SET c = 1;", "UPDATE t SET c = 0;")
        mod = scan_mods(self.mods).mods[0]
        self.assertEqual(mod.source, SOURCE_SQL_PAIR)
        self.assertTrue(mod.has_inverse_sql)

    def test_layout_4_unusable_folder_is_reported_not_crashed(self) -> None:
        (self.mods / "junk").mkdir()
        (self.mods / "junk" / "notes.txt").write_text("hello", encoding="utf-8")
        result = scan_mods(self.mods)
        self.assertEqual(result.mods, [])
        self.assertEqual(len(result.failures), 1)
        self.assertIn("neither mod.json nor a .sql file", result.failures[0].reason)

    def test_several_sql_files_without_mod_json_is_an_error(self) -> None:
        folder = self.mods / "ambiguous"
        folder.mkdir()
        (folder / "one.sql").write_text("SELECT 1;", encoding="utf-8")
        (folder / "two.sql").write_text("SELECT 2;", encoding="utf-8")
        result = scan_mods(self.mods)
        self.assertIn("2 .sql files", result.failures[0].reason)

    def test_underscore_and_dot_folders_are_skipped(self) -> None:
        write_mod(self.mods, "_example", SIMPLE_PATCH)
        write_mod(self.mods, ".git", SIMPLE_PATCH)
        write_mod(self.mods, "real", SIMPLE_PATCH)
        result = scan_mods(self.mods)
        self.assertEqual([m.id for m in result.mods], ["real"])
        self.assertEqual({s.folder for s in result.skipped}, {"_example", ".git"})

    def test_one_broken_mod_does_not_hide_the_others(self) -> None:
        write_mod(self.mods, "good-a", SIMPLE_PATCH)
        write_mod(self.mods, "broken", {"patches": [{"type": "nonsense"}]})
        write_mod(self.mods, "good-b", SIMPLE_PATCH)
        result = scan_mods(self.mods)
        self.assertEqual([m.id for m in result.mods], ["good-a", "good-b"])
        self.assertEqual([f.folder for f in result.failures], ["broken"])

    def test_missing_mods_folder_is_a_failure_not_an_exception(self) -> None:
        result = scan_mods(self.mods / "nope")
        self.assertEqual(result.mods, [])
        self.assertIn("does not exist", result.failures[0].reason)


class LoadOrderTests(unittest.TestCase):
    """The load order belongs to the user; nothing reorders it silently."""

    def test_a_mod_before_its_dependency_is_reported(self) -> None:
        mods = [stub("text", ["prices"]), stub("prices")]
        self.assertEqual(out_of_order_requirements(mods), [("text", "prices")])

    def test_the_right_way_round_is_silent(self) -> None:
        mods = [stub("prices"), stub("text", ["prices"])]
        self.assertEqual(out_of_order_requirements(mods), [])

    def test_a_dependency_not_in_the_list_is_not_an_order_problem(self) -> None:
        # That case is "missing requirement", reported separately.
        self.assertEqual(out_of_order_requirements([stub("only", ["missing"])]), [])

    def test_a_cycle_does_not_hang(self) -> None:
        problems = out_of_order_requirements([stub("a", ["b"]), stub("b", ["a"])])
        self.assertEqual(problems, [("a", "b")])

    def test_transitive_chain_in_order(self) -> None:
        mods = [stub("a"), stub("b", ["a"]), stub("c", ["b"])]
        self.assertEqual(out_of_order_requirements(mods), [])


if __name__ == "__main__":
    unittest.main()
