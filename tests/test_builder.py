"""Building a mod by editing values instead of writing SQL.

The builder's one real promise is that it cannot produce a mod the rest of the
program would not accept, because it never writes a mod itself - it edits a
scratch copy of the database and hands that to the ordinary import path. Most
of what is worth testing is the ways an edit could go wrong on the way there:
changing a key, typing words into a number, scaling an integer into a decimal,
or quietly counting an edit that put a value back where it started.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fixtures import build_db

from lid_db_manager import builder_data
from lid_db_manager.builder import (
    BuildError,
    ModBuilder,
    blueprint_part_id,
    currency_columns,
)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication  # noqa: F401

    HAVE_QT = True
except ImportError:  # pragma: no cover - depends on the environment
    HAVE_QT = False


def a_database(path: Path) -> Path:
    """A small database shaped like the parts of masters.db we edit."""
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE master_safe_level (level INTEGER PRIMARY KEY, "limit" INT, price INT);
        CREATE TABLE master_body_detail (
            type TEXT, grade INT, limit_break INT, price INT, skill_slots TEXT,
            PRIMARY KEY (type, grade, limit_break));
        CREATE TABLE master_const_float (id TEXT PRIMARY KEY, value REAL);
        CREATE TABLE master_text (sct TEXT, id TEXT, lang TEXT, txt TEXT);
    """)
    # The game's own name for the BAL fighter type, so row labels resolve the
    # way they do against the real database.
    con.execute("INSERT INTO master_text (sct, id, lang, txt) VALUES (?,?,?,?)",
                ("FTYPE_NAME", "TXT_BAL", "int", "All-rounder"))
    con.executemany("INSERT INTO master_safe_level VALUES (?,?,?)",
                    [(1, 50000, 0), (2, 60000, 100), (3, 70000, 200)])
    con.executemany("INSERT INTO master_body_detail VALUES (?,?,?,?,?)",
                    [("BAL", 1, 0, 0, "1"), ("BAL", 2, 0, 4000, "1,2")])
    con.execute("INSERT INTO master_const_float VALUES ('ABDUCT_RATE', 0.03)")
    con.commit()
    con.close()
    return path


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.vanilla = a_database(Path(self.dir.name) / "vanilla.db")
        self.builder = ModBuilder(self.vanilla)
        self.addCleanup(self.builder.close)

    def keys(self, table: str) -> list[tuple]:
        return [r.key for r in self.builder.view(table, limit=500).rows]


class ItEditsAScratchCopy(Base):
    def test_the_vanilla_file_is_never_touched(self) -> None:
        """The whole safety argument rests on this."""
        before = self.vanilla.read_bytes()
        self.builder.set_value("master_safe_level", (1,), "limit", 999)
        self.assertEqual(self.vanilla.read_bytes(), before)

    def test_the_scratch_copy_does_change(self) -> None:
        self.builder.set_value("master_safe_level", (1,), "limit", 999)
        con = sqlite3.connect(self.builder.scratch)
        value = con.execute('SELECT "limit" FROM master_safe_level WHERE level=1').fetchone()[0]
        con.close()
        self.assertEqual(value, 999)

    def test_closing_removes_the_scratch_copy(self) -> None:
        scratch = self.builder.scratch
        self.assertTrue(scratch.is_file())
        self.builder.close()
        self.assertFalse(scratch.exists())

    def test_no_vanilla_is_a_clear_refusal(self) -> None:
        with self.assertRaises(BuildError) as caught:
            ModBuilder(Path(self.dir.name) / "nothing-here.db")
        self.assertIn("clean copy", str(caught.exception))


class WhatItRefusesToDo(Base):
    def test_a_key_column_cannot_be_edited(self) -> None:
        """Editing a key would move the row, not change it."""
        with self.assertRaises(BuildError) as caught:
            self.builder.set_value("master_safe_level", (1,), "level", 9)
        self.assertIn("identifies the row", str(caught.exception))

    def test_words_in_a_number_column_are_refused_by_name(self) -> None:
        with self.assertRaises(BuildError) as caught:
            self.builder.set_value("master_safe_level", (1,), "limit", "lots")
        said = str(caught.exception)
        self.assertIn("holds a whole number", said)
        self.assertIn("the most Kill Coins it can hold", said)

    def test_an_unknown_column_is_refused(self) -> None:
        with self.assertRaises(BuildError):
            self.builder.set_value("master_safe_level", (1,), "nonesuch", 1)

    def test_text_cannot_be_multiplied(self) -> None:
        with self.assertRaises(BuildError):
            self.builder.scale("master_body_detail", self.keys("master_body_detail"),
                               "skill_slots", 2)

    def test_saving_nothing_is_refused(self) -> None:
        with self.assertRaises(BuildError) as caught:
            self.builder.candidate()
        self.assertIn("Nothing has been changed", str(caught.exception))


class CountingChanges(Base):
    def test_putting_a_value_back_is_not_a_change(self) -> None:
        """Otherwise the mod would carry rows identical to vanilla."""
        self.builder.set_value("master_safe_level", (1,), "limit", 999)
        self.assertEqual(self.builder.change_count(), 1)
        self.builder.set_value("master_safe_level", (1,), "limit", 50000)
        self.assertEqual(self.builder.change_count(), 0)
        self.assertFalse(self.builder.dirty)

    def test_editing_the_same_cell_twice_counts_once(self) -> None:
        self.builder.set_value("master_safe_level", (1,), "limit", 10)
        self.builder.set_value("master_safe_level", (1,), "limit", 20)
        self.assertEqual(self.builder.change_count(), 1)

    def test_revert_all_puts_everything_back(self) -> None:
        for key in self.keys("master_safe_level"):
            self.builder.set_value("master_safe_level", key, "limit", 1)
        self.builder.revert_all()
        self.assertFalse(self.builder.dirty)
        con = sqlite3.connect(self.builder.scratch)
        limits = [r[0] for r in con.execute('SELECT "limit" FROM master_safe_level ORDER BY level')]
        con.close()
        self.assertEqual(limits, [50000, 60000, 70000])

    def test_the_summary_names_tables_in_plain_english(self) -> None:
        self.builder.set_value("master_safe_level", (1,), "limit", 1)
        self.assertEqual(self.builder.summary(), ["Buffalo Bank (Kill Bank) upgrades: 1 value"])


class BulkEdits(Base):
    def test_setting_every_row_at_once(self) -> None:
        changed = self.builder.set_many("master_safe_level",
                                        self.keys("master_safe_level"), "limit", 7)
        self.assertEqual(changed, 3)
        self.assertEqual(self.builder.change_count(), 3)

    def test_multiplying_keeps_whole_numbers_whole(self) -> None:
        """1.5x on an integer column must not write 75000.0 - the game reads an int."""
        self.builder.scale("master_safe_level", [(1,)], "limit", 1.5)
        con = sqlite3.connect(self.builder.scratch)
        value = con.execute('SELECT "limit" FROM master_safe_level WHERE level=1').fetchone()[0]
        con.close()
        self.assertEqual(value, 75000)
        self.assertIsInstance(value, int)

    def test_multiplying_keeps_decimals_as_decimals(self) -> None:
        self.builder.scale("master_const_float", [("ABDUCT_RATE",)], "value", 2)
        con = sqlite3.connect(self.builder.scratch)
        value = con.execute("SELECT value FROM master_const_float").fetchone()[0]
        con.close()
        self.assertAlmostEqual(value, 0.06)


class DescribingColumns(Base):
    def test_a_described_column_shows_its_meaning_and_unit(self) -> None:
        column = next(c for c in self.builder.columns("master_safe_level") if c.name == "limit")
        self.assertEqual(column.heading, "the most Kill Coins it can hold (KC)")
        self.assertTrue(column.described)

    def test_an_undescribed_column_falls_back_to_its_real_name(self) -> None:
        con = sqlite3.connect(self.builder.scratch)
        con.execute("CREATE TABLE master_nonesuch (id TEXT PRIMARY KEY, wibble INT)")
        con.commit()
        con.close()
        self.builder._schema.clear()
        column = next(c for c in self.builder.columns("master_nonesuch") if c.name == "wibble")
        self.assertEqual(column.heading, "wibble")
        self.assertFalse(column.described)

    def test_described_columns_come_first(self) -> None:
        """master_part has 90 editable columns and 7 worth touching.

        In schema order the useful ones are buried behind dozens nobody has a
        name for, so anything described is shown first.
        """
        con = sqlite3.connect(self.builder.scratch)
        con.execute('ALTER TABLE master_safe_level ADD COLUMN wibble INT DEFAULT 0')
        con.commit()
        con.close()
        self.builder._schema.clear()
        editable = [c for c in self.builder.columns("master_safe_level") if not c.is_key]
        self.assertTrue(editable[0].described)
        self.assertEqual(editable[-1].name, "wibble")

    def test_list_columns_are_flagged_so_they_are_not_edited_as_numbers(self) -> None:
        """skill_slots is "1,2,3" - a spin box would turn three slots into one."""
        column = next(c for c in self.builder.columns("master_body_detail")
                      if c.name == "skill_slots")
        self.assertTrue(column.is_list)
        self.builder.set_value("master_body_detail", ("BAL", 2, 0), "skill_slots", "1,2,3")
        con = sqlite3.connect(self.builder.scratch)
        value = con.execute(
            "SELECT skill_slots FROM master_body_detail WHERE type='BAL' AND grade=2"
        ).fetchone()[0]
        con.close()
        self.assertEqual(value, "1,2,3")

    def test_rows_are_named_the_way_the_explanation_tab_names_them(self) -> None:
        """A row reads as the game's own words, not as its key."""
        rows = self.builder.view("master_body_detail").rows
        self.assertIn("All-rounder", rows[0].label)

    def test_a_row_is_still_readable_when_the_game_text_is_missing(self) -> None:
        """A mod may have emptied master_text. The editor must stay usable."""
        con = sqlite3.connect(self.builder.scratch)
        con.execute("DELETE FROM master_text")
        con.commit()
        con.close()
        fresh = ModBuilder(self.builder.scratch)
        self.addCleanup(fresh.close)
        label = fresh.view("master_body_detail").rows[0].label
        self.assertIn("grade 1", label)
        self.assertNotIn("None", label)


class Paging(Base):
    def test_a_page_is_capped_but_the_total_is_honest(self) -> None:
        view = self.builder.view("master_safe_level", limit=2)
        self.assertEqual(len(view.rows), 2)
        self.assertEqual(view.total, 3)

    def test_searching_narrows_the_total_too(self) -> None:
        view = self.builder.view("master_const_float", search="ABDUCT")
        self.assertEqual(view.total, 1)

    def test_search_matches_numbers_as_well_as_words(self) -> None:
        view = self.builder.view("master_safe_level", search="60000")
        self.assertEqual(view.total, 1)


class NamingBlueprints(unittest.TestCase):
    """All 1,899 blueprints are called "RMAP" or "UNKNOWN_RMAP" in the game's
    own text, so a picker that showed their names would show nothing useful.
    Each one is named by the weapon or armour it makes instead."""

    def test_a_blueprint_maps_onto_the_part_it_makes(self) -> None:
        self.assertEqual(blueprint_part_id("ITMP_ARM_WP001_001"), "PT_ARM_WP001_001")

    def test_the_unidentified_version_maps_to_the_same_part(self) -> None:
        """A trailing U is the same blueprint, not yet identified."""
        self.assertEqual(blueprint_part_id("ITMP_ARM_WP001_001U"), "PT_ARM_WP001_001")

    def test_something_that_is_not_a_blueprint_is_left_alone(self) -> None:
        self.assertEqual(blueprint_part_id("PT_ARM_WP001_001"), "PT_ARM_WP001_001")


class StockingTheVendingMachine(Base):
    """The machine keeps no price of its own - every pack_ and discount column
    is zero on all 315 vanilla rows - so what it charges is the item's price."""

    def setUp(self) -> None:
        # Names are read from the VANILLA file, not the scratch copy, so that
        # what things are called cannot drift as you edit. The shop tables have
        # to exist there before the builder takes its copy.
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.vanilla = a_database(Path(self.dir.name) / "vanilla.db")
        con = sqlite3.connect(self.vanilla)
        con.executescript("""
            CREATE TABLE master_item (
                itemid TEXT PRIMARY KEY, itemtype TEXT, name TEXT, rarity INT,
                buy_money INT, buy_recycle_point INT, buy_bloodnium INT);
            CREATE TABLE master_automaticshop_lineup (
                goods_id INT, lineup_id TEXT, entity_type TEXT, type_id TEXT,
                is_stable INT, freq INT, is_special INT, display_priority INT,
                currency_type INT, stock INT, pack_count INT, pack_money INT,
                pack_metal INT, pack_recycle_point INT, pack_bloodnium INT,
                money_discount_rate INT, metal_discount_rate INT,
                recycle_point_discount_rate INT, bloodnium_discount_rate INT);
        """)
        con.executemany("INSERT INTO master_item VALUES (?,?,?,?,?,?,?)", [
            ("ITMT_ALUMI_1", "ITTP_MATERIAL", "ITEM.TXT_ALUMI", 3, 100, 0, 0),
            ("ITMT_COPPER_1", "ITTP_MATERIAL", "ITEM.TXT_COPPER", 2, 200, 0, 0),
        ])
        con.execute(
            "INSERT INTO master_automaticshop_lineup VALUES "
            "(1,'MON','ITEM','ITMT_ALUMI_1',1,0,0,1,4,1,1,0,0,0,0,0,0,0,0)"
        )
        con.executemany("INSERT INTO master_text (sct, id, lang, txt) VALUES (?,?,?,?)", [
            ("ITEM", "TXT_ALUMI", "int", "Aluminum Scraps"),
            ("ITEM", "TXT_COPPER", "int", "Copper Scraps"),
        ])
        con.commit()
        con.close()
        self.builder = ModBuilder(self.vanilla)
        self.addCleanup(self.builder.close)

    def test_the_catalogue_names_things_properly(self) -> None:
        names = {s.name for s in self.builder.catalogue()}
        self.assertIn("Aluminum Scraps", names)

    def test_it_says_where_something_is_already_sold(self) -> None:
        found = next(s for s in self.builder.catalogue() if s.item_id == "ITMT_ALUMI_1")
        self.assertEqual(found.already, "MON")

    def test_a_new_row_copies_the_tab_it_joins(self) -> None:
        """currency_type decides which tab it shows on, so copy the list's."""
        self.builder.stock_machine(["ITMT_COPPER_1"], "MON")
        con = sqlite3.connect(self.builder.scratch)
        row = con.execute(
            "SELECT currency_type, display_priority FROM master_automaticshop_lineup "
            "WHERE type_id='ITMT_COPPER_1'"
        ).fetchone()
        con.close()
        self.assertEqual(row[0], 4)       # the same as the MON list's other rows
        self.assertEqual(row[1], 2)       # after the one already there

    def test_adding_something_already_on_that_tab_does_nothing(self) -> None:
        self.assertEqual(self.builder.stock_machine(["ITMT_ALUMI_1"], "MON"), 0)
        self.assertFalse(self.builder.dirty)

    def test_setting_a_price_changes_the_item_not_the_machine(self) -> None:
        self.builder.stock_machine(["ITMT_COPPER_1"], "MON", price=1)
        con = sqlite3.connect(self.builder.scratch)
        price = con.execute(
            "SELECT buy_bloodnium FROM master_item WHERE itemid='ITMT_COPPER_1'"
        ).fetchone()[0]
        pack = con.execute(
            "SELECT pack_money FROM master_automaticshop_lineup WHERE type_id='ITMT_COPPER_1'"
        ).fetchone()[0]
        con.close()
        self.assertEqual(price, 1)
        self.assertEqual(pack, 0, "the machine carries no price of its own")

    def test_an_unknown_tab_is_refused(self) -> None:
        with self.assertRaises(BuildError):
            self.builder.stock_machine(["ITMT_COPPER_1"], "NOPE")

    def test_added_rows_count_as_changes(self) -> None:
        self.builder.stock_machine(["ITMT_COPPER_1"], "MON")
        self.assertTrue(self.builder.dirty)
        self.assertEqual(self.builder.change_count(), 1)

    def test_categories_only_offer_what_the_machine_sells(self) -> None:
        self.assertEqual(
            {s.category for s in self.builder.catalogue(category="Materials")},
            {"Materials"},
        )


class Richer(Base):
    """The small database plus whatever extra tables a test class needs.

    They go into the vanilla file before the builder takes its copy, because
    names and the stock ranges are both read from vanilla.
    """

    EXTRA = ""

    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.vanilla = a_database(Path(self.dir.name) / "vanilla.db")
        con = sqlite3.connect(self.vanilla)
        con.executescript(self.EXTRA)
        con.commit()
        con.close()
        self.builder = ModBuilder(self.vanilla)
        self.addCleanup(self.builder.close)

    def cell(self, sql: str, *params):
        con = sqlite3.connect(self.builder.scratch)
        try:
            return con.execute(sql, params).fetchone()[0]
        finally:
            con.close()


class Dates(Richer):
    """Dates are stored two ways, and a value in the wrong shape is ignored by
    the game rather than refused - so the builder shows and takes real dates."""

    EXTRA = """
        CREATE TABLE master_event_schedule (
            id TEXT PRIMARY KEY, type TEXT, start INTEGER, "end" INTEGER);
        INSERT INTO master_event_schedule VALUES
            ('EVENT_SEASON_WINTER', 'SEASON', 1759140000, 0);
        CREATE TABLE master_automaticshop_schedule (
            expire DATETIME, bloodnium_exchange_lineup_id TEXT);
        INSERT INTO master_automaticshop_schedule VALUES ('2026-08-01 00:00:00', 'SUN');
    """

    def column(self, table: str, name: str):
        return next(c for c in self.builder.columns(table) if c.name == name)

    def test_seconds_are_shown_as_a_date(self) -> None:
        start = self.column("master_event_schedule", "start")
        self.assertEqual(start.show(1759140000), "2025-09-29 10:00")
        self.assertIn("date, UTC", start.heading)

    def test_none_stays_as_it_is(self) -> None:
        """0 and -1 mean "no date"; turning them into 1970 would mislead."""
        end = self.column("master_event_schedule", "end")
        self.assertEqual(end.show(0), "0")
        self.assertEqual(end.show(-1), "-1")

    def test_a_typed_date_is_stored_as_seconds(self) -> None:
        self.builder.set_value("master_event_schedule", ("EVENT_SEASON_WINTER",),
                               "start", "2027-01-31")
        stored = self.cell("SELECT start FROM master_event_schedule")
        self.assertEqual(stored, 1801353600)
        self.assertIsInstance(stored, int)

    def test_a_date_with_a_time_is_taken_too(self) -> None:
        self.builder.set_value("master_event_schedule", ("EVENT_SEASON_WINTER",),
                               "start", "2027-01-31 10:00")
        self.assertEqual(self.cell("SELECT start FROM master_event_schedule"),
                         1801353600 + 36000)

    def test_the_raw_number_still_works(self) -> None:
        self.builder.set_value("master_event_schedule", ("EVENT_SEASON_WINTER",),
                               "start", "12345")
        self.assertEqual(self.cell("SELECT start FROM master_event_schedule"), 12345)

    def test_something_that_is_not_a_date_is_refused(self) -> None:
        with self.assertRaises(BuildError) as caught:
            self.builder.set_value("master_event_schedule", ("EVENT_SEASON_WINTER",),
                                   "start", "next tuesday")
        self.assertIn("2027-01-31", str(caught.exception))

    def test_a_text_date_keeps_the_shape_the_table_uses(self) -> None:
        """The vending schedule stores '2026-08-01 00:00:00', not a number."""
        rowid = self.builder.view("master_automaticshop_schedule").rows[0].key
        self.builder.set_value("master_automaticshop_schedule", rowid, "expire", "2027-03-01")
        self.assertEqual(self.cell("SELECT expire FROM master_automaticshop_schedule"),
                         "2027-03-01 00:00:00")

    def test_a_date_cannot_be_multiplied(self) -> None:
        with self.assertRaises(BuildError):
            self.builder.scale("master_event_schedule", [("EVENT_SEASON_WINTER",)],
                               "start", 2)


class TheStockRange(Base):
    """A decal draw set to 1,000,000 crashed the game, where nothing in the
    stock game's price column passed 200,000. The builder says so - and still
    saves it, because going past the stock numbers is sometimes the point."""

    def test_a_value_inside_the_range_says_nothing(self) -> None:
        self.assertEqual(self.builder.range_note("master_safe_level", "limit", 60000), "")

    def test_a_value_past_the_top_is_named_with_the_stock_highest(self) -> None:
        note = self.builder.range_note("master_safe_level", "limit", 1_000_000)
        self.assertIn("1,000,000", note)
        self.assertIn("highest 70,000", note)

    def test_a_value_under_the_bottom_is_named_too(self) -> None:
        note = self.builder.range_note("master_safe_level", "limit", 5)
        self.assertIn("lowest 50,000", note)

    def test_it_warns_but_never_refuses(self) -> None:
        self.builder.set_value("master_safe_level", (1,), "limit", 1_000_000)
        self.assertEqual(self.builder.value("master_safe_level", (1,), "limit"), 1_000_000)
        risks = self.builder.risks()
        self.assertEqual(len(risks), 1)
        self.assertIn("Buffalo Bank", str(risks[0]))

    def test_the_range_is_the_stock_one_not_the_edited_one(self) -> None:
        """Otherwise one big edit would widen the range and hide the next."""
        self.builder.set_value("master_safe_level", (2,), "limit", 1_000_000)
        self.assertEqual(self.builder.stock_range("master_safe_level", "limit"),
                         (50000, 70000))

    def test_text_and_lists_are_never_flagged(self) -> None:
        self.assertEqual(self.builder.range_note("master_body_detail", "skill_slots", 99), "")

    def test_a_value_put_back_is_no_longer_a_risk(self) -> None:
        self.builder.set_value("master_safe_level", (1,), "limit", 1_000_000)
        self.builder.set_value("master_safe_level", (1,), "limit", 50000)
        self.assertEqual(self.builder.risks(), [])


class FloorsStoredTwice(Richer):
    """master_floor and the master_tmpfloor_ tables hold the same values, and
    which one the game reads is not recorded - so both change together."""

    EXTRA = """
        CREATE TABLE master_floor (
            id TEXT, areaid TEXT, refareaid TEXT, itemmax INT, clnum INT,
            PRIMARY KEY (id, areaid, refareaid));
        CREATE TABLE master_tmpfloor_item (
            id TEXT, areaid TEXT, refareaid TEXT, itemmax INT, clnum INT,
            PRIMARY KEY (id, areaid, refareaid));
        INSERT INTO master_floor VALUES ('F1', 'A1', '', 2, 0);
        INSERT INTO master_tmpfloor_item VALUES ('F1', 'A1', '', 2, 1);
    """
    KEY = ("F1", "A1", "")

    def test_an_edit_to_one_copy_reaches_the_other(self) -> None:
        self.builder.set_value("master_tmpfloor_item", self.KEY, "itemmax", 9)
        self.assertEqual(self.builder.value("master_floor", self.KEY, "itemmax"), 9)
        self.assertEqual(self.builder.change_count(), 2, "two cells really changed")

    def test_copies_that_already_disagreed_are_left_alone(self) -> None:
        """clnum differs between the copies in the stock game; one edit must
        not flatten both into the same value."""
        self.builder.set_value("master_floor", self.KEY, "clnum", 5)
        self.assertEqual(self.builder.value("master_tmpfloor_item", self.KEY, "clnum"), 1)

    def test_discarding_puts_both_back(self) -> None:
        self.builder.set_value("master_floor", self.KEY, "itemmax", 9)
        self.builder.revert_all()
        self.assertEqual(self.builder.value("master_tmpfloor_item", self.KEY, "itemmax"), 2)
        self.assertFalse(self.builder.dirty)


class RowsTheSteamGameSkips(Richer):
    """39 decals are PS4-only. The Steam game never loads them, so an edit to
    one is written, applied, and does nothing at all."""

    EXTRA = """
        CREATE TABLE master_skill (id TEXT PRIMARY KEY, name TEXT, platform INT,
                                   no_steam INT, val0 INT);
        INSERT INTO master_skill VALUES ('SKL_PATROL', '', 1, 0, 10);
        INSERT INTO master_skill VALUES ('SKL_SPY', '', 0, 253, 40);
    """

    def test_a_ps4_only_row_says_so(self) -> None:
        rows = {r.key[0]: r for r in self.builder.view("master_skill").rows}
        self.assertTrue(rows["SKL_PATROL"].skipped)
        self.assertIn("PS4 only", rows["SKL_PATROL"].label)
        self.assertFalse(rows["SKL_SPY"].skipped)

    def test_fixing_the_platform_clears_the_flag(self) -> None:
        self.builder.set_value("master_skill", ("SKL_PATROL",), "platform", 0)
        rows = {r.key[0]: r for r in self.builder.view("master_skill").rows}
        self.assertFalse(rows["SKL_PATROL"].skipped)


class AddingRows(Base):
    def test_a_copy_starts_identical_under_its_new_key(self) -> None:
        made = self.builder.copy_row("master_body_detail", ("BAL", 2, 0),
                                     {"type": "BAL", "grade": "10", "limit_break": "0"})
        self.assertEqual(made, ("BAL", 10, 0))
        self.assertEqual(self.builder.value("master_body_detail", made, "price"), 4000)
        self.assertEqual(self.builder.value("master_body_detail", made, "skill_slots"), "1,2")
        self.assertIn("grade 10", self.builder.label("master_body_detail", made))

    def test_a_copy_counts_as_one_change_however_it_is_edited(self) -> None:
        made = self.builder.copy_row("master_body_detail", ("BAL", 2, 0),
                                     {"type": "BAL", "grade": 7, "limit_break": 0})
        self.builder.set_value("master_body_detail", made, "price", 9000)
        self.builder.set_value("master_body_detail", made, "skill_slots", "1,2,3,4")
        self.assertEqual(self.builder.change_count(), 1)

    def test_a_copy_needs_a_key_of_its_own(self) -> None:
        with self.assertRaises(BuildError):
            self.builder.copy_row("master_body_detail", ("BAL", 2, 0),
                                  {"type": "BAL", "grade": 2, "limit_break": 0})

    def test_a_copy_cannot_land_on_a_row_that_exists(self) -> None:
        with self.assertRaises(BuildError) as caught:
            self.builder.copy_row("master_body_detail", ("BAL", 2, 0),
                                  {"type": "BAL", "grade": 1, "limit_break": 0})
        self.assertIn("already has", str(caught.exception))

    def test_a_copy_must_say_every_part_of_its_key(self) -> None:
        with self.assertRaises(BuildError):
            self.builder.copy_row("master_body_detail", ("BAL", 2, 0), {"grade": 7})

    def test_a_table_keyed_on_a_row_number_needs_no_key(self) -> None:
        text = self.builder.view("master_text").rows[0].key
        made = self.builder.copy_row("master_text", text)
        self.assertNotEqual(made, text)
        self.assertEqual(self.builder.value("master_text", made, "txt"), "All-rounder")

    def test_discarding_takes_added_rows_away(self) -> None:
        """It used to put edited cells back and leave added rows behind."""
        self.builder.copy_row("master_body_detail", ("BAL", 2, 0),
                              {"type": "BAL", "grade": 7, "limit_break": 0})
        self.builder.set_value("master_safe_level", (1,), "limit", 1)
        self.builder.revert_all()
        self.assertFalse(self.builder.dirty)
        self.assertEqual(self.builder.view("master_body_detail").total, 2)


class TheMachinesLists(Richer):
    """MON to SUN are not days of the week: they are seven Bloodnium lists the
    schedule moves through month by month. What a list charges in is its
    currency_type, which the game's own script sorts the tabs by."""

    EXTRA = """
        CREATE TABLE master_item (
            itemid TEXT PRIMARY KEY, itemtype TEXT, name TEXT, rarity INT,
            buy_money INT, buy_recycle_point INT, buy_bloodnium INT);
        INSERT INTO master_item VALUES ('ITMT_A', 'ITTP_MATERIAL', '', 1, 10, 20, 30);
        INSERT INTO master_item VALUES ('ITMT_B', 'ITTP_MATERIAL', '', 1, 10, 20, 30);
        CREATE TABLE master_automaticshop_lineup (
            goods_id INT PRIMARY KEY, lineup_id TEXT, entity_type TEXT, type_id TEXT,
            is_stable INT, freq INT, is_special INT, display_priority INT,
            currency_type INT, stock INT, pack_count INT, pack_money INT,
            pack_metal INT, pack_recycle_point INT, pack_bloodnium INT,
            money_discount_rate INT, metal_discount_rate INT,
            recycle_point_discount_rate INT, bloodnium_discount_rate INT);
        INSERT INTO master_automaticshop_lineup VALUES
            (1,'COMMON','ITEM','ITMT_A',1,0,1,1,0,1,1,0,0,0,0,0,0,0,0),
            (2,'RE','ITEM','ITMT_A',1,0,0,1,3,1,1,0,0,0,0,0,0,0,0),
            (3,'MON','ITEM','ITMT_A',1,0,0,1,4,1,1,0,0,0,0,0,0,0,0),
            (4,'TUE','ITEM','ITMT_A',1,0,0,1,4,1,1,0,0,0,0,0,0,0,0),
            (5,'ODD','ITEM','ITMT_A',1,0,0,1,1,1,1,0,0,0,0,0,0,0,0);
        CREATE TABLE master_automaticshop_schedule (
            expire DATETIME, purchase_lineup_id TEXT, common_lineup_id TEXT,
            purchase_goods_min INT, purchase_goods_max INT, exchange_lineup_id TEXT,
            exchange_goods_min INT, exchange_goods_max INT,
            bloodnium_exchange_lineup_id TEXT, bloodnium_exchange_goods_min INT,
            bloodnium_exchange_goods_max INT, discount_group_id INT);
        INSERT INTO master_automaticshop_schedule VALUES
            ('2026-07-01 00:00:00','AP','COMMON',1,1,'RE',1,1,'MON',44,44,0),
            ('2026-08-01 00:00:00','AP','COMMON',1,1,'RE',1,1,'TUE',44,44,0);
    """

    def price(self, column: str) -> int:
        return self.cell(f"SELECT {column} FROM master_item WHERE itemid='ITMT_B'")

    def test_a_bloodnium_list_prices_in_bloodnium(self) -> None:
        """This used to write the Kill Coin price, which that tab never charges."""
        self.builder.stock_machine(["ITMT_B"], "MON", price=5)
        self.assertEqual(self.price("buy_bloodnium"), 5)
        self.assertEqual(self.price("buy_money"), 10)

    def test_the_recycle_exchange_prices_in_recycle_points(self) -> None:
        self.builder.stock_machine(["ITMT_B"], "RE", price=5)
        self.assertEqual(self.price("buy_recycle_point"), 5)

    def test_the_kill_coin_shop_prices_in_kill_coins(self) -> None:
        self.builder.stock_machine(["ITMT_B"], "COMMON", price=5)
        self.assertEqual(self.price("buy_money"), 5)

    def test_a_list_whose_currency_is_unknown_takes_no_price(self) -> None:
        with self.assertRaises(BuildError):
            self.builder.stock_machine(["ITMT_B"], "ODD", price=5)
        self.assertFalse(self.builder.dirty, "nothing half-done")

    def test_lists_are_named_for_what_they_are(self) -> None:
        self.assertEqual(self.builder.lineup_name("COMMON"), "Kill Coin shop")
        self.assertIn("monthly", self.builder.lineup_name("MON"))
        self.assertNotIn("Monday", self.builder.lineup_name("MON"))

    def test_making_room_lets_every_item_be_offered(self) -> None:
        """Stock offers exactly the list's size, so a new item would push
        another out at random unless the count goes up with it."""
        self.builder.stock_machine(["ITMT_B"], "RE")
        self.assertEqual(self.builder.make_room("RE"), 4)   # min and max, two months
        self.assertEqual(self.cell("SELECT min(exchange_goods_max) FROM "
                                   "master_automaticshop_schedule"), 2)
        self.assertEqual(self.cell("SELECT min(exchange_goods_min) FROM "
                                   "master_automaticshop_schedule"), 2)

    def test_room_is_only_made_where_it_is_short(self) -> None:
        """MON already offers up to 44; one more item fits without a change."""
        self.builder.stock_machine(["ITMT_B"], "MON")
        self.assertEqual(self.builder.make_room("MON"), 0)

    def test_the_schedule_carries_the_rotation_on(self) -> None:
        added = self.builder.extend_schedule(2026)
        self.assertEqual(added, 5)                    # September to January 1st
        con = sqlite3.connect(self.builder.scratch)
        months = con.execute(
            "SELECT expire, bloodnium_exchange_lineup_id FROM "
            "master_automaticshop_schedule ORDER BY expire"
        ).fetchall()
        con.close()
        self.assertEqual(months[2], ("2026-09-01 00:00:00", "MON"))
        self.assertEqual(months[3][1], "TUE")
        self.assertEqual(months[-1][0], "2027-01-01 00:00:00")
        self.assertEqual(self.builder.schedule_end(), "2027-01-01 00:00:00")

    def test_extending_counts_and_discards_like_any_change(self) -> None:
        self.builder.extend_schedule(2026)
        self.assertEqual(self.builder.change_count(), 5)
        self.builder.revert_all()
        self.assertEqual(self.builder.schedule_end(), "2026-08-01 00:00:00")

    def test_a_silly_year_is_refused(self) -> None:
        with self.assertRaises(BuildError):
            self.builder.extend_schedule(9999)


class NamingItemsThroughAnotherTable(Richer):
    """The machine's rows used to show a blueprint as its bare id."""

    EXTRA = """
        CREATE TABLE master_part (id TEXT PRIMARY KEY, name TEXT);
        INSERT INTO master_part VALUES ('PT_ARM_WP001_001', 'PART_NAME.TXT_MACHETE');
        INSERT INTO master_text VALUES ('PART_NAME', 'TXT_MACHETE', 'int', 'Battle Machete');
        CREATE TABLE master_item (itemid TEXT PRIMARY KEY, name TEXT);
        INSERT INTO master_item VALUES ('ITMP_ARM_WP001_001', 'ITEM.TXT_RMAP');
        INSERT INTO master_text VALUES ('ITEM', 'TXT_RMAP', 'int', 'RMAP');
        CREATE TABLE master_automaticshop_lineup (
            goods_id INT PRIMARY KEY, lineup_id TEXT, type_id TEXT);
        INSERT INTO master_automaticshop_lineup VALUES (1, 'SAT', 'ITMP_ARM_WP001_001');
    """

    def test_a_blueprint_on_the_machine_is_named_by_what_it_makes(self) -> None:
        label = self.builder.view("master_automaticshop_lineup").rows[0].label
        self.assertIn("Battle Machete", label)
        self.assertIn("Bloodnium list SAT", label)


class WhatTheBuilderKnows(unittest.TestCase):
    """The lessons in builder_data must point at things that exist."""

    VANILLA = Path(__file__).resolve().parent.parent / "LiD Vanilla DB" / "5.0.4.2" / "masters.db"

    def test_every_tip_is_for_a_described_table(self) -> None:
        from lid_db_manager.explain_data import TABLES
        for table in builder_data.TIPS:
            with self.subTest(table=table):
                self.assertIn(table, TABLES)

    def test_every_grouped_table_with_a_date_or_mirror_is_real(self) -> None:
        if not self.VANILLA.is_file():
            self.skipTest("needs the shipped 5.0.4.2 vanilla database")
        con = sqlite3.connect(f"file:{self.VANILLA.as_posix()}?mode=ro", uri=True)
        self.addCleanup(con.close)

        def columns(table):
            return {r[1] for r in con.execute(f'PRAGMA table_info("{table}")')}

        for (table, column), kind in builder_data.DATE_COLUMNS.items():
            with self.subTest(table=table, column=column):
                self.assertIn(column, columns(table))
                self.assertIn(kind, ("epoch", "text"))
        for family in builder_data.MIRRORED:
            for table in family:
                with self.subTest(table=table):
                    self.assertTrue(columns(table))
        for table in builder_data.SKIPPED_ROWS:
            condition = builder_data.SKIPPED_ROWS[table][0]
            found = con.execute(f"SELECT count(*) FROM {table} WHERE {condition}").fetchone()[0]
            self.assertEqual(found, 39, "the 39 PS4-only decals")

    def test_the_stock_schedule_really_uses_the_lists_as_months(self) -> None:
        """One Bloodnium list per month, cycling - not one per weekday."""
        if not self.VANILLA.is_file():
            self.skipTest("needs the shipped 5.0.4.2 vanilla database")
        con = sqlite3.connect(f"file:{self.VANILLA.as_posix()}?mode=ro", uri=True)
        self.addCleanup(con.close)
        lists = [r[0] for r in con.execute(
            "SELECT bloodnium_exchange_lineup_id FROM master_automaticshop_schedule "
            "ORDER BY expire")]
        self.assertEqual(lists, ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"])
        currencies = dict(con.execute(
            "SELECT lineup_id, max(currency_type) FROM master_automaticshop_lineup "
            "GROUP BY lineup_id"))
        self.assertEqual(currencies["COMMON"], 0)
        self.assertEqual(currencies["RE"], 3)
        self.assertEqual(currencies["MON"], 4)
        for code, (_, column) in builder_data.CURRENCY_TYPES.items():
            self.assertIn(column, {r[1] for r in con.execute("PRAGMA table_info(master_item)")})

    def test_the_whole_real_database_opens(self) -> None:
        """Every heading's tables open against the real thing, with tips."""
        if not self.VANILLA.is_file():
            self.skipTest("needs the shipped 5.0.4.2 vanilla database")
        with ModBuilder(self.VANILLA) as builder:
            for name, _, tables in builder.groups():
                if name == builder_data.EVERYTHING_ELSE:
                    continue
                for table in tables:
                    with self.subTest(table=table):
                        builder.view(table, limit=5)
            self.assertTrue(builder.priced_in("Bloodnium"))


class TheGroupings(unittest.TestCase):
    def test_no_table_is_listed_under_two_headings(self) -> None:
        seen = set()
        for _, _, tables in builder_data.GROUPS:
            for table in tables:
                self.assertNotIn(table, seen, f"{table} is in two groups")
                seen.add(table)

    def test_every_grouped_table_is_described(self) -> None:
        """A table nobody has described has no business being a headline tab."""
        from lid_db_manager.explain_data import TABLES
        for name, _, tables in builder_data.GROUPS:
            for table in tables:
                with self.subTest(group=name, table=table):
                    self.assertIn(table, TABLES)

    def test_currency_views_find_columns(self) -> None:
        """These come free from the units written against columns."""
        self.assertGreater(len(currency_columns("KC")), 30)
        self.assertGreater(len(currency_columns("SPLithium")), 10)
        self.assertEqual(currency_columns("not a real unit"), [])


@unittest.skipUnless(HAVE_QT, "needs PySide6")
class TheWindow(unittest.TestCase):
    def test_it_opens_and_lists_the_groups(self) -> None:
        from lid_db_manager.manager import Manager
        from lid_db_manager.paths import AppPaths
        from lid_db_manager.ui.builder_window import BuilderWindow

        app = QApplication.instance() or QApplication([])
        home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, home, True)
        (Path(home) / "mods").mkdir()
        db = Path(home) / "masters.db"
        a_database(db)
        shutil.copy2(db, Path(home) / "masters.db.original")
        manager = Manager(AppPaths(Path(home)))
        manager.set_db_path(db)

        window = BuilderWindow(manager, parent=None)
        self.addCleanup(window.close)
        app.processEvents()
        headings = [window.tree.topLevelItem(i).text(0)
                    for i in range(window.tree.topLevelItemCount())]
        self.assertIn("Everything else", headings)
        self.assertTrue(window.grid.rowCount() > 0, "the first table should be shown")
        self.assertFalse(window.save_button.isEnabled(), "nothing changed yet")


@unittest.skipUnless(HAVE_QT, "needs PySide6")
class TheWindowFindsItsWay(unittest.TestCase):
    """Headings and currencies open pages of links rather than nothing."""

    def setUp(self) -> None:
        from lid_db_manager.manager import Manager
        from lid_db_manager.paths import AppPaths
        from lid_db_manager.ui.builder_window import BuilderWindow

        self.app = QApplication.instance() or QApplication([])
        home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, home, True)
        (Path(home) / "mods").mkdir()
        db = Path(home) / "masters.db"
        a_database(db)
        shutil.copy2(db, Path(home) / "masters.db.original")
        manager = Manager(AppPaths(Path(home)))
        manager.set_db_path(db)
        self.window = BuilderWindow(manager, parent=None)
        self.addCleanup(self.window._shut_builder)
        self.addCleanup(self.window.close)

    def heading(self, name: str):
        tree = self.window.tree
        return next(tree.topLevelItem(i) for i in range(tree.topLevelItemCount())
                    if tree.topLevelItem(i).text(0) == name)

    def test_find_a_price_sits_above_everything_else(self) -> None:
        tree = self.window.tree
        names = [tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())]
        self.assertEqual(names[-2:], [builder_data.FIND_BY_CURRENCY,
                                      builder_data.EVERYTHING_ELSE])
        self.assertEqual(names[0], "Prices & money")

    def test_a_heading_shows_its_tables_as_links(self) -> None:
        from lid_db_manager.ui.builder_window import LINKS_PAGE

        self.window.tree.setCurrentItem(self.heading("Prices & money"))
        self.assertEqual(self.window.stack.currentIndex(), LINKS_PAGE)
        self.assertEqual(self.window.links.count(), 1)
        self.assertIn("Buffalo Bank", self.window.links.item(0).text())
        self.assertFalse(self.window.search.isVisibleTo(self.window))

    def test_a_currency_lists_every_column_priced_in_it(self) -> None:
        prices = self.heading(builder_data.FIND_BY_CURRENCY)
        kill_coins = next(prices.child(i) for i in range(prices.childCount())
                          if prices.child(i).text(0) == "Kill Coins")
        self.window.tree.setCurrentItem(kill_coins)
        texts = [self.window.links.item(i).text() for i in range(self.window.links.count())]
        self.assertTrue(any("the most Kill Coins it can hold" in t for t in texts))

    def test_following_a_link_opens_the_table_on_that_column(self) -> None:
        from lid_db_manager.ui.builder_window import COLUMN_ROLE

        prices = self.heading(builder_data.FIND_BY_CURRENCY)
        kill_coins = prices.child(0)
        self.window.tree.setCurrentItem(kill_coins)
        link = next(self.window.links.item(i) for i in range(self.window.links.count())
                    if "Buffalo Bank" in self.window.links.item(i).text())
        self.window._follow_link(link)
        self.assertEqual(self.window.table, "master_safe_level")
        self.assertEqual(self.window.bulk_column.currentData(), link.data(COLUMN_ROLE))

    def test_tips_show_above_a_table_that_has_them(self) -> None:
        self.window.tree.setCurrentItem(self.window._tree_items["master_safe_level"])
        self.assertIn("2,560,000", self.window.tips_label.text())
        self.assertTrue(self.window.tips_label.isVisibleTo(self.window))

    def test_the_save_dialog_needs_a_name_and_names_the_risks(self) -> None:
        from lid_db_manager.ui.builder_window import SaveDialog

        builder = self.window.builder
        builder.set_value("master_safe_level", (1,), "limit", 5_000_000)
        dialog = SaveDialog(builder, self.window)
        self.addCleanup(dialog.deleteLater)
        self.assertFalse(dialog.ok_button.isEnabled())
        dialog.name.setText("Big bank")
        self.assertTrue(dialog.ok_button.isEnabled())
        self.assertIn("5,000,000", dialog.risk_label.text())
        self.assertFalse(dialog.risk_label.isHidden())
