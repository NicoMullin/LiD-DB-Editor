"""Mod packs: a mod list packed up whole, its mods, settings and plugins with it.

What matters: a pack holds every ticked mod's files, its chosen settings and
parts, the plugins those mods go with (and the plugin's own settings file, and
the loader), so it loads to exactly that list on a PC that has none of it; a
different copy of a mod already there is only swapped for the pack's when the
player says so, and is backed up first; a pack that loads to what is there
already writes nothing; the old list-only packs still load; a save can be
handed out in a pack with the mods it is played with, and comes in noted as
played with them; and a pack that holds something it should not is refused.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from fixtures import build_db, write_mod
from test_savelibrary import STEAM, save_bytes

from lid_db_manager import get_mods, modpack, savegame
from lid_db_manager.manager import Manager, ModpackExists
from lid_db_manager.paths import AppPaths

PROJECT = Path(__file__).resolve().parent.parent
BANK_LIMIT = PROJECT / "mods" / "bank-limit"
SKILL_PATCH = [{"type": "update_set", "table": "master_skill",
                "set": {"buy_money": 5}, "where": "id = 'SKL_EXPUP_01'"}]
LOADER_BYTES = b"MZ loader " + get_mods.LOADER_MARK


class PC:
    """One install of the manager, with its own game folder."""

    def __init__(self, root: Path, plugins: bool = True) -> None:
        self.paths = AppPaths(root / "app").ensure()
        self.game = root / "game"
        self.win64 = get_mods.win64(self.game)
        self.plugins = self.win64 / get_mods.PLUGINS
        self.plugins.mkdir(parents=True)
        if plugins:
            (self.win64 / get_mods.LOADER).write_bytes(LOADER_BYTES)
            (self.plugins / "lid-hud.dll").write_bytes(b"MZ hud 0.11.3")
            (self.plugins / "lid-hud.ini").write_text("enemies_left_x=40\n", encoding="utf-8")
        (self.game / "Savedata" / STEAM).mkdir(parents=True)
        (self.game / "Savedata" / STEAM / savegame.SAVE_NAME).write_bytes(save_bytes(kc=1000))
        self.manager = Manager(self.paths)
        self.manager.set_db_path(build_db(self.game / "BrgGame" / "Content" / "masters.db"))
        self.manager.set_game_root_override(self.game)


@unittest.skipUnless(BANK_LIMIT.is_dir(), "needs the Bank Limit mod")
class Packs(unittest.TestCase):
    """A home PC with Bank Limit at x3 (its Splithium part off) and the HUD with its plugin ticked."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.home = PC(self.tmp / "home")
        mods = self.home.paths.mods_dir
        shutil.copytree(BANK_LIMIT, mods / "bank-limit")
        write_mod(mods, "HUD", {"patches": [], "plugin": "lid-hud.dll"})
        write_mod(mods, "cheap", {"patches": SKILL_PATCH}, **{"readme.md": "a file of its own"})
        self.home.manager.rescan()
        m = self.home.manager
        m.set_enabled("bank-limit", True)
        m.set_enabled("HUD", True)
        m.state.mod_settings["bank-limit"] = {"multiplier": 3}
        m.state.disabled_patches["bank-limit"] = ["sp"]
        m.state.mod_settings["cheap"] = {"x": 1}          # a mod that is off: not part of the list

    def away(self, plugins: bool = False) -> PC:
        return PC(self.tmp / "away", plugins=plugins)


class ModPacks(Packs):
    def test_saving_packs_the_mods_their_settings_and_plugins(self) -> None:
        pack = self.home.manager.save_modpack("My run")
        self.assertEqual(pack.path.parent, self.home.paths.modpacks_dir)
        self.assertEqual(pack.choice, {"enabled_mods": ["bank-limit", "HUD"],
                                       "mod_settings": {"bank-limit": {"multiplier": 3}},
                                       "disabled_patches": {"bank-limit": ["sp"]}, "enabled_patches": {}})
        with zipfile.ZipFile(pack.path) as archive:
            names = set(archive.namelist())
            self.assertEqual(archive.read("lid-plugins/lid-hud.dll"), b"MZ hud 0.11.3")
        self.assertTrue({"mods/bank-limit/mod.json", "mods/HUD/mod.json", "lid-plugins/lid-hud.ini",
                         "dinput8.dll", modpack.MANIFEST} <= names)
        self.assertFalse(any(n.startswith("mods/cheap/") for n in names))       # not ticked
        self.assertEqual((pack.plugins, pack.configs, pack.loader), (["lid-hud.dll"], ["lid-hud.ini"], True))
        self.assertEqual([i.name for i in self.home.manager.modpacks()], ["My run"])

    def test_it_loads_to_the_same_list_on_a_pc_with_none_of_it(self) -> None:
        packed = self.home.manager.save_modpack("My run")
        away = self.away()
        away.manager.state.mod_settings["bank-limit"] = {"multiplier": 10}
        away.manager.state.mod_settings["other"] = {"y": 2}
        pack = away.manager.import_modpack(packed.path)
        opened = away.manager.open_modpack(pack.name)
        self.assertEqual(([m.id for m in opened.plan.add], opened.plan.new_plugins, opened.plan.loader),
                         (["bank-limit", "HUD"], ["lid-hud.dll"], True))
        self.assertIn("put the plugin lid-hud.dll in lid-plugins", opened.plan.lines())
        self.assertEqual(away.manager.load_modpack(pack.name, opened=opened), ["bank-limit", "HUD"])
        state = away.manager.state
        self.assertEqual(state.mod_settings, {"bank-limit": {"multiplier": 3}, "other": {"y": 2}})
        self.assertEqual(state.disabled_patches, {"bank-limit": ["sp"]})
        self.assertEqual((away.plugins / "lid-hud.dll").read_bytes(), b"MZ hud 0.11.3")
        self.assertEqual((away.plugins / "lid-hud.ini").read_text(encoding="utf-8"), "enemies_left_x=40\n")
        self.assertEqual(get_mods.loader_state(away.game), "ours")
        self.assertEqual(modpack.folder_digest(away.paths.mods_dir / "bank-limit"),
                         modpack.folder_digest(self.home.paths.mods_dir / "bank-limit"))
        self.assertFalse((away.paths.root / "cache" / "modpack").exists())    # unpacked copy tidied up

    def test_loading_what_is_there_already_writes_nothing(self) -> None:
        self.home.manager.save_modpack("My run")
        self.home.manager.set_enabled("HUD", False)
        opened = self.home.manager.open_modpack("My run")
        self.assertFalse(opened.plan.writes_anything)
        self.home.manager.load_modpack("My run", opened=opened)
        self.assertEqual(self.home.manager.state.enabled_mods, ["bank-limit", "HUD"])

    def test_a_different_copy_here_is_kept_unless_the_player_says(self) -> None:
        packed = self.home.manager.save_modpack("My run")
        away = self.away(plugins=True)
        write_mod(away.paths.mods_dir, "HUD", {"patches": [], "plugin": "lid-hud.dll", "version": "0.9"})
        (away.plugins / "lid-hud.ini").write_text("my own layout\n", encoding="utf-8")
        away.manager.rescan()
        away.manager.import_modpack(packed.path)
        opened = away.manager.open_modpack("My run")
        self.assertEqual([(m.id, have) for m, have in opened.plan.replace], [("HUD", "0.9")])
        self.assertEqual(opened.plan.configs, ["lid-hud.ini"])
        self.assertTrue(opened.plan.replaces_anything)
        away.manager.load_modpack("My run", keep_mine=True, opened=opened)
        self.assertEqual(away.manager.scan.by_id["HUD"].version, "0.9")
        self.assertEqual((away.plugins / "lid-hud.ini").read_text(encoding="utf-8"), "my own layout\n")
        self.assertIn("bank-limit", away.manager.scan.by_id)                  # what was missing came

        away.manager.load_modpack("My run")                                   # the pack's copies this time
        self.assertEqual(away.manager.scan.by_id["HUD"].version, "1.0.0")
        self.assertEqual((away.plugins / "lid-hud.ini").read_text(encoding="utf-8"), "enemies_left_x=40\n")
        kept = list((away.paths.backups_dir / "modpacks").glob("*/mods/HUD/mod.json"))
        self.assertEqual(len(kept), 1)
        self.assertIn('"0.9"', kept[0].read_text(encoding="utf-8"))
        self.assertTrue(list((away.paths.backups_dir / "modpacks").glob("*/lid-plugins/lid-hud.ini")))

    def test_the_old_list_only_packs_still_load_and_are_packed_on_export(self) -> None:
        m = self.home.manager
        m.state.save_modpack("Old", ["cheap"])
        (old,) = [i for i in m.modpacks() if i.name == "Old"]
        self.assertFalse(old.packed)
        self.assertEqual(m.load_modpack("Old"), ["cheap"])
        pack = m.export_modpack("Old", self.tmp / "Old.lidpack")
        self.assertEqual(pack.mod_ids, ["cheap"])
        self.assertEqual(pack.choice["mod_settings"], {"cheap": {"x": 1}})
        m.save_modpack("Old")                                                 # saved again: packed now
        self.assertNotIn("Old", m.state.modpacks)
        self.assertTrue(m.modpack("Old").packed)

    def test_a_different_pack_of_the_same_name(self) -> None:
        packed = self.home.manager.save_modpack("My run")
        away = self.away()
        away.manager.import_modpack(packed.path)
        away.manager.import_modpack(packed.path)                              # the same file: fine
        other = self.tmp / "other.lidpack"
        self.home.manager.set_enabled("HUD", False)
        self.home.manager._write_pack(other, "My run", self.home.manager.mod_choice())
        with self.assertRaises(ModpackExists):
            away.manager.import_modpack(other)
        away.manager.import_modpack(other, replace=True)
        self.assertEqual(away.manager.modpack("My run").enabled, ["bank-limit"])
        self.assertEqual(len(list(away.paths.modpacks_dir.glob("*.lidpack"))), 1)

    def test_deleting(self) -> None:
        pack = self.home.manager.save_modpack("My run")
        self.home.manager.delete_modpack("My run")
        self.assertFalse(pack.path.exists())
        self.assertEqual(self.home.manager.modpacks(), [])

    def test_a_save_handed_out_with_its_mods(self) -> None:
        m = self.home.manager
        entry = m.add_live_save(STEAM, "Main")
        m.library.remember_mods(entry.id, m.mod_choice())
        pack = m.pack_save(entry.id, self.tmp / "Main with mods.lidpack")
        self.assertEqual(pack.save, {"nickname": "Main"})
        self.assertEqual(modpack.save_bytes(pack), save_bytes(kc=1000))

        away = self.away()
        kept = away.manager.import_modpack(pack.path)
        away.manager.load_modpack(kept.name)
        friend = away.manager.keep_pack_save(kept.name, STEAM, "From a friend")
        self.assertEqual(friend.mods["enabled_mods"], ["bank-limit", "HUD"])
        self.assertEqual(away.manager.save_mods(friend.id)["mod_settings"], {"bank-limit": {"multiplier": 3}})
        self.assertEqual(away.manager.library.get(friend.id).save.read_bytes(), save_bytes(kc=1000))

    def test_a_save_whose_mods_are_not_known_is_not_packed(self) -> None:
        other = self.tmp / "o.sav"
        other.write_bytes(save_bytes(kc=5))
        entry = self.home.manager.import_save(other, STEAM, "Imported")
        with self.assertRaises(modpack.PackError) as caught:
            self.home.manager.pack_save(entry.id, self.tmp / "x.lidpack")
        self.assertIn("not known which mods", str(caught.exception))

    def test_a_pack_that_holds_what_it_should_not_is_refused(self) -> None:
        good = self.home.manager.save_modpack("My run").path
        with zipfile.ZipFile(good) as archive:
            manifest = json.loads(archive.read(modpack.MANIFEST))

        def repack(name: str, data: dict, extra: dict | None = None) -> Path:
            path = self.tmp / name
            with zipfile.ZipFile(good) as src, zipfile.ZipFile(path, "w") as out:
                for item in src.infolist():
                    if item.filename != modpack.MANIFEST:
                        out.writestr(item, src.read(item))
                out.writestr(modpack.MANIFEST, json.dumps(data))
                for member, body in (extra or {}).items():
                    out.writestr(member, body)
            return path

        lying = repack("lying.lidpack", {**manifest, "mods": manifest["mods"] + [
            {"id": "Ghost", "version": "1"}]})
        with self.assertRaises(modpack.PackError):
            modpack.read(lying)
        escaping = repack("escaping.lidpack", manifest, {"../outside.txt": "x"})
        with self.assertRaises(modpack.PackError):
            modpack.unpack(modpack.read(escaping), self.tmp / "unpacked")
        self.assertFalse((self.tmp / "outside.txt").exists())
        newer = repack("newer.lidpack", {**manifest, "format": 99})
        with self.assertRaises(modpack.PackError) as caught:
            modpack.read(newer)
        self.assertIn("update the manager", str(caught.exception))


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog, QMessageBox

    HAVE_QT = True
except ImportError:  # pragma: no cover - depends on the environment
    HAVE_QT = False


@unittest.skipUnless(HAVE_QT and BANK_LIMIT.is_dir(), "needs PySide6 and the Bank Limit mod")
class TheWindow(Packs):
    """Save as..., a pack dropped on another PC's window, Load, and Share with its mods."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def window(self, pc: PC):
        from test_ui import destroy
        from lid_db_manager.ui.main_window import MainWindow

        for name in ("_first_run_checks", "check_for_updates"):     # no first-run boxes, nothing online
            patcher = mock.patch.object(MainWindow, name)
            patcher.start()
            self.addCleanup(patcher.stop)
        window = MainWindow(pc.manager)
        self.addCleanup(destroy, window)
        self.wait(window)
        return window

    def wait(self, window, seconds: float = 30.0) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            QCoreApplication.processEvents()
            if window.task is None and not window._install_queue:
                break
            time.sleep(0.01)
        for _ in range(6):
            QCoreApplication.processEvents()

    def test_saved_dropped_on_another_pc_and_loaded(self) -> None:
        home = self.window(self.home)
        asked: list = []
        with mock.patch.object(QInputDialog, "getText", return_value=("My run", True)), \
                mock.patch.object(QMessageBox, "information", side_effect=lambda *a: asked.append(a[2])):
            home.save_modpack()
            self.wait(home)
        self.assertEqual(home.modpack_box.currentData(), "My run")
        self.assertIn("Packed \"My run\": 2 mod(s) with their settings, 1 plugin(s)", asked[-1])

        away_pc = self.away()
        away = self.window(away_pc)
        questions: list = []
        with mock.patch.object(QMessageBox, "question",
                               side_effect=lambda *a: questions.append(a[2]) or QMessageBox.StandardButton.Yes), \
                mock.patch.object(type(away), "_ask_modpack_load", return_value="pack") as ask, \
                mock.patch.object(QMessageBox, "information"):
            away._install_queue.append(self.home.manager.modpack("My run").pack.path)
            away._drain_install_queue()
            self.wait(away)
            self.wait(away)
        self.assertIn("Load it now?", questions[-1])
        self.assertTrue(ask.called)
        self.assertEqual(away_pc.manager.state.enabled_mods, ["bank-limit", "HUD"])
        self.assertTrue(away.unsaved)
        self.assertEqual((away_pc.plugins / "lid-hud.dll").read_bytes(), b"MZ hud 0.11.3")

    def test_sharing_a_save_from_the_saves_tab(self) -> None:
        home = self.window(self.home)
        m = self.home.manager
        entry = m.add_live_save(STEAM, "Main")
        m.library.remember_mods(entry.id, m.mod_choice())
        tab = home.saves_tab
        tab.refresh()
        tab._select_after_refresh(entry.id)
        self.assertTrue(tab.share_button.isEnabled())
        target = self.tmp / "Main.lidpack"
        with mock.patch.object(QFileDialog, "getSaveFileName", return_value=(str(target), "")), \
                mock.patch.object(QMessageBox, "information") as told:
            tab.share()
        self.assertIn("with its 2 mod(s)", told.call_args.args[2])
        self.assertEqual(modpack.read(target).save, {"nickname": "Main"})


if __name__ == "__main__":
    unittest.main()
