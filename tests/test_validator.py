"""Pre-apply validation: hard failures vs warnings."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fixtures import build_db, query, write_mod

from lid_db_manager.mod_loader import scan_mods
from lid_db_manager.validator import validate


class ValidatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.mods = self.root / "mods"
        self.mods.mkdir()
        self.db = build_db(self.root / "masters.db")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _validate(self):
        return validate(self.db, scan_mods(self.mods).mods)

    def test_a_good_mod_passes(self) -> None:
        write_mod(
            self.mods,
            "good",
            {"patches": [{"type": "update_set", "table": "master_skill", "set": {"buy_money": 1}}]},
        )
        report = self._validate()
        self.assertTrue(report.ok, report.summary_line())

    def test_missing_table_fails(self) -> None:
        write_mod(
            self.mods,
            "bad-table",
            {"patches": [{"type": "update_set", "table": "no_such_table", "set": {"x": 1}}]},
        )
        report = self._validate()
        self.assertFalse(report.ok)
        self.assertIn("does not exist", report.failed[0].errors[0])

    def test_missing_column_names_the_columns_that_do_exist(self) -> None:
        write_mod(
            self.mods,
            "bad-column",
            {"patches": [{"type": "update_set", "table": "master_skill", "set": {"nope": 1}}]},
        )
        report = self._validate()
        self.assertFalse(report.ok)
        message = report.failed[0].errors[0]
        self.assertIn("nope", message)
        self.assertIn("buy_money", message)

    def test_broken_where_clause_fails(self) -> None:
        write_mod(
            self.mods,
            "bad-where",
            {
                "patches": [
                    {
                        "type": "update_set",
                        "table": "master_skill",
                        "set": {"buy_money": 1},
                        "where": "buy_money >>> ",
                    }
                ]
            },
        )
        report = self._validate()
        self.assertFalse(report.ok)
        self.assertIn("not valid SQL", report.failed[0].errors[0])

    def test_broken_raw_sql_fails_without_running(self) -> None:
        write_mod(
            self.mods,
            "bad-sql",
            {"patches": [{"type": "raw_sql", "sql": "DELETE FROM master_skill WHERE"}]},
        )
        report = self._validate()
        self.assertFalse(report.ok)
        self.assertIn("not valid", report.failed[0].errors[0])
        # ... and validation is read-only, so nothing was deleted.
        self.assertEqual(len(query(self.db, "SELECT id FROM master_skill")), 4)

    def test_raw_sql_against_a_missing_table_fails(self) -> None:
        write_mod(
            self.mods, "ghost", {"patches": [{"type": "raw_sql", "sql": "UPDATE ghost SET a = 1"}]}
        )
        self.assertFalse(self._validate().ok)

    def test_a_schema_qualified_table_name_is_not_mistaken_for_the_schema(self) -> None:
        """DB Browser for SQLite's own SQL export writes "main"."table" -

        the table name has to be read past that prefix, or it looks like the
        mod writes to a table literally called "main", which does not exist.
        """
        write_mod(
            self.mods,
            "qualified",
            {
                "patches": [
                    {
                        "type": "raw_sql",
                        "sql": 'INSERT INTO "main"."master_skill" '
                        '("id","name","buy_money","val0") '
                        "VALUES ('SKL_NEW','New',7,7);",
                    }
                ]
            },
        )
        report = self._validate()
        self.assertTrue(report.ok, report.summary_line())

    def test_words_in_the_text_a_mod_writes_are_not_read_as_tables(self) -> None:
        """A mod that rewrites master_text carries the game's own dialogue.

        Lines like "this update adds new routes" and the German "Update wird"
        read as UPDATE followed by a table name to anything that scans the
        script as if it were all SQL, and the mod was then turned away for
        writing to tables called "adds" and "wird" that no database has.
        """
        write_mod(
            self.mods,
            "dialogue",
            {
                "patches": [
                    {
                        "type": "raw_sql",
                        "sql": 'INSERT INTO "master_text" '
                        '("sct","id","snd","lang","txt","type") VALUES '
                        "('MAIL','TXT_MAIL_1','','int',"
                        "'This update adds new routes from 51F upwards.',0);",
                    }
                ]
            },
        )
        report = self._validate()
        self.assertTrue(report.ok, report.summary_line())

    def test_a_foreign_key_rule_is_not_read_as_a_table(self) -> None:
        """"ON UPDATE RESTRICT" is a rule inside a CREATE TABLE.

        The word after that UPDATE is an action, not a table, so reading it as
        one meant a perfectly ordinary table definition was rejected for
        writing to a table called RESTRICT.
        """
        write_mod(
            self.mods,
            "keyed",
            {
                "patches": [
                    {
                        "type": "raw_sql",
                        "sql": 'CREATE TABLE "mod_extra" ('
                        '"id" TEXT NOT NULL, '
                        '"skill" TEXT REFERENCES "master_skill"("id") '
                        "ON UPDATE RESTRICT ON DELETE CASCADE, "
                        'PRIMARY KEY("id"));',
                    }
                ]
            },
        )
        report = self._validate()
        self.assertTrue(report.ok, report.summary_line())

    def test_a_double_dash_in_the_text_does_not_hide_the_sql_after_it(self) -> None:
        """Dialogue is full of "--". It opens no comment inside a quote.

        Treating it as one swallowed the rest of the line and the statement
        sitting there with it, so that table went unlisted - and a table that
        is not listed is not snapshotted, which is how a mod ends up unable to
        be reverted.
        """
        from lid_db_manager.patch import RawSqlPatch

        patch = RawSqlPatch(
            {
                "sql": 'INSERT INTO "master_text" ("sct","id","snd","lang","txt","type") '
                "VALUES ('MAIL','TXT_MAIL_2','','int','Wait -- what?',0); "
                "UPDATE master_skill SET buy_money = 1;"
            },
            self.mods,
            0,
        )
        self.assertEqual(patch.tables(), {"master_text", "master_skill"})

    def test_several_statements_on_one_line_are_run_one_at_a_time(self) -> None:
        """Nothing makes a mod put each statement on a line of its own.

        They used to be handed over as one string, which SQLite's driver
        refuses - "you can only execute one statement at a time", which tells
        the person nothing they can act on.
        """
        write_mod(
            self.mods,
            "oneline",
            {
                "patches": [
                    {
                        "type": "raw_sql",
                        "sql": "UPDATE master_skill SET buy_money = 11 "
                        "WHERE id = 'SKL_FREE_01'; "
                        "UPDATE master_skill SET buy_money = 22 "
                        "WHERE id = 'SKL_POWER_01';",
                    }
                ]
            },
        )
        report = self._validate()
        self.assertTrue(report.ok, report.summary_line())

    def test_a_trigger_body_is_not_split_at_its_semicolons(self) -> None:
        """A trigger's BEGIN...END holds statements of its own.

        Cutting at those semicolons would hand over half a trigger.
        """
        from lid_db_manager.sqlutil import split_statements

        sql = (
            "CREATE TRIGGER t AFTER INSERT ON master_skill BEGIN "
            "UPDATE master_skill SET val0 = 1; "
            "UPDATE master_skill SET val0 = 2; "
            "END;"
        )
        self.assertEqual(split_statements(sql), [sql])

    def test_a_huge_script_does_not_build_a_huge_preview(self) -> None:
        """A dump of whole tables is six figures of INSERTs.

        The preview used to list every one of them - a 23 MB block of text that
        the window then had to lay out, which is what made it go blank and look
        as though the program had died.
        """
        from lid_db_manager.patch import PREVIEW_STATEMENT_LIMIT, RawSqlPatch
        from lid_db_manager.sqlutil import connect

        sql = "".join(
            f"UPDATE master_skill SET buy_money = {n} WHERE id = 'SKL_FREE_01';\n"
            for n in range(500)
        )
        patch = RawSqlPatch({"sql": sql}, self.mods, 0)
        con = connect(self.db)
        try:
            note = patch.preview(con).note
        finally:
            con.close()
        self.assertLess(len(note), 20_000, "the preview is still unbounded")
        self.assertIn("and 460 more statement(s)", note)
        self.assertEqual(
            note.count("UPDATE master_skill"), PREVIEW_STATEMENT_LIMIT
        )

    def test_a_sql_file_is_only_read_through_once(self) -> None:
        """One refresh of the mod list asks a patch for its statements, its
        tables and the tables it creates over and over. For a 24 MB dump each
        of those meant reading the file again and splitting it again - seconds
        of work, repeated, on the thread that paints the window.
        """
        from lid_db_manager.patch import RawSqlFilePatch

        folder = self.mods / "filed"
        folder.mkdir()
        (folder / "mod.sql").write_text(
            "UPDATE master_skill SET buy_money = 1;\n", encoding="utf-8"
        )
        patch = RawSqlFilePatch({"path": "mod.sql"}, folder, 0)

        reads = []
        original = patch.sql_text

        def counted() -> str:
            reads.append(1)
            return original()

        patch.sql_text = counted
        for _ in range(5):
            patch.statements()
            patch.tables()
            patch.creates_tables()
        self.assertEqual(len(reads), 1, "the file was read more than once")

        # ... and editing it is still picked up.
        (folder / "mod.sql").write_text(
            "UPDATE master_shop_product_price SET price = 1;\n", encoding="utf-8"
        )
        self.assertEqual(patch.tables(), {"master_shop_product_price"})

    def test_content_that_is_already_there_is_said_plainly(self) -> None:
        """A mod whose rows are all in the database already changes nothing.

        That is normal - usually the content's own installer ran first - but it
        looks like a fault unless somebody says so.
        """
        write_mod(
            self.mods,
            "already",
            {
                "patches": [
                    {
                        "type": "raw_sql",
                        "sql": 'INSERT OR REPLACE INTO "master_skill" '
                        '("id","name","buy_money","val0") '
                        "VALUES ('SKL_FREE_01','Freebie',1,0);",
                    }
                ]
            },
        )
        report = self._validate()
        self.assertTrue(report.ok, report.summary_line())
        warnings = report.for_mod("already").warnings
        self.assertTrue(
            any("already in your database" in w for w in warnings), warnings
        )

    def test_a_dump_of_whole_tables_is_not_called_already_installed(self) -> None:
        """A dump rewrites the table rather than adding to it.

        Nearly every row in one matches the database whatever the mod changes,
        so finding rows already there says nothing about whether the mod is in.
        Saying it was would tell somebody their rebalance was installed when
        not one value of it had been applied.
        """
        write_mod(
            self.mods,
            "dump",
            {
                "patches": [
                    {
                        "type": "raw_sql",
                        "sql": 'DROP TABLE IF EXISTS "master_skill"; '
                        'CREATE TABLE "master_skill" ('
                        '"id" TEXT PRIMARY KEY, "name" TEXT, '
                        '"buy_money" INTEGER, "val0" INTEGER); '
                        'INSERT INTO "master_skill" ("id","name","buy_money","val0") VALUES '
                        "('SKL_EXPUP_01','Exp Up',500,10); "
                        'INSERT INTO "master_skill" ("id","name","buy_money","val0") VALUES '
                        "('SKL_EXPUP_02','Nitro Boost',5000,40); "
                        'INSERT INTO "master_skill" ("id","name","buy_money","val0") VALUES '
                        "('SKL_POWER_01','Power Up',1200,5); "
                        'INSERT INTO "master_skill" ("id","name","buy_money","val0") VALUES '
                        "('SKL_FREE_01','Freebie',1,0);",
                    }
                ]
            },
        )
        report = self._validate()
        warnings = report.for_mod("dump").warnings
        self.assertFalse(
            any("already in your database" in w for w in warnings), warnings
        )

    def test_expected_rows_mismatch_is_only_a_warning(self) -> None:
        write_mod(
            self.mods,
            "drifted",
            {
                "patches": [
                    {
                        "type": "update_set",
                        "table": "master_skill",
                        "set": {"buy_money": 1},
                        "where": "buy_money > 1",
                        "expected_rows": 320,
                    }
                ]
            },
        )
        report = self._validate()
        self.assertTrue(report.ok)
        self.assertIn("expected 320 rows", report.results[0].warnings[0])

    def test_matching_no_rows_is_only_a_warning(self) -> None:
        write_mod(
            self.mods,
            "no-op",
            {
                "patches": [
                    {
                        "type": "update_set",
                        "table": "master_skill",
                        "set": {"buy_money": 1},
                        "where": "buy_money > 999999",
                    }
                ]
            },
        )
        report = self._validate()
        self.assertTrue(report.ok)
        self.assertIn("0 rows", report.results[0].warnings[0])

    def test_absent_find_text_warns_by_default(self) -> None:
        write_mod(
            self.mods,
            "text-drift",
            {
                "patches": [
                    {
                        "type": "text_replace",
                        "table": "master_text",
                        "match": {"id": "TXT_BIBLE_19_NOTE_G", "lang": "int"},
                        "replace": [{"find": "not in there", "with": "x"}],
                    }
                ]
            },
        )
        report = self._validate()
        self.assertTrue(report.ok)
        self.assertIn("is not present", report.results[0].warnings[0])

    def test_absent_find_text_fails_when_require_find_is_set(self) -> None:
        write_mod(
            self.mods,
            "text-strict",
            {
                "patches": [
                    {
                        "type": "text_replace",
                        "table": "master_text",
                        "match": {"id": "TXT_BIBLE_19_NOTE_G", "lang": "int"},
                        "replace": [{"find": "not in there", "with": "x"}],
                        "require_find": True,
                    }
                ]
            },
        )
        self.assertFalse(self._validate().ok)

    def test_missing_sql_file_fails(self) -> None:
        write_mod(
            self.mods, "no-file", {"patches": [{"type": "raw_sql_file", "path": "gone.sql"}]}
        )
        report = self._validate()
        self.assertFalse(report.ok)
        self.assertIn("missing", report.failed[0].errors[0])

    def test_missing_database_is_a_fatal_report_not_a_crash(self) -> None:
        report = validate(self.root / "nope.db", [])
        self.assertFalse(report.ok)
        self.assertIn("not found", report.fatal)

    def test_requirements_are_reported(self) -> None:
        write_mod(
            self.mods,
            "needs-other",
            {
                "requires": ["not-installed"],
                "patches": [{"type": "update_set", "table": "master_skill", "set": {"buy_money": 1}}],
            },
        )
        report = self._validate()
        self.assertTrue(report.ok)  # a missing requirement warns, it does not block
        self.assertIn("not installed", report.conflicts.missing_requirements[0].message())


if __name__ == "__main__":
    unittest.main()
