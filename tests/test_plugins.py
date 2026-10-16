"""Plugin DLLs switched on and off with the mods that name them (plugins.py).

The Plugin Loader loads every .dll in lid-plugins and skips one renamed to
.dll.off. What matters: switching only ever renames (never deletes), nothing
is renamed while the game runs, a plugin follows the mods that name it on
every save (on while any of them is on), a plugin can have a mod that is
nothing but that line, and an update through Get mods keeps a switched-off
plugin off.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fixtures import build_db, write_mod
from test_get_mods import Base as GetModsBase, package_zip

from lid_db_manager import asset_runner
from lid_db_manager import get_mods as G
from lid_db_manager import plugins as P
from lid_db_manager.errors import ModLoadError
from lid_db_manager.manager import Manager
from lid_db_manager.mod_loader import load_mod_folder
from lid_db_manager.paths import AppPaths

SKILL_PATCH = [{"type": "update_set", "table": "master_skill",
                "set": {"buy_money": 5}, "where": "id = 'SKL_EXPUP_01'"}]


class GameRunning:
    def __enter__(self):
        self.original = asset_runner.game_lock_reason
        asset_runner.game_lock_reason = lambda db_path=None: "LET IT DIE is running."

    def __exit__(self, *exc):
        asset_runner.game_lock_reason = self.original


class Folder(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.game = Path(self._tmp.name) / "game"
        self.dir = P.folder(self.game)
        self.dir.mkdir(parents=True)
        (self.dir / "lid-hud.dll").write_bytes(b"MZ hud")
        (self.dir / "lid-offlinefixes.dll.off").write_bytes(b"MZ fixes")
        (self.dir / "PUT-PLUGINS-HERE.txt").write_text("not a plugin")

    def names(self) -> list[str]:
        return sorted(p.name for p in self.dir.iterdir())

    def test_every_plugin_on_or_off(self) -> None:
        self.assertEqual(P.present(self.game), {"lid-hud.dll": ("lid-hud.dll", True),
                                                "lid-offlinefixes.dll": ("lid-offlinefixes.dll", False)})

    def test_switching_only_renames(self) -> None:
        self.assertTrue(P.switch(self.game, "lid-hud.dll", False))
        self.assertTrue(P.switch(self.game, "lid-offlinefixes.dll", True))
        self.assertEqual(self.names(), ["PUT-PLUGINS-HERE.txt", "lid-hud.dll.off", "lid-offlinefixes.dll"])
        self.assertEqual((self.dir / "lid-hud.dll.off").read_bytes(), b"MZ hud")
        self.assertFalse(P.switch(self.game, "lid-hud.dll", False))       # already off

    def test_both_copies_there_counts_the_loaded_one_and_switching_tidies_up(self) -> None:
        (self.dir / "lid-hud.dll.off").write_bytes(b"MZ old")
        self.assertEqual(P.present(self.game)["lid-hud.dll"], ("lid-hud.dll", True))
        P.switch(self.game, "lid-hud.dll", False)
        self.assertEqual((self.dir / "lid-hud.dll.off").read_bytes(), b"MZ hud")
        self.assertFalse((self.dir / "lid-hud.dll").exists())

    def test_nothing_is_renamed_while_the_game_runs(self) -> None:
        with GameRunning(), self.assertRaises(P.PluginError) as caught:
            P.switch(self.game, "lid-hud.dll", False)
        self.assertIn("running", str(caught.exception))
        self.assertTrue((self.dir / "lid-hud.dll").is_file())

    def test_only_a_dll_name_is_taken(self) -> None:
        for bad in ("..\\dinput8.dll", "lid-hud", "a/b.dll"):
            with self.assertRaises(P.PluginError):
                P.switch(self.game, bad, False)


class TheModField(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.mods = Path(self._tmp.name)

    def write(self, plugin, patches=SKILL_PATCH) -> Path:
        data = {"plugin": plugin}
        if patches is not None:
            data["patches"] = patches
        return write_mod(self.mods, "Timers", data)

    def test_a_mod_names_its_plugin(self) -> None:
        mod = load_mod_folder(self.write("lid-timers.dll"))
        self.assertEqual(mod.plugins, ["lid-timers.dll"])
        self.assertIn("plugin: lid-timers.dll", mod.affects_label())

    def test_or_several(self) -> None:
        mod = load_mod_folder(self.write(["lid-deathmetals.dll", "lid-stewpull.dll", "LID-STEWPULL.dll"]))
        self.assertEqual(mod.plugins, ["lid-deathmetals.dll", "lid-stewpull.dll"])

    def test_a_plugin_alone_is_a_mod(self) -> None:
        mod = load_mod_folder(self.write("lid-offlinefixes.dll", patches=None))
        self.assertEqual((mod.plugins, mod.patches), (["lid-offlinefixes.dll"], []))

    def test_without_a_plugin_patches_are_still_needed(self) -> None:
        with self.assertRaises(ModLoadError):
            load_mod_folder(write_mod(self.mods, "empty", {"patches": []}))

    def test_a_name_that_is_not_a_dll_is_refused(self) -> None:
        for bad in ("lid-timers", "..\\dinput8.dll", "C:/x.dll", 7):
            with self.subTest(bad=bad), self.assertRaises(ModLoadError):
                load_mod_folder(self.write(bad))

    def test_the_timers_mod_names_lid_timers(self) -> None:
        timers = Path(__file__).resolve().parents[2] / "LiD Timers" / "mods" / "Timers"
        if not timers.is_dir():
            self.skipTest("the LiD Timers repository is not next to this one")
        self.assertEqual(load_mod_folder(timers).plugins, ["lid-timers.dll"])


class SavingSwitchesThePlugins(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.paths = AppPaths(root / "app").ensure()
        self.game = root / "game"
        db = build_db(self.game / "BrgGame" / "Content" / "masters.db")
        (self.game / "BrgGame" / "CookedPCConsole").mkdir(parents=True)
        self.dir = P.folder(self.game)
        self.dir.mkdir(parents=True)
        for name in ("lid-timers.dll.off", "lid-offlinefixes.dll", "lid-stewpull.dll", "lid-other.dll"):
            (self.dir / name).write_bytes(b"MZ")
        write_mod(self.paths.mods_dir, "Timers", {"plugin": "lid-timers.dll", "patches": SKILL_PATCH})
        write_mod(self.paths.mods_dir, "Offline Fixes", {"plugin": "lid-offlinefixes.dll"})
        write_mod(self.paths.mods_dir, "Stew Multi Pull", {"plugin": "lid-stewpull.dll", "patches": SKILL_PATCH})
        write_mod(self.paths.mods_dir, "Death Metals", {"plugin": ["lid-stewpull.dll"], "patches": SKILL_PATCH})
        self.manager = Manager(self.paths)
        self.manager.set_db_path(db)

    def on(self, name: str) -> bool:
        return (self.dir / name).is_file()

    def save(self, **enabled) -> None:
        for mod_id, on in enabled.items():
            self.manager.set_enabled(mod_id.replace("_", " "), on)
        report = self.manager.save_mod_list()
        self.assertTrue(report.ok, report.error)

    def test_a_mod_switches_its_plugin_on_and_off_again(self) -> None:
        self.save(Timers=True)
        self.assertTrue(self.on("lid-timers.dll"))
        self.save(Timers=False)
        self.assertTrue(self.on("lid-timers.dll.off"))
        self.assertFalse(self.on("lid-timers.dll"))
        self.assertTrue(self.on("lid-other.dll"))           # a plugin no mod names is left alone

    def test_a_plugins_own_mod_is_its_switch(self) -> None:
        self.save(Offline_Fixes=False)
        self.assertTrue(self.on("lid-offlinefixes.dll.off"))
        self.save(Offline_Fixes=True)
        self.assertTrue(self.on("lid-offlinefixes.dll"))

    def test_a_shared_plugin_stays_on_while_any_of_its_mods_is(self) -> None:
        self.save(Stew_Multi_Pull=True, Death_Metals=True)
        self.save(Stew_Multi_Pull=False)
        self.assertTrue(self.on("lid-stewpull.dll"))         # Death Metals still wants it
        self.save(Death_Metals=False)
        self.assertTrue(self.on("lid-stewpull.dll.off"))

    def test_reverting_the_mod_switches_its_plugin_off(self) -> None:
        self.save(Timers=True)
        self.manager.revert(["Timers"])
        self.assertTrue(self.on("lid-timers.dll.off"))

    def test_a_missing_plugin_is_only_mentioned(self) -> None:
        (self.dir / "lid-timers.dll.off").unlink()
        self.save(Timers=True)
        self.assertFalse(any(self.dir.glob("lid-timers*")))


class GetModsKeepsItOff(GetModsBase):
    def setUp(self) -> None:
        super().setUp()
        self.github.publish(G.LOADER_OFFER, "v1.0.0", package_zip("Plugin Loader", "1.0.0", loader=True))
        self.get(G.LOADER_OFFER)
        self.entry = G.offer("offlinefixes")

    def publish(self, version: str) -> None:
        self.github.publish(self.entry, f"v{version}", package_zip(
            "Offline Fixes", version, mods=["Offline Fixes"], plugins=["lid-offlinefixes.dll"]))

    def test_an_update_of_a_switched_off_plugin_stays_off(self) -> None:
        self.publish("0.1.0")
        self.get(self.entry)
        P.switch(self.game, "lid-offlinefixes.dll", False)
        self.assertTrue(G.installed(self.entry, self.mods, self.game).plugin)
        self.publish("0.2.0")
        _, done = self.get(self.entry)
        self.assertFalse((self.plugins() / "lid-offlinefixes.dll").exists())
        self.assertEqual((self.plugins() / "lid-offlinefixes.dll.off").read_bytes(),
                         b"MZ lid-offlinefixes.dll 0.2.0")
        self.assertIn("still switched off", done[-1])

    def test_an_old_parked_copy_beside_a_loaded_one_is_cleared(self) -> None:
        self.publish("0.1.0")
        self.get(self.entry)
        (self.plugins() / "lid-offlinefixes.dll.off").write_bytes(b"MZ ancient")
        self.publish("0.2.0")
        self.get(self.entry)
        self.assertFalse((self.plugins() / "lid-offlinefixes.dll.off").exists())
        self.assertEqual((self.plugins() / "lid-offlinefixes.dll").read_bytes(),
                         b"MZ lid-offlinefixes.dll 0.2.0")


if __name__ == "__main__":
    unittest.main()
