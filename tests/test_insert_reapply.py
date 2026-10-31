"""A mod's plain INSERTs, run again by every save after the first.

Every save runs the whole enabled list on a database that already holds last
time's rows. ikibo found what a plain INSERT does then, with the third-party
Quality Of Death mod: "UNIQUE constraint failed: master_quest.qid" on every
save after the first, whatever else had changed, until everything was reverted
and the vanilla backup put back. Into a table with no key it is quieter and
worse - the rows go in a second time. Run again, an INSERT into a keyed table
is now an INSERT OR REPLACE, and one into a keyless table adds only the rows
that are not there; the first time it runs exactly as written.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from fixtures import build_db, query, write_mod

from lid_db_manager.manager import Manager
from lid_db_manager.paths import AppPaths
from lid_db_manager.sqlutil import bare_insert

# Quality Of Death's shape: plain rows, and rows made by a WITH clause.
QUEST_LIKE_SQL = """
INSERT INTO "master_skill" ("id","name","buy_money","val0") VALUES ('SKL_QOD_001','Mod skill',100,1);
WITH RECURSIVE weeks(n) AS (SELECT 0 UNION ALL SELECT n+1 FROM weeks WHERE n < 9)
INSERT INTO "master_skill" ("id","name","buy_money","val0")
SELECT 'SKL_QOD_W' || printf('%02d', n), 'Weekly', 10 * n, n FROM weeks;
"""

KEYLESS_SQL = """
INSERT INTO master_radio_channel (channel, music_id1) VALUES ('600', 'SN_BGM_Radio_Music_0200');
INSERT INTO "main"."master_radio_channel" (channel, music_id1, channel_name) VALUES ('601', 'SN_BGM_Radio_Music_0201', 'Mine');
"""


class FindingThePlainInsert(unittest.TestCase):
    def test_the_insert_and_its_table(self) -> None:
        for sql, table in (
            ("INSERT INTO master_quest (qid) VALUES ('a');", "master_quest"),
            ('insert into "master_quest" VALUES (1)', "master_quest"),
            ('INSERT INTO "main"."master_text" VALUES (1)', "master_text"),
            ("WITH w(n) AS (SELECT 1) INSERT INTO master_quest SELECT n FROM w", "master_quest"),
        ):
            found = bare_insert(sql)
            self.assertIsNotNone(found, sql)
            self.assertEqual(found.table, table)
            self.assertEqual(sql[found.insert_end - 6:found.insert_end].upper(), "INSERT")

    def test_not_one_that_can_already_run_twice_or_is_not_an_insert(self) -> None:
        for sql in (
            "INSERT OR IGNORE INTO master_quest VALUES (1)",
            "INSERT OR REPLACE INTO master_quest VALUES (1)",
            "REPLACE INTO master_quest VALUES (1)",
            "WITH w(n) AS (SELECT 1) INSERT OR REPLACE INTO master_quest SELECT n FROM w",
            "UPDATE master_text SET txt = 'insert into master_quest values' WHERE id = 'x'",
            "CREATE TRIGGER t AFTER DELETE ON a BEGIN INSERT INTO b VALUES (1); END",
            "-- insert into master_quest\nDELETE FROM master_quest WHERE qid = 'a'",
        ):
            self.assertIsNone(bare_insert(sql), sql)


class SavingAgain(unittest.TestCase):
    def setUp(self) -> None:
        run = tempfile.TemporaryDirectory()
        self.addCleanup(run.cleanup)
        self.root = Path(run.name)
        self.paths = AppPaths(self.root / "manager").ensure()
        self.db = build_db(self.root / "game" / "masters.db")
        con = sqlite3.connect(str(self.db))
        con.execute("CREATE TABLE master_radio_channel (channel TEXT NOT NULL, music_id1 TEXT NOT NULL, "
                    "channel_name TEXT NOT NULL DEFAULT 'Radio', rand_grp INTEGER NOT NULL DEFAULT 0)")
        con.execute("INSERT INTO master_radio_channel VALUES ('000', 'OBS', 'OBS Needle Sounds', 0)")
        con.commit()
        con.close()
        self.manager = Manager(self.paths)
        self.manager.set_db_path(self.db)

    def mod(self, mod_id: str, sql: str) -> None:
        write_mod(self.paths.mods_dir, mod_id, {"patches": [{"type": "raw_sql_file", "path": "mod.sql"}]},
                  **{"mod.sql": sql})
        self.manager.rescan()
        self.manager.set_enabled(mod_id, True)

    def save(self) -> None:
        report = self.manager.save_mod_list()
        self.assertTrue(report.ok, report.error)

    def test_a_plain_insert_mod_survives_the_next_saves(self) -> None:
        self.mod("quality-of-death", QUEST_LIKE_SQL)
        self.save()
        rows = query(self.db, "SELECT * FROM master_skill WHERE id LIKE 'SKL_QOD%' ORDER BY id")
        self.assertEqual(len(rows), 11)
        # what ikibo did: tick another mod, save; change nothing, save
        self.mod("another", "UPDATE master_skill SET buy_money = 999 WHERE id = 'SKL_EXPUP_01';")
        self.save()
        self.save()
        self.assertEqual(query(self.db, "SELECT * FROM master_skill WHERE id LIKE 'SKL_QOD%' ORDER BY id"), rows)

    def test_switching_it_off_still_takes_its_rows_away(self) -> None:
        before = query(self.db, "SELECT * FROM master_skill ORDER BY id")
        self.mod("quality-of-death", QUEST_LIKE_SQL)
        self.save()
        self.save()
        self.manager.set_enabled("quality-of-death", False)
        self.save()
        self.assertEqual(query(self.db, "SELECT * FROM master_skill ORDER BY id"), before)

    def test_a_table_with_no_key_does_not_get_the_rows_twice(self) -> None:
        self.mod("my-radio", KEYLESS_SQL)
        self.save()
        self.save()
        self.save()
        self.assertEqual(query(self.db, "SELECT * FROM master_radio_channel ORDER BY channel"), [
            ("000", "OBS", "OBS Needle Sounds", 0),
            ("600", "SN_BGM_Radio_Music_0200", "Radio", 0),      # the column default, kept
            ("601", "SN_BGM_Radio_Music_0201", "Mine", 0),
        ])
        self.manager.set_enabled("my-radio", False)
        self.save()
        self.assertEqual(query(self.db, "SELECT channel FROM master_radio_channel"), [("000",)])

    def test_the_first_time_a_clash_is_still_reported(self) -> None:
        # a row the database already had, not one this mod put there
        self.mod("clash", "INSERT INTO master_skill (id, name) VALUES ('SKL_EXPUP_01', 'Mine');")
        report = self.manager.save_mod_list()
        self.assertFalse(report.ok)
        self.assertIn("UNIQUE", report.error)
        self.assertEqual(query(self.db, "SELECT name FROM master_skill WHERE id = 'SKL_EXPUP_01'"), [("Exp Up",)])


if __name__ == "__main__":
    unittest.main()
