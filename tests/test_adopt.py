"""Recognising mods that are already in somebody's database.

The point of these: someone arrives with a masters.db that has been modded
already, and the manager has to say which of those changes it recognises and
which belong to nobody - without ever treating a modded file as if it were
stock.
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fixtures import build_db, write_mod

from lid_db_manager import adopt, dbdiff, vanilla_library
from lid_db_manager.manager import Manager
from lid_db_manager.mod_loader import scan_mods
from lid_db_manager.paths import AppPaths


def apply_sql(db: Path, *statements: str) -> None:
    con = sqlite3.connect(str(db))
    try:
        for statement in statements:
            con.execute(statement)
        con.commit()
    finally:
        con.close()


class AdoptionScanTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.paths = AppPaths(self.root).ensure()
        self.vanilla = build_db(self.root / "clean" / "masters.db")
        self.theirs = self.root / "game" / "masters.db"
        self.theirs.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.vanilla, self.theirs)

        write_mod(
            self.paths.mods_dir,
            "revive-1kc",
            {
                "patches": [
                    {
                        "type": "update_set",
                        "table": "master_shop_product_price",
                        "set": {"price": 1},
                        "where": "id LIKE 'PRD_CONTINUE%'",
                    }
                ]
            },
        )
        write_mod(
            self.paths.mods_dir,
            "bank-2x",
            {
                "patches": [
                    {
                        "type": "raw_sql",
                        "sql": 'UPDATE master_safe_level SET "limit" = "limit" * 2;',
                    }
                ]
            },
        )
        self.mods = scan_mods(self.paths.mods_dir).mods

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _delta_for(self, mod):
        return dbdiff.delta_for_mod(self.vanilla, mod)

    def _scan(self, game_root: Path | None = None) -> adopt.AdoptionReport:
        return adopt.scan(self.vanilla, self.theirs, self.mods, self._delta_for, game_root)

    def test_an_untouched_database_recognises_nothing(self) -> None:
        report = self._scan()
        self.assertTrue(report.ok)
        self.assertTrue(report.total.empty)
        self.assertEqual(report.recognised, [])
        self.assertFalse(report.has_leftover)

    def test_a_mod_already_applied_is_recognised(self) -> None:
        apply_sql(self.theirs, "UPDATE master_shop_product_price SET price = 1 "
                               "WHERE id LIKE 'PRD_CONTINUE%'")
        report = self._scan()
        found = {m.mod_id for m in report.recognised}
        self.assertIn("revive-1kc", found)
        self.assertNotIn("bank-2x", found)

    def test_the_recognised_mods_changes_are_not_left_over(self) -> None:
        apply_sql(self.theirs, "UPDATE master_shop_product_price SET price = 1 "
                               "WHERE id LIKE 'PRD_CONTINUE%'")
        report = self._scan()
        self.assertFalse(report.has_leftover, report.leftover.summary())

    def test_a_hand_edit_nobody_owns_is_left_over(self) -> None:
        apply_sql(self.theirs, "UPDATE master_skill SET buy_money = 7 WHERE id = 'SKL_POWER_01'")
        report = self._scan()
        self.assertEqual(report.recognised, [])
        self.assertTrue(report.has_leftover)
        tables = {t.table for t in report.leftover.tables}
        self.assertEqual(tables, {"master_skill"})

    def test_a_mod_and_a_hand_edit_are_told_apart(self) -> None:
        apply_sql(
            self.theirs,
            "UPDATE master_shop_product_price SET price = 1 WHERE id LIKE 'PRD_CONTINUE%'",
            "UPDATE master_skill SET buy_money = 7 WHERE id = 'SKL_POWER_01'",
        )
        report = self._scan()
        self.assertEqual([m.mod_id for m in report.recognised], ["revive-1kc"])
        self.assertEqual({t.table for t in report.leftover.tables}, {"master_skill"})

    def test_two_edits_to_one_row_are_split_by_column(self) -> None:
        # The mod owns the price; the player also changed the medal cost on the
        # same row. Only the medal is nobody's.
        apply_sql(
            self.theirs,
            "UPDATE master_shop_product_price SET price = 1 WHERE id LIKE 'PRD_CONTINUE%'",
            "UPDATE master_shop_product_price SET medal = 9 WHERE id = 'PRD_CONTINUE_G1'",
        )
        report = self._scan()
        self.assertEqual([m.mod_id for m in report.recognised], ["revive-1kc"])
        left = report.leftover.tables[0]
        self.assertEqual(left.table, "master_shop_product_price")
        self.assertEqual([sorted(u.changes) for u in left.updates], [["medal"]])

    def test_half_a_mod_reads_as_partial_not_applied(self) -> None:
        # Only one of the two rows the mod writes.
        apply_sql(self.theirs, "UPDATE master_shop_product_price SET price = 1 "
                               "WHERE id = 'PRD_CONTINUE_G1'")
        report = self._scan()
        self.assertEqual(report.recognised, [])
        partial = {m.mod_id for m in report.partial}
        self.assertIn("revive-1kc", partial)
        match = next(m for m in report.matches if m.mod_id == "revive-1kc")
        self.assertLess(match.found, match.changes)

    def test_a_different_value_is_not_a_match(self) -> None:
        apply_sql(self.theirs, "UPDATE master_shop_product_price SET price = 99 "
                               "WHERE id LIKE 'PRD_CONTINUE%'")
        report = self._scan()
        self.assertEqual(report.recognised, [])
        self.assertTrue(report.has_leftover)

    def test_rows_added_to_a_table_with_no_primary_key_still_match(self) -> None:
        """Some of the game's tables have no key - master_part_equipment is one.

        A row added to one of those has nothing to be addressed by, so it is
        matched on its values. Before that, the Crossover pack's ten rows in
        that table could never match and the whole pack read as half-applied.
        """
        for database in (self.vanilla, self.theirs):
            apply_sql(database, "CREATE TABLE master_loose (part TEXT, slot INTEGER)")
        write_mod(
            self.paths.mods_dir,
            "loose-rows",
            {
                "patches": [
                    {
                        "type": "raw_sql",
                        "sql": "INSERT INTO master_loose (part, slot) VALUES "
                               "('PT_A', 1), ('PT_B', 2);",
                    }
                ]
            },
        )
        self.mods = scan_mods(self.paths.mods_dir).mods
        apply_sql(
            self.theirs,
            "INSERT INTO master_loose (part, slot) VALUES ('PT_A', 1), ('PT_B', 2)",
        )

        report = self._scan()
        match = next(m for m in report.matches if m.mod_id == "loose-rows")
        self.assertEqual((match.inserts_present, match.inserts), (2, 2))
        self.assertTrue(match.applied, "keyless rows were never matched")
        self.assertNotIn(
            "master_loose",
            {t.table for t in report.leftover.tables},
            "rows credited to a mod were also reported as belonging to nobody",
        )

    def test_a_keyless_row_nobody_added_is_still_left_over(self) -> None:
        for database in (self.vanilla, self.theirs):
            apply_sql(database, "CREATE TABLE master_loose (part TEXT, slot INTEGER)")
        apply_sql(self.theirs, "INSERT INTO master_loose (part, slot) VALUES ('PT_Z', 9)")
        self.mods = scan_mods(self.paths.mods_dir).mods
        report = self._scan()
        self.assertIn("master_loose", {t.table for t in report.leftover.tables})

    def test_a_missing_game_file_keeps_a_mod_out_of_recognised(self) -> None:
        game = self.root / "gamefolder"
        (game / "BrgGame" / "CookedPCConsole").mkdir(parents=True)
        write_mod(
            self.paths.mods_dir,
            "art-mod",
            {
                "patches": [
                    {
                        "type": "update_set",
                        "table": "master_skill",
                        "set": {"buy_money": 3},
                        "where": "id = 'SKL_FREE_01'",
                    },
                    {"type": "asset_file", "source": "assets", "target": "BrgGame/CookedPCConsole"},
                ]
            },
        )
        assets = self.paths.mods_dir / "art-mod" / "assets"
        assets.mkdir(parents=True, exist_ok=True)
        (assets / "Thing.upk").write_bytes(b"x")
        self.mods = scan_mods(self.paths.mods_dir).mods
        apply_sql(self.theirs, "UPDATE master_skill SET buy_money = 3 WHERE id = 'SKL_FREE_01'")

        report = self._scan(game_root=game)
        match = next(m for m in report.matches if m.mod_id == "art-mod")
        self.assertEqual((match.files, match.files_present), (1, 0))
        self.assertFalse(match.applied, "its artwork is not in the game, so it is not applied")

        (game / "BrgGame" / "CookedPCConsole" / "Thing.upk").write_bytes(b"x")
        report = self._scan(game_root=game)
        match = next(m for m in report.matches if m.mod_id == "art-mod")
        self.assertTrue(match.applied)


class VanillaLibraryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.library = self.root / "LiD Vanilla DB"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _add(self, label: str, version: str | None, steam: str | None = None) -> Path:
        folder = self.library / label
        folder.mkdir(parents=True, exist_ok=True)
        database = build_db(folder / "masters.db")
        if version is not None:
            con = sqlite3.connect(str(database))
            con.execute(
                'CREATE TABLE master_const_str ("id" CHARACTER(64) NOT NULL, '
                '"value" TEXT NOT NULL, PRIMARY KEY ("id"))'
            )
            con.execute(
                "INSERT INTO master_const_str (id, value) VALUES ('TITLE_VERSION', ?)",
                (version,),
            )
            if steam is not None:
                con.execute(
                    "INSERT INTO master_const_str (id, value) "
                    "VALUES ('TITLE_VERSION_STEAM', ?)",
                    (steam,),
                )
            con.commit()
            con.close()
        return database

    def test_it_finds_each_build_and_reads_its_version(self) -> None:
        self._add("5.0.2.0", "5.0.2.0.0 - 1.86")
        self._add("5.0.3.0", "5.0.3.0.0 - 1.87")
        found = {b.label: b.version for b in vanilla_library.builds(self.root)}
        self.assertEqual(
            found, {"5.0.2.0": "5.0.2.0.0 - 1.86", "5.0.3.0": "5.0.3.0.0 - 1.87"}
        )

    def test_a_database_that_does_not_say_falls_back_to_its_folder_name(self) -> None:
        self._add("some-copy", None)
        build = vanilla_library.builds(self.root)[0]
        self.assertEqual(build.version, "")
        self.assertEqual(build.name, "some-copy")

    def test_it_picks_the_copy_matching_a_database(self) -> None:
        self._add("5.0.2.0", "5.0.2.0.0 - 1.86")
        self._add("5.0.3.0", "5.0.3.0.0 - 1.87")
        theirs = self.root / "game" / "masters.db"
        theirs.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.library / "5.0.3.0" / "masters.db", theirs)
        build = vanilla_library.best_for(theirs, self.root)
        self.assertIsNotNone(build)
        self.assertEqual(build.label, "5.0.3.0")

    def test_an_unknown_build_matches_nothing_rather_than_the_closest(self) -> None:
        self._add("5.0.3.0", "5.0.3.0.0 - 1.87")
        theirs = self.root / "game" / "masters.db"
        theirs.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.library / "5.0.3.0" / "masters.db", theirs)
        con = sqlite3.connect(str(theirs))
        con.execute("UPDATE master_const_str SET value = '9.9.9.9.9 - 2.00'")
        con.commit()
        con.close()
        self.assertIsNone(vanilla_library.best_for(theirs, self.root))

    def test_a_steam_only_bump_is_a_different_build(self) -> None:
        # 5.0.4.2.0 changed TITLE_VERSION_STEAM and left TITLE_VERSION at 1.89.
        self._add("5.0.4.1", "5.0.4.1.0 - 1.89", "5.0.4.1.0")
        theirs = self.root / "game" / "masters.db"
        theirs.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.library / "5.0.4.1" / "masters.db", theirs)
        con = sqlite3.connect(str(theirs))
        con.execute(
            "UPDATE master_const_str SET value = '5.0.4.2.0' "
            "WHERE id = 'TITLE_VERSION_STEAM'"
        )
        con.commit()
        con.close()
        self.assertEqual(
            vanilla_library.database_version(theirs), "5.0.4.1.0 - 1.89 (Steam 5.0.4.2.0)"
        )
        self.assertIsNone(vanilla_library.best_for(theirs, self.root))
        self._add("5.0.4.2", "5.0.4.1.0 - 1.89", "5.0.4.2.0")
        self.assertEqual(vanilla_library.best_for(theirs, self.root).label, "5.0.4.2")

    def test_a_matching_steam_number_leaves_the_name_alone(self) -> None:
        self._add("5.0.4.1", "5.0.4.1.0 - 1.89", "5.0.4.1.0")
        self.assertEqual(vanilla_library.builds(self.root)[0].version, "5.0.4.1.0 - 1.89")

    def test_an_empty_folder_is_not_a_build(self) -> None:
        (self.library / "nothing-here").mkdir(parents=True)
        self.assertEqual(vanilla_library.builds(self.root), [])


class ManagerAdoptionTests(unittest.TestCase):
    """The manager end: which clean copy it uses, and the rebuild."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.paths = AppPaths(self.root).ensure()
        library = self.root / "LiD Vanilla DB" / "5.0.3.0"
        library.mkdir(parents=True, exist_ok=True)
        self.clean = build_db(library / "masters.db")
        con = sqlite3.connect(str(self.clean))
        con.execute(
            'CREATE TABLE master_const_str ("id" CHARACTER(64) NOT NULL, '
            '"value" TEXT NOT NULL, PRIMARY KEY ("id"))'
        )
        con.execute(
            "INSERT INTO master_const_str (id, value) VALUES "
            "('TITLE_VERSION', '5.0.3.0.0 - 1.87')"
        )
        con.commit()
        con.close()

        self.theirs = self.root / "game" / "masters.db"
        self.theirs.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.clean, self.theirs)
        # They arrive with a mod already applied, plus an edit of their own.
        apply_sql(
            self.theirs,
            "UPDATE master_shop_product_price SET price = 1 WHERE id LIKE 'PRD_CONTINUE%'",
            "UPDATE master_skill SET buy_money = 7 WHERE id = 'SKL_POWER_01'",
        )
        write_mod(
            self.paths.mods_dir,
            "revive-1kc",
            {
                "patches": [
                    {
                        "type": "update_set",
                        "table": "master_shop_product_price",
                        "set": {"price": 1},
                        "where": "id LIKE 'PRD_CONTINUE%'",
                    }
                ]
            },
        )
        self.manager = Manager(self.paths)
        self.manager.set_db_path(self.theirs)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_the_shipped_copy_is_preferred_over_a_modded_original(self) -> None:
        # set_db_path kept their already-modded file as masters.db.original.
        self.assertEqual(self.manager.vanilla_path, self.clean)

    def test_a_content_pack_goes_to_the_top_of_the_load_order(self) -> None:
        """Top applies first, so a pack has to be above the tweaks on it.

        Otherwise a small mod retuning a table the pack also writes is applied
        first, buried by the pack, and reads to the player as simply not
        working - with nothing on screen to say why.
        """
        pack = write_mod(
            self.paths.mods_dir,
            "art-pack",
            {
                "patches": [
                    {
                        "type": "update_set",
                        "table": "master_skill",
                        "set": {"buy_money": 5},
                        "where": "id = 'SKL_FREE_01'",
                    },
                    {"type": "asset_file", "source": "assets", "target": "BrgGame/CookedPCConsole"},
                ]
            },
        )
        (pack / "assets").mkdir(parents=True, exist_ok=True)
        (pack / "assets" / "Thing.upk").write_bytes(b"x")
        self.manager.rescan()

        self.assertEqual(
            self.manager.adoption_order(["revive-1kc", "art-pack"]),
            ["art-pack", "revive-1kc"],
        )
        # And the other way round, so it is the pack that moves, not the input.
        self.assertEqual(
            self.manager.adoption_order(["art-pack", "revive-1kc"]),
            ["art-pack", "revive-1kc"],
        )

    def test_the_pack_that_rewrites_more_goes_above_one_that_only_swaps_art(self) -> None:
        """Buttons-style mods ship a file and touch no table, so they can bury
        nothing; the content pack has to be the one at the very top."""
        for mod_id, patches in (
            (
                "content-pack",
                [
                    {
                        "type": "update_set",
                        "table": "master_skill",
                        "set": {"buy_money": 5},
                        "where": "id = 'SKL_FREE_01'",
                    },
                    {
                        "type": "update_set",
                        "table": "master_body_detail",
                        "set": {"price": 5},
                        "where": "id = 'BODY_03'",
                    },
                    {"type": "asset_file", "source": "assets", "target": "BrgGame/CookedPCConsole"},
                ],
            ),
            (
                "art-only",
                [{"type": "asset_file", "source": "assets", "target": "BrgGame/CookedPCConsole"}],
            ),
        ):
            folder = write_mod(self.paths.mods_dir, mod_id, {"patches": patches})
            (folder / "assets").mkdir(parents=True, exist_ok=True)
            (folder / "assets" / f"{mod_id}.upk").write_bytes(b"x")
        self.manager.rescan()

        # Given in the order the scan would hand them over (sorted by name).
        self.assertEqual(
            self.manager.adoption_order(["art-only", "content-pack", "revive-1kc"]),
            ["content-pack", "art-only", "revive-1kc"],
        )

    def test_ordinary_mods_keep_the_order_they_were_given(self) -> None:
        write_mod(
            self.paths.mods_dir,
            "second-mod",
            {
                "patches": [
                    {
                        "type": "update_set",
                        "table": "master_skill",
                        "set": {"buy_money": 2},
                        "where": "id = 'SKL_FREE_01'",
                    }
                ]
            },
        )
        self.manager.rescan()
        self.assertEqual(
            self.manager.adoption_order(["second-mod", "revive-1kc"]),
            ["second-mod", "revive-1kc"],
        )

    def test_the_order_the_database_was_saved_with_is_kept(self) -> None:
        # A pack would normally jump to the top; the player's own saved order
        # beats that guess.
        pack = write_mod(
            self.paths.mods_dir,
            "art-pack",
            {"patches": [{"type": "asset_file", "source": "assets",
                          "target": "BrgGame/CookedPCConsole"}]},
        )
        (pack / "assets").mkdir(parents=True, exist_ok=True)
        (pack / "assets" / "Thing.upk").write_bytes(b"x")
        self.manager.rescan()
        self.assertEqual(
            self.manager.adoption_order(["art-pack", "revive-1kc"], ["revive-1kc", "art-pack"]),
            ["revive-1kc", "art-pack"],
        )

    def test_mods_the_record_does_not_name_are_placed_around_it(self) -> None:
        pack = write_mod(
            self.paths.mods_dir,
            "art-pack",
            {"patches": [{"type": "asset_file", "source": "assets",
                          "target": "BrgGame/CookedPCConsole"}]},
        )
        (pack / "assets").mkdir(parents=True, exist_ok=True)
        (pack / "assets" / "Thing.upk").write_bytes(b"x")
        write_mod(
            self.paths.mods_dir,
            "second-mod",
            {"patches": [{"type": "update_set", "table": "master_skill",
                          "set": {"buy_money": 2}, "where": "id = 'SKL_FREE_01'"}]},
        )
        self.manager.rescan()
        # Only revive-1kc was recorded: the new pack goes above it, the new
        # tweak below.
        self.assertEqual(
            self.manager.adoption_order(
                ["second-mod", "revive-1kc", "art-pack"], ["revive-1kc"]
            ),
            ["art-pack", "revive-1kc", "second-mod"],
        )

    def test_a_failed_rebuild_puts_their_own_database_back(self) -> None:
        from unittest import mock

        from lid_db_manager.runner import ApplyReport

        before = self.theirs.read_bytes()
        self.manager.state.enabled_mods = ["revive-1kc"]
        self.manager.state.save()
        snapshot = self.paths.snapshots_dir / "revive-1kc.json"
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_text("{}", encoding="utf-8")

        real = self.manager._apply

        def fails_after_writing(**kwargs):
            real(**kwargs)  # the clean copy is in place and the mods applied...
            return ApplyReport(error="game files could not be written")  # ...then this

        with mock.patch.object(self.manager, "_apply", side_effect=fails_after_writing):
            report = self.manager.adopt_rebuild(["revive-1kc"])
        self.assertFalse(report.ok)
        self.assertEqual(self.theirs.read_bytes(), before, "their database was not put back")
        self.assertEqual(self.manager.state.enabled_mods, ["revive-1kc"])
        self.assertEqual(snapshot.read_text(encoding="utf-8"), "{}", "old snapshots were lost")

    def test_a_mod_that_is_not_installed_is_dropped(self) -> None:
        self.assertEqual(self.manager.adoption_order(["revive-1kc", "ghost"]), ["revive-1kc"])

    def test_the_scan_names_the_mod_and_the_leftover(self) -> None:
        report = self.manager.adopt_scan()
        self.assertTrue(report.ok, report.error)
        self.assertEqual([m.mod_id for m in report.recognised], ["revive-1kc"])
        self.assertEqual({t.table for t in report.leftover.tables}, {"master_skill"})

    def test_rebuilding_gives_a_database_of_vanilla_plus_the_chosen_mods(self) -> None:
        report = self.manager.adopt_rebuild(["revive-1kc"])
        self.assertTrue(report.ok, report.error)
        con = sqlite3.connect(str(self.theirs))
        try:
            prices = [
                row[0]
                for row in con.execute(
                    "SELECT price FROM master_shop_product_price "
                    "WHERE id LIKE 'PRD_CONTINUE%' ORDER BY id"
                )
            ]
            hand_edit = con.execute(
                "SELECT buy_money FROM master_skill WHERE id = 'SKL_POWER_01'"
            ).fetchone()[0]
        finally:
            con.close()
        self.assertEqual(prices, [1, 1], "the chosen mod was not applied")
        self.assertEqual(hand_edit, 1200, "the unclaimed edit should be gone after a rebuild")

    def test_the_rebuild_leaves_a_genuinely_stock_original(self) -> None:
        self.manager.adopt_rebuild(["revive-1kc"])
        original = self.theirs.with_name(self.theirs.name + ".original")
        self.assertTrue(original.is_file())
        delta = dbdiff.compare(self.clean, original)
        self.assertTrue(delta.empty, "the kept copy still is not stock")

    def test_the_rebuild_backs_up_what_they_had(self) -> None:
        before = sorted(p.name for p in self.paths.backups_dir.glob("*.db"))
        self.manager.adopt_rebuild(["revive-1kc"])
        after = sorted(p.name for p in self.paths.backups_dir.glob("*.db"))
        self.assertGreater(len(after), len(before), "their old database was not kept")

    def test_a_rebuilt_mod_can_still_be_switched_off(self) -> None:
        self.manager.adopt_rebuild(["revive-1kc"])
        results = self.manager.revert(["revive-1kc"])
        self.assertTrue(all(r.ok for r in results), [r.error for r in results])
        con = sqlite3.connect(str(self.theirs))
        try:
            prices = sorted(
                row[0]
                for row in con.execute(
                    "SELECT price FROM master_shop_product_price WHERE id LIKE 'PRD_CONTINUE%'"
                )
            )
        finally:
            con.close()
        self.assertEqual(prices, [5000, 10000], "revert did not put the stock prices back")


if __name__ == "__main__":
    unittest.main()
