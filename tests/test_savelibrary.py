"""The save library and the save summary.

What matters: a save goes into the library and back into the game as the
same bytes, its .bak travelling with it; switching first puts what the game
added back into the save being left; a save in the game that is not in the
library is never replaced or taken out; nothing in the game is touched while
it runs; and the summary reads the numbers a player knows from the game.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lid_db_manager import newsave, savegame, savelibrary
from lid_db_manager.savelibrary import BAK_NAME, Library, LibraryError
from lid_db_manager.savesummary import Names, play_time_text, read_summary, summarize

STEAM = "76561190000000000"
UID = 1001111
REAL_DB = Path(__file__).resolve().parent.parent / "LiD Vanilla DB" / "5.0.4.2" / "masters.db"
REAL_SAVES = Path(os.environ.get(
    "LID_SAVEDATA", r"C:\Program Files (x86)\Steam\steamapps\common\LET IT DIE\Savedata"))


def document(kc: int = 1000, played: int = 7260) -> dict:
    cid = "4230403a-5f88-4d5b-a9e6-892db0b0ae6b"
    return {
        "user": {"uid": UID, "nm": "Fighter", "free_medal": 30, "paid_medal": 3, "created": 1740280529},
        "soul": {
            "uid": UID, "free_money": kc, "paid_money": 0, "spirit": 200, "bloodnium_point": 40,
            "recycle_point": 9, "rank": 12, "safe_level": 7, "spirit_tank_level": 8,
            "chr": {"chrs": {str(UID): [{"uid": UID, "cid": cid, "type": "COL", "grade": 4,
                                          "limit_break": 1, "name": "Leila", "state": "USE"}]}},
            "partresearch": {"user": [
                {"ptid": "PT_ARM_WP001_001", "lvl": 1, "research_type": "FINISHED"},
                {"ptid": "PT_ARM_WP001_001", "lvl": 2, "research_type": "FINISHED"},
                {"ptid": "PT_FAN_HEAD_007", "lvl": 1, "research_type": "MAP"},
            ]},
        },
        "bodyuser": {str(UID): [{"uid": UID, "cid": cid, "lvl": 118, "hp": 20, "str": 20, "dex": 20,
                                 "vit": 20, "stm": 20, "luk": 20, "stm_bonus": 1}]},
        "playlog": {"base": {"total_play_time": played, "max_floor": 27},
                    "died": {"total_died_cnt": 4}, "kill": {"total_enemy_cnt": "321"}},
    }


def save_bytes(**kw) -> bytes:
    return savegame.write_container(2, json.dumps(document(**kw), separators=(",", ":")).encode())


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.game = root / "game"
        self.folder = self.game / "Savedata" / STEAM
        self.folder.mkdir(parents=True)
        self.running = ""
        self.lib = Library(root / "manager", lock_reason=lambda: self.running)
        self.put(save_bytes(kc=1000), bak=save_bytes(kc=900))

    def put(self, data: bytes, bak: bytes | None = None) -> None:
        (self.folder / savegame.SAVE_NAME).write_bytes(data)
        if bak is not None:
            (self.folder / BAK_NAME).write_bytes(bak)

    def in_game(self) -> bytes:
        return (self.folder / savegame.SAVE_NAME).read_bytes()

    def kc_in_game(self) -> int:
        return read_summary(self.folder / savegame.SAVE_NAME).kill_coins


class TheLibrary(Base):
    def test_adding_the_game_save_keeps_its_bytes_bak_and_mods(self) -> None:
        mods = {"enabled_mods": ["HUD"], "mod_settings": {"HUD": {"x": 1}}, "stray": 1}
        entry = self.lib.add_live(self.game, STEAM, "  Main run ", mods)
        self.assertEqual(entry.nickname, "Main run")
        self.assertEqual(entry.save.read_bytes(), self.in_game())
        self.assertEqual(entry.bak.read_bytes(), save_bytes(kc=900))
        live = self.lib.live_for(self.game, STEAM)
        self.assertTrue(live.known and live.in_sync)
        self.assertEqual(self.lib.get(entry.id).mods, {"enabled_mods": ["HUD"], "mod_settings": {"HUD": {"x": 1}}})

    def test_switching_puts_the_other_save_in_and_keeps_what_was_played(self) -> None:
        main = self.lib.add_live(self.game, STEAM, "Main")
        other = self.lib.import_file(self._other_file(kc=5), STEAM, "Challenge")
        self.put(save_bytes(kc=1234))                      # played since: the game rewrote it
        self.lib.switch_to(self.game, other.id)
        self.assertEqual(self.kc_in_game(), 5)
        self.assertEqual(read_summary(self.lib.get(main.id).save).kill_coins, 1234)   # kept
        self.assertEqual((self.folder / BAK_NAME).read_bytes(), self.in_game())        # bak matches
        self.lib.switch_to(self.game, main.id)
        self.assertEqual(self.kc_in_game(), 1234)
        self.assertEqual((self.folder / BAK_NAME).read_bytes(), save_bytes(kc=900))
        self.assertEqual(len(list((self.lib.backups / STEAM).iterdir())), 2)

    def test_a_save_not_in_the_library_is_never_replaced(self) -> None:
        other = self.lib.import_file(self._other_file(kc=5), STEAM, "Challenge")
        with self.assertRaises(LibraryError) as caught:
            self.lib.switch_to(self.game, other.id)
        self.assertIn("not in the library", str(caught.exception))
        self.assertEqual(self.kc_in_game(), 1000)
        with self.assertRaises(LibraryError):
            self.lib.start_fresh(self.game, STEAM, "Fresh", "New")
        self.assertTrue((self.folder / savegame.SAVE_NAME).is_file())

    def test_nothing_in_the_game_changes_while_it_runs(self) -> None:
        main = self.lib.add_live(self.game, STEAM, "Main")
        other = self.lib.import_file(self._other_file(kc=5), STEAM, "Challenge")
        self.running = "LET IT DIE is running."
        for action in (lambda: self.lib.switch_to(self.game, other.id),
                       lambda: self.lib.start_fresh(self.game, STEAM, "Fresh", "New"),
                       lambda: self.lib.store_live(self.game, STEAM)):
            with self.assertRaises(LibraryError) as caught:
                action()
            self.assertIn("Close the game", str(caught.exception))
        self.assertEqual(self.kc_in_game(), 1000)
        self.assertEqual(self.lib.live_for(self.game, STEAM).entry.id, main.id)

    def test_starting_fresh_puts_a_new_game_in_and_keeps_the_old_one(self) -> None:
        main = self.lib.add_live(self.game, STEAM, "Main")
        self.put(save_bytes(kc=4321))                       # played since it was kept
        fresh = self.lib.start_fresh(self.game, STEAM, "Rookie", "Second run")
        # not taken away (Steam Cloud would put it back) but replaced
        self.assertEqual(self.in_game(), fresh.save.read_bytes())
        self.assertEqual((self.folder / BAK_NAME).read_bytes(), self.in_game())
        s = read_summary(self.folder / savegame.SAVE_NAME)
        self.assertEqual((s.player, s.kill_coins, s.rank, len(s.fighters)), ("Rookie", 0, 1, 1))
        self.assertEqual(self.lib.live_for(self.game, STEAM).entry.id, fresh.id)
        self.assertEqual(read_summary(self.lib.get(main.id).save).kill_coins, 4321)
        self.assertEqual(len(list((self.lib.backups / STEAM).iterdir())), 1)
        self.lib.switch_to(self.game, main.id)
        self.assertEqual(self.kc_in_game(), 4321)

    def test_a_bad_profile_name_changes_nothing(self) -> None:
        self.lib.add_live(self.game, STEAM, "Main")
        for bad in ("", "x" * 17, "tab\there"):
            with self.subTest(bad=bad), self.assertRaises(savegame.SaveError):
                self.lib.start_fresh(self.game, STEAM, bad, "New")
        self.assertEqual(self.kc_in_game(), 1000)
        self.assertEqual(len(self.lib.entries()), 1)

    def test_a_file_that_is_not_a_save_is_refused(self) -> None:
        junk = Path(self._tmp.name) / "junk.sav"
        junk.write_bytes(b"not a save at all, sorry")
        with self.assertRaises(LibraryError):
            self.lib.import_file(junk, STEAM, "Junk")
        self.assertEqual(self.lib.entries(), [])

    def test_nickname_mods_export_and_delete(self) -> None:
        main = self.lib.add_live(self.game, STEAM, "Main")
        other = self.lib.import_file(self._other_file(kc=5), STEAM, "Challenge")
        self.assertEqual(self.lib.rename(other.id, "No-mods run").nickname, "No-mods run")
        self.lib.remember_mods(other.id, {"enabled_mods": []})
        self.assertEqual(self.lib.get(other.id).mods, {"enabled_mods": []})
        out = self.lib.export(other.id, Path(self._tmp.name) / "out" / "copy.sav")
        self.assertEqual(out.read_bytes(), save_bytes(kc=5))
        with self.assertRaises(LibraryError):
            self.lib.delete(main.id)                        # it is the one in the game
        self.lib.delete(other.id)
        self.assertEqual([e.id for e in self.lib.entries()], [main.id])
        for bad in ("..", "../x", ""):
            with self.assertRaises(LibraryError):
                self.lib.get(bad)

    def _other_file(self, kc: int) -> Path:
        path = Path(self._tmp.name) / f"other-{kc}.sav"
        path.write_bytes(save_bytes(kc=kc))
        return path


class SaveSlots(Base):
    def other(self, kc: int) -> Path:
        path = Path(self._tmp.name) / f"slot-{kc}.sav"
        path.write_bytes(save_bytes(kc=kc))
        return path

    def test_saves_fill_the_first_empty_slot_and_stop_at_thirty(self) -> None:
        main = self.lib.add_live(self.game, STEAM, "Main")
        self.assertEqual(main.slot, 1)
        second = self.lib.import_file(self.other(2), STEAM, "Two")
        self.assertEqual(second.slot, 2)
        self.lib.delete(second.id)
        self.assertEqual(self.lib.import_file(self.other(3), STEAM, "Three").slot, 2)   # the gap is reused
        for n in range(3, savelibrary.SLOTS + 1):
            self.lib.import_file(self.other(n + 10), STEAM, f"Save {n}")
        self.assertIsNone(self.lib.first_free_slot())
        with self.assertRaises(LibraryError) as caught:
            self.lib.import_file(self.other(99), STEAM, "One too many")
        self.assertIn("all 30 save slots are full", str(caught.exception))
        self.assertEqual(len(self.lib.entries()), savelibrary.SLOTS)
        with self.assertRaises(LibraryError):
            self.lib.start_fresh(self.game, STEAM, "Rookie", "New")
        self.assertEqual(self.kc_in_game(), 1000)                       # nothing was replaced

    def test_moving_a_save_swaps_it_with_the_one_there(self) -> None:
        main = self.lib.add_live(self.game, STEAM, "Main")
        other = self.lib.import_file(self.other(5), STEAM, "Other")
        self.lib.move_to_slot(main.id, 7)
        self.assertEqual([(e.slot, e.nickname) for e in self.lib.entries()], [(2, "Other"), (7, "Main")])
        self.lib.move_to_slot(other.id, 7)
        self.assertEqual({e.nickname: e.slot for e in self.lib.entries()}, {"Other": 7, "Main": 2})
        for bad in (0, 31):
            with self.assertRaises(LibraryError):
                self.lib.move_to_slot(main.id, bad)

    def test_saves_kept_before_there_were_slots_get_one(self) -> None:
        main = self.lib.add_live(self.game, STEAM, "Main")
        other = self.lib.import_file(self.other(5), STEAM, "Other")
        for entry in (main, other):
            data = json.loads((entry.folder / "entry.json").read_text(encoding="utf-8"))
            data.pop("slot")
            (entry.folder / "entry.json").write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual(sorted(e.slot for e in self.lib.entries()), [1, 2])
        self.assertEqual(sorted(self.lib.get(e.id).slot for e in (main, other)), [1, 2])    # and keep it

    def test_a_save_brought_back_from_the_cloud_keeps_its_slot(self) -> None:
        main = self.lib.add_live(self.game, STEAM, "Main")
        self.lib.move_to_slot(main.id, 12)
        self.lib.set_cloud(main.id, True, self.game)
        elsewhere = Library(Path(self._tmp.name) / "other-pc", lock_reason=lambda: "", game_root=self.game)
        (found,) = elsewhere.cloud_saves()
        self.assertEqual(elsewhere.restore_from_cloud(found).slot, 12)


class SteamCloudCopies(Base):
    def cloud_files(self) -> list[str]:
        return sorted(p.name for p in self.folder.iterdir() if p.name.startswith(savelibrary.CLOUD_PREFIX))

    def test_a_save_backed_up_goes_beside_the_games_named_like_its_own(self) -> None:
        main = self.lib.add_live(self.game, STEAM, "Main", {"enabled_mods": ["HUD"]})
        self.lib.set_cloud(main.id, True, self.game)
        name = savelibrary.CLOUD_PREFIX + main.id
        self.assertEqual(self.cloud_files(), [name, name + ".json"])
        self.assertTrue(name.startswith("brggame.sav."))          # what Steam carries
        self.assertEqual((self.folder / name).read_bytes(), self.in_game())
        info = json.loads((self.folder / (name + ".json")).read_text(encoding="utf-8"))
        self.assertEqual((info["id"], info["nickname"], info["mods"]), (main.id, "Main", {"enabled_mods": ["HUD"]}))
        self.assertTrue(self.lib.get(main.id).cloud)

    def test_it_follows_the_save_its_name_and_mods(self) -> None:
        lib = Library(self.lib.root.parent, lock_reason=lambda: "", game_root=self.game)
        main = lib.add_live(self.game, STEAM, "Main")
        lib.set_cloud(main.id, True)
        name = savelibrary.CLOUD_PREFIX + main.id
        self.put(save_bytes(kc=777))
        lib.store_live(self.game, STEAM)
        self.assertEqual(read_summary(self.folder / name).kill_coins, 777)
        lib.rename(main.id, "Speedrun")
        lib.remember_mods(main.id, {"enabled_mods": ["Timers"]})
        info = json.loads((self.folder / (name + ".json")).read_text(encoding="utf-8"))
        self.assertEqual((info["nickname"], info["mods"]), ("Speedrun", {"enabled_mods": ["Timers"]}))

    def test_switching_it_off_or_deleting_takes_the_copy_away(self) -> None:
        main = self.lib.add_live(self.game, STEAM, "Main")
        other = self.lib.import_file(self._other(), STEAM, "Other")
        for entry in (main, other):
            self.lib.set_cloud(entry.id, True, self.game)
        self.lib.set_cloud(main.id, False, self.game)
        self.assertFalse(self.lib.get(main.id).cloud)
        lib = Library(self.lib.root.parent, lock_reason=lambda: "", game_root=self.game)
        lib.delete(other.id)
        self.assertEqual(self.cloud_files(), [])
        self.assertTrue((self.folder / savegame.SAVE_NAME).is_file())    # the game's own save stays

    def test_the_cloud_is_never_filled_past_the_budget(self) -> None:
        main = self.lib.add_live(self.game, STEAM, "Main")
        with mock.patch.object(savelibrary, "CLOUD_BUDGET", self.lib.cloud_usage(STEAM, self.game) + 10):
            with self.assertRaises(LibraryError) as caught:
                self.lib.set_cloud(main.id, True, self.game)
        self.assertIn("Steam Cloud", str(caught.exception))
        self.assertEqual(self.cloud_files(), [])
        self.assertFalse(self.lib.get(main.id).cloud)

    def test_another_pc_finds_the_backups_and_brings_them_in(self) -> None:
        main = self.lib.add_live(self.game, STEAM, "Main", {"enabled_mods": ["HUD"]})
        self.lib.set_cloud(main.id, True, self.game)
        (self.folder / (savelibrary.CLOUD_PREFIX + "..%2Fevil.json")).write_text("{}", encoding="utf-8")
        elsewhere = Library(Path(self._tmp.name) / "other-pc", lock_reason=lambda: "", game_root=self.game)
        (found,) = elsewhere.cloud_saves()
        self.assertEqual((found.id, found.nickname, found.steam_id), (main.id, "Main", STEAM))
        entry = elsewhere.restore_from_cloud(found)
        self.assertEqual((entry.id, entry.nickname, entry.mods, entry.cloud),
                         (main.id, "Main", {"enabled_mods": ["HUD"]}, True))
        self.assertEqual(entry.save.read_bytes(), main.save.read_bytes())
        self.assertEqual(elsewhere.cloud_saves(), [])
        self.assertEqual(self.lib.cloud_saves(self.game), [])           # this PC already has it

    def _other(self) -> Path:
        path = Path(self._tmp.name) / "other.sav"
        path.write_bytes(save_bytes(kc=5))
        return path


class TheNewSave(unittest.TestCase):
    def test_it_is_the_games_own_new_save_for_this_account(self) -> None:
        for start, template_path in newsave.TEMPLATES.items():
            with self.subTest(start=start):
                data = newsave.new_save(STEAM, 'Kiwako "K/9"', start)
                doc = json.loads(savegame.read_container(data)[1])
                self.assertEqual((doc["user"]["psnacid"], doc["user"]["nm"]), (STEAM, 'Kiwako "K/9"'))
                self.assertIn(rb'"nm":"Kiwako \"K\/9\""', savegame.read_container(data)[1])
                # everything else is the template as the game made it
                _, template = savegame.read_container(template_path.read_bytes())
                again = json.loads(template)
                again["user"]["psnacid"], again["user"]["nm"] = STEAM, 'Kiwako "K/9"'
                self.assertGreater(doc["user"]["created"], again["user"]["created"])   # made now
                again["user"]["created"] = doc["user"]["created"]
                self.assertEqual(doc, again)

    def test_the_two_starts_are_before_and_after_the_tutorial(self) -> None:
        def progress(start):
            doc = json.loads(savegame.read_container(newsave.new_save(STEAM, "A", start))[1])
            flags = {f["var"]: f["value"] for f in doc["gameflg"]["sv"] if isinstance(f, dict)}
            return flags.get("KGF_TUTORIAL_PROGRESS", 0), doc["playlog"]["base"]["max_floor"]
        self.assertEqual(progress(newsave.TUTORIAL), (0, 0))
        self.assertEqual(progress(newsave.AFTER_TUTORIAL), (50, 1))
        with self.assertRaises(savegame.SaveError):
            newsave.new_save(STEAM, "A", "halfway")

    def test_the_templates_belong_to_nobody(self) -> None:
        for start, template_path in newsave.TEMPLATES.items():
            with self.subTest(start=start):
                _, template = savegame.read_container(template_path.read_bytes())
                user = json.loads(template)["user"]
                self.assertEqual((user["psnacid"], user["nm"], user["sid"], user["skey"]), ("", "", "", ""))
                self.assertNotRegex(template, rb"7656119\d{10}")

    def test_only_a_steam_id_is_taken(self) -> None:
        for bad in ("", "12345", "../76561190000000000", "7656119000000000x"):
            with self.subTest(bad=bad), self.assertRaises(savegame.SaveError):
                newsave.new_save(bad, "Name")


class TheSummary(unittest.TestCase):
    def test_the_numbers_a_player_knows(self) -> None:
        s = summarize(document(kc=1000, played=7260))
        self.assertEqual((s.player, s.kill_coins, s.death_metals, s.splithium, s.bloodnium),
                         ("Fighter", 1000, 33, 200, 40))
        self.assertEqual((s.highest_floor, s.deaths, s.kills, s.rank), (27, 4, 321, 12))
        self.assertEqual(s.play_time, "2 h 01 min")
        self.assertEqual((s.blueprints_found, s.blueprints_researched), (2, 1))
        (f,) = s.fighters
        self.assertEqual(f.title, "Leila - Collector G4 +1, level 118")
        self.assertEqual(f.state_label, "Playing")
        self.assertIn("STM 20+1", f.stat_line())

    def test_a_sparse_save_does_not_break_it(self) -> None:
        s = summarize({"user": {"uid": "5"}, "soul": {"free_money": "12", "paid_money": 3}})
        self.assertEqual((s.kill_coins, s.fighters, s.highest_floor), (15, (), 0))
        self.assertEqual(play_time_text(59), "0 min")

    @unittest.skipUnless(REAL_DB.is_file(), "needs the vanilla database")
    def test_the_database_names_classes_and_counts_blueprints(self) -> None:
        names = Names(REAL_DB)
        self.assertEqual(names.class_name("BAL"), "All-rounder")
        self.assertGreater(names.blueprints, 1000)
        self.assertIn("(of ", summarize(document(), names).blueprints)

    def test_a_real_save_if_there_is_one(self) -> None:
        saves = sorted(REAL_SAVES.glob(f"*/{savegame.SAVE_NAME}")) if REAL_SAVES.is_dir() else []
        if not saves:
            self.skipTest("no LET IT DIE save on this machine")
        s = read_summary(saves[0])
        self.assertTrue(s.uid)
        self.assertGreater(s.play_seconds, 0)


if __name__ == "__main__":
    unittest.main()
