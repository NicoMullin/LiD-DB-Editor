"""Foreign keys the game itself breaks.

masters.db declares foreign keys, and its own shipped data does not satisfy
them: on game 5.0.3 there are 256 offending rows, 255 of them in master_asset
with an empty `type`, plus two tables whose foreign key definitions do not even
resolve. SQLite only checks a row when something touches it, so the game never
trips over this and nor does an ordinary mod.

Restoring a whole-table snapshot *does* touch them - it writes every row back -
and the constraint then fails on the game's data rather than on anything a mod
did. That is what made unticking a mod fail with "FOREIGN KEY constraint
failed" on a real install. A restore only ever puts back rows that were already
there, so the check has nothing to protect and is off for that path only.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from lid_db_manager.sqlutil import begin_immediate, connect

# A parent table, a child that references it, and a child row the parent has no
# match for - exactly the shape masters.db ships.
SCHEMA = """
CREATE TABLE asset_type (id TEXT PRIMARY KEY);
CREATE TABLE asset (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    FOREIGN KEY (type) REFERENCES asset_type (id)
);
"""


def a_database_with_the_games_own_broken_rows(path: Path) -> None:
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    con.execute("INSERT INTO asset_type VALUES ('ASSETTP_FJ')")
    con.execute("INSERT INTO asset VALUES ('GOOD', 'ASSETTP_FJ')")
    # The shipped-broken one: a type that is not in the parent table at all.
    con.execute("PRAGMA foreign_keys = OFF")
    con.execute("INSERT INTO asset VALUES ('SHIPPED_BROKEN', '')")
    con.commit()
    con.close()


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "masters.db"
        a_database_with_the_games_own_broken_rows(self.db)

    def rewrite_every_row(self, con: sqlite3.Connection) -> None:
        """What a whole-table snapshot restore does."""
        rows = con.execute("SELECT id, type FROM asset").fetchall()
        con.execute("DELETE FROM asset")
        con.executemany("INSERT INTO asset (id, type) VALUES (?, ?)",
                        [(r[0], r[1]) for r in rows])


class ThePremise(Base):
    def test_the_database_really_does_break_its_own_constraint(self) -> None:
        con = connect(self.db, read_only=True)
        violations = con.execute("PRAGMA foreign_key_check(asset)").fetchall()
        con.close()
        self.assertEqual(len(violations), 1)

    def test_nothing_notices_until_something_touches_the_row(self) -> None:
        """Which is why the game runs and ordinary mods apply fine."""
        con = connect(self.db)
        begin_immediate(con)
        con.execute("UPDATE asset SET type = 'ASSETTP_FJ' WHERE id = 'GOOD'")
        con.execute("COMMIT")   # must not raise
        con.close()


class RestoringWithForeignKeysOn(Base):
    def test_it_fails_on_the_games_own_data(self) -> None:
        """The bug, pinned down: nothing a mod did, and no way to avoid it."""
        con = connect(self.db)           # foreign_keys=True, the default
        begin_immediate(con)
        with self.assertRaises(sqlite3.Error):
            self.rewrite_every_row(con)
            con.execute("COMMIT")
        con.execute("ROLLBACK")
        con.close()


class RestoringWithThemOff(Base):
    def test_it_succeeds(self) -> None:
        con = connect(self.db, foreign_keys=False)
        begin_immediate(con)
        self.rewrite_every_row(con)
        con.execute("COMMIT")            # must not raise
        con.close()

    def test_and_puts_the_rows_back_unchanged(self) -> None:
        con = connect(self.db, foreign_keys=False)
        begin_immediate(con)
        self.rewrite_every_row(con)
        con.execute("COMMIT")
        rows = sorted(tuple(r) for r in con.execute("SELECT id, type FROM asset"))
        con.close()
        self.assertEqual(rows, [("GOOD", "ASSETTP_FJ"), ("SHIPPED_BROKEN", "")])


class EverywhereElseStillChecks(Base):
    def test_connect_defaults_to_enforcing(self) -> None:
        con = connect(self.db)
        self.assertEqual(con.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        con.close()

    def test_off_is_opt_in_only(self) -> None:
        con = connect(self.db, foreign_keys=False)
        self.assertEqual(con.execute("PRAGMA foreign_keys").fetchone()[0], 0)
        con.close()

    def test_a_mod_still_cannot_add_a_dangling_reference(self) -> None:
        """Applying is unchanged: new rows are still checked, at commit."""
        con = connect(self.db)
        begin_immediate(con)
        con.execute("INSERT INTO asset VALUES ('NEW', 'NO_SUCH_TYPE')")
        with self.assertRaises(sqlite3.IntegrityError):
            con.execute("COMMIT")
        con.execute("ROLLBACK")
        con.close()


if __name__ == "__main__":
    unittest.main()
