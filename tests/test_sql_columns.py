"""Reading the written column names out of a mod's raw SQL.

The conflict list only reports the same *box* - same table, same rows, same
column - so it is only as good as this. A column it cannot name is a conflict it
cannot report, and third-party .sql mods are exactly the ones most likely to
collide: they are whole-table rewrites, and they are written by tools that quote
identifiers in [brackets] and put a CREATE TABLE IF NOT EXISTS on the front.

Conservative in one direction only. Failing to read a column loses a warning;
reading the *wrong* column would invent one, or hide one, so anything unclear
must come back None.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fixtures import write_mod

from lid_db_manager.mod_loader import scan_mods


class ReadingTheColumns(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.mods = Path(self._tmp.name) / "mods"
        self.mods.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def resolved(self, *sql: str):
        write_mod(self.mods, "a-mod", {
            "patches": [{"type": "raw_sql", "sql": "\n".join(sql)}],
        })
        patch = scan_mods(self.mods).by_id["a-mod"].patches[0]
        return patch.resolved_targets()

    # -- the quoting styles real mods use -----------------------------------

    def test_bare_identifiers(self):
        self.assertEqual(self.resolved("UPDATE master_skill SET val0 = 1;"),
                         {("master_skill", "val0")})

    def test_double_quoted_identifiers(self):
        self.assertEqual(self.resolved('UPDATE "master_skill" SET "val0" = 1;'),
                         {("master_skill", "val0")})

    def test_bracket_quoted_identifiers(self):
        # What every generated .sql in this project's mods folder uses.
        self.assertEqual(self.resolved("UPDATE [master_skill] SET [val0] = 1;"),
                         {("master_skill", "val0")})

    def test_backtick_quoted_identifiers(self):
        self.assertEqual(self.resolved("UPDATE `master_skill` SET `val0` = 1;"),
                         {("master_skill", "val0")})

    def test_update_or_replace_is_still_an_update(self):
        self.assertEqual(self.resolved("UPDATE OR REPLACE master_skill SET val0 = 1;"),
                         {("master_skill", "val0")})

    def test_lower_case_keywords(self):
        self.assertEqual(self.resolved("update master_skill set val0 = 1 where id = 'x';"),
                         {("master_skill", "val0")})

    # -- values that used to defeat it --------------------------------------

    def test_a_function_call_in_the_value(self):
        self.assertEqual(self.resolved("UPDATE master_skill SET val0 = max(1, 2);"),
                         {("master_skill", "val0")})

    def test_a_subquery_in_the_value(self):
        self.assertEqual(
            self.resolved("UPDATE master_skill SET val0 = "
                          "(SELECT MAX(val0) FROM master_skill);"),
            {("master_skill", "val0")},
        )

    def test_a_bracket_inside_a_string_literal(self):
        # 'AKAMI (STAR)' - a real value from Floor Material Names. Counting
        # brackets without skipping strings would never come back level.
        self.assertEqual(
            self.resolved("UPDATE [master_text] SET [txt] = 'AKAMI (STAR)' "
                          "WHERE [id] = 'TXT_AMS_0000';"),
            {("master_text", "txt")},
        )

    def test_a_comma_inside_a_string_literal(self):
        self.assertEqual(
            self.resolved("UPDATE master_text SET txt = 'one, two' WHERE id = 'x';"),
            {("master_text", "txt")},
        )

    def test_the_word_where_inside_a_string_literal(self):
        self.assertEqual(
            self.resolved("UPDATE master_text SET txt = 'from where to here';"),
            {("master_text", "txt")},
        )

    def test_an_escaped_quote_inside_a_string_literal(self):
        self.assertEqual(
            self.resolved("UPDATE master_text SET txt = 'don''t, really' "
                          "WHERE id = 'x';"),
            {("master_text", "txt")},
        )

    def test_several_assignments(self):
        self.assertEqual(
            self.resolved("UPDATE master_part SET atk = 1, dur = 2, def = 3;"),
            {("master_part", "atk"), ("master_part", "dur"), ("master_part", "def")},
        )

    def test_several_assignments_with_brackets_between_them(self):
        self.assertEqual(
            self.resolved("UPDATE master_part SET atk = max(1, 2), dur = min(3, 4);"),
            {("master_part", "atk"), ("master_part", "dur")},
        )

    def test_a_where_clause_with_brackets_is_not_mistaken_for_a_column(self):
        self.assertEqual(
            self.resolved("UPDATE master_part SET atk = 1 "
                          "WHERE id IN ('A', 'B') AND (dur > 1 OR def > 1);"),
            {("master_part", "atk")},
        )

    # -- statements that write no nameable cell -----------------------------

    def test_a_create_table_in_front_does_not_spoil_the_updates(self):
        # How a generated .sql starts. It cannot overwrite an existing cell, so
        # it says nothing about columns and must not blank out the whole answer.
        self.assertEqual(
            self.resolved(
                'CREATE TABLE IF NOT EXISTS "master_text" ("sct" TEXT, "txt" TEXT);',
                "UPDATE [master_text] SET [txt] = 'a' WHERE [id] = 'x';",
            ),
            {("master_text", "txt")},
        )

    def test_a_create_index_does_not_spoil_them_either(self):
        self.assertEqual(
            self.resolved(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix ON master_text (sct);",
                "UPDATE master_text SET txt = 'a';",
            ),
            {("master_text", "txt")},
        )

    def test_comments_are_skipped(self):
        self.assertEqual(
            self.resolved("-- a header nobody should parse",
                          "UPDATE master_skill SET val0 = 1;"),
            {("master_skill", "val0")},
        )

    # -- and the things it must refuse to guess at --------------------------

    def test_an_insert_gives_up_the_whole_patch(self):
        self.assertIsNone(self.resolved(
            "INSERT INTO master_skill (id, val0) VALUES ('x', 1);"))

    def test_a_delete_gives_up_the_whole_patch(self):
        self.assertIsNone(self.resolved("DELETE FROM master_skill WHERE id = 'x';"))

    def test_one_insert_among_updates_gives_up_the_whole_patch(self):
        # Partly true is worse than nothing: the INSERT could write any column.
        self.assertIsNone(self.resolved(
            "UPDATE master_skill SET val0 = 1;",
            "INSERT INTO master_skill (id, val0) VALUES ('x', 1);",
        ))

    def test_a_column_list_assignment_is_refused(self):
        # SQLite allows SET (a, b) = (1, 2). The column names are in there, but
        # not where this reads them, so it says so rather than guessing.
        self.assertIsNone(self.resolved(
            "UPDATE master_skill SET (val0, val1) = (1, 2);"))

    def test_an_unbalanced_bracket_is_refused(self):
        self.assertIsNone(self.resolved("UPDATE master_skill SET val0 = max(1, 2;"))

    def test_an_unclosed_string_is_refused(self):
        self.assertIsNone(self.resolved("UPDATE master_text SET txt = 'oops;"))

    def test_an_empty_assignment_is_refused(self):
        self.assertIsNone(self.resolved("UPDATE master_skill SET val0 = ;"))

    def test_a_doubled_comma_is_refused(self):
        self.assertIsNone(self.resolved("UPDATE master_skill SET val0 = 1,, val1 = 2;"))

    def test_nothing_at_all_resolves_to_nothing(self):
        self.assertIsNone(self.resolved("-- only a comment"))

    def test_a_create_table_on_its_own_resolves_to_nothing(self):
        self.assertIsNone(self.resolved("CREATE TABLE t (a TEXT);"))


class AgainstTheRealModsInThisWorkingCopy(unittest.TestCase):
    """The mods that made this worth doing, when they are installed.

    Both are gitignored local-only mods, so this skips on a clean checkout.
    """

    def setUp(self) -> None:
        self.scan = scan_mods(Path(__file__).resolve().parents[1] / "mods")

    def _only_raw_sql(self, mod_id: str):
        mod = self.scan.by_id.get(mod_id)
        if mod is None:
            self.skipTest(f"{mod_id} is not installed in this working copy")
        from lid_db_manager.patch import RawSqlPatch

        return [p for p in mod.patches if isinstance(p, RawSqlPatch)]

    def test_floor_material_names_resolves_to_one_column(self):
        # 191 bracket-quoted UPDATEs behind a CREATE TABLE IF NOT EXISTS.
        for patch in self._only_raw_sql("Floor Material Names"):
            self.assertEqual(patch.resolved_targets(), {("master_text", "txt")})

    def test_overlap_test_resolves_to_the_same_column(self):
        for patch in self._only_raw_sql("overlap-test"):
            self.assertEqual(patch.resolved_targets(), {("master_text", "txt")})

    def test_the_crossover_pack_still_names_no_column(self):
        # 594 INSERTs. Nothing to resolve, and it must not pretend otherwise.
        for patch in self._only_raw_sql("LET IT DIE Crossover Content v3.79"):
            self.assertIsNone(patch.resolved_targets())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
