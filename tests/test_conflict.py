"""The conflict rules table."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fixtures import build_db, write_mod

from lid_db_manager.conflict import (
    KIND_COLUMN,
    KIND_DECLARED,
    KIND_TEXT,
    SERIOUS,
    analyze,
)
from lid_db_manager.mod_loader import scan_mods


def update_set(table: str, column: str, where: str | None = None) -> dict:
    patch = {"type": "update_set", "table": table, "set": {column: 1}}
    if where:
        patch["where"] = where
    return {"patches": [patch]}


def text_replace(text_id: str, lang: str = "int") -> dict:
    return {
        "patches": [
            {
                "type": "text_replace",
                "table": "master_text",
                "match": {"id": text_id, "lang": lang},
                "replace": [{"find": "a", "with": "b"}],
            }
        ]
    }


def raw_sql(sql: str, **extra) -> dict:
    return {"patches": [{"type": "raw_sql", "sql": sql}], **extra}


# SQL whose SET expression cannot be read but whose WHERE can. A WHERE resolves
# independently of the SET, so this is the only shape that reaches the raw-SQL
# branch with its rows known - which is what the comparison now needs.
OPAQUE = "UPDATE master_skill SET val0 = max(1, 2)"


class ConflictTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.mods = self.root / "mods"
        self.mods.mkdir()
        self.db = build_db(self.root / "masters.db")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _analyze(self, *definitions: tuple[str, dict]):
        """Analysis with no database, so no mod's rows can be worked out.

        Nothing is reported from this: a conflict now has to be shown, and
        without the rows there is nothing to show. The app always has a database
        by the time anything can be applied.
        """
        for mod_id, data in definitions:
            write_mod(self.mods, mod_id, data)
        found = scan_mods(self.mods)
        return analyze(found.mods, set(found.by_id))

    def _analyze_with_db(self, *definitions: tuple[str, dict]):
        """Analysis against the real rows - what the app actually does."""
        from lid_db_manager.sqlutil import connect

        for mod_id, data in definitions:
            write_mod(self.mods, mod_id, data)
        found = scan_mods(self.mods)
        con = connect(self.db, read_only=True)
        try:
            return analyze(found.mods, set(found.by_id), con)
        finally:
            con.close()

    def test_same_table_and_column_conflicts(self) -> None:
        report = self._analyze_with_db(
            ("a-mod", update_set("master_skill", "buy_money")),
            ("b-mod", update_set("master_skill", "buy_money")),
        )
        self.assertEqual([c.kind for c in report.conflicts], [KIND_COLUMN])
        self.assertIn("master_skill.buy_money", report.conflicts[0].detail)
        self.assertIn("b-mod wins", report.conflicts[0].message())

    def test_the_same_column_says_nothing_until_the_rows_are_known(self) -> None:
        # Same column, but which rows is unknowable without the database, so
        # there is nothing to report. Neither mod is accused of anything.
        report = self._analyze(
            ("a-mod", update_set("master_skill", "buy_money")),
            ("b-mod", update_set("master_skill", "buy_money")),
        )
        self.assertEqual(report.conflicts, [])

    def test_different_columns_of_the_same_table_do_not_conflict(self) -> None:
        report = self._analyze(
            ("a-mod", update_set("master_skill", "buy_money")),
            ("b-mod", update_set("master_skill", "val0")),
        )
        self.assertEqual(report.conflicts, [])

    def test_update_set_and_text_replace_are_different_layers(self) -> None:
        report = self._analyze(
            ("a-mod", update_set("master_text", "lang")),
            ("b-mod", text_replace("TXT_A")),
        )
        self.assertEqual(report.conflicts, [])

    def test_text_replace_conflicts_only_on_the_same_row(self) -> None:
        same = self._analyze(("a-mod", text_replace("TXT_A")), ("b-mod", text_replace("TXT_A")))
        self.assertEqual([c.kind for c in same.conflicts], [KIND_TEXT])

        self.tearDown()
        self.setUp()
        different = self._analyze(
            ("a-mod", text_replace("TXT_A")), ("b-mod", text_replace("TXT_B"))
        )
        self.assertEqual(different.conflicts, [])

    def test_text_replace_conflicts_only_within_the_same_language(self) -> None:
        report = self._analyze(
            ("a-mod", text_replace("TXT_A", "int")), ("b-mod", text_replace("TXT_A", "jpn"))
        )
        self.assertEqual(report.conflicts, [])

    def test_a_plain_update_is_read_down_to_its_columns(self) -> None:
        """Two mods writing different columns of one table do not fight."""
        report = self._analyze(
            ("a-mod", raw_sql("UPDATE master_skill SET val0 = 1")),
            ("b-mod", update_set("master_skill", "buy_money")),
        )
        self.assertEqual([c.message() for c in report.conflicts], [])

    def test_the_same_column_still_conflicts(self) -> None:
        report = self._analyze_with_db(
            ("a-mod", raw_sql("UPDATE master_skill SET buy_money = 1")),
            ("b-mod", update_set("master_skill", "buy_money")),
        )
        self.assertEqual([c.kind for c in report.conflicts], [KIND_COLUMN])

    #: Statements that write cells nobody can name a column for.
    NAMES_NO_COLUMN = (
        "DELETE FROM master_skill WHERE id = 'SKL_EXPUP_01'",
        "INSERT OR REPLACE INTO master_skill (id, buy_money, val0) "
        "VALUES ('SKL_NEW_01', 1, 1)",
    )

    def test_sql_that_names_no_column_resolves_to_nothing(self) -> None:
        """A DELETE or an INSERT cannot be pinned to a column, so it claims none.

        This is the deliberate hole. Such a mod is now trusted rather than
        suspected, because "it might be the same box" is the guess that produced
        thirteen yellow dots nobody could act on.
        """
        from lid_db_manager.conflict import footprint

        for sql in self.NAMES_NO_COLUMN:
            with self.subTest(sql=sql):
                write_mod(self.mods, "a-mod", raw_sql(sql))
                mod = scan_mods(self.mods).by_id["a-mod"]
                self.assertEqual(footprint(mod).columns, set())

    def test_sql_that_names_no_column_is_never_a_conflict(self) -> None:
        for sql in self.NAMES_NO_COLUMN:
            with self.subTest(sql=sql):
                report = self._analyze_with_db(
                    ("a-mod", raw_sql(sql)),
                    ("b-mod", update_set("master_skill", "buy_money")),
                )
                self.assertEqual(report.conflicts, [])

    def test_an_awkward_set_expression_still_gives_up_its_column(self) -> None:
        # The value is unreadable; the column name never was. Brackets and a
        # comma inside a function call must not cut the assignment in half.
        report = self._analyze_with_db(
            ("a-mod", raw_sql("UPDATE master_skill SET val0 = max(1, 2)")),
            ("b-mod", update_set("master_skill", "val0")),
        )
        self.assertEqual([c.kind for c in report.conflicts], [KIND_COLUMN])
        self.assertIn("master_skill.val0", report.conflicts[0].detail)

    def test_an_awkward_set_expression_on_another_column_is_quiet(self) -> None:
        report = self._analyze_with_db(
            ("a-mod", raw_sql("UPDATE master_skill SET val0 = max(1, 2)")),
            ("b-mod", update_set("master_skill", "buy_money")),
        )
        self.assertEqual(report.conflicts, [])

    def test_raw_sql_opt_out_suppresses_the_warning(self) -> None:
        report = self._analyze(
            (
                "a-mod",
                raw_sql(
                    "UPDATE master_skill SET val0 = 1",
                    raw_sql_files_do_not_touch=["master_skill"],
                ),
            ),
            ("b-mod", update_set("master_skill", "buy_money")),
        )
        self.assertEqual(report.conflicts, [])

    def test_two_raw_sql_mods_on_different_tables_are_fine(self) -> None:
        report = self._analyze(
            ("a-mod", raw_sql("UPDATE master_skill SET val0 = 1")),
            ("b-mod", raw_sql("DELETE FROM master_text WHERE lang = 'jpn'")),
        )
        self.assertEqual(report.conflicts, [])

    def test_declared_conflicts_are_reported(self) -> None:
        report = self._analyze(
            ("a-mod", {**update_set("master_skill", "buy_money"), "conflicts_with": ["b-mod"]}),
            ("b-mod", update_set("master_body_detail", "price")),
        )
        self.assertEqual([c.kind for c in report.conflicts], [KIND_DECLARED])

    def test_missing_requirement_distinguishes_disabled_from_absent(self) -> None:
        write_mod(self.mods, "installed-dep", update_set("master_skill", "val0"))
        write_mod(
            self.mods,
            "needy",
            {**update_set("master_skill", "buy_money"), "requires": ["installed-dep", "ghost"]},
        )
        found = scan_mods(self.mods)
        report = analyze([found.get("needy")], set(found.by_id))
        messages = {r.required_id: r.message() for r in report.missing_requirements}
        self.assertIn("installed but not enabled", messages["installed-dep"])
        self.assertIn("not installed", messages["ghost"])

    def test_raw_sql_on_the_same_table_but_different_rows_is_not_a_conflict(self) -> None:
        """Two master_text mods editing unrelated sections must stay quiet."""
        report = self._analyze_with_db(
            ("area-names", raw_sql(
                "UPDATE master_text SET txt = 'AKAMI (STAR)' "
                "WHERE sct = 'AREA_NAME' AND id = 'TXT_AMS_0000'")),
            ("skill-text", raw_sql(
                "UPDATE master_text SET txt = 'now 100,000%' "
                "WHERE sct = 'SKILL_DESCRIPTION' AND id = 'TXT_SKL_EXPUP_02'")),
        )
        self.assertEqual([c.message() for c in report.conflicts], [])

    def test_raw_sql_hitting_the_same_rows_still_conflicts(self) -> None:
        report = self._analyze_with_db(
            ("a-mod", raw_sql(
                "UPDATE master_text SET txt = 'one' WHERE sct = 'AREA_NAME'")),
            ("b-mod", raw_sql(
                "UPDATE master_text SET txt = 'two' WHERE id = 'TXT_AMS_0000'")),
        )
        self.assertEqual([c.kind for c in report.conflicts], [KIND_COLUMN])
        self.assertIn("shared row", report.conflicts[0].detail)

    def test_raw_sql_without_a_where_clause_conflicts_with_everything(self) -> None:
        report = self._analyze_with_db(
            ("a-mod", raw_sql("UPDATE master_text SET txt = 'everything'")),
            ("b-mod", raw_sql(
                "UPDATE master_text SET txt = 'x' WHERE sct = 'AREA_NAME'")),
        )
        self.assertEqual([c.kind for c in report.conflicts], [KIND_COLUMN])

    def test_an_insert_adds_a_row_nobody_can_name_so_nothing_is_claimed(self) -> None:
        # An INSERT writes a row that does not exist yet and names no column to
        # compare, so sharing master_text with it is not a conflict.
        report = self._analyze_with_db(
            ("a-mod", raw_sql(
                "INSERT OR REPLACE INTO master_text (sct, id, snd, lang, txt, type) "
                "VALUES ('AREA_NAME', 'TXT_NEW', '', 'int', 'hi', 0)")),
            ("b-mod", raw_sql(
                "UPDATE master_text SET txt = 'x' WHERE sct = 'AREA_NAME'")),
        )
        self.assertEqual(report.conflicts, [])

    def test_two_raw_sql_mods_on_the_same_box_are_reported(self) -> None:
        # The shape that matters in practice: two third-party .sql mods, both
        # bracket-quoted, both rewriting the same cell.
        report = self._analyze_with_db(
            ("a-mod", raw_sql(
                "UPDATE [master_text] SET [txt] = 'a' WHERE [sct] = 'AREA_NAME'")),
            ("b-mod", raw_sql(
                "UPDATE [master_text] SET [txt] = 'b' WHERE [sct] = 'AREA_NAME'")),
        )
        self.assertEqual([c.kind for c in report.conflicts], [KIND_COLUMN])
        self.assertIn("master_text.txt", report.conflicts[0].detail)

    def test_two_raw_sql_mods_on_the_same_row_but_other_columns_are_quiet(self) -> None:
        # The user's example: damage on the metal bat versus its durability.
        report = self._analyze_with_db(
            ("a-mod", raw_sql(
                "UPDATE [master_text] SET [txt] = 'a' WHERE [sct] = 'AREA_NAME'")),
            ("b-mod", raw_sql(
                "UPDATE [master_text] SET [snd] = 'b' WHERE [sct] = 'AREA_NAME'")),
        )
        self.assertEqual(report.conflicts, [])

    def test_update_set_on_the_same_column_but_different_rows_is_not_a_conflict(self) -> None:
        report = self._analyze_with_db(
            ("a-mod", update_set("master_skill", "buy_money", "id = 'SKL_EXPUP_01'")),
            ("b-mod", update_set("master_skill", "buy_money", "id = 'SKL_POWER_01'")),
        )
        self.assertEqual([c.message() for c in report.conflicts], [])

    def test_update_set_on_the_same_column_and_same_rows_conflicts(self) -> None:
        report = self._analyze_with_db(
            ("a-mod", update_set("master_skill", "buy_money", "buy_money > 1")),
            ("b-mod", update_set("master_skill", "buy_money", "id = 'SKL_EXPUP_01'")),
        )
        self.assertEqual([c.kind for c in report.conflicts], [KIND_COLUMN])
        self.assertIn("1 shared row", report.conflicts[0].detail)

    def test_raw_sql_conflicting_with_an_update_set_on_shared_rows(self) -> None:
        report = self._analyze_with_db(
            ("a-mod", raw_sql(
                "UPDATE master_skill SET buy_money = 1 WHERE id = 'SKL_EXPUP_01'")),
            ("b-mod", update_set("master_skill", "buy_money", "id = 'SKL_EXPUP_01'")),
        )
        self.assertEqual([c.kind for c in report.conflicts], [KIND_COLUMN])

    def test_raw_sql_and_update_set_on_different_rows_of_one_table(self) -> None:
        report = self._analyze_with_db(
            ("a-mod", raw_sql("UPDATE master_skill SET val0 = 1 WHERE id = 'SKL_EXPUP_01'")),
            ("b-mod", update_set("master_skill", "buy_money", "id = 'SKL_POWER_01'")),
        )
        self.assertEqual([c.message() for c in report.conflicts], [])

    def test_for_mod_collects_everything_naming_that_mod(self) -> None:
        report = self._analyze_with_db(
            ("a-mod", update_set("master_skill", "buy_money")),
            ("b-mod", update_set("master_skill", "buy_money")),
        )
        self.assertEqual(len(report.for_mod("a-mod")), 1)
        self.assertEqual(report.for_mod("unrelated"), [])


    # -- severity: red when the same cells are overwritten, yellow when they
    # only might be -----------------------------------------------------------

    def test_the_same_cells_in_shared_rows_is_serious(self) -> None:
        report = self._analyze_with_db(
            ("a-mod", update_set("master_skill", "buy_money", "buy_money > 1")),
            ("b-mod", update_set("master_skill", "buy_money", "id = 'SKL_EXPUP_01'")),
        )
        self.assertEqual([c.severity for c in report.conflicts], [SERIOUS])
        self.assertEqual(report.severity_for("a-mod"), SERIOUS)
        self.assertEqual(report.severity_for("b-mod"), SERIOUS)

    def test_the_same_column_with_rows_unknown_says_nothing_at_all(self) -> None:
        report = self._analyze(
            ("a-mod", update_set("master_skill", "buy_money")),
            ("b-mod", update_set("master_skill", "buy_money")),
        )
        self.assertEqual(report.conflicts, [])
        self.assertEqual(report.severity_for("a-mod"), "")

    def test_raw_sql_on_the_same_box_is_a_red_dot_like_any_other(self) -> None:
        report = self._analyze_with_db(
            ("a-mod", raw_sql(f"{OPAQUE} WHERE id = 'SKL_EXPUP_01'")),
            ("b-mod", raw_sql(f"{OPAQUE} WHERE id = 'SKL_EXPUP_01'")),
        )
        self.assertEqual([c.severity for c in report.conflicts], [SERIOUS])
        self.assertEqual(report.severity_for("a-mod"), SERIOUS)

    def test_a_shared_table_alone_is_not_a_warning_any_more(self) -> None:
        report = self._analyze_with_db(
            ("a-mod", raw_sql(f"{OPAQUE} WHERE id = 'SKL_EXPUP_01'")),
            ("b-mod", raw_sql(f"{OPAQUE} WHERE id = 'SKL_POWER_01'")),
        )
        self.assertEqual(report.conflicts, [])
        self.assertEqual(report.severity_for("a-mod"), "")

    def test_a_shared_row_alone_is_not_a_warning_either(self) -> None:
        # Same row of the same table, different columns - the metal bat's damage
        # and its durability. This is the case the user asked to go quiet.
        report = self._analyze_with_db(
            ("a-mod", update_set("master_skill", "buy_money", "id = 'SKL_EXPUP_01'")),
            ("b-mod", update_set("master_skill", "val0", "id = 'SKL_EXPUP_01'")),
        )
        self.assertEqual(report.conflicts, [])
        self.assertEqual(report.severity_for("a-mod"), "")

    def test_the_same_text_entry_is_serious(self) -> None:
        report = self._analyze(("a-mod", text_replace("TXT_A")), ("b-mod", text_replace("TXT_A")))
        self.assertEqual(report.severity_for("b-mod"), SERIOUS)

    def test_declared_incompatible_is_serious(self) -> None:
        report = self._analyze(
            ("a-mod", {**update_set("master_skill", "buy_money"), "conflicts_with": ["b-mod"]}),
            ("b-mod", update_set("master_text", "txt")),
        )
        self.assertEqual(report.severity_for("a-mod"), SERIOUS)

    def test_messages_for_colours_each_line_by_its_own_severity(self) -> None:
        """The details panel paints per message; the list's dot is per mod.

        An overwritten cell shown in the same yellow as "a mod it needs is not
        ticked" tells the reader those are the same kind of thing.
        """
        write_mod(self.mods, "a-mod", {
            "requires": ["not-installed"],
            **update_set("master_skill", "buy_money"),
        })
        write_mod(self.mods, "b-mod", update_set("master_skill", "buy_money"))
        from lid_db_manager.sqlutil import connect

        found = scan_mods(self.mods)
        con = connect(self.db, read_only=True)
        try:
            report = analyze(found.mods, set(found.by_id), con)
        finally:
            con.close()
        by_severity = dict((severity, message)
                           for severity, message in report.messages_for("a-mod"))
        self.assertIn(SERIOUS, by_severity)
        self.assertIn("master_skill.buy_money", by_severity[SERIOUS])
        self.assertIn("warning", by_severity)
        self.assertIn("not-installed", by_severity["warning"])

    def test_messages_for_says_the_same_things_as_for_mod(self) -> None:
        report = self._analyze_with_db(
            ("a-mod", update_set("master_skill", "buy_money")),
            ("b-mod", update_set("master_skill", "buy_money")),
        )
        self.assertEqual([m for _s, m in report.messages_for("a-mod")],
                         report.for_mod("a-mod"))

    def test_messages_for_a_mod_nobody_mentions_is_empty(self) -> None:
        report = self._analyze_with_db(
            ("a-mod", update_set("master_skill", "buy_money")),
            ("b-mod", update_set("master_text", "txt")),
        )
        self.assertEqual(report.messages_for("a-mod"), [])

    def test_a_mod_nobody_mentions_has_no_severity(self) -> None:
        report = self._analyze(
            ("a-mod", update_set("master_skill", "buy_money")),
            ("b-mod", update_set("master_text", "txt")),
        )
        self.assertEqual(report.severity_for("a-mod"), "")


if __name__ == "__main__":
    unittest.main()
