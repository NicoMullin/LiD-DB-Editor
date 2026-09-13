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
        self.assertIn("holds a number", said)
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
        """currency_type is not documented anywhere, so copy rather than guess."""
        self.builder.stock_machine(["ITMT_COPPER_1"], "MON")
        con = sqlite3.connect(self.builder.scratch)
        row = con.execute(
            "SELECT currency_type, display_priority FROM master_automaticshop_lineup "
            "WHERE type_id='ITMT_COPPER_1'"
        ).fetchone()
        con.close()
        self.assertEqual(row[0], 4)       # the same as Monday's other rows
        self.assertEqual(row[1], 2)       # after the one already there

    def test_adding_something_already_on_that_tab_does_nothing(self) -> None:
        self.assertEqual(self.builder.stock_machine(["ITMT_ALUMI_1"], "MON"), 0)
        self.assertFalse(self.builder.dirty)

    def test_setting_a_price_changes_the_item_not_the_machine(self) -> None:
        self.builder.stock_machine(["ITMT_COPPER_1"], "MON", price=1)
        con = sqlite3.connect(self.builder.scratch)
        price = con.execute(
            "SELECT buy_money FROM master_item WHERE itemid='ITMT_COPPER_1'"
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
