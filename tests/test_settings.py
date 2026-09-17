"""Values a player can choose for a mod, instead of one mod per strength.

What matters most here is the refusals: a setting is how a number typed into a
box ends up inside SQL, so it must only ever arrive as a number, inside the
mod's own limits, and a mod that asks for a setting it never declared must not
load at all.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fixtures import build_db, query, write_mod

from lid_db_manager import dbdiff, migrations
from lid_db_manager.errors import ModLoadError
from lid_db_manager.manager import Manager
from lid_db_manager.mod import load_mod_json
from lid_db_manager.paths import AppPaths
from lid_db_manager.settings import (
    ModSetting,
    format_number,
    parse_settings,
    render,
    render_text,
)
from lid_db_manager.state import AppliedRecord, State

MULTIPLIER = {
    "id": "multiplier", "label": "Multiplier", "type": "integer",
    "default": 2, "min": 1, "max": 100, "unit": "x",
}


def a_multiplier_mod(mods_dir: Path, mod_id: str = "cheap-skills", **extra) -> Path:
    data = {
        "description": "Skills cost {{multiplier}} times less... in spirit",
        "apply": "diff",
        "settings": [MULTIPLIER],
        "patches": [{
            "type": "raw_sql",
            "description": "buy_money x{{multiplier}}",
            "sql": "UPDATE master_skill SET buy_money = buy_money * {{multiplier}};",
        }],
    }
    data.update(extra)
    return write_mod(mods_dir, mod_id, data)


class NumbersAndText(unittest.TestCase):
    def test_styles(self) -> None:
        self.assertEqual(format_number(100000), "100000")
        self.assertEqual(format_number(100000, "comma"), "100,000")
        self.assertEqual(format_number(100000, "dot"), "100.000")
        self.assertEqual(format_number(2.0, "comma"), "2")

    def test_a_lone_placeholder_becomes_the_number_itself(self) -> None:
        self.assertEqual(render({"price": "{{price}}"}, {"price": 250}), {"price": 250})

    def test_placeholders_inside_text_are_filled_in(self) -> None:
        self.assertEqual(
            render_text("x{{m}} = {{m:comma}} / {{m:dot}}", {"m": 12000}),
            "x12000 = 12,000 / 12.000",
        )

    def test_display(self) -> None:
        setting = ModSetting("price", "Price", "integer", 10000, 1, 1000000, unit="KC")
        self.assertEqual(setting.display(10000), "10,000 KC")
        self.assertEqual(ModSetting("m", "M", "integer", 2, 1, 9, unit="x").display(5), "x5")
        self.assertEqual(ModSetting("p", "P", "integer", 2, 1, 9, unit="%").display(5), "5%")


class ChoosingAValue(unittest.TestCase):
    def setUp(self) -> None:
        self.setting = parse_settings([MULTIPLIER], "t")[0]

    def test_a_good_value(self) -> None:
        self.assertEqual(self.setting.coerce(5), 5)
        self.assertEqual(self.setting.coerce("7"), 7)

    def test_out_of_range_is_refused_with_the_limits(self) -> None:
        with self.assertRaises(ValueError) as caught:
            self.setting.coerce(500)
        self.assertIn("x1", str(caught.exception))
        self.assertIn("x100", str(caught.exception))

    def test_a_decimal_in_a_whole_number_setting_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            self.setting.coerce(2.5)

    def test_text_that_is_not_a_number_never_gets_through(self) -> None:
        for bad in ("2; DROP TABLE master_skill", "'", "abc", True, None, float("nan")):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    self.setting.coerce(bad)


class DeclaringSettings(unittest.TestCase):
    def test_bad_declarations_are_refused(self) -> None:
        cases = {
            "bad id": [{**MULTIPLIER, "id": "Bad-Id"}],
            "duplicate": [MULTIPLIER, MULTIPLIER],
            "unknown type": [{**MULTIPLIER, "type": "text"}],
            "default outside": [{**MULTIPLIER, "default": 500}],
            "min above max": [{**MULTIPLIER, "min": 50, "max": 5}],
            "decimal limit": [{**MULTIPLIER, "max": 9.5}],
            "missing default": [{k: v for k, v in MULTIPLIER.items() if k != "default"}],
            "too large": [{**MULTIPLIER, "max": 10_000_000_000}],
        }
        for name, raw in cases.items():
            with self.subTest(case=name):
                with self.assertRaises(ModLoadError):
                    parse_settings(raw, "t")


class LoadingAModWithSettings(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.mods = Path(self._tmp.name) / "mods"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_it_loads_filled_in_with_the_defaults(self) -> None:
        mod = load_mod_json(a_multiplier_mod(self.mods))
        self.assertEqual(mod.values, {"multiplier": 2})
        self.assertIn("* 2;", mod.patches[0].sql_text())
        self.assertEqual(mod.description, "Skills cost 2 times less... in spirit")

    def test_choosing_a_value_rebuilds_from_the_original_not_the_filled_copy(self) -> None:
        mod = load_mod_json(a_multiplier_mod(self.mods))
        five = mod.with_settings({"multiplier": 5})
        three = five.with_settings({"multiplier": 3})
        self.assertIn("* 5;", five.patches[0].sql_text())
        self.assertIn("* 3;", three.patches[0].sql_text())
        self.assertIn("* 2;", mod.patches[0].sql_text(), "the loaded mod was changed in place")

    def test_an_undeclared_placeholder_will_not_load(self) -> None:
        folder = write_mod(self.mods, "typo", {
            "settings": [MULTIPLIER],
            "patches": [{"type": "raw_sql", "sql": "UPDATE master_skill SET val0 = {{multiplyer};"}],
        })
        (folder / "mod.json").write_text(
            (folder / "mod.json").read_text(encoding="utf-8").replace("{{multiplyer}", "{{multiplyer}}"),
            encoding="utf-8",
        )
        with self.assertRaises(ModLoadError) as caught:
            load_mod_json(folder)
        self.assertIn("multiplyer", str(caught.exception))

    def test_a_mod_without_settings_keeps_braces_as_plain_text(self) -> None:
        folder = write_mod(self.mods, "braces", {"patches": [{
            "type": "raw_sql",
            "sql": "UPDATE master_text SET txt = 'see {{here}}' WHERE id = 'X';",
        }]})
        mod = load_mod_json(folder)
        self.assertIn("{{here}}", mod.patches[0].sql_text())

    def test_an_unused_setting_is_warned_about(self) -> None:
        folder = write_mod(self.mods, "unused", {
            "settings": [MULTIPLIER],
            "patches": [{"type": "raw_sql", "sql": "UPDATE master_skill SET val0 = 1;"}],
        })
        self.assertTrue(any("never used" in w for w in load_mod_json(folder).load_warnings))

    def test_a_sql_file_is_filled_in_too(self) -> None:
        folder = write_mod(
            self.mods, "from-file",
            {"settings": [MULTIPLIER],
             "patches": [{"type": "raw_sql_file", "path": "change.sql"}]},
            change__sql="UPDATE master_skill SET buy_money = buy_money * {{multiplier}};",
        )
        mod = load_mod_json(folder).with_settings({"multiplier": 7})
        self.assertIn("* 7;", mod.patches[0].sql_text())


class ThroughTheManager(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.paths = AppPaths(self.root).ensure()
        self.db = build_db(self.root / "game" / "masters.db")
        a_multiplier_mod(self.paths.mods_dir)
        self.manager = Manager(self.paths)
        self.manager.set_db_path(self.db)
        self.manager.set_enabled("cheap-skills", True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def prices(self):
        return sorted(r[0] for r in query(self.db, "SELECT buy_money FROM master_skill"))

    def test_the_chosen_value_is_what_gets_applied(self) -> None:
        self.manager.set_mod_setting("cheap-skills", "multiplier", 3)
        self.assertTrue(self.manager.save_mod_list().ok)
        self.assertEqual(self.prices(), [3, 1500, 3600, 15000])

    def test_changing_it_and_saving_again_does_not_compound(self) -> None:
        self.manager.set_mod_setting("cheap-skills", "multiplier", 3)
        self.manager.save_mod_list()
        self.manager.set_mod_setting("cheap-skills", "multiplier", 5)
        self.assertTrue(self.manager.save_mod_list().ok)
        self.assertEqual(self.prices(), [5, 2500, 6000, 25000])

    def test_switching_it_off_still_puts_stock_back(self) -> None:
        self.manager.set_mod_setting("cheap-skills", "multiplier", 4)
        self.manager.save_mod_list()
        self.manager.set_enabled("cheap-skills", False)
        self.manager.save_mod_list()
        self.assertEqual(self.prices(), [1, 500, 1200, 5000])

    def test_a_refused_value_changes_nothing(self) -> None:
        with self.assertRaises(ValueError):
            self.manager.set_mod_setting("cheap-skills", "multiplier", 1000)
        self.assertNotIn("cheap-skills", self.manager.state.mod_settings)

    def test_the_default_is_not_stored(self) -> None:
        self.manager.set_mod_setting("cheap-skills", "multiplier", 6)
        self.manager.set_mod_setting("cheap-skills", "multiplier", 2)
        self.assertNotIn("cheap-skills", self.manager.state.mod_settings)

    def test_the_choice_survives_a_restart(self) -> None:
        self.manager.set_mod_setting("cheap-skills", "multiplier", 9)
        again = Manager(self.paths)
        self.assertEqual(again.configured_mod("cheap-skills").values, {"multiplier": 9})

    def test_a_stored_value_the_mod_no_longer_allows_falls_back(self) -> None:
        self.manager.state.mod_settings["cheap-skills"] = {"multiplier": 9999}
        self.assertEqual(self.manager.configured_mod("cheap-skills").values, {"multiplier": 2})

    def test_the_preview_follows_the_value(self) -> None:
        manager = self.manager
        manager.set_mod_setting("cheap-skills", "multiplier", 3)
        first = manager.mod_delta(manager.configured_mod("cheap-skills"))
        manager.set_mod_setting("cheap-skills", "multiplier", 8)
        second = manager.mod_delta(manager.configured_mod("cheap-skills"))
        if first is None or second is None:
            self.skipTest("no vanilla copy to measure against")
        self.assertNotEqual(
            [u.changes for t in first.tables for u in t.updates],
            [u.changes for t in second.tables for u in t.updates],
        )


class CarryingPlayersAcross(unittest.TestCase):
    """Old strength variants become one mod at the same value."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.paths = AppPaths(self.root).ensure()
        write_mod(self.paths.mods_dir, "weapon-durability", {
            "settings": [MULTIPLIER], "apply": "diff",
            "patches": [{"type": "raw_sql",
                         "sql": "UPDATE master_part SET dur = dur * {{multiplier}} WHERE type = 'PTTP_ARM';"}],
        })
        write_mod(self.paths.mods_dir, "decal-draw-price", {
            "settings": [{"id": "price", "label": "Price", "type": "integer",
                          "default": 10000, "min": 1, "max": 1000000}],
            "patches": [{"type": "update_set", "table": "master_shop_product_price",
                         "set": {"price": "{{price}}"}, "where": "id = 'PRD_SKILL_GACHA'"}],
        })

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _state(self, **fields) -> None:
        state = State.load(self.paths.state_file)
        for key, value in fields.items():
            setattr(state, key, value)
        state.save()

    def test_an_enabled_variant_becomes_the_new_mod_at_its_value_in_its_place(self) -> None:
        self._state(enabled_mods=["other", "weapon-durability-5x", "last"])
        manager = Manager(self.paths)
        self.assertEqual(manager.state.enabled_mods, ["other", "weapon-durability", "last"])
        self.assertEqual(manager.configured_mod("weapon-durability").values, {"multiplier": 5})

    def test_an_applied_variant_keeps_its_way_back(self) -> None:
        snapshots = self.paths.snapshots_dir
        (snapshots / "weapon-durability-5x.json").write_text(
            json.dumps({"mod_id": "weapon-durability-5x", "entries": []}), encoding="utf-8"
        )
        self._state(
            enabled_mods=["weapon-durability-5x"],
            applied={"weapon-durability-5x": AppliedRecord(applied_at="then", rows_changed=385)},
        )
        manager = Manager(self.paths)
        self.assertIn("weapon-durability", manager.state.applied)
        self.assertTrue((snapshots / "weapon-durability.json").is_file())
        moved = json.loads((snapshots / "weapon-durability.json").read_text(encoding="utf-8"))
        self.assertEqual(moved["mod_id"], "weapon-durability")

    def test_the_wrong_decal_mods_leave_their_history_to_be_undone(self) -> None:
        """v1.0.0 changed different rows, so its record must not be claimed."""
        self._state(
            enabled_mods=["decal-cost-5k"],
            applied={"decal-cost-5k": AppliedRecord(applied_at="then", rows_changed=34)},
        )
        manager = Manager(self.paths)
        self.assertEqual(manager.state.enabled_mods, ["decal-draw-price"])
        self.assertIn("decal-cost-5k", manager.state.applied)
        self.assertNotIn("decal-draw-price", manager.state.applied)
        self.assertIn("decal-cost-5k", manager.deleted_mods())

    def test_our_old_folder_is_moved_aside_not_deleted(self) -> None:
        write_mod(self.paths.mods_dir, "tdm-rewards-5x", {"author": "KSFA", "patches": [
            {"type": "raw_sql", "sql": "UPDATE master_tdm_rank SET idx = idx;"}]})
        Manager(self.paths)
        self.assertFalse((self.paths.mods_dir / "tdm-rewards-5x").exists())
        self.assertTrue((self.paths.mods_dir / "_retired" / "tdm-rewards-5x" / "mod.json").is_file())

    def test_someone_elses_folder_with_that_name_is_left_alone(self) -> None:
        write_mod(self.paths.mods_dir, "tdm-rewards-5x", {"author": "someone", "patches": [
            {"type": "raw_sql", "sql": "UPDATE master_tdm_rank SET idx = idx;"}]})
        Manager(self.paths)
        self.assertTrue((self.paths.mods_dir / "tdm-rewards-5x").is_dir())

    def test_a_newer_bundled_pack_takes_over_the_old_ones_place_and_history(self) -> None:
        old, new = "LET IT DIE Crossover Content v3.75", "LET IT DIE Crossover Content v3.79"
        pack = {"author": "S3er0i9ng", "patches": [
            {"type": "raw_sql", "sql": "UPDATE master_tdm_rank SET idx = idx;"}]}
        write_mod(self.paths.mods_dir, old, dict(pack, version="3.75"))
        write_mod(self.paths.mods_dir, new, dict(pack, version="3.79"))
        from lid_db_manager.snapshot import snapshot_path

        snapshot_path(self.paths.snapshots_dir, old).write_text(
            json.dumps({"mod_id": old, "entries": []}), encoding="utf-8"
        )
        self._state(
            enabled_mods=["weapon-durability", old],
            applied={old: AppliedRecord(applied_at="then", version="3.75")},
        )
        manager = Manager(self.paths)
        self.assertFalse((self.paths.mods_dir / old).exists())
        self.assertEqual(manager.state.enabled_mods, ["weapon-durability", new])
        self.assertEqual(manager.state.applied[new].version, "3.75",
                         "the old version number is what makes the next save an update")
        self.assertTrue(snapshot_path(self.paths.snapshots_dir, new).is_file())

    def test_every_retired_id_points_at_a_shipped_mod(self) -> None:
        shipped = Path(__file__).resolve().parent.parent / "mods"
        for old_id, retired in migrations.RETIRED.items():
            with self.subTest(old=old_id):
                folder = shipped / retired.new_id
                self.assertTrue(folder.is_dir(), f"{retired.new_id} is not in mods/")
                mod = load_mod_json(folder)
                for key in retired.values:
                    self.assertIsNotNone(mod.setting(key), f"{retired.new_id} has no {key}")


if __name__ == "__main__":
    unittest.main()
