"""Writing keys into a game config file: the text editing, the patch type,
the merge across mods, and the shipped Reward Pickup mod."""

from __future__ import annotations

import codecs
import hashlib
import json
import os
import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from fixtures import write_mod
from test_asset_file import build_game_tree
from test_exe_checksums import an_executable

from lid_db_manager import asset_runner, configini, exe_check_off, validator
from lid_db_manager.errors import ModLoadError
from lid_db_manager.mod import _filled_in, load_mod_json
from lid_db_manager.manager import Manager
from lid_db_manager.mod_loader import load_mod_folder, scan_mods
from lid_db_manager.paths import AppPaths
from lid_db_manager.patch import ConfigIniPatch

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# The real package, for the test that checks the keys against the game itself.
GAME_PACKAGE = Path(os.environ.get(
    "LID_GAME_UPK",
    r"C:\Program Files (x86)\Steam\steamapps\common\LET IT DIE"
    r"\BrgGame\CookedPCConsole\BrgGame.upk"))
HAVE_GAME = GAME_PACKAGE.is_file()

TARGET = "BrgGame/Config/BrgUIDebugEditParams.ini"
SECTION = "BrgGame.BrgUIDebugEditParams"


def a_patch(**over) -> ConfigIniPatch:
    data = {
        "type": "config_ini",
        "target": TARGET,
        "section": SECTION,
        "values": {"mCoin_SpawnWait": 0},
    }
    data.update(over)
    return ConfigIniPatch(data, Path("mod"), 0)


# ---------------------------------------------------------------------------
# The text editing
# ---------------------------------------------------------------------------


class SniffTests(unittest.TestCase):
    def test_plain_text_is_utf8_with_no_mark(self):
        self.assertEqual(configini.sniff(b"[A]\r\nx=1\r\n"), ("utf-8", b""))

    def test_utf16_le_is_recognised(self):
        raw = codecs.BOM_UTF16_LE + "[A]".encode("utf-16-le")
        self.assertEqual(configini.sniff(raw), ("utf-16-le", codecs.BOM_UTF16_LE))

    def test_utf32_le_is_not_mistaken_for_utf16(self):
        # UTF-32-LE begins with the UTF-16-LE mark followed by two zero bytes,
        # so the order the marks are tried in is load-bearing.
        raw = codecs.BOM_UTF32_LE + "[A]".encode("utf-32-le")
        self.assertEqual(configini.sniff(raw)[0], "utf-32-le")

    def test_utf8_mark_is_kept_through_a_round_trip(self):
        raw = codecs.BOM_UTF8 + b"[A]\r\nx=1\r\n"
        out = configini.build(raw, [(("A"), "y", "2")])
        self.assertTrue(out.startswith(codecs.BOM_UTF8))
        self.assertIn("y=2", configini.decode(out))

    def test_utf16_file_stays_utf16(self):
        raw = codecs.BOM_UTF16_LE + "[A]\r\nx=1\r\n".encode("utf-16-le")
        out = configini.build(raw, [("A", "y", "2")])
        self.assertTrue(out.startswith(codecs.BOM_UTF16_LE))
        self.assertEqual(configini.decode(out), "[A]\r\nx=1\r\ny=2\r\n")


class SetKeyTests(unittest.TestCase):
    def test_a_new_file_looks_like_the_ones_beside_it(self):
        # Plain text, CRLF, [Package.Class] - the shape of BrgGraphicsConfig.ini.
        out = configini.build(None, [(SECTION, "mCoin_SpawnWait", 0)])
        self.assertEqual(out, b"[BrgGame.BrgUIDebugEditParams]\r\nmCoin_SpawnWait=0\r\n")

    def test_an_existing_value_is_replaced_where_it_stands(self):
        text = "[A]\r\nx=1\r\ny=2\r\nz=3\r\n"
        self.assertEqual(
            configini.set_key(text, "A", "y", "9"), "[A]\r\nx=1\r\ny=9\r\nz=3\r\n"
        )

    def test_repeats_of_a_key_are_collapsed_into_one(self):
        text = "[A]\r\nx=1\r\nx=2\r\nx=3\r\n"
        self.assertEqual(configini.set_key(text, "A", "x", "9"), "[A]\r\nx=9\r\n")

    def test_a_key_is_matched_whatever_its_case_and_spacing(self):
        text = "[A]\r\n  X  =  1  \r\n"
        self.assertEqual(configini.set_key(text, "a", "x", "9"), "[A]\r\nx=9\r\n")

    def test_other_sections_are_untouched(self):
        text = "[A]\r\nx=1\r\n[B]\r\nx=1\r\n"
        self.assertEqual(
            configini.set_key(text, "B", "x", "9"), "[A]\r\nx=1\r\n[B]\r\nx=9\r\n"
        )

    def test_a_missing_section_is_added_at_the_end(self):
        text = "[A]\r\nx=1\r\n"
        self.assertEqual(
            configini.set_key(text, "B", "y", "2"), "[A]\r\nx=1\r\n[B]\r\ny=2\r\n"
        )

    def test_a_new_key_goes_with_its_section_not_after_the_gap(self):
        text = "[A]\r\nx=1\r\n\r\n[B]\r\ny=2\r\n"
        self.assertEqual(
            configini.set_key(text, "A", "z", "3"),
            "[A]\r\nx=1\r\nz=3\r\n\r\n[B]\r\ny=2\r\n",
        )

    def test_a_file_with_no_final_line_ending_does_not_run_two_keys_together(self):
        self.assertEqual(configini.set_key("[A]\nx=1", "A", "y", "2"), "[A]\nx=1\ny=2\n")

    def test_line_endings_are_the_files_own(self):
        self.assertEqual(configini.set_key("[A]\nx=1\n", "A", "y", "2"), "[A]\nx=1\ny=2\n")

    def test_comments_and_blank_lines_survive(self):
        text = "; mine\r\n[A]\r\n; why\r\nx=1\r\n"
        self.assertEqual(configini.set_key(text, "A", "x", "9"), "; mine\r\n[A]\r\n; why\r\nx=9\r\n")

    def test_get_reads_a_value_back(self):
        self.assertEqual(configini.get("[A]\r\nx = 7 \r\n", "a", "X"), "7")
        self.assertIsNone(configini.get("[A]\r\nx=7\r\n", "B", "x"))
        self.assertEqual(configini.get("", "A", "x", "none"), "none")

    def test_build_applies_every_entry_in_order(self):
        out = configini.build(None, [("A", "x", 1), ("A", "x", 2), ("A", "y", 3)])
        self.assertEqual(configini.decode(out), "[A]\r\nx=2\r\ny=3\r\n")


# ---------------------------------------------------------------------------
# The patch type
# ---------------------------------------------------------------------------


class LoadTests(unittest.TestCase):
    def test_entries_come_out_in_the_order_they_were_written(self):
        patch = a_patch(values={"b": 2, "a": 1, "c": 3})
        self.assertEqual(
            patch.ini_entries(), [(SECTION, "b", "2"), (SECTION, "a", "1"), (SECTION, "c", "3")]
        )

    def test_the_config_file_is_the_patchs_asset_target(self):
        self.assertEqual(a_patch().asset_targets(), {TARGET})

    def test_it_writes_no_database_rows(self):
        patch = a_patch()
        self.assertEqual(patch.tables(), set())
        self.assertEqual(patch.targets(), set())
        self.assertEqual(patch.snapshot_specs(None), [])
        self.assertEqual(patch.apply(None, "m").rows_changed, 0)

    def test_booleans_are_written_the_way_unreal_writes_them(self):
        patch = a_patch(values={"on": True, "off": False})
        self.assertEqual([v for _s, _k, v in patch.ini_entries()], ["True", "False"])

    def test_a_target_that_is_not_an_ini_is_refused(self):
        with self.assertRaises(ModLoadError) as caught:
            a_patch(target="BrgGame/CookedPCConsole/BrgGame.upk")
        self.assertIn(".ini", str(caught.exception))

    def test_a_target_outside_the_game_folder_is_refused(self):
        for bad in ("../x.ini", "C:/x.ini", "/x.ini"):
            with self.assertRaises(ModLoadError):
                a_patch(target=bad)

    def test_a_program_file_is_refused_even_named_as_an_ini(self):
        with self.assertRaises(ModLoadError):
            a_patch(target="BrgGame/Config/evil.ini/../../../run.dll")

    def test_a_missing_section_is_refused(self):
        with self.assertRaises(ModLoadError):
            a_patch(section="")

    def test_a_section_written_with_its_brackets_is_refused(self):
        # Silently stripping them would work, and then the one mod that meant a
        # section actually called "[x]" would write a header nobody can read.
        with self.assertRaises(ModLoadError) as caught:
            a_patch(section="[BrgGame.BrgUIDebugEditParams]")
        self.assertIn("without them", str(caught.exception))

    def test_no_values_is_refused(self):
        for bad in ({}, None, [], "x=1"):
            with self.assertRaises(ModLoadError):
                a_patch(values=bad)

    def test_a_value_with_a_line_break_is_refused(self):
        # It would write a second key the mod never declared.
        with self.assertRaises(ModLoadError) as caught:
            a_patch(values={"x": "1\r\nmCoin_FullAutoMode=1"})
        self.assertIn("line break", str(caught.exception))

    def test_an_unusable_key_is_refused(self):
        for bad in ("", "  ", "x=y", "[A]", "; x"):
            with self.assertRaises(ModLoadError):
                a_patch(values={bad: 1})

    def test_a_value_of_null_is_refused(self):
        with self.assertRaises(ModLoadError):
            a_patch(values={"x": None})

    def test_validate_says_the_check_has_to_be_off(self):
        warnings = a_patch().validate(None, "mod")
        self.assertEqual(len(warnings), 1)
        self.assertIn("check", warnings[0])

    def test_the_preview_names_every_key_and_its_value(self):
        preview = a_patch(values={"x": 1, "y": 2}).preview(None)
        self.assertEqual(preview.total_rows, 2)
        self.assertEqual([(r.key, r.after) for r in preview.rows], [("x", "1"), ("y", "2")])


class SettingsTests(unittest.TestCase):
    def test_a_placeholder_is_filled_in_from_the_players_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_mod(Path(tmp), "m", {
                "settings": [{"id": "wait", "label": "Wait", "type": "integer",
                              "default": 4, "min": 0, "max": 20}],
                "patches": [{"type": "config_ini", "target": TARGET, "section": SECTION,
                             "values": {"mCoin_SpawnWait": "{{wait}}"}}],
            })
            mod = load_mod_folder(Path(tmp) / "m")
            self.assertEqual(
                _filled_in(mod, {"wait": 4}).patches[0].ini_entries(),
                [(SECTION, "mCoin_SpawnWait", "4")],
            )
            self.assertEqual(
                _filled_in(mod, {"wait": 0}).patches[0].ini_entries(),
                [(SECTION, "mCoin_SpawnWait", "0")],
            )

    def test_an_undeclared_placeholder_is_refused_at_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_mod(Path(tmp), "m", {
                "settings": [{"id": "wait", "label": "Wait", "type": "integer",
                              "default": 4, "min": 0, "max": 20}],
                "patches": [{"type": "config_ini", "target": TARGET, "section": SECTION,
                             "values": {"mCoin_SpawnWait": "{{wait}}", "x": "{{nosuch}}"}}],
            })
            with self.assertRaises(ModLoadError) as caught:
                load_mod_folder(Path(tmp) / "m")
            self.assertIn("nosuch", str(caught.exception))


# ---------------------------------------------------------------------------
# Applying it to a game folder
# ---------------------------------------------------------------------------


class Game:
    """A game folder and a backups folder, with mods loaded from JSON."""

    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.root = tmp / "game"
        (self.root / "BrgGame" / "Config").mkdir(parents=True)
        self.backups = tmp / "backups"
        self.backups.mkdir()
        self.mods_dir = tmp / "mods"
        self.mods_dir.mkdir()
        self.target = self.root / "BrgGame" / "Config" / "BrgUIDebugEditParams.ini"

    def mod(self, mod_id: str, values: dict, **over):
        write_mod(self.mods_dir, mod_id, {
            "patches": [{"type": "config_ini", "target": TARGET, "section": SECTION,
                         "values": values, **over}],
        })
        return load_mod_folder(self.mods_dir / mod_id)

    def apply(self, *mods):
        return asset_runner.apply_asset_patches(list(mods), self.root, self.backups)

    def restore(self, *mods):
        targets = {t for mod in mods for t in mod.asset_targets()}
        return asset_runner.restore_targets(targets, self.root, self.backups)

    def text(self) -> str:
        return configini.decode(self.target.read_bytes())


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.game = Game(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_the_file_is_created_when_the_game_does_not_ship_one(self):
        mod = self.game.mod("m", {"mCoin_SpawnWait": 0})
        report = self.game.apply(mod)
        self.assertTrue(report.ok, report.error)
        self.assertEqual(report.copied, [TARGET])
        self.assertEqual(self.game.text(), f"[{SECTION}]\r\nmCoin_SpawnWait=0\r\n")

    def test_switching_the_mod_off_deletes_a_file_it_created(self):
        mod = self.game.mod("m", {"mCoin_SpawnWait": 0})
        self.game.apply(mod)
        self.game.restore(mod)
        self.assertFalse(self.game.target.exists())

    def test_a_file_the_player_already_had_comes_back_byte_for_byte(self):
        before = b"; mine\r\n[BrgGame.BrgUIDebugEditParams]\r\nmCoin_SpawnWait=9\r\nmMine=7\r\n"
        self.game.target.write_bytes(before)
        mod = self.game.mod("m", {"mCoin_SpawnWait": 0})
        self.game.apply(mod)
        self.assertIn("mCoin_SpawnWait=0", self.game.text())
        self.assertIn("mMine=7", self.game.text())
        self.game.restore(mod)
        self.assertEqual(self.game.target.read_bytes(), before)

    def test_running_twice_changes_nothing_the_second_time(self):
        mod = self.game.mod("m", {"mCoin_SpawnWait": 0})
        self.game.apply(mod)
        first = self.game.target.read_bytes()
        report = self.game.apply(mod)
        self.assertTrue(report.ok, report.error)
        self.assertEqual(report.copied, [])
        self.assertEqual(report.skipped, 1)
        self.assertEqual(self.game.target.read_bytes(), first)

    def test_two_mods_writing_different_keys_both_get_them(self):
        # The whole reason a config file is not just a copied asset: last-wins
        # per file would give the second mod's keys and throw the first's away.
        first = self.game.mod("first", {"mCoin_SpawnWait": 0})
        second = self.game.mod("second", {"mCoin_FullAutoMode": 1})
        self.assertTrue(self.game.apply(first, second).ok)
        self.assertIn("mCoin_SpawnWait=0", self.game.text())
        self.assertIn("mCoin_FullAutoMode=1", self.game.text())

    def test_load_order_decides_a_key_two_mods_both_set(self):
        first = self.game.mod("first", {"mCoin_SpawnWait": 1})
        second = self.game.mod("second", {"mCoin_SpawnWait": 2})
        self.game.apply(first, second)
        self.assertEqual(configini.get(self.game.text(), SECTION, "mCoin_SpawnWait"), "2")
        self.game.restore(first, second)
        self.game.apply(second, first)
        self.assertEqual(configini.get(self.game.text(), SECTION, "mCoin_SpawnWait"), "1")

    def test_a_key_no_longer_written_does_not_linger(self):
        # Built from the pristine file, never from what the last run left, so
        # unticking a part takes its values out rather than leaving them behind.
        both = self.game.mod("m", {"mCoin_SpawnWait": 0, "mCoin_CoinDivNum": 2})
        self.game.apply(both)
        self.assertIn("mCoin_CoinDivNum=2", self.game.text())
        fewer = replace(both, patches=[
            ConfigIniPatch({"type": "config_ini", "target": TARGET, "section": SECTION,
                            "values": {"mCoin_SpawnWait": 0}}, both.folder, 0)])
        self.assertTrue(self.game.apply(fewer).ok)
        self.assertNotIn("CoinDivNum", self.game.text())
        self.assertIn("mCoin_SpawnWait=0", self.game.text())

    def test_a_mod_with_no_config_patch_leaves_the_pipeline_alone(self):
        write_mod(self.game.mods_dir, "plain", {
            "patches": [{"type": "raw_sql", "sql": "SELECT 1;"}]})
        mod = load_mod_folder(self.game.mods_dir / "plain")
        report = self.game.apply(mod)
        self.assertTrue(report.ok, report.error)
        self.assertEqual(report.copied, [])
        self.assertFalse(self.game.target.exists())

    def test_the_kept_copy_going_missing_is_reported_not_guessed_at(self):
        self.game.target.write_bytes(b"[A]\r\nx=1\r\n")
        mod = self.game.mod("m", {"mCoin_SpawnWait": 0})
        self.game.apply(mod)
        store = self.game.backups / "game_files"
        kept = list(store.glob("*.original"))
        self.assertEqual(len(kept), 1)
        kept[0].unlink()
        report = self.game.apply(self.game.mod("m", {"mCoin_SpawnWait": 5}))
        self.assertFalse(report.ok)
        self.assertIn("saved copy", report.error)


class CheckOffTests(unittest.TestCase):
    """The game checks its config files too, and the Hash Patcher covers them.

    This is the whole reason the mod can exist without patching program code: a
    config file is an ordinary entry in the same list the packages are in, so
    taking its name off that list is the same one-byte edit, not a new trick.
    """

    NAME = "BrgUIDebugEditParams.ini"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.game = build_game_tree(self.root)
        self.mods = self.root / "mods"
        self.mods.mkdir()
        write_mod(self.mods, "cfg", {
            "requires_check_off": [self.NAME],
            "patches": [{"type": "config_ini", "target": TARGET, "section": SECTION,
                         "values": {"mCoin_SpawnWait": 0}}],
        })

    def tearDown(self):
        self._tmp.cleanup()

    def exe(self, entries) -> None:
        exe = self.game / "Binaries" / "Win64" / "BrgGame-Steam.exe"
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.write_bytes(an_executable(entries))

    def mod(self):
        return scan_mods(self.mods).by_id["cfg"]

    def test_it_is_blocked_while_the_game_still_checks_the_config(self):
        self.exe([(self.NAME, hashlib.sha1(b"stock").hexdigest())])
        self.assertEqual([self.NAME], validator.still_checked(self.mod(), self.game))
        reason = validator.check_off_error(self.mod(), self.game)
        self.assertIn(self.NAME, reason)
        self.assertIn("tools > hash patcher", reason.lower())

    def test_it_is_allowed_once_the_name_is_off_the_list(self):
        # ".inX" is what switching the check off leaves behind - the same edit
        # the packages get, which is the point.
        self.exe([("BrgUIDebugEditParams.inX", hashlib.sha1(b"stock").hexdigest())])
        self.assertEqual([], validator.still_checked(self.mod(), self.game))
        self.assertEqual("", validator.check_off_error(self.mod(), self.game))

    def test_the_switch_reads_the_name_back_from_a_config_entry(self):
        raw = an_executable([(self.NAME, hashlib.sha1(b"stock").hexdigest())])
        off, done = exe_check_off.switch_off(raw, [self.NAME])
        self.assertEqual(done, [self.NAME.lower()])
        self.assertEqual(exe_check_off.checked_names(off), [])
        back, restored = exe_check_off.switch_on(off, None)
        self.assertEqual(restored, [self.NAME.lower()])
        # No record of the change is kept anywhere: ".inx" can only have been
        # ".ini", which is what makes putting it back possible at all.
        self.assertEqual(back, raw)

    def test_the_shipped_mod_declares_the_file_it_needs_off(self):
        mod = load_mod_json(PROJECT_ROOT / "mods" / "reward-pickup")
        self.assertEqual(mod.requires_check_off, [self.NAME])
        self.exe([(self.NAME, hashlib.sha1(b"stock").hexdigest())])
        self.assertEqual([self.NAME], validator.still_checked(mod, self.game))


class WholeSaveTests(unittest.TestCase):
    """The shipped mod through the manager, the way a player meets it."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.game = build_game_tree(self.root)
        exe = self.game / "Binaries" / "Win64" / "BrgGame-Steam.exe"
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.write_bytes(an_executable(
            [("BrgUIDebugEditParams.ini", hashlib.sha1(b"stock").hexdigest())]))
        self.paths = AppPaths(self.root / "data").ensure()
        shutil.copytree(PROJECT_ROOT / "mods" / "reward-pickup",
                        self.paths.mods_dir / "reward-pickup")
        self.manager = Manager(self.paths)
        self.manager.rescan()
        self.manager.set_db_path(self.game / "BrgGame" / "Content" / "masters.db")
        self.manager.state.set_enabled("reward-pickup", True)
        self.target = self.game / "BrgGame" / "Config" / "BrgUIDebugEditParams.ini"

    def tearDown(self):
        self._tmp.cleanup()

    def text(self) -> str:
        return self.target.read_text(encoding="utf-8")

    def part(self, key: str, on: bool) -> None:
        self.manager.state.set_part_enabled("reward-pickup", key, on, ships_on=False)

    def test_nothing_is_written_while_the_game_still_checks_the_file(self):
        self.assertEqual(self.manager.blocked_packages(), ["BrgUIDebugEditParams.ini"])
        report = self.manager.save_mod_list()
        self.assertFalse(report.ok)
        self.assertFalse(self.target.exists())

    def test_the_whole_cycle_once_the_check_is_off(self):
        self.manager.switch_file_check_off(self.manager.blocked_packages())

        # On: the pickup part only, because the other one ships switched off.
        self.assertTrue(self.manager.save_mod_list().ok)
        self.assertTrue(self.target.is_file())
        self.assertEqual(self.manager.state.applied["reward-pickup"].parts, ["pickup"])
        self.assertEqual(len([ln for ln in self.text().splitlines() if "=" in ln]), 10)
        self.assertNotIn("DivNum", self.text())

        # Ticking the second part adds its keys.
        self.part("fewer", True)
        self.assertTrue(self.manager.save_mod_list().ok)
        self.assertIn("mCoin_CoinDivNum=2", self.text())

        # Unticking it takes them back out, and leaves the first part alone.
        self.part("fewer", False)
        self.assertTrue(self.manager.save_mod_list().ok)
        self.assertNotIn("DivNum", self.text())
        self.assertIn("mCoin_SpawnWait=0", self.text())

        # Switching the mod off deletes the file the manager created.
        self.manager.state.set_enabled("reward-pickup", False)
        self.assertTrue(self.manager.save_mod_list().ok)
        self.assertFalse(self.target.exists())

    def test_a_chosen_setting_reaches_the_file(self):
        self.manager.switch_file_check_off(self.manager.blocked_packages())
        self.manager.set_mod_setting("reward-pickup", "stagger", 6)
        self.assertTrue(self.manager.save_mod_list().ok)
        for kind in ("Coin", "Spirit", "Bloodnium"):
            self.assertEqual(
                configini.get(self.text(), SECTION, f"m{kind}_SpawnWait"), "6")


# ---------------------------------------------------------------------------
# The shipped mod
# ---------------------------------------------------------------------------


class RewardPickupTests(unittest.TestCase):
    """The mod as it ships, against what the game's own name table holds."""

    # Every key below was read out of BrgGame.upk's name table, so a typo here
    # is a key the game silently ignores rather than an error anybody sees.
    KINDS = ("Coin", "Spirit", "Bloodnium")

    @classmethod
    def setUpClass(cls):
        cls.mod = load_mod_json(PROJECT_ROOT / "mods" / "reward-pickup")

    def filled(self, stagger: int = 0):
        return _filled_in(self.mod, {"stagger": stagger})

    def test_it_says_the_config_file_needs_the_check_off(self):
        self.assertEqual(self.mod.requires_check_off, ["BrgUIDebugEditParams.ini"])

    def test_it_has_a_pickup_part_on_and_a_fewer_part_off(self):
        parts = [(p.key, p.ships_on) for p in self.filled().patches]
        self.assertEqual(parts, [("pickup", True), ("fewer", False)])

    def test_the_pickup_part_sets_wait_and_full_auto_for_all_three(self):
        keys = dict((k, v) for _s, k, v in self.filled(7).patches[0].ini_entries())
        for kind in self.KINDS:
            self.assertEqual(keys[f"m{kind}_SpawnWait"], "7")
            self.assertEqual(keys[f"m{kind}_FullAutoMode"], "1")

    def test_the_pickup_part_zeroes_only_the_coins_launch(self):
        # The coin is the only currency that is thrown, and Full Auto will not
        # collect one until it settles - so the impulse has to go too.
        keys = dict((k, v) for _s, k, v in self.filled().patches[0].ini_entries())
        for axis in ("Z", "XY"):
            for end in ("Min", "Max"):
                self.assertEqual(keys[f"mCoin_Implus{axis}_{end}"], "0")
        self.assertFalse([k for k in keys if "Implus" in k and not k.startswith("mCoin_")])

    def test_the_fewer_part_covers_all_three_currencies(self):
        keys = dict((k, v) for _s, k, v in self.filled().patches[1].ini_entries())
        for kind in self.KINDS:
            self.assertEqual(keys[f"m{kind}_{kind}DivNum"], "2")
            self.assertEqual(keys[f"m{kind}_Min{kind}Num"], "3")

    def test_both_parts_write_the_same_section_of_the_same_file(self):
        for patch in self.filled().patches:
            self.assertEqual(patch.target, TARGET)
            self.assertEqual(patch.section, SECTION)

    def test_the_stagger_setting_only_allows_whole_hundredths(self):
        setting = self.mod.settings[0]
        self.assertEqual(setting.id, "stagger")
        self.assertEqual((setting.default, setting.minimum, setting.maximum), (0, 0, 20))
        with self.assertRaises(ValueError):
            setting.coerce(2.5)
        with self.assertRaises(ValueError):
            setting.coerce(21)

    def test_every_key_belongs_to_a_family_the_readme_describes(self):
        # A key outside these five is a change to somebody's game that nothing
        # tells them about, so adding one has to mean adding it to the readme.
        # First match wins, so DivNum is decided before the bare Num of MinNum.
        families = (("SpawnWait", "SpawnWait"), ("FullAutoMode", "FullAutoMode"),
                    ("Implus", "Implus"), ("DivNum", "DivNum"), ("Num", "MinNum"))
        readme = (PROJECT_ROOT / "mods" / "reward-pickup" / "readme.md").read_text(
            encoding="utf-8")
        for patch in self.filled().patches:
            for _section, key, _value in patch.ini_entries():
                named = next((word for part, word in families if part in key), "")
                self.assertTrue(named, f"{key} is not one of the documented kinds")
                self.assertIn(named, readme, f"{named} is not described in the readme")

    def test_it_applies_to_a_game_folder_and_reverts_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            game = Game(Path(tmp))
            mod = self.filled(4)
            report = asset_runner.apply_asset_patches([mod], game.root, game.backups)
            self.assertTrue(report.ok, report.error)
            text = game.text()
            self.assertTrue(text.startswith(f"[{SECTION}]\r\n"))
            self.assertEqual(configini.get(text, SECTION, "mCoin_SpawnWait"), "4")
            self.assertEqual(len([ln for ln in text.splitlines() if "=" in ln]), 16)
            asset_runner.restore_targets(
                mod.asset_targets(), game.root, game.backups)
            self.assertFalse(game.target.exists())

    def test_mod_json_is_valid_json_with_no_trailing_comma(self):
        raw = (PROJECT_ROOT / "mods" / "reward-pickup" / "mod.json").read_text(
            encoding="utf-8")
        self.assertIsInstance(json.loads(raw), dict)


@unittest.skipUnless(HAVE_GAME, "needs LET IT DIE installed (set LID_GAME_UPK)")
class AgainstTheRealPackageTests(unittest.TestCase):
    """Every key the mod writes is one the game actually has.

    A config key the game does not know is not an error anywhere: the line is
    read, no property matches it, and it is ignored. So a typo here produces a
    mod that installs cleanly, says it worked, and does nothing - which is why
    this reads the names out of the package rather than trusting the mod.
    """

    @classmethod
    def setUpClass(cls):
        from lid_db_manager.upk import package

        cls.names = {entry.text for entry in package.read_tables(GAME_PACKAGE).names}
        cls.mod = _filled_in(load_mod_json(PROJECT_ROOT / "mods" / "reward-pickup"),
                             {"stagger": 0})

    def test_the_class_the_config_file_is_named_after_exists(self):
        # assertTrue rather than assertIn throughout: there are 79,000 names in
        # there, and assertIn prints all of them on a failure.
        self.assertTrue("BrgUIDebugEditParams" in self.names)

    def test_every_key_is_a_property_the_game_has(self):
        for patch in self.mod.patches:
            for _section, key, _value in patch.ini_entries():
                self.assertTrue(key in self.names,
                                f"{key} is not a property of any class in the package")

    def test_only_the_coin_has_an_impulse_to_zero(self):
        # The reason the coin needs the extra four keys and the others do not.
        thrown = {n.split("_")[0] for n in self.names if "_Implus" in n}
        self.assertEqual(thrown, {"mCoin"})


if __name__ == "__main__":
    unittest.main()
