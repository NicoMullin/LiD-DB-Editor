"""Noticing that a mod was built against a different game build.

A mod is a set of changes measured against one database. When the game is
patched most of them still land exactly as intended, because rows are addressed
by name - but when one does not, nothing raises. The mod applies and something
is quietly wrong in game, which is the worst shape a failure can take.

So this is a warning, not a refusal: the usual outcome really is that it is
fine, and refusing would block a mod that works.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from lid_db_manager.mod import Mod
from lid_db_manager.sqlutil import connect, database_game_version
from lid_db_manager.validator import game_version_warning

BUILD = "5.0.3.0.0 - 1.87"


def a_database(path: Path, version: str | None = BUILD) -> None:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE master_const_str (id TEXT PRIMARY KEY, value TEXT)")
    if version is not None:
        con.execute("INSERT INTO master_const_str VALUES ('TITLE_VERSION', ?)", (version,))
    con.execute("INSERT INTO master_const_str VALUES ('SOMETHING_ELSE', 'x')")
    con.commit()
    con.close()


def a_mod(game_version: str = "") -> Mod:
    return Mod(id="m", name="A mod", description="d", version="1.0.0",
               author="a", folder=Path("."), game_version=game_version)


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)


class ReadingTheVersion(Base):
    def test_it_comes_off_the_database(self) -> None:
        path = self.root / "db.sqlite"
        a_database(path)
        con = connect(path, read_only=True)
        self.assertEqual(database_game_version(con), BUILD)
        con.close()

    def test_a_database_that_does_not_say_gives_nothing(self) -> None:
        path = self.root / "db.sqlite"
        a_database(path, version=None)
        con = connect(path, read_only=True)
        self.assertEqual(database_game_version(con), "")
        con.close()

    def test_a_database_without_the_table_is_not_an_error(self) -> None:
        path = self.root / "empty.sqlite"
        sqlite3.connect(path).close()
        con = connect(path, read_only=True)
        self.assertEqual(database_game_version(con), "")
        con.close()


class Warning(Base):
    def setUp(self) -> None:
        super().setUp()
        self.path = self.root / "db.sqlite"
        a_database(self.path)
        self.con = connect(self.path, read_only=True)
        self.addCleanup(self.con.close)

    def test_a_matching_build_says_nothing(self) -> None:
        self.assertEqual(game_version_warning(self.con, a_mod(BUILD)), "")

    def test_a_mod_that_does_not_say_is_not_guessed_about(self) -> None:
        self.assertEqual(game_version_warning(self.con, a_mod("")), "")

    def test_a_different_build_warns_with_both_versions(self) -> None:
        warning = game_version_warning(self.con, a_mod("5.0.2.0.0 - 1.86"))
        self.assertIn("5.0.2.0.0 - 1.86", warning)
        self.assertIn(BUILD, warning)

    def test_it_says_the_mod_will_still_apply(self) -> None:
        """A refusal would block mods that work. This has to read as a caution."""
        warning = game_version_warning(self.con, a_mod("5.0.2.0.0 - 1.86"))
        self.assertIn("still apply", warning)

    def test_a_database_that_does_not_say_stays_quiet(self) -> None:
        path = self.root / "quiet.sqlite"
        a_database(path, version=None)
        con = connect(path, read_only=True)
        self.assertEqual(game_version_warning(con, a_mod("5.0.2.0.0 - 1.86")), "")
        con.close()


class ItReachesTheValidationReport(Base):
    def test_the_warning_is_attached_to_the_mod(self) -> None:
        from lid_db_manager.validator import validate_mod

        path = self.root / "db.sqlite"
        a_database(path)
        con = connect(path, read_only=True)
        result = validate_mod(con, a_mod("5.0.2.0.0 - 1.86"))
        con.close()
        self.assertTrue(any("5.0.2" in w for w in result.warnings))
        self.assertTrue(result.ok, "a version mismatch must not fail validation")


class ItSurvivesModJson(Base):
    def test_it_is_read_back_from_a_mod_folder(self) -> None:
        import json

        from lid_db_manager.mod import load_mod_json

        folder = self.root / "a-mod"
        folder.mkdir()
        (folder / "mod.json").write_text(json.dumps({
            "id": "a-mod", "name": "A", "description": "d", "version": "1",
            "author": "x", "game_version": BUILD, "patches": [{"type": "raw_sql", "sql": "SELECT 1;"}],
        }), encoding="utf-8")
        self.assertEqual(load_mod_json(folder).game_version, BUILD)

    def test_a_mod_without_it_loads_fine(self) -> None:
        import json

        from lid_db_manager.mod import load_mod_json

        folder = self.root / "b-mod"
        folder.mkdir()
        (folder / "mod.json").write_text(json.dumps({
            "id": "b-mod", "name": "B", "description": "d", "version": "1",
            "author": "x", "patches": [{"type": "raw_sql", "sql": "SELECT 1;"}],
        }), encoding="utf-8")
        self.assertEqual(load_mod_json(folder).game_version, "")


if __name__ == "__main__":
    unittest.main()
