"""Applying a mod as a difference from vanilla, rather than running its SQL.

The point of this mode is compatibility: a mod that rewrites a whole table
should contribute only the values it actually alters, so it stops wiping out
whatever another mod put in the rows it does not care about.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fixtures import build_db, query, write_mod

from lid_db_manager.manager import Manager
from lid_db_manager.paths import AppPaths

# A blunt mod: rewrites every row of master_skill, the way an exported dump does.
DUMP_SQL = """
UPDATE master_skill SET buy_money = 1, val0 = 0;
"""

# A precise mod wanting one value on SKL_FREE_01. Vanilla already has that row
# at buy_money=1, val0=0 - exactly what the dump would set it to - so the dump
# does not really change it. A blanket UPDATE still writes over it; a diff does
# not. That difference is the whole feature.
TWEAK = {
    "patches": [
        {
            "type": "update_set",
            "table": "master_skill",
            "set": {"val0": 999},
            "where": "id = 'SKL_FREE_01'",
        }
    ]
}


class DiffApplyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.paths = AppPaths(self.root).ensure()
        self.db = build_db(self.root / "game" / "masters.db")
        self.manager = Manager(self.paths)
        self.manager.set_db_path(self.db)  # keeps masters.db.original

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write_dump(self, apply_mode: str) -> None:
        folder = self.paths.mods_dir / "dump"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "dump.sql").write_text(DUMP_SQL, encoding="utf-8")
        write_mod(
            self.paths.mods_dir,
            "dump",
            {
                "apply": apply_mode,
                "patches": [{"type": "raw_sql_file", "path": "dump.sql"}],
            },
        )

    def _skills(self) -> dict:
        return {
            row[0]: (row[1], row[2])
            for row in query(self.db, "SELECT id, buy_money, val0 FROM master_skill")
        }

    def test_direct_apply_lets_a_dump_clobber_the_mod_above_it(self) -> None:
        """The behaviour that makes shared dumps painful - and still the default."""
        self._write_dump("direct")
        write_mod(self.paths.mods_dir, "tweak", TWEAK)
        self.manager.rescan()
        self.manager.set_enabled("tweak", True)   # applies first
        self.manager.set_enabled("dump", True)    # applies second, wins

        self.assertTrue(self.manager.save_mod_list().ok)
        self.assertEqual(
            self._skills()["SKL_FREE_01"][1], 0,
            "the blanket UPDATE wrote over a row it did not actually change",
        )

    def test_diff_apply_leaves_the_other_mod_alone(self) -> None:
        """Same order, same mods - but the dump only writes what it really changes."""
        self._write_dump("diff")
        write_mod(self.paths.mods_dir, "tweak", TWEAK)
        self.manager.rescan()
        self.manager.set_enabled("tweak", True)
        self.manager.set_enabled("dump", True)

        self.assertTrue(self.manager.save_mod_list().ok)
        skills = self._skills()
        self.assertEqual(skills["SKL_FREE_01"][1], 999, "the tweak should have survived")
        # ... while the dump still did its own job on rows it really changes.
        self.assertEqual(skills["SKL_POWER_01"], (1, 0))
        self.assertEqual(skills["SKL_EXPUP_02"], (1, 0))

    def test_a_genuine_collision_is_still_last_wins(self) -> None:
        """Diff-apply narrows what counts as a collision; it does not remove it."""
        self._write_dump("diff")
        write_mod(
            self.paths.mods_dir,
            "clash",
            {
                "patches": [{
                    "type": "update_set", "table": "master_skill",
                    "set": {"val0": 777}, "where": "id = 'SKL_EXPUP_02'",
                }]
            },
        )
        self.manager.rescan()
        self.manager.set_enabled("clash", True)
        self.manager.set_enabled("dump", True)   # applies last

        self.assertTrue(self.manager.save_mod_list().ok)
        # The dump really does change EXPUP_02's val0 (40 -> 0), so it wins.
        self.assertEqual(self._skills()["SKL_EXPUP_02"][1], 0)

    def test_diff_apply_is_idempotent_even_for_relative_sql(self) -> None:
        """`SET x = x * 2` run twice would double twice. As a diff it cannot."""
        folder = self.paths.mods_dir / "double"
        folder.mkdir(parents=True)
        (folder / "double.sql").write_text(
            "UPDATE master_skill SET buy_money = buy_money * 2;", encoding="utf-8"
        )
        write_mod(
            self.paths.mods_dir,
            "double",
            {"apply": "diff", "patches": [{"type": "raw_sql_file", "path": "double.sql"}]},
        )
        self.manager.rescan()
        self.manager.set_enabled("double", True)

        self.manager.save_mod_list()
        once = self._skills()
        self.manager.reapply_all()
        self.manager.reapply_all()
        self.assertEqual(self._skills(), once, "re-applying must not compound")

    def test_direct_apply_does_compound(self) -> None:
        """The contrast, so the guarantee above is meaningful."""
        folder = self.paths.mods_dir / "double"
        folder.mkdir(parents=True)
        (folder / "double.sql").write_text(
            "UPDATE master_skill SET buy_money = buy_money * 2;", encoding="utf-8"
        )
        write_mod(
            self.paths.mods_dir,
            "double",
            {"patches": [{"type": "raw_sql_file", "path": "double.sql"}]},
        )
        self.manager.rescan()
        self.manager.set_enabled("double", True)
        self.manager.save_mod_list()
        once = self._skills()["SKL_EXPUP_01"][0]
        self.manager.reapply_all()
        self.assertEqual(self._skills()["SKL_EXPUP_01"][0], once * 2)

    def test_inserts_and_deletes_are_carried_across(self) -> None:
        folder = self.paths.mods_dir / "rows"
        folder.mkdir(parents=True)
        (folder / "rows.sql").write_text(
            "INSERT INTO master_skill (id, name, buy_money, val0) "
            "VALUES ('SKL_BRAND_NEW', 'New', 5, 5);\n"
            "DELETE FROM master_skill WHERE id = 'SKL_FREE_01';",
            encoding="utf-8",
        )
        write_mod(
            self.paths.mods_dir,
            "rows",
            {"apply": "diff", "patches": [{"type": "raw_sql_file", "path": "rows.sql"}]},
        )
        self.manager.rescan()
        self.manager.set_enabled("rows", True)
        self.assertTrue(self.manager.save_mod_list().ok)

        ids = {row[0] for row in query(self.db, "SELECT id FROM master_skill")}
        self.assertIn("SKL_BRAND_NEW", ids)
        self.assertNotIn("SKL_FREE_01", ids)

    def test_reverting_a_diff_applied_mod_restores_the_rows(self) -> None:
        self._write_dump("diff")
        self.manager.rescan()
        self.manager.set_enabled("dump", True)
        self.manager.save_mod_list()

        results = self.manager.revert(["dump"])
        self.assertTrue(results[0].ok, results[0].error)
        self.assertEqual(
            sorted(query(self.db, "SELECT buy_money FROM master_skill")),
            [(1,), (500,), (1200,), (5000,)],
        )

    def test_without_a_vanilla_copy_it_says_so_and_still_applies(self) -> None:
        self._write_dump("diff")
        self.manager.rescan()
        self.manager.set_enabled("dump", True)
        self.manager.save_mod_list()
        Path(str(self.db) + ".original").unlink()

        # Re-apply rather than save: saving would simply write the copy again.
        report = self.manager.reapply_all()
        self.assertTrue(report.ok, report.error)
        warnings = " ".join(report.for_mod("dump").warnings)
        self.assertIn("no vanilla copy", warnings)
        self.assertEqual(self._skills()["SKL_POWER_01"], (1, 0), "it still applied")

    def test_an_unknown_apply_mode_is_rejected_at_load(self) -> None:
        from lid_db_manager.errors import ModLoadError
        from lid_db_manager.mod import load_mod_json

        folder = write_mod(self.paths.mods_dir, "weird", {**TWEAK, "apply": "sideways"})
        with self.assertRaises(ModLoadError) as caught:
            load_mod_json(folder)
        self.assertIn("sideways", caught.exception.reason)


if __name__ == "__main__":
    unittest.main()
