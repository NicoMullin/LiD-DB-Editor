"""Applying a mod to a database that already has its changes in it.

Both bugs here came out of a real install. A player had the same content put in
by its own installer first, so when the manager applied its version on top,
every row was already there - and two things went wrong that never show up
against a clean database:

* ``INSERT OR REPLACE`` is a delete and then an insert, and several of this
  game's tables are pointed at with ON DELETE RESTRICT. Putting a row back
  exactly as it was failed on the delete half.
* a table with no primary key has nothing for ``OR REPLACE`` to replace on, so
  the row was simply appended again.

Neither is specific to that content pack: both apply to any mod built by
diffing a database, which is most of what this tool makes.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from lid_db_manager import dbdiff
from lid_db_manager.sqlutil import (
    begin_immediate,
    connect,
    inserts_already_present,
    row_already_there,
    split_statements,
)

# A parent table, a child that restricts deleting from it, and a table with no
# primary key at all - the three shapes that matter, and all three are real.
SCHEMA = """
CREATE TABLE master_asset (
    id TEXT PRIMARY KEY,
    mesh TEXT
);
CREATE TABLE master_part_asset (
    id TEXT NOT NULL,
    asset TEXT NOT NULL,
    PRIMARY KEY (id, asset),
    FOREIGN KEY (asset) REFERENCES master_asset (id) ON DELETE RESTRICT
);
CREATE TABLE master_part_equipment (
    id TEXT NOT NULL,
    slot INTEGER NOT NULL,
    note TEXT
);
"""


def a_database(path: Path) -> None:
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    con.execute("INSERT INTO master_asset VALUES ('ASSET_BASE', 'base.mesh')")
    con.execute("INSERT INTO master_part_asset VALUES ('PT_BASE', 'ASSET_BASE')")
    con.execute("INSERT INTO master_part_equipment VALUES ('PT_BASE', 1, 'base')")
    con.commit()
    con.close()


def add_content(path: Path) -> None:
    """What a mod adds: a new asset, something pointing at it, a keyless row."""
    con = sqlite3.connect(path)
    con.execute("INSERT INTO master_asset VALUES ('ASSET_NEW', 'new.mesh')")
    con.execute("INSERT INTO master_part_asset VALUES ('PT_NEW', 'ASSET_NEW')")
    con.execute("INSERT INTO master_part_equipment VALUES ('PT_NEW', 2, 'added')")
    con.commit()
    con.close()


def apply_sql(sql: str, database: Path) -> None:
    """Apply it the way the manager does: foreign keys on, one transaction."""
    con = connect(database)
    try:
        begin_immediate(con)
        for statement in split_statements(sql):
            if statement.strip():
                con.execute(statement)
        con.execute("COMMIT")
    finally:
        con.close()


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.vanilla = self.root / "vanilla.db"
        a_database(self.vanilla)
        modded = self.root / "modded.db"
        a_database(modded)
        add_content(modded)
        self.sql = dbdiff.to_sql(dbdiff.compare(self.vanilla, modded))

    def rows(self, database: Path, table: str) -> int:
        con = sqlite3.connect(database)
        count = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        con.close()
        return count


class OntoACleanDatabase(Base):
    def test_it_applies(self) -> None:
        target = self.root / "target.db"
        a_database(target)
        apply_sql(self.sql, target)
        self.assertEqual(self.rows(target, "master_asset"), 2)
        self.assertEqual(self.rows(target, "master_part_equipment"), 2)


class OntoADatabaseThatAlreadyHasIt(Base):
    """The case that failed on a real install."""

    def setUp(self) -> None:
        super().setUp()
        self.target = self.root / "target.db"
        a_database(self.target)
        add_content(self.target)   # put there by something else first

    def test_a_restricted_parent_row_can_be_rewritten(self) -> None:
        """INSERT OR REPLACE deletes first, and a child row restricts that."""
        apply_sql(self.sql, self.target)   # must not raise
        self.assertEqual(self.rows(self.target, "master_asset"), 2)

    def test_a_table_with_no_primary_key_does_not_gain_duplicates(self) -> None:
        before = self.rows(self.target, "master_part_equipment")
        apply_sql(self.sql, self.target)
        self.assertEqual(self.rows(self.target, "master_part_equipment"), before)

    def test_applying_it_repeatedly_settles(self) -> None:
        for _ in range(3):
            apply_sql(self.sql, self.target)
        self.assertEqual(self.rows(self.target, "master_asset"), 2)
        self.assertEqual(self.rows(self.target, "master_part_asset"), 2)
        self.assertEqual(self.rows(self.target, "master_part_equipment"), 2)


class WhatTheSqlLooksLike(Base):
    def test_a_keyless_table_gets_a_guarded_insert(self) -> None:
        self.assertIn("INSERT INTO \"master_part_equipment\"", self.sql)
        self.assertIn("WHERE NOT EXISTS", self.sql)

    def test_a_keyed_table_still_uses_or_replace(self) -> None:
        self.assertIn('INSERT OR REPLACE INTO "master_asset"', self.sql)

    def test_the_guard_matches_nulls(self) -> None:
        """A NULL in the row has to match the NULL already in the table."""
        modded = self.root / "nulls.db"
        a_database(modded)
        con = sqlite3.connect(modded)
        con.execute("INSERT INTO master_part_equipment VALUES ('PT_N', 3, NULL)")
        con.commit()
        con.close()
        sql = dbdiff.to_sql(dbdiff.compare(self.vanilla, modded))
        self.assertIn("IS NULL", sql)

        target = self.root / "target.db"
        a_database(target)
        apply_sql(sql, target)
        apply_sql(sql, target)
        self.assertEqual(self.rows(target, "master_part_equipment"), 2)


class ForeignKeysAreStillEnforced(Base):
    """Deferred to commit, not switched off."""

    def test_a_broken_reference_will_not_commit(self) -> None:
        target = self.root / "target.db"
        a_database(target)
        con = connect(target)
        try:
            begin_immediate(con)
            con.execute("INSERT INTO master_part_asset VALUES ('PT_X', 'ASSET_MISSING')")
            with self.assertRaises(sqlite3.IntegrityError):
                con.execute("COMMIT")
        finally:
            con.close()
        self.assertEqual(self.rows(target, "master_part_asset"), 1)



class ErrorMessagesPointAtTheStatement(unittest.TestCase):
    """A generated .sql starts with a header, and comments ride along with the
    statement after them - so a plain truncation shows only the title."""

    def test_the_header_is_skipped(self) -> None:
        from lid_db_manager.patch import _statement_gist

        statement = (
            "-- LET IT DIE Crossover Content Pack v3.75\n"
            "-- Database changes only.\n"
            "\n"
            "INSERT OR REPLACE INTO \"master_asset\" (\"id\") VALUES ('ASSET_NEW');"
        )
        gist = _statement_gist(statement)
        self.assertTrue(gist.startswith("INSERT OR REPLACE"), gist)
        self.assertNotIn("Crossover Content Pack", gist)

    def test_a_comment_only_statement_still_shows_something(self) -> None:
        from lid_db_manager.patch import _statement_gist

        self.assertEqual(_statement_gist("-- nothing but a comment"),
                         "-- nothing but a comment")

    def test_it_is_kept_to_one_line(self) -> None:
        from lid_db_manager.patch import _statement_gist

        gist = _statement_gist("UPDATE t\n   SET a = 1\n   WHERE b = 2;")
        self.assertEqual(gist, "UPDATE t SET a = 1 WHERE b = 2;")


class SayingSoBeforeItLooksLikeAFault(Base):
    """Content already in the database is normal, not a failure.

    It happens whenever the same thing was installed by its own installer
    first. The apply goes through and changes nothing, which looks broken
    unless somebody says why.
    """

    def statements(self) -> list[str]:
        return [s for s in split_statements(self.sql) if s.strip()]

    def test_a_clean_database_says_nothing(self) -> None:
        target = self.root / "clean.db"
        a_database(target)
        con = connect(target)
        checked, present = inserts_already_present(con, self.statements())
        con.close()
        self.assertTrue(checked, "nothing was checked at all")
        self.assertEqual(present, 0)

    def test_a_database_that_already_has_it_is_noticed(self) -> None:
        target = self.root / "already.db"
        a_database(target)
        add_content(target)
        con = connect(target)
        checked, present = inserts_already_present(con, self.statements())
        con.close()
        self.assertTrue(checked)
        self.assertEqual(present, checked)

    def test_one_row_at_a_time(self) -> None:
        target = self.root / "already.db"
        a_database(target)
        add_content(target)
        con = connect(target)
        for statement in self.statements():
            answer = row_already_there(con, statement)
            if answer is not None:
                self.assertTrue(answer, statement)
        con.close()

    def test_anything_unrecognised_answers_i_cannot_tell(self) -> None:
        """A wrong guess would send someone fixing the wrong thing."""
        target = self.root / "clean.db"
        a_database(target)
        con = connect(target)
        for statement in (
            "UPDATE master_asset SET mesh = 'x' WHERE id = 'ASSET_BASE';",
            "DELETE FROM master_asset WHERE id = 'ASSET_BASE';",
            "CREATE TABLE whatever (a TEXT);",
            "-- just a comment",
            "INSERT INTO no_such_table (a) VALUES (1);",
            "not sql at all",
        ):
            with self.subTest(statement=statement):
                self.assertIsNone(row_already_there(con, statement))
        con.close()

    def test_a_null_in_the_row_still_matches(self) -> None:
        target = self.root / "nulls.db"
        a_database(target)
        con = sqlite3.connect(target)
        con.execute("INSERT INTO master_part_equipment VALUES ('PT_N', 9, NULL)")
        con.commit()
        con.close()
        con = connect(target)
        self.assertTrue(row_already_there(
            con,
            'INSERT OR REPLACE INTO "master_part_equipment" ("id", "slot", "note") '
            "VALUES ('PT_N', 9, NULL);",
        ))
        con.close()


class ExplainingAFailure(Base):
    def test_an_already_present_row_is_explained(self) -> None:
        from lid_db_manager.patch import RawSqlPatch

        target = self.root / "already.db"
        a_database(target)
        add_content(target)
        con = connect(target)
        statement = (
            'INSERT OR REPLACE INTO "master_asset" ("id", "mesh") '
            "VALUES ('ASSET_NEW', 'new.mesh');"
        )
        why = RawSqlPatch._why(con, statement, sqlite3.IntegrityError("FOREIGN KEY constraint failed"))
        con.close()
        self.assertIn("already in your database", why)
        self.assertIn("installed another way", why)

    def test_an_unrelated_error_is_not_guessed_at(self) -> None:
        from lid_db_manager.patch import RawSqlPatch

        target = self.root / "clean.db"
        a_database(target)
        con = connect(target)
        why = RawSqlPatch._why(con, "UPDATE master_asset SET mesh = 'x';",
                               sqlite3.OperationalError("no such column"))
        con.close()
        self.assertEqual(why, "")


if __name__ == "__main__":
    unittest.main()
