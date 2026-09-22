"""The controller both front ends drive, plus a sanity check on the shipped mods."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from fixtures import build_db, query, write_mod

from lid_db_manager.manager import MOD_APPLIED, MOD_DISABLED, MOD_PENDING, Manager
from lid_db_manager.mod_loader import scan_mods
from lid_db_manager.paths import AppPaths

PROJECT_ROOT = Path(__file__).resolve().parent.parent

COST_MOD = {
    "patches": [
        {
            "type": "update_set",
            "table": "master_skill",
            "set": {"buy_money": 1},
            "where": "buy_money > 1",
        }
    ]
}


class ManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.paths = AppPaths(self.root).ensure()
        self.db = build_db(self.root / "game" / "masters.db")
        write_mod(self.paths.mods_dir, "cost", COST_MOD)
        self.manager = Manager(self.paths)
        self.manager.set_db_path(self.db)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_choosing_a_database_keeps_an_untouched_copy_at_once(self) -> None:
        """Not at first save - by then the file may already have moved on."""
        original = self.db.parent / "masters.db.original"
        self.assertTrue(original.is_file(), "picking the database should keep a copy")
        self.assertEqual(original.read_bytes()[:16], self.db.read_bytes()[:16])
        self.assertEqual(self.manager.vanilla_path, original)

    def test_the_untouched_copy_is_never_replaced_by_a_later_pick(self) -> None:
        original = self.db.parent / "masters.db.original"
        before = original.stat().st_mtime_ns

        # The database changes, then the user points at it again.
        build_db(self.db)
        self.manager.set_db_path(self.db)
        self.assertEqual(original.stat().st_mtime_ns, before, "must not be rewritten")

    def test_a_second_database_gets_its_own_untouched_copy(self) -> None:
        other = build_db(self.root / "game" / "other.db")
        self.manager.set_db_path(other)
        self.assertTrue((self.root / "game" / "other.db.original").is_file())
        self.assertTrue((self.root / "game" / "masters.db.original").is_file())

    def test_pointing_at_a_missing_file_does_not_crash(self) -> None:
        self.manager.set_db_path(self.root / "game" / "nope.db")
        self.assertIsNone(self.manager.vanilla_path)

    def test_a_read_only_folder_warns_instead_of_failing(self) -> None:
        from unittest import mock

        other = build_db(self.root / "game" / "locked.db")
        with mock.patch(
            "lid_db_manager.backup.copy_database", side_effect=OSError("access denied")
        ):
            self.manager.set_db_path(other)
        self.assertFalse((self.root / "game" / "locked.db.original").exists())
        self.assertTrue(
            any("Could not keep an untouched copy" in line.message
                for line in self.manager.log.lines)
        )

    def test_save_mod_list_backs_up_applies_and_stamps(self) -> None:
        self.manager.set_enabled("cost", True)
        report = self.manager.save_mod_list()

        self.assertTrue(report.ok, report.error)
        self.assertEqual(query(self.db, "SELECT DISTINCT buy_money FROM master_skill"), [(1,)])
        self.assertEqual(len(self.manager.dated_backups()), 1)
        self.assertTrue((self.db.parent / "masters.db.backup").is_file())
        self.assertTrue((self.db.parent / "masters.db.original").is_file())
        self.assertEqual(self.manager.db_status().state, "ok")
        self.assertEqual(self.manager.mod_status("cost"), MOD_APPLIED)

    def test_state_survives_a_restart(self) -> None:
        self.manager.set_enabled("cost", True)
        self.manager.save_mod_list()

        reopened = Manager(self.paths)
        self.assertEqual(reopened.state.enabled_mods, ["cost"])
        self.assertEqual(reopened.db_status().state, "ok")
        self.assertEqual(reopened.mod_status("cost"), MOD_APPLIED)

    def test_a_replaced_database_goes_stale_and_auto_re_applies(self) -> None:
        self.manager.set_enabled("cost", True)
        self.manager.save_mod_list()
        self.manager.state.settings.auto_reapply = True  # off by default - opt in for this test

        build_db(self.db)  # the game pushes a fresh copy
        status = self.manager.db_status()
        self.assertTrue(status.changed)
        self.assertEqual(self.manager.mod_status("cost"), MOD_PENDING)

        report = self.manager.on_db_changed(status)
        self.assertIsNotNone(report)
        self.assertTrue(report.ok)
        self.assertEqual(query(self.db, "SELECT DISTINCT buy_money FROM master_skill"), [(1,)])
        self.assertEqual(self.manager.db_status().state, "ok")

    def test_auto_re_apply_can_be_turned_off(self) -> None:
        self.manager.set_enabled("cost", True)
        self.manager.save_mod_list()
        self.manager.state.settings.auto_reapply = False

        build_db(self.db)
        self.assertIsNone(self.manager.on_db_changed(self.manager.db_status()))
        self.assertNotEqual(query(self.db, "SELECT DISTINCT buy_money FROM master_skill"), [(1,)])

    def test_re_apply_all_does_not_take_another_backup(self) -> None:
        self.manager.set_enabled("cost", True)
        self.manager.save_mod_list()
        self.manager.reapply_all()
        self.assertEqual(len(self.manager.dated_backups()), 1)

    def test_re_applying_does_not_destroy_the_ability_to_revert(self) -> None:
        """A second apply must not snapshot the state the first one produced."""
        self.manager.set_enabled("cost", True)
        self.manager.save_mod_list()
        self.manager.reapply_all()
        self.manager.reapply_all()

        self.assertTrue(self.manager.revert(["cost"])[0].ok)
        self.assertEqual(
            sorted(query(self.db, "SELECT buy_money FROM master_skill")),
            [(1,), (500,), (1200,), (5000,)],
            "revert should restore the original values, not the modded ones",
        )

    def test_a_replaced_database_does_get_a_fresh_snapshot(self) -> None:
        """The other half: after a game update the old snapshot is the wrong one."""
        self.manager.set_enabled("cost", True)
        self.manager.save_mod_list()

        build_db(self.db)  # the game ships a new database
        self.manager.reapply_all()

        self.assertTrue(self.manager.revert(["cost"])[0].ok)
        self.assertEqual(
            sorted(query(self.db, "SELECT buy_money FROM master_skill")),
            [(1,), (500,), (1200,), (5000,)],
        )

    def test_revert_disables_the_mod_and_restores_the_rows(self) -> None:
        self.manager.set_enabled("cost", True)
        self.manager.save_mod_list()

        results = self.manager.revert(["cost"])
        self.assertTrue(results[0].ok)
        self.assertEqual(self.manager.mod_status("cost"), MOD_DISABLED)
        self.assertEqual(
            sorted(query(self.db, "SELECT buy_money FROM master_skill")),
            [(1,), (500,), (1200,), (5000,)],
        )

    def test_a_deleted_mod_folder_is_detected(self) -> None:
        self.manager.set_enabled("cost", True)
        self.manager.save_mod_list()
        shutil.rmtree(self.paths.mods_dir / "cost")
        self.manager.rescan()

        self.assertEqual(self.manager.deleted_mods(), ["cost"])
        results = self.manager.revert(["cost"])
        self.assertTrue(results[0].ok)
        self.assertEqual(self.manager.deleted_mods(), [])

    def test_forget_drops_bookkeeping_without_touching_the_database(self) -> None:
        self.manager.set_enabled("cost", True)
        self.manager.save_mod_list()
        shutil.rmtree(self.paths.mods_dir / "cost")
        self.manager.rescan()

        self.manager.forget(["cost"])
        self.assertEqual(self.manager.deleted_mods(), [])
        self.assertEqual(query(self.db, "SELECT DISTINCT buy_money FROM master_skill"), [(1,)])

    def test_load_order_decides_who_wins(self) -> None:
        """The last mod in the load order overwrites the ones above it."""
        write_mod(self.paths.mods_dir, "sets-5", {
            "patches": [{"type": "update_set", "table": "master_skill",
                         "set": {"buy_money": 5}, "where": "id = 'SKL_EXPUP_01'"}]})
        write_mod(self.paths.mods_dir, "sets-9", {
            "patches": [{"type": "update_set", "table": "master_skill",
                         "set": {"buy_money": 9}, "where": "id = 'SKL_EXPUP_01'"}]})
        self.manager.rescan()
        self.manager.set_enabled("sets-5", True)
        self.manager.set_enabled("sets-9", True)

        self.assertTrue(self.manager.save_mod_list().ok)
        value = dict(query(self.db, "SELECT id, buy_money FROM master_skill"))["SKL_EXPUP_01"]
        self.assertEqual(value, 9, "the mod lower in the load order should win")

        # Move it up and the other one wins - and nothing else had to change.
        self.manager.move_mod("sets-9", -1)
        self.assertEqual(self.manager.state.enabled_mods, ["sets-9", "sets-5"])
        self.assertTrue(self.manager.save_mod_list().ok)
        value = dict(query(self.db, "SELECT id, buy_money FROM master_skill"))["SKL_EXPUP_01"]
        self.assertEqual(value, 5)

    def test_re_ticking_a_mod_no_longer_silently_reorders_the_rest(self) -> None:
        """Re-ticking still appends, but the order is now visible and movable."""
        write_mod(self.paths.mods_dir, "other", COST_MOD)
        self.manager.rescan()
        self.manager.set_enabled("cost", True)
        self.manager.set_enabled("other", True)
        self.assertEqual(self.manager.state.enabled_mods, ["cost", "other"])

        self.manager.set_enabled("cost", False)
        self.manager.set_enabled("cost", True)
        self.assertEqual(self.manager.state.enabled_mods, ["other", "cost"])
        # ... and unlike before, the user can put it back.
        self.manager.move_mod("cost", -1)
        self.assertEqual(self.manager.state.enabled_mods, ["cost", "other"])

    def test_listed_mods_puts_enabled_ones_first_in_load_order(self) -> None:
        write_mod(self.paths.mods_dir, "aaa-disabled", COST_MOD)
        write_mod(self.paths.mods_dir, "zzz-enabled", COST_MOD)
        self.manager.rescan()
        self.manager.set_enabled("zzz-enabled", True)
        listed = [mod.id for mod in self.manager.listed_mods()]
        self.assertEqual(listed[0], "zzz-enabled")
        self.assertIn("aaa-disabled", listed[1:])

    def test_a_mod_above_its_dependency_is_reported(self) -> None:
        write_mod(self.paths.mods_dir, "base", COST_MOD)
        write_mod(self.paths.mods_dir, "needs-base", {**COST_MOD, "requires": ["base"]})
        self.manager.rescan()
        self.manager.set_enabled("needs-base", True)
        self.manager.set_enabled("base", True)

        messages = [p.message() for p in self.manager.conflicts().order_problems]
        self.assertEqual(len(messages), 1)
        self.assertIn("applied before base", messages[0])

        self.manager.move_mod("base", -1)
        self.assertEqual(self.manager.conflicts().order_problems, [])

    def test_modpacks_round_trip(self) -> None:
        self.manager.set_enabled("cost", True)
        self.manager.save_modpack("everything")
        self.manager.set_enabled("cost", False)
        self.assertEqual(self.manager.load_modpack("everything"), ["cost"])

    def test_pointing_at_a_different_database_clears_the_stamp(self) -> None:
        self.manager.set_enabled("cost", True)
        self.manager.save_mod_list()
        other = build_db(self.root / "game" / "other.db")
        self.manager.set_db_path(other)
        self.assertEqual(self.manager.db_status().state, "unstamped")

    def test_applying_with_no_database_reports_instead_of_crashing(self) -> None:
        manager = Manager(AppPaths(self.root / "empty").ensure())
        report = manager.save_mod_list()
        self.assertFalse(report.ok)
        self.assertIn("no database", report.error)

    def test_restoring_a_backup_resets_the_applied_record(self) -> None:
        self.manager.set_enabled("cost", True)
        self.manager.save_mod_list()
        self.manager.restore_backup(self.manager.dated_backups()[0])
        self.assertEqual(self.manager.state.applied, {})
        self.assertEqual(self.manager.db_status().state, "ok")

    def test_the_backup_list_includes_the_files_beside_the_database(self) -> None:
        self.manager.set_enabled("cost", True)
        self.manager.save_mod_list()
        kinds = [entry.kind for entry in self.manager.backups()]
        self.assertEqual(kinds, ["original", "rolling", "dated"])

    def test_restoring_the_original_undoes_every_save(self) -> None:
        self.manager.set_enabled("cost", True)
        self.manager.save_mod_list()
        self.manager.save_mod_list()  # second save: rolling now holds modded data

        original = self.manager.backups()[0]
        self.assertEqual(original.kind, "original")
        self.manager.restore_backup(original)
        self.assertEqual(
            sorted(query(self.db, "SELECT buy_money FROM master_skill")),
            [(1,), (500,), (1200,), (5000,)],
        )


class DecalDrawPriceTests(unittest.TestCase):
    """The decal mods change what a draw costs, and nothing else.

    Version 1.0.0 of these lowered 34 individual decal prices and never touched
    the draw. The draw's price sits in a table where another row - a revive -
    costs exactly the same 50,000, so the mods have to pick the row by name.
    """

    PRICES = (25000, 10000, 5000, 1)

    def setUp(self) -> None:
        import sqlite3

        self._tmp = tempfile.TemporaryDirectory()
        self.db = build_db(Path(self._tmp.name) / "masters.db")
        con = sqlite3.connect(str(self.db))
        con.executemany(
            "INSERT INTO master_shop_product_price (id, price, medal) VALUES (?, ?, 0)",
            [("PRD_SKILL_GACHA", 50000), ("PRD_CONTINUE_6", 50000)],
        )
        con.commit()
        con.close()
        self.mods = scan_mods(PROJECT_ROOT / "mods")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _at(self, price):
        return self.mods.get("decal-draw-price").with_settings({"price": price})

    def test_any_chosen_price_changes_the_draw_price_and_only_that(self) -> None:
        from lid_db_manager import dbdiff

        for price in self.PRICES:
            with self.subTest(price=price):
                delta = dbdiff.delta_for_mod(self.db, self._at(price))
                changed = {
                    (t.table, u.key): u.changes for t in delta.tables for u in t.updates
                }
                self.assertEqual(
                    changed,
                    {("master_shop_product_price", ("PRD_SKILL_GACHA",)): {"price": price}},
                )
                self.assertEqual(delta.insert_count + delta.delete_count, 0)

    def test_the_revive_price_beside_it_is_left_alone(self) -> None:
        from lid_db_manager import dbdiff

        delta = dbdiff.delta_for_mod(self.db, self._at(5000))
        keys = {u.key for t in delta.tables for u in t.updates}
        self.assertNotIn(("PRD_CONTINUE_6",), keys)

    def test_no_individual_decal_price_moves(self) -> None:
        self.assertNotIn("master_skill", self._at(5000).tables())


class ShippedModTests(unittest.TestCase):
    """Every mod in the repo's mods/ folder must at least parse and validate."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = build_db(Path(self._tmp.name) / "masters.db")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    SHIPPED = {
        "revive-cost",
        "fighter-tier-prices",
        "nitro-boost-exp",
        "weapon-durability",
        "armor-durability",
        "weapon-ammo",
        "weapon-magazine",
        "bank-limit",
        "reward-box-limit",
        "storage-limit",
        "decal-draw-price",
        "tdm-rewards",
        "LET IT DIE Crossover Content v3.79",
        "Colored PlayStation Buttons v1.4",
        "Tower Static Radio",
        "instant-drops",
    }

    # By S3er0i9ng, shipped with their permission. Named exactly as a drop of
    # the pack's own folder installs them, so a player who already dropped one
    # in gets the same folder rather than a second copy.
    BUNDLED = {
        "LET IT DIE Crossover Content v3.79",
        "Colored PlayStation Buttons v1.4",
        "Tower Static Radio",
    }
    VANILLA_DB = PROJECT_ROOT / "LiD Vanilla DB" / "5.0.4.0" / "masters.db"

    # Pairs that are the same change at two strengths. They are meant to
    # collide - you pick one - and each declares the other in conflicts_with,
    # so the manager warns instead of silently letting one win.
    # There used to be strength variants here - x2 and x5 of the same thing,
    # meant to collide. They are one mod each now, with the strength a setting,
    # so nothing shipped is supposed to conflict with anything.
    ALTERNATIVES: set = set()
    ADJUSTABLE = {
        "decal-draw-price", "weapon-durability", "armor-durability", "tdm-rewards",
        "bank-limit", "weapon-ammo", "weapon-magazine", "storage-limit",
        "reward-box-limit", "revive-cost", "fighter-tier-prices", "nitro-boost-exp",
    }

    # Mods that may sit in a working copy but are not part of the repo: the
    # conflict demo, and anything by another author that is not ours to
    # redistribute. Allowed to be there, never required.
    LOCAL_ONLY = {
        "overlap-test",
        "diff-demo-blunt",
        "diff-demo-tweak",
        "Floor Material Names",
        "Shop Always Appears",
    }

    def _real_mods(self):
        return [m for m in scan_mods(PROJECT_ROOT / "mods").mods if m.id not in self.LOCAL_ONLY]

    def test_every_shipped_mod_loads(self) -> None:
        result = scan_mods(PROJECT_ROOT / "mods")
        found = {mod.id for mod in result.mods}
        self.assertEqual(result.failures, [], f"mods failed to load: {result.failures}")
        self.assertEqual(self.SHIPPED - found, set(), "a documented mod is missing")
        # The diagnostic mod is optional - it is not published - but anything
        # else turning up here is a mod nobody has written a test for yet.
        self.assertEqual(found - self.SHIPPED - self.LOCAL_ONLY, set(), "unexpected mod folder")

    def test_every_shipped_mod_validates_against_the_test_schema(self) -> None:
        from lid_db_manager.validator import validate

        # The bundled pack writes tables the test schema does not have; it is
        # checked against the real database below instead.
        mods = [m for m in scan_mods(PROJECT_ROOT / "mods").mods if m.id not in self.BUNDLED]
        report = validate(self.db, mods)
        errors = {r.mod_id: r.errors for r in report.results if r.errors}
        self.assertEqual(errors, {})

    def test_the_bundled_mods_validate_against_the_real_vanilla_database(self) -> None:
        from lid_db_manager.validator import validate

        if not self.VANILLA_DB.is_file():
            self.skipTest(f"no vanilla database at {self.VANILLA_DB}")
        copy = Path(self._tmp.name) / "vanilla.db"
        shutil.copy2(self.VANILLA_DB, copy)
        mods = [m for m in scan_mods(PROJECT_ROOT / "mods").mods if m.id in self.BUNDLED]
        self.assertEqual({m.id for m in mods}, self.BUNDLED)
        report = validate(copy, mods)
        errors = {r.mod_id: r.errors for r in report.results if r.errors}
        self.assertEqual(errors, {})

    def test_the_templates_are_hidden_from_the_mod_list(self) -> None:
        result = scan_mods(PROJECT_ROOT / "mods")
        skipped = {entry.folder for entry in result.skipped}
        self.assertEqual(skipped & {"_example", "_example-sql"}, {"_example", "_example-sql"})
        self.assertFalse({mod.id for mod in result.mods} & skipped)

    def _analyze(self, mods, with_db: bool = True):
        from lid_db_manager.conflict import analyze
        from lid_db_manager.sqlutil import connect

        if not with_db:
            return analyze(mods, {mod.id for mod in mods})
        con = connect(self.db, read_only=True)
        try:
            return analyze(mods, {mod.id for mod in mods}, con)
        finally:
            con.close()

    # The no-database fallback (whole-table granularity) is covered
    # synthetically in test_conflict.py - it needs two mods sharing a table,
    # which the shipped set deliberately does not have.

    def test_the_real_mods_do_not_conflict_with_each_other(self) -> None:
        """Apart from the x2/x5 pairs, which are alternatives on purpose."""
        # The Crossover pack is raw SQL, so it can only be compared a whole table
        # at a time, and it shares master_part and master_text with the
        # multipliers and Nitro Boost. It shares no rows with them: its
        # master_part updates only set `platform` to unhide existing gear, and
        # its master_text rows are new ids. Those table-wide warnings are the
        # only overlap it is allowed.
        report = self._analyze(self._real_mods())
        unexpected = [
            c.message() for c in report.conflicts
            if frozenset({c.first, c.second}) not in self.ALTERNATIVES
            and not ({c.first, c.second} & self.BUNDLED and "both write table" in c.message())
        ]
        self.assertEqual(unexpected, [])
        self.assertEqual([r.message() for r in report.missing_requirements], [])

    def test_each_number_mod_is_adjustable(self) -> None:
        """One mod per thing, with the number a setting - not x2, x5 and x10."""
        mods = {m.id: m for m in self._real_mods()}
        for mod_id in sorted(self.ADJUSTABLE):
            with self.subTest(mod=mod_id):
                self.assertTrue(mods[mod_id].settings, "it has no value to choose")
                self.assertEqual(mods[mod_id].load_warnings, [])

    def test_every_default_is_inside_its_own_limits(self) -> None:
        for mod in self._real_mods():
            for setting in mod.settings:
                with self.subTest(mod=mod.id, setting=setting.id):
                    self.assertEqual(setting.coerce(setting.default), setting.default)

    def test_the_multiplier_mods_cannot_compound(self) -> None:
        """They multiply, so a second save would square them unless they are
        measured against vanilla each time."""
        from lid_db_manager.mod import APPLY_DIFF

        multipliers = [
            m for m in self._real_mods() if any(s.unit.lower() == "x" for s in m.settings)
        ]
        self.assertEqual(len(multipliers), 6)
        for mod in multipliers:
            with self.subTest(mod=mod.id):
                self.assertEqual(mod.apply_mode, APPLY_DIFF)

    def test_the_diagnostic_mod_really_does_conflict(self) -> None:
        """The other half: a genuine row collision must still be reported.

        This is what stops the row-level check from 'passing' by simply never
        warning about anything. Skipped where mods/overlap-test has been
        deleted, since it is a local demo rather than a published mod - the
        same guarantee is covered synthetically in test_conflict.py.
        """
        mods = scan_mods(PROJECT_ROOT / "mods").mods
        present = {mod.id for mod in mods}
        if not {"overlap-test", "Floor Material Names"} <= present:
            self.skipTest("the conflict demo pair is not installed in this working copy")
        report = self._analyze(mods)
        # Other local-only mods may conflict too, so look for this pair rather
        # than assuming it is the only warning in the working copy.
        pair = [
            c.message()
            for c in report.conflicts
            if {c.first, c.second} == {"overlap-test", "Floor Material Names"}
        ]
        self.assertEqual(len(pair), 1, [c.message() for c in report.conflicts])
        self.assertIn("1 shared row(s)", pair[0])

if __name__ == "__main__":
    unittest.main()
