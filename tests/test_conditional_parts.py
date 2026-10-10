"""Parts a setting switches on and off ("when"), and mods that need a newer manager.

A setting can already change a number or a choice inside a part. Some parts
have nothing to put a placeholder in - a package patch is one - so a part can
instead say when it applies at all:

    {"type": "tfc_installer", "source": "tfc/vip", "when": {"vip": true}}

Off means the part is not there: not applied, not in the conflict check, and
taken away again if it was applied before.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fixtures import build_db, query, write_mod

from lid_db_manager import __version__
from lid_db_manager.errors import ModLoadError
from lid_db_manager.manager import Manager
from lid_db_manager.mod import load_mod_json
from lid_db_manager.paths import AppPaths

VIP = {"id": "vip", "label": "VIP in Death Metals", "type": "toggle", "default": False}
PRICE = {"id": "price", "label": "Price", "type": "integer", "default": 33, "min": 1, "max": 999}
COST = {"id": "cost", "label": "Cost", "type": "choice", "default": "dm",
        "options": [{"value": "dm", "label": "Death Metals"}, {"value": "kc", "label": "Kill Coins"}]}

ALWAYS = {"type": "raw_sql", "id": "always",
          "sql": "UPDATE master_skill SET val0 = 77 WHERE id = 'SKL_EXPUP_01';"}
WHEN_VIP = {"type": "raw_sql", "id": "vip-price", "when": {"vip": True},
            "sql": "UPDATE master_skill SET buy_money = {{price}} WHERE id = 'SKL_POWER_01';"}


def a_vip_mod(mods_dir: Path, mod_id: str = "vip-test", **extra) -> Path:
    return write_mod(mods_dir, mod_id, {
        "description": "test",
        "apply": "diff",
        "settings": [VIP, PRICE],
        "patches": [WHEN_VIP, ALWAYS],
        **extra,
    })


class Loading(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.mods = Path(self._tmp.name) / "mods"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_off_by_default_leaves_the_part_out(self) -> None:
        mod = load_mod_json(a_vip_mod(self.mods))
        self.assertEqual(mod.load_warnings, [])
        self.assertEqual([p.key for p in mod.patches], ["always"])

    def test_switching_it_on_brings_the_part_back_filled_in(self) -> None:
        mod = load_mod_json(a_vip_mod(self.mods)).with_settings({"vip": True, "price": 40})
        self.assertEqual([p.key for p in mod.patches], ["vip-price", "always"])
        self.assertIn("buy_money = 40", mod.patches[0].sql_text())

    def test_a_part_keeps_its_place_number(self) -> None:
        # No ids: the always-on part is the second, and stays "#2" either way.
        no_ids = [{k: v for k, v in WHEN_VIP.items() if k != "id"},
                  {k: v for k, v in ALWAYS.items() if k != "id"}]
        mod = load_mod_json(write_mod(self.mods, "keys", {
            "settings": [VIP, PRICE], "patches": no_ids}))
        self.assertEqual([p.key for p in mod.patches], ["#2"])
        self.assertEqual([p.key for p in mod.with_settings({"vip": 1}).patches], ["#1", "#2"])

    def test_a_setting_used_only_in_when_is_not_unused(self) -> None:
        mod = load_mod_json(write_mod(self.mods, "only-when", {
            "settings": [VIP], "patches": [{**ALWAYS, "when": {"vip": True}}]}))
        self.assertEqual(mod.load_warnings, [])

    def test_a_choice_and_a_list_of_values(self) -> None:
        folder = write_mod(self.mods, "choice", {
            "settings": [COST],
            "patches": [{**ALWAYS, "id": "dm", "when": {"cost": "dm"}},
                        {**WHEN_VIP, "id": "either", "when": {"cost": ["dm", "kc"]},
                         "sql": "SELECT 1;"}]})
        mod = load_mod_json(folder)
        self.assertEqual([p.key for p in mod.patches], ["dm", "either"])
        self.assertEqual([p.key for p in mod.with_settings({"cost": "kc"}).patches], ["either"])

    def test_naming_two_settings_needs_both(self) -> None:
        folder = write_mod(self.mods, "both", {
            "settings": [VIP, COST],
            "patches": [{**ALWAYS, "when": {"vip": True, "cost": "kc"}}, {**ALWAYS, "id": "x"}]})
        mod = load_mod_json(folder)
        self.assertEqual(len(mod.with_settings({"vip": 1, "cost": "dm"}).patches), 1)
        self.assertEqual(len(mod.with_settings({"vip": 1, "cost": "kc"}).patches), 2)

    def test_mistakes_are_refused(self) -> None:
        cases = {
            "unknown-setting": {"settings": [VIP], "patches": [{**ALWAYS, "when": {"nope": True}}]},
            "bad-value": {"settings": [COST], "patches": [{**ALWAYS, "when": {"cost": "gold"}}]},
            "out-of-range": {"settings": [PRICE], "patches": [{**ALWAYS, "when": {"price": 5000}}]},
            "empty": {"settings": [VIP], "patches": [{**ALWAYS, "when": {}}]},
            "not-an-object": {"settings": [VIP], "patches": [{**ALWAYS, "when": "vip"}]},
            "no-settings": {"patches": [{**ALWAYS, "when": {"vip": True}}]},
        }
        for name, data in cases.items():
            with self.subTest(name), self.assertRaises(ModLoadError):
                load_mod_json(write_mod(self.mods, name, data))

    def test_a_broken_part_that_is_off_is_still_found(self) -> None:
        folder = write_mod(self.mods, "broken-off", {
            "settings": [VIP],
            "patches": [{"type": "raw_sql", "when": {"vip": True}}, ALWAYS]})
        with self.assertRaises(ModLoadError):
            load_mod_json(folder)


class NeedsANewerManager(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.mods = Path(self._tmp.name) / "mods"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def load(self, version: str):
        return load_mod_json(write_mod(self.mods, f"v{version}", {
            "min_manager_version": version, "patches": [ALWAYS]}))

    def test_this_version_or_older_loads(self) -> None:
        self.load(__version__)
        self.load("0.1.0")

    def test_a_newer_version_is_refused_with_the_reason(self) -> None:
        major, minor, *_ = (int(p) for p in __version__.split("."))
        newer = f"{major}.{minor + 1}.0"
        with self.assertRaises(ModLoadError) as caught:
            self.load(newer)
        self.assertIn(newer, str(caught.exception))
        self.assertIn(__version__, str(caught.exception))

    def test_not_a_version_is_refused(self) -> None:
        with self.assertRaises(ModLoadError):
            self.load("soon")


class ThroughTheManager(unittest.TestCase):
    """On writes the part's rows; off takes them away and leaves the rest."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.paths = AppPaths(self.root).ensure()
        self.db = build_db(self.root / "game" / "masters.db")
        self.stock = self.prices()
        a_vip_mod(self.paths.mods_dir)
        self.manager = Manager(self.paths)
        self.manager.set_db_path(self.db)
        self.manager.set_enabled("vip-test", True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def prices(self):
        return dict(query(self.db, "SELECT id, buy_money FROM master_skill"))

    def val0(self, skill: str):
        return query(self.db, f"SELECT val0 FROM master_skill WHERE id = '{skill}'")[0][0]

    def choose(self, **values) -> None:
        for setting_id, value in values.items():
            self.manager.set_mod_setting("vip-test", setting_id, value)
        self.assertTrue(self.manager.save_mod_list().ok)

    def test_off_applies_only_the_other_part(self) -> None:
        self.assertTrue(self.manager.save_mod_list().ok)
        self.assertEqual(self.prices(), self.stock)
        self.assertEqual(self.val0("SKL_EXPUP_01"), 77)

    def test_on_then_off_then_on(self) -> None:
        self.choose(vip=True, price=40)
        self.assertEqual(self.prices()["SKL_POWER_01"], 40)
        self.choose(price=55)
        self.assertEqual(self.prices()["SKL_POWER_01"], 55)
        self.choose(vip=False)
        self.assertEqual(self.prices(), self.stock)
        self.assertEqual(self.val0("SKL_EXPUP_01"), 77)
        self.choose(vip=True)
        self.assertEqual(self.prices()["SKL_POWER_01"], 55)

    def test_switching_the_mod_off_leaves_stock(self) -> None:
        self.choose(vip=True)
        self.manager.set_enabled("vip-test", False)
        self.assertTrue(self.manager.save_mod_list().ok)
        self.assertEqual(self.prices(), self.stock)


if __name__ == "__main__":
    unittest.main()
