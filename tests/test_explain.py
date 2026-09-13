"""Saying what a mod changes in plain English.

The point of this is a player reading "the most Kill Coins the Buffalo Bank can
hold is doubled, for levels 1-99" instead of 99 rows of numbers. So the tests
are mostly about the wording being right, and about the two things that must
never happen: guessing at a column nobody has described, and letting a display
problem take the panel down.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fixtures import build_db

from lid_db_manager import explain, explain_data
from lid_db_manager.dbdiff import DbDelta, RowUpdate, TableDelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication

    from lid_db_manager.manager import Manager
    from lid_db_manager.paths import AppPaths
    from lid_db_manager.ui.diff_view import DiffView

    HAVE_QT = True
except ImportError:  # pragma: no cover - depends on the environment
    HAVE_QT = False


def bank_delta(*rows) -> DbDelta:
    """A change to the Kill Bank's capacity: (level, before, after)..."""
    td = TableDelta("master_safe_level", ["level"], ["level", "limit"])
    for level, before, after in rows:
        td.updates.append(RowUpdate((level,), {"limit": after}, {"limit": before}))
    return DbDelta(tables=[td])


def first_sentence(explanation) -> str:
    return explanation.tables[0].changes[0].sentence


class Wording(unittest.TestCase):
    def test_it_names_the_thing_and_the_units(self) -> None:
        text = first_sentence(explain.explain_delta(bank_delta((1, 50000, 100000))))
        self.assertIn("the most Kill Coins it can hold", text)
        self.assertIn("100,000 KC", text)

    def test_the_table_gets_its_real_name(self) -> None:
        out = explain.explain_delta(bank_delta((1, 50000, 100000)))
        self.assertEqual(out.tables[0].title, "Buffalo Bank (Kill Bank) upgrades")

    def test_one_new_value_everywhere_reads_as_is_now(self) -> None:
        text = first_sentence(explain.explain_delta(bank_delta((1, 5, 7), (2, 6, 7))))
        self.assertIn("Sets", text)
        self.assertIn("to 7", text)

    def test_a_consistent_ratio_reads_as_doubled(self) -> None:
        text = first_sentence(explain.explain_delta(bank_delta((1, 50, 100), (2, 60, 120))))
        self.assertIn("Doubles", text)
        self.assertNotIn("multiplied", text)

    def test_halved_and_multiplied_have_their_own_words(self) -> None:
        # Two rows, because a single row reads better as its new value.
        self.assertIn(
            "Halves",
            first_sentence(explain.explain_delta(bank_delta((1, 100, 50), (2, 200, 100)))),
        )
        self.assertIn(
            "Multiplies",
            first_sentence(explain.explain_delta(bank_delta((1, 100, 150), (2, 200, 300)))),
        )

    def test_mixed_changes_do_not_pretend_to_a_pattern(self) -> None:
        text = first_sentence(explain.explain_delta(bank_delta((1, 100, 150), (2, 200, 700))))
        self.assertIn("Raises", text)

    def test_consecutive_levels_collapse_into_a_range(self) -> None:
        """99 changed rows have to read as four characters, not a wall."""
        text = first_sentence(explain.explain_delta(bank_delta(*[(n, 1, 2) for n in range(1, 100)])))
        self.assertIn("for levels 1-99", text)

    def test_gaps_in_the_range_are_kept(self) -> None:
        text = first_sentence(explain.explain_delta(bank_delta((1, 1, 2), (2, 1, 2), (7, 1, 2))))
        self.assertIn("1-2, 7", text)

    def test_one_level_is_singular(self) -> None:
        self.assertIn("for level 3", first_sentence(explain.explain_delta(bank_delta((3, 1, 2)))))

    def test_an_example_shows_the_before_and_after(self) -> None:
        out = explain.explain_delta(bank_delta((1, 50000, 100000)))
        self.assertEqual(out.tables[0].changes[0].example, "level 1: 50,000 KC → 100,000 KC")

    def test_ranges_helper(self) -> None:
        self.assertEqual(explain.ranges([3, 1, 2, 9, 10]), "1-3, 9-10")
        self.assertEqual(explain.ranges([]), "")


class NotGuessing(unittest.TestCase):
    """An undescribed column must say so, not invent a meaning."""

    # A table that will never be described, so describing more of the real
    # database cannot quietly turn this test into a no-op.
    UNKNOWN_TABLE = "master_nobody_has_described_this"

    def _unknown(self) -> DbDelta:
        td = TableDelta(self.UNKNOWN_TABLE, ["id"], ["id", "some_number"])
        td.updates.append(RowUpdate(("ROW_1",), {"some_number": 0}, {"some_number": 100}))
        return DbDelta(tables=[td])

    def test_it_shows_the_real_column_name(self) -> None:
        text = first_sentence(explain.explain_delta(self._unknown()))
        self.assertIn("some_number", text)
        self.assertIn("nobody has described this one yet", text)

    def test_the_table_keeps_its_real_name(self) -> None:
        out = explain.explain_delta(self._unknown())
        self.assertEqual(out.tables[0].title, self.UNKNOWN_TABLE)
        self.assertFalse(out.tables[0].described)

    def test_and_a_note_says_how_to_fix_it(self) -> None:
        out = explain.explain_delta(self._unknown())
        self.assertIn("table-notes.json", out.note)

    def test_a_described_table_gets_no_such_note(self) -> None:
        self.assertEqual(explain.explain_delta(bank_delta((1, 1, 2))).note, "")


class TheGamesOwnWords(unittest.TestCase):
    """Names and descriptions that need nothing written by hand."""

    def setUp(self) -> None:
        """A database shaped like the real one: rows point at the text table."""
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "masters.db"
        con = sqlite3.connect(str(self.db))
        con.executescript("""
            CREATE TABLE master_skill (
                id TEXT PRIMARY KEY, name TEXT, "desc" TEXT, buy_money INTEGER,
                val0 INTEGER, val1 INTEGER, val2 INTEGER,
                val3 INTEGER, val4 INTEGER, val5 INTEGER);
            CREATE TABLE master_text (
                sct TEXT, id TEXT, snd TEXT, lang TEXT, txt TEXT, type INTEGER,
                PRIMARY KEY (sct, id, snd, lang));
        """)
        con.execute(
            "INSERT INTO master_skill VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("SKL_EXPUP_02", "SKILL_NAME.TXT_SKL_EXPUP_02",
             "SKILL_DESCRIPTION.TXT_SKL_EXPUP_02", 500, 40, 0, 0, 0, 0, 0),
        )
        con.executemany(
            "INSERT INTO master_text VALUES (?,?,'',?,?,0)",
            [("SKILL_NAME", "TXT_SKL_EXPUP_02", "int", "Nitro Boost"),
             ("SKILL_DESCRIPTION", "TXT_SKL_EXPUP_02", "int", "Increases EXP gained by 40%.")],
        )
        con.commit()
        con.close()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _skill_delta(self, skill_id: str, before, after) -> DbDelta:
        td = TableDelta("master_skill", ["id"], ["id", "val0"])
        td.updates.append(RowUpdate((skill_id,), {"val0": after}, {"val0": before}))
        return DbDelta(tables=[td])

    def test_a_skill_is_named_from_the_game_not_its_id(self) -> None:
        td = TableDelta("master_skill", ["id"], ["id", "buy_money"])
        td.updates.append(RowUpdate(("SKL_EXPUP_02",), {"buy_money": 1}, {"buy_money": 500}))
        out = explain.explain_delta(DbDelta(tables=[td]), db_path=self.db)
        self.assertIn("Nitro Boost", out.tables[0].changes[0].sentence)

    def test_a_val_column_is_explained_by_the_game_description(self) -> None:
        """val0 means something different on every skill - so quote the game."""
        out = explain.explain_delta(self._skill_delta("SKL_EXPUP_02", 40, 100000), db_path=self.db)
        quotes = " ".join(out.tables[0].changes[0].quotes)
        self.assertIn("Nitro Boost", quotes)
        self.assertIn("Increases EXP", quotes)

    def test_without_a_database_it_falls_back_to_the_id(self) -> None:
        out = explain.explain_delta(self._skill_delta("SKL_EXPUP_02", 40, 1), db_path=None)
        self.assertTrue(out.tables, "it should still say something")

    def test_a_placeholder_is_filled_with_the_old_and_new_values(self) -> None:
        con = sqlite3.connect(str(self.db))
        con.execute(
            "UPDATE master_text SET txt = 'Boosts something by #0%.' "
            "WHERE sct='SKILL_DESCRIPTION' AND id='TXT_SKL_EXPUP_02' AND lang='int'"
        )
        con.commit()
        con.close()
        out = explain.explain_delta(self._skill_delta("SKL_EXPUP_02", 40, 90), db_path=self.db)
        quotes = " ".join(out.tables[0].changes[0].quotes)
        self.assertIn("Boosts something by 40%", quotes)
        self.assertIn("Boosts something by 90%", quotes)

    def test_runtime_placeholders_are_dropped_from_a_name(self) -> None:
        """Quest names read "Naked Victory #0" in the file; the game fills it in."""
        self.assertEqual(explain._clean_label("Naked Victory #0"), "Naked Victory")


class HomeFolderNotes(unittest.TestCase):
    """Anyone can describe a table without touching the program."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    UNKNOWN_TABLE = "master_nobody_has_described_this"

    def _delta(self) -> DbDelta:
        td = TableDelta(self.UNKNOWN_TABLE, ["id"], ["id", "some_number"])
        td.updates.append(RowUpdate(("R1",), {"some_number": 0}, {"some_number": 100}))
        return DbDelta(tables=[td])

    def test_a_home_file_can_describe_a_new_table(self) -> None:
        (self.home / "table-notes.json").write_text(json.dumps({
            self.UNKNOWN_TABLE: {
                "title": "Something Else", "word": "thing",
                "columns": {"some_number": ["the points needed to reach it", "pts"]},
            }
        }), encoding="utf-8")
        out = explain.explain_delta(self._delta(), home=self.home)
        self.assertEqual(out.tables[0].title, "Something Else")
        self.assertIn("the points needed to reach it", out.tables[0].changes[0].sentence)
        self.assertIn("0 pts", out.tables[0].changes[0].sentence)

    def test_a_broken_file_is_ignored_rather_than_fatal(self) -> None:
        (self.home / "table-notes.json").write_text("{ not json", encoding="utf-8")
        out = explain.explain_delta(self._delta(), home=self.home)
        self.assertEqual(out.tables[0].title, self.UNKNOWN_TABLE)

    def test_the_built_in_notes_still_apply(self) -> None:
        (self.home / "table-notes.json").write_text("{}", encoding="utf-8")
        out = explain.explain_delta(bank_delta((1, 1, 2)), home=self.home)
        self.assertEqual(out.tables[0].title, "Buffalo Bank (Kill Bank) upgrades")


class GameTextIsCountedPerLine(unittest.TestCase):
    """One changed name is ten changed rows - one per language.

    Reporting "1,528 rows" for a mod that renames 191 floors is true and
    useless, so game text is counted in lines and languages instead.
    """

    def _text_delta(self, *rows) -> DbDelta:
        td = TableDelta("master_text", ["sct", "id", "snd", "lang"],
                        ["sct", "id", "snd", "lang", "txt"])
        for text_id, lang, before, after in rows:
            td.updates.append(
                RowUpdate(("AREA_NAME", text_id, "", lang), {"txt": after}, {"txt": before})
            )
        return DbDelta(tables=[td])

    def test_languages_are_counted_not_listed(self) -> None:
        rows = [("TXT_A", lang, "Old", "New") for lang in ("int", "deu", "fra", "jpn")]
        out = explain.explain_delta(self._text_delta(*rows))
        sentence = out.tables[0].changes[0].sentence
        self.assertIn("1 line of text", sentence)
        self.assertIn("4 languages", sentence)

    def test_several_lines_read_as_lines(self) -> None:
        rows = [(f"TXT_{n}", lang, "Old", "New") for n in range(3) for lang in ("int", "deu")]
        sentence = explain.explain_delta(self._text_delta(*rows)).tables[0].changes[0].sentence
        self.assertIn("3 lines of text", sentence)
        self.assertIn("2 languages", sentence)

    def test_the_example_is_the_english_one(self) -> None:
        out = explain.explain_delta(self._text_delta(
            ("TXT_A", "deu", "Alt", "Neu"), ("TXT_A", "int", "Old", "New")))
        self.assertIn("Old", out.tables[0].changes[0].example)
        self.assertIn("New", out.tables[0].changes[0].example)

    def test_the_example_is_flattened_onto_one_line(self) -> None:
        """The game stores descriptions with newlines and trailing spaces."""
        messy_before = "Old" + chr(10) + " "
        messy_after = "New" + chr(10) + " "
        out = explain.explain_delta(self._text_delta(("TXT_A", "int", messy_before, messy_after)))
        self.assertNotIn(chr(10), out.tables[0].changes[0].example)

    def test_the_table_is_named(self) -> None:
        out = explain.explain_delta(self._text_delta(("TXT_A", "int", "a", "b")))
        self.assertEqual(out.tables[0].title, "Game text (names and descriptions)")


class ProductNames(unittest.TestCase):
    def _price_delta(self, *ids) -> DbDelta:
        td = TableDelta("master_shop_product_price", ["id"], ["id", "price"])
        for product in ids:
            td.updates.append(RowUpdate((product,), {"price": 1}, {"price": 100}))
        return DbDelta(tables=[td])

    def test_a_known_product_gets_its_real_name(self) -> None:
        out = explain.explain_delta(self._price_delta("PRD_CONTINUE"))
        self.assertIn("Revive", out.tables[0].changes[0].sentence)

    def test_numbered_products_stay_distinct(self) -> None:
        """Otherwise eight revive grades all read as "Revive; Revive; Revive"."""
        out = explain.explain_delta(self._price_delta("PRD_CONTINUE", "PRD_CONTINUE_2"))
        sentence = out.tables[0].changes[0].sentence
        self.assertIn("Revive 2", sentence)
        self.assertEqual(sentence.count("Revive"), 2)

    def test_an_unknown_product_keeps_its_id(self) -> None:
        out = explain.explain_delta(self._price_delta("PRD_MYSTERY"))
        self.assertIn("PRD_MYSTERY", out.tables[0].changes[0].sentence)


class UnusedTables(unittest.TestCase):
    """Ten tables are never named in the game executable, so they are dead.

    Saying "this does nothing" is more useful than describing the columns of a
    table the offline game does not read.
    """

    def _delta(self, table: str) -> DbDelta:
        td = TableDelta(table, ["id"], ["id", "no"])
        td.updates.append(RowUpdate(("X",), {"no": 1}, {"no": 2}))
        return DbDelta(tables=[td])

    def test_it_says_so_before_anything_else(self) -> None:
        out = explain.explain_delta(self._delta("master_quest_online"))
        self.assertIn("never reads this table", out.tables[0].changes[0].sentence)

    def test_a_live_table_says_no_such_thing(self) -> None:
        out = explain.explain_delta(bank_delta((1, 1, 2)))
        self.assertNotIn("never reads", " ".join(c.sentence for c in out.tables[0].changes))

    def test_an_empty_table_is_not_called_dead(self) -> None:
        """A table with no rows in vanilla is not evidence the game ignores it.

        The ten dead ones are named nowhere in the game executable, which is
        real evidence. "No rows" is not, so those must leave the question open
        instead of telling a modder not to bother.
        """
        out = explain.explain_delta(self._delta("master_quest_condition_int"))
        said = out.tables[0].changes[0].sentence
        self.assertIn("leaves this table empty", said)
        self.assertNotIn("never reads", said)


class EveryTableModsTouchIsDescribed(unittest.TestCase):
    """The point of the dictionary is the tables mods actually edit.

    Covering all 221 tables is not the goal; covering the ones people mod is.
    If a new mod starts touching something undescribed, this should fail and
    prompt an entry rather than letting the tab quietly show raw column names.
    """

    # Every table the shipped mods, the local third-party mods, and the
    # Crossover pack's own installer write to.
    TOUCHED = {
        "master_skill", "master_text", "master_shop_product_price",
        "master_body_detail", "master_const_int", "master_shop_appearance",
        "master_part", "master_item", "master_quest", "master_reward",
        "master_skillgacha", "master_skillgacha_odds", "master_asset",
        "master_part_asset", "master_part_research", "master_part_defattr",
        "master_part_equipment", "master_part_param_offset",
    }

    def test_all_of_them_have_an_entry(self) -> None:
        from lid_db_manager.explain_data import TABLES

        missing = sorted(self.TOUCHED - set(TABLES))
        self.assertEqual(missing, [], f"undescribed tables that mods touch: {missing}")


class CodedValues(unittest.TestCase):
    """The database is full of codes. A player should not have to read them."""

    def _defattr(self, attr: str) -> DbDelta:
        td = TableDelta("master_part_defattr", ["id", "attr"], ["id", "attr", "value"])
        td.updates.append(RowUpdate(("PT_X", attr), {"value": 99}, {"value": 5}))
        return DbDelta(tables=[td])

    def test_a_coded_value_is_shown_in_plain_words(self) -> None:
        out = explain.explain_delta(self._defattr("ATKATTR_FIRE"))
        self.assertIn("fire", out.tables[0].changes[0].sentence)
        self.assertNotIn("ATKATTR", out.tables[0].changes[0].sentence)

    def test_an_unknown_code_is_left_exactly_as_it_is(self) -> None:
        """Never invent a friendly name for something we have not looked up."""
        out = explain.explain_delta(self._defattr("ATKATTR_MYSTERY"))
        self.assertIn("ATKATTR_MYSTERY", out.tables[0].changes[0].sentence)


class RowLabels(unittest.TestCase):
    def test_blank_key_parts_are_skipped(self) -> None:
        """A blank condition column should not read as "nothing" in a name."""
        td = TableDelta("master_stage_trbox", ["pntid", "stgid", "unit", "game_flg"],
                        ["pntid", "stgid", "unit", "game_flg", "freq"])
        td.updates.append(RowUpdate(("TB_00", "S_AMS", "A10", ""), {"freq": 1}, {"freq": 2}))
        sentence = explain.explain_delta(DbDelta(tables=[td])).tables[0].changes[0].sentence
        self.assertIn("TB_00 / Amusement / A10", sentence)
        self.assertNotIn("nothing", sentence)


class QuestNamePlaceholders(unittest.TestCase):
    """Quest names are written with a gap - "Element Limit #0" - and the part
    that tells one quest in a series from the next lives in another table. Without
    filling it in, every quest in a series reads as the same name."""

    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.db = Path(self.dir.name) / "masters.db"
        con = sqlite3.connect(self.db)
        con.executescript("""
            CREATE TABLE master_text (sct TEXT, id TEXT, lang TEXT, txt TEXT);
            CREATE TABLE master_quest (qid TEXT PRIMARY KEY, name TEXT, cond_flr INT);
            CREATE TABLE master_quest_param_text (qid TEXT, no INT, val TEXT);
        """)
        # A pointer is 'SECTION.ID', and master_text keeps the two halves apart.
        con.executemany("INSERT INTO master_text (sct, id, lang, txt) VALUES (?,?,?,?)", [
            ("QUEST_NAME", "TXT_LIMIT", "int", "Element Limit #0"),
            ("NUMBER", "TXT_001", "int", "##001"),
            ("NUMBER", "TXT_002", "int", "##002"),
        ])
        con.executemany("INSERT INTO master_quest VALUES (?,?,?)", [
            ("ATK_001", "QUEST_NAME.TXT_LIMIT", 1), ("ATK_002", "QUEST_NAME.TXT_LIMIT", 1),
        ])
        con.executemany("INSERT INTO master_quest_param_text VALUES (?,?,?)", [
            ("ATK_001", 0, "NUMBER.TXT_001"), ("ATK_002", 0, "NUMBER.TXT_002"),
        ])
        con.commit()
        con.close()

    def sentence(self) -> str:
        td = TableDelta("master_quest", ["qid"], ["qid", "cond_flr"])
        td.updates.append(RowUpdate(("ATK_001",), {"cond_flr": 5}, {"cond_flr": 1}))
        td.updates.append(RowUpdate(("ATK_002",), {"cond_flr": 5}, {"cond_flr": 1}))
        return explain.explain_delta(DbDelta(tables=[td]), db_path=self.db).tables[0].changes[0].sentence

    def test_quests_sharing_a_name_are_told_apart(self) -> None:
        sentence = self.sentence()
        self.assertIn("Element Limit #001", sentence)
        self.assertIn("Element Limit #002", sentence)

    def test_the_doubled_hash_the_game_uses_is_not_shown(self) -> None:
        """The game writes a literal # as ##; the player should see one."""
        self.assertNotIn("##", self.sentence())

    def test_a_missing_parameter_leaves_the_name_readable(self) -> None:
        """A mod may delete the parameter row. The name must still say something."""
        con = sqlite3.connect(self.db)
        con.execute("DELETE FROM master_quest_param_text")
        con.commit()
        con.close()
        sentence = self.sentence()
        self.assertIn("Element Limit", sentence)
        self.assertNotIn("None", sentence)


class RowsNamedFromTheirOwnColumns(unittest.TestCase):
    """A table keyed on a plain row number has nothing useful in its key, so
    "for 12,970" tells a player nothing. Those rows are named from their columns."""

    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.db = Path(self.dir.name) / "masters.db"
        con = sqlite3.connect(self.db)
        con.execute("CREATE TABLE master_login_bonus "
                    "(cond_type TEXT, cond_date TEXT, cond_days INT, rwdid TEXT)")
        con.executemany("INSERT INTO master_login_bonus VALUES (?,?,?,?)", [
            ("DATE", "2019-11-02", 0, "RWD_A"),
            ("DAYS", "0000-00-00", 5, "RWD_A"),
        ])
        con.commit()
        con.close()

    def sentence(self) -> str:
        td = TableDelta("master_login_bonus", ["rowid"],
                        ["rowid", "cond_type", "cond_date", "cond_days", "rwdid"])
        td.updates.append(RowUpdate((1,), {"rwdid": "RWD_B"}, {"rwdid": "RWD_A"}))
        td.updates.append(RowUpdate((2,), {"rwdid": "RWD_B"}, {"rwdid": "RWD_A"}))
        return explain.explain_delta(DbDelta(tables=[td]), db_path=self.db).tables[0].changes[0].sentence

    def test_a_row_is_named_by_what_earns_it(self) -> None:
        sentence = self.sentence()
        self.assertIn("2019-11-02 / DATE", sentence)
        self.assertIn("5 / DAYS", sentence)

    def test_the_row_number_is_not_shown(self) -> None:
        """The rowid is an accident of storage, not something a player knows."""
        self.assertNotIn("for 1;", self.sentence())

    def test_filler_values_are_left_out(self) -> None:
        """A date of 0000-00-00 means "not by date" - saying it would mislead."""
        self.assertNotIn("0000-00-00", self.sentence())


class TablesThatAreNeverQuotedFrom(unittest.TestCase):
    """The banned-word list is 2,627 slurs and swearwords. A mod emptying it is
    perfectly ordinary, but printing a sample would put them in front of the
    player for no reason, so that table is counted and never quoted."""

    def delta(self) -> DbDelta:
        td = TableDelta("master_ngword", ["id"], ["id", "word", "type"])
        td.deletes.extend([(1,), (2,), (3,)])
        return DbDelta(tables=[td])

    def test_it_says_how_many_without_listing_them(self) -> None:
        sentence = explain.explain_delta(self.delta()).tables[0].changes[0].sentence
        self.assertEqual(sentence, "Removes 3 words.")

    def test_other_tables_still_list_their_rows(self) -> None:
        """The flag must be opt-in, or every explanation loses its detail."""
        td = TableDelta("master_safe_level", ["level"], ["level", "limit"])
        td.deletes.extend([(1,), (2,)])
        sentence = explain.explain_delta(DbDelta(tables=[td])).tables[0].changes[0].sentence
        self.assertIn("1", sentence)
        self.assertIn(":", sentence)


class TableEntriesAreWellFormed(unittest.TestCase):
    """A misspelt rule key does not fail - it silently falls back to a default.

    That is how "describes": {"text": ...} quietly described the wrong column
    for a while: the real key is "column", so the rule fell back to "desc" and
    the tab showed flavour text instead of the wording carrying the numbers.
    Nothing complained. These check the shape of every entry instead.
    """

    ROW_KINDS = {"level", "number", "labelled", "via", "text", "key", "columns",
                 "item", "fighter_tier", "shop_product", "game_text"}
    ROW_KEYS = {"kind", "word", "words", "column", "columns", "table", "other_key",
                "text_column", "with", "params", "skip", "lang", "label"}
    DESCRIBES_KEYS = {"column", "values"}
    ENTRY_KEYS = {"word", "title", "about", "row", "columns", "describes",
                  "unused", "empty", "no_examples"}

    def test_every_entry_uses_known_keys(self) -> None:
        for table, entry in explain_data.TABLES.items():
            with self.subTest(table=table):
                self.assertLessEqual(set(entry), self.ENTRY_KEYS)

    def test_every_row_rule_is_known(self) -> None:
        for table, entry in explain_data.TABLES.items():
            rule = entry.get("row", {})
            with self.subTest(table=table):
                self.assertIn(rule.get("kind"), self.ROW_KINDS)
                self.assertLessEqual(set(rule), self.ROW_KEYS)

    def test_every_describes_rule_is_known(self) -> None:
        for table, entry in explain_data.TABLES.items():
            describes = entry.get("describes")
            if describes is None:
                continue
            with self.subTest(table=table):
                self.assertLessEqual(set(describes), self.DESCRIBES_KEYS)
                self.assertTrue(describes.get("values"))

    def test_every_column_entry_is_a_meaning_and_a_unit(self) -> None:
        for table, entry in explain_data.TABLES.items():
            for column, described in entry.get("columns", {}).items():
                with self.subTest(table=table, column=column):
                    self.assertIsInstance(described, tuple)
                    self.assertEqual(len(described), 2)
                    self.assertTrue(described[0])


class Plurals(unittest.TestCase):
    def test_words_are_pluralised(self) -> None:
        self.assertEqual(explain.plural("entry", 2), "entries")
        self.assertEqual(explain.plural("level", 2), "levels")
        self.assertEqual(explain.plural("level", 1), "level")
        self.assertEqual(explain.plural("bonus", 2), "bonuses")
        self.assertEqual(explain.plural("draw", 2), "draws")


class AddedAndRemovedRows(unittest.TestCase):
    def test_added_rows_are_counted(self) -> None:
        td = TableDelta("master_quest", ["qid"], ["qid", "no"])
        td.inserts.append(("NEW_QUEST", 1))
        out = explain.explain_delta(DbDelta(tables=[td]))
        self.assertIn("Adds 1 new quest", out.tables[0].changes[0].sentence)

    def test_a_new_table_is_called_out(self) -> None:
        td = TableDelta("master_thing", ["id"], ["id"], create_sql="CREATE TABLE master_thing (id)")
        td.inserts.append(("X",))
        out = explain.explain_delta(DbDelta(tables=[td]))
        self.assertIn("does not normally have", out.tables[0].changes[0].sentence)

    def test_nothing_at_all_is_said_plainly(self) -> None:
        self.assertTrue(explain.explain_delta(DbDelta()).empty)
        self.assertTrue(explain.explain_delta(None).empty)


@unittest.skipUnless(HAVE_QT, "PySide6 is not installed")
class ThePlainEnglishTab(unittest.TestCase):
    app = None

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        from fixtures import write_mod

        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.paths = AppPaths(self.root / "app").ensure()
        self.db = build_db(self.root / "game" / "masters.db")
        write_mod(self.paths.mods_dir, "cheap-skills", {
            "patches": [{"type": "update_set", "table": "master_skill",
                         "set": {"buy_money": 1}, "where": "1=1"}]})
        self.manager = Manager(self.paths)
        self.manager.set_db_path(self.db)
        self.view = DiffView(self.manager, dark=True)

    def tearDown(self) -> None:
        self.view.deleteLater()
        self._tmp.cleanup()

    def test_the_tab_exists_and_is_second(self) -> None:
        titles = [self.view.tabs.tabText(i) for i in range(self.view.tabs.count())]
        self.assertEqual(titles, ["Details", "In plain English", "Diff preview", "Readme"])

    def test_it_explains_a_real_mod(self) -> None:
        self.view.show_mod("cheap-skills")
        self.view._load_plain(self.manager.scan.get("cheap-skills"))
        text = self.view.plain.toPlainText()
        self.assertIn("Skills and decals", text)
        self.assertIn("the price to buy it", text)

    def test_it_warns_that_the_game_may_not_show_it(self) -> None:
        self.view.show_mod("cheap-skills")
        self.view._load_plain(self.manager.scan.get("cheap-skills"))
        self.assertIn("draws as pictures", self.view.plain.toPlainText())

    def test_a_broken_mod_does_not_take_the_panel_down(self) -> None:
        mod = self.manager.scan.get("cheap-skills")
        original = self.manager.mod_delta
        self.manager.mod_delta = lambda m: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            self.view._load_plain(mod)
        finally:
            self.manager.mod_delta = original
        self.assertIn("Could not work it out", self.view.plain.toPlainText())


if __name__ == "__main__":
    unittest.main()
