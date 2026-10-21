"""The mod list noted beside the save in the game, so it goes wherever the save goes.

What matters: the note says which save it is about and is never taken for
another save's; it is rewritten every time the mod list is applied and every
time a save is switched in; a new game starts with none; and a second PC that
gets the save (two managers sharing one game folder here, as Steam Cloud
shares it) sees the save's mods differ from its own and can take them.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fixtures import build_db, write_mod
from test_savelibrary import STEAM, document, save_bytes

from lid_db_manager import savegame, savelink
from lid_db_manager.manager import Manager
from lid_db_manager.paths import AppPaths

SKILL_PATCH = [{"type": "update_set", "table": "master_skill",
                "set": {"buy_money": 5}, "where": "id = 'SKL_EXPUP_01'"}]
OTHER_PATCH = [{"type": "update_set", "table": "master_skill",
                "set": {"buy_money": 7}, "where": "id = 'SKL_EXPUP_01'"}]


class TheNote(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.save = Path(self._tmp.name) / STEAM / savegame.SAVE_NAME
        self.save.parent.mkdir()
        self.save.write_bytes(save_bytes(kc=10))
        self.mods = {"enabled_mods": ["HUD", "Timers"], "mod_settings": {"Timers": {"x": 2}}}

    def test_it_says_which_save_and_survives_play(self) -> None:
        link = savelink.write(self.save, self.mods, {"HUD": "0.2.1"}, "LID DB Mod Manager 0.12.9")
        self.assertEqual(self.save.with_name("brggame.sav.lidmm.json"), savelink.link_path(self.save))
        self.assertTrue(savelink.LINK_NAME.startswith("brggame.sav."))     # Steam Cloud carries it
        self.save.write_bytes(save_bytes(kc=99999, played=999999))          # played on
        again = savelink.read(self.save)
        self.assertEqual((again.save, again.mods, again.versions), (link.save, self.mods, {"HUD": "0.2.1"}))
        self.assertEqual(savelink.check(self.save, self.mods, {"HUD": "0.2.1", "Timers": "0.3.2"}).state,
                         "matches")

    def test_it_tells_what_differs(self) -> None:
        savelink.write(self.save, self.mods, {"HUD": "0.2.0", "Timers": "0.3.2"}, "x")
        check = savelink.check(self.save, {"enabled_mods": ["HUD"]}, {"HUD": "0.2.1"})
        self.assertEqual(check.state, "differs")
        self.assertEqual(check.missing, ("Timers",))
        self.assertEqual(check.versions, (("HUD", "0.2.0", "0.2.1"),))
        # the same mods but another setting is a different list too
        other = dict(self.mods, mod_settings={"Timers": {"x": 3}})
        self.assertEqual(savelink.check(self.save, other, {"HUD": "1", "Timers": "1"}).state, "differs")

    def test_a_note_about_another_save_is_never_used(self) -> None:
        savelink.write(self.save, self.mods, {}, "x")
        doc = document()
        doc["user"]["created"] = 1600000000                                # another save altogether
        self.save.write_bytes(savegame.write_container(2, json.dumps(doc).encode()))
        self.assertEqual(savelink.check(self.save, {}, {}).state, "other")

    def test_no_note_or_a_broken_one(self) -> None:
        self.assertEqual(savelink.check(self.save, {}, {}).state, "none")
        savelink.link_path(self.save).write_text("{not json", encoding="utf-8")
        self.assertEqual(savelink.check(self.save, {}, {}).state, "none")


class TwoPCs(unittest.TestCase):
    """Two installs of the manager, one game folder: the save moves between them as with Steam Cloud."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.game = self.root / "game"
        self.folder = self.game / "Savedata" / STEAM
        self.folder.mkdir(parents=True)
        self.save = self.folder / savegame.SAVE_NAME
        self.save.write_bytes(save_bytes(kc=1000))
        db = build_db(self.game / "BrgGame" / "Content" / "masters.db")
        self.pc1, self.pc2 = (self.install(name, db) for name in ("pc1", "pc2"))

    def install(self, name: str, db: Path) -> Manager:
        paths = AppPaths(self.root / name).ensure()
        write_mod(paths.mods_dir, "Cheap EXP", {"patches": SKILL_PATCH})
        write_mod(paths.mods_dir, "Other", {"patches": OTHER_PATCH})
        manager = Manager(paths)
        manager.set_db_path(db)
        manager.set_game_root_override(self.game)
        manager.rescan()
        return manager

    def apply(self, manager: Manager, *mod_ids: str) -> None:
        for mod_id in ("Cheap EXP", "Other"):
            manager.set_enabled(mod_id, mod_id in mod_ids)
        self.assertTrue(manager.save_mod_list().ok)

    def state(self, manager: Manager) -> str:
        (check,) = manager.save_mod_checks()
        return check.state

    def test_the_save_brings_its_mods_to_the_other_pc(self) -> None:
        self.apply(self.pc1, "Cheap EXP")
        self.assertEqual(self.state(self.pc1), "matches")
        self.assertEqual(savelink.read(self.save).enabled, ["Cheap EXP"])
        self.apply(self.pc2, "Other")                      # PC 2 had its own list
        # ...which it now writes beside the save, so back on PC 1 it is PC 1 that differs
        self.assertEqual(self.state(self.pc1), "differs")
        (check,) = self.pc1.save_mod_checks()
        self.assertEqual(check.link.enabled, ["Other"])
        self.assertEqual(self.pc1.use_mod_choice(check.link.mods), [])
        self.assertTrue(self.pc1.save_mod_list().ok)
        self.assertEqual(self.state(self.pc1), "matches")
        self.assertEqual(self.state(self.pc2), "matches")

    def test_keeping_the_save_on_the_other_pc_takes_its_noted_mods(self) -> None:
        self.apply(self.pc1, "Cheap EXP")
        entry = self.pc2.add_live_save(STEAM, "From PC 1")
        self.assertEqual(entry.mods["enabled_mods"], ["Cheap EXP"])

    def test_switching_and_new_games_rewrite_the_note(self) -> None:
        self.apply(self.pc1, "Cheap EXP")
        main = self.pc1.add_live_save(STEAM, "Main")
        other_file = self.root / "other.sav"
        other_file.write_bytes(save_bytes(kc=5))
        other = self.pc1.import_save(other_file, STEAM, "No mods")
        self.pc1.library.remember_mods(other.id, {"enabled_mods": []})
        self.pc1.switch_save(other.id)
        self.assertEqual(savelink.read(self.save).enabled, [])
        self.assertEqual(self.state(self.pc1), "differs")          # until Save Mod List
        self.pc1.switch_save(main.id)
        self.assertEqual(savelink.read(self.save).enabled, ["Cheap EXP"])
        self.assertEqual(self.state(self.pc1), "matches")
        self.pc1.start_new_save(STEAM, "Rookie", "New")
        self.assertEqual(self.state(self.pc1), "none")             # nothing known about a new game yet
        self.assertTrue(self.pc1.save_mod_list().ok)
        self.assertEqual(self.state(self.pc1), "matches")

    def test_a_mod_list_already_in_the_game_is_not_applied_again(self) -> None:
        self.apply(self.pc1, "Cheap EXP")
        noted = savelink.read(self.save).mods
        self.assertEqual(self.pc1.mods_in_place(noted), "applied")
        self.pc1.set_enabled("Other", True)                 # the ticks moved, nothing saved
        self.assertEqual(self.pc1.mods_in_place(noted), "applied")
        self.assertEqual(self.pc1.mods_in_place(self.pc1.mod_choice()), "ticked")
        self.assertEqual(self.pc1.mods_in_place({"enabled_mods": ["Other"]}), "")

    def test_a_replaced_database_needs_the_mods_saved_again(self) -> None:
        self.apply(self.pc1, "Cheap EXP")
        noted = savelink.read(self.save).mods
        db = self.game / "BrgGame" / "Content" / "masters.db"
        con = sqlite3.connect(db)                           # as a game update or Steam's check would
        con.execute("CREATE TABLE stand_in_for_an_update (x)")
        con.commit()
        con.close()
        self.assertEqual(self.pc1.mods_in_place(noted), "ticked")

    def test_reverting_a_mod_takes_it_off_the_note(self) -> None:
        self.apply(self.pc1, "Cheap EXP", "Other")
        self.pc1.revert(["Other"])
        self.assertEqual(savelink.read(self.save).enabled, ["Cheap EXP"])
        self.assertEqual(self.state(self.pc1), "matches")


if __name__ == "__main__":
    unittest.main()
