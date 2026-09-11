"""The asset_file patch type and the file-level apply/revert runner."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fixtures import build_db, write_mod

from lid_db_manager import asset_runner
from lid_db_manager import dbdiff
from lid_db_manager import install as install_module
from lid_db_manager.conflict import KIND_ASSET, analyze
from lid_db_manager.errors import ModLoadError, ValidationError
from lid_db_manager.manager import Manager
from lid_db_manager.mod_loader import load_mod_folder, scan_mods
from lid_db_manager.paths import AppPaths
from lid_db_manager.patch import AssetFilePatch, Patch

COOKED = "BrgGame/CookedPCConsole"


def build_game_tree(root: Path) -> Path:
    """A synthetic <root>/game/BrgGame/{Content,CookedPCConsole}/ layout."""
    game = root / "game"
    (game / "BrgGame" / "Content").mkdir(parents=True, exist_ok=True)
    cooked = game / "BrgGame" / "CookedPCConsole"
    cooked.mkdir(parents=True, exist_ok=True)
    build_db(game / "BrgGame" / "Content" / "masters.db")
    return game


def write_asset_mod(mods_dir: Path, mod_id: str, files: dict[str, bytes], *, target=COOKED) -> Path:
    folder = write_mod(
        mods_dir,
        mod_id,
        {"patches": [{"type": "asset_file", "source": "assets", "target": target}]},
    )
    assets = folder / "assets"
    assets.mkdir(exist_ok=True)
    for name, blob in files.items():
        (assets / name).write_bytes(blob)
    return folder


class GameRootTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_derives_root_from_the_standard_layout(self) -> None:
        game = build_game_tree(self.root)
        db = game / "BrgGame" / "Content" / "masters.db"
        self.assertEqual(asset_runner.game_root_for(db), game.resolve())

    def test_none_for_a_loose_database(self) -> None:
        db = build_db(self.root / "somewhere" / "masters.db")
        self.assertIsNone(asset_runner.game_root_for(db))

    def test_none_when_cookedpcconsole_is_absent(self) -> None:
        db = self.root / "g" / "BrgGame" / "Content" / "masters.db"
        db.parent.mkdir(parents=True)
        build_db(db)
        self.assertIsNone(asset_runner.game_root_for(db))


class AssetFilePatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.mods = self.root / "mods"
        self.mods.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_folder_source_enumerates_every_file(self) -> None:
        folder = write_asset_mod(self.mods, "pack", {"A_SF.upk": b"a", "B_SF.upk": b"bb"})
        patch = Patch.from_dict(
            {"type": "asset_file", "source": "assets", "target": COOKED}, folder, 0
        )
        self.assertIsInstance(patch, AssetFilePatch)
        self.assertEqual(
            patch.asset_targets(),
            {f"{COOKED}/A_SF.upk", f"{COOKED}/B_SF.upk"},
        )
        self.assertEqual(patch.tables(), set())
        self.assertEqual(patch.snapshot_specs(None), [])

    def test_single_file_source(self) -> None:
        folder = write_mod(
            self.mods,
            "one",
            {"patches": [{"type": "asset_file", "source": "assets/A_SF.upk",
                          "target": f"{COOKED}/A_SF.upk"}]},
        )
        (folder / "assets").mkdir()
        (folder / "assets" / "A_SF.upk").write_bytes(b"a")
        patch = Patch.from_dict(
            {"type": "asset_file", "source": "assets/A_SF.upk", "target": f"{COOKED}/A_SF.upk"},
            folder,
            0,
        )
        self.assertEqual(patch.asset_targets(), {f"{COOKED}/A_SF.upk"})

    def test_target_may_not_escape_the_game_folder(self) -> None:
        folder = self.mods / "bad"
        folder.mkdir()
        for bad in ("../evil.upk", "/abs/evil.upk", "C:/evil.upk"):
            with self.assertRaises(ModLoadError):
                Patch.from_dict({"type": "asset_file", "target": bad}, folder, 0)

    def test_missing_target_is_a_load_error(self) -> None:
        folder = self.mods / "bad2"
        folder.mkdir()
        with self.assertRaises(ModLoadError):
            Patch.from_dict({"type": "asset_file"}, folder, 0)

    def test_source_outside_the_mod_folder_is_a_load_error(self) -> None:
        folder = self.mods / "bad3"
        folder.mkdir()
        with self.assertRaises(ModLoadError):
            Patch.from_dict(
                {"type": "asset_file", "source": "../../secret", "target": COOKED}, folder, 0
            )

    def test_validate_flags_a_missing_source(self) -> None:
        folder = write_mod(
            self.mods, "empty",
            {"patches": [{"type": "asset_file", "source": "assets", "target": COOKED}]},
        )
        patch = Patch.from_dict(
            {"type": "asset_file", "source": "assets", "target": COOKED}, folder, 0
        )
        with self.assertRaises(ValidationError):
            patch.validate(None, "empty")


class AssetRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.game = build_game_tree(self.root)
        self.cooked = self.game / "BrgGame" / "CookedPCConsole"
        self.backups = self.root / "backups"
        self.backups.mkdir()
        self.mods = self.root / "mods"
        self.mods.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _load(self, *mod_ids):
        scan = scan_mods(self.mods)
        by_id = scan.by_id
        return [by_id[m] for m in mod_ids]

    def test_adds_new_files_and_is_idempotent(self) -> None:
        write_asset_mod(self.mods, "pack", {"A_SF.upk": b"aaa", "B_SF.upk": b"bbbb"})
        mods = self._load("pack")

        report = asset_runner.apply_asset_patches(mods, self.game, self.backups)
        self.assertTrue(report.ok, report.error)
        self.assertEqual(sorted(report.copied), [f"{COOKED}/A_SF.upk", f"{COOKED}/B_SF.upk"])
        self.assertEqual((self.cooked / "A_SF.upk").read_bytes(), b"aaa")

        again = asset_runner.apply_asset_patches(mods, self.game, self.backups)
        self.assertTrue(again.ok)
        self.assertEqual(again.copied, [])
        self.assertEqual(again.skipped, 2)

    def test_manifest_records_added_files_as_absent(self) -> None:
        write_asset_mod(self.mods, "pack", {"A_SF.upk": b"aaa"})
        asset_runner.apply_asset_patches(self._load("pack"), self.game, self.backups)
        manifest = json.loads(
            (self.backups / "game_files" / "manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest[f"{COOKED}/A_SF.upk"], {"backup": None})

    def test_restore_deletes_a_file_the_mod_added(self) -> None:
        write_asset_mod(self.mods, "pack", {"A_SF.upk": b"aaa"})
        asset_runner.apply_asset_patches(self._load("pack"), self.game, self.backups)
        self.assertTrue((self.cooked / "A_SF.upk").is_file())

        asset_runner.restore_targets({f"{COOKED}/A_SF.upk"}, self.game, self.backups)
        self.assertFalse((self.cooked / "A_SF.upk").exists())
        manifest = json.loads(
            (self.backups / "game_files" / "manifest.json").read_text(encoding="utf-8")
        )
        self.assertNotIn(f"{COOKED}/A_SF.upk", manifest)

    def test_replacing_an_existing_file_backs_it_up_and_restores(self) -> None:
        (self.cooked / "A_SF.upk").write_bytes(b"VANILLA")
        write_asset_mod(self.mods, "pack", {"A_SF.upk": b"MODDED"})

        asset_runner.apply_asset_patches(self._load("pack"), self.game, self.backups)
        self.assertEqual((self.cooked / "A_SF.upk").read_bytes(), b"MODDED")

        asset_runner.restore_targets({f"{COOKED}/A_SF.upk"}, self.game, self.backups)
        self.assertEqual((self.cooked / "A_SF.upk").read_bytes(), b"VANILLA")

    def test_a_failed_copy_rolls_the_whole_run_back(self) -> None:
        # The second file's target parent is a regular file, so its copy fails.
        (self.cooked / "sub").write_bytes(b"not a directory")
        folder = write_mod(
            self.mods, "pack",
            {"patches": [{"type": "asset_file", "source": "assets", "target": COOKED}]},
        )
        assets = folder / "assets"
        assets.mkdir()
        (assets / "A_SF.upk").write_bytes(b"aaa")
        (assets / "sub").mkdir()
        (assets / "sub" / "B_SF.upk").write_bytes(b"bbb")

        report = asset_runner.apply_asset_patches(self._load("pack"), self.game, self.backups)
        self.assertFalse(report.ok)
        self.assertIn("rolled back", report.error)
        # A_SF.upk was added first this run - it must be gone again.
        self.assertFalse((self.cooked / "A_SF.upk").exists())
        manifest_path = self.backups / "game_files" / "manifest.json"
        if manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertNotIn(f"{COOKED}/A_SF.upk", manifest)

    def test_last_mod_in_load_order_wins_per_file(self) -> None:
        write_asset_mod(self.mods, "a-pack", {"X_SF.upk": b"from-a"})
        write_asset_mod(self.mods, "b-pack", {"X_SF.upk": b"from-b"})
        # apply_asset_patches takes mods already in load order; b after a.
        asset_runner.apply_asset_patches(self._load("a-pack", "b-pack"), self.game, self.backups)
        self.assertEqual((self.cooked / "X_SF.upk").read_bytes(), b"from-b")


class AssetConflictTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.mods = self.root / "mods"
        self.mods.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_two_mods_same_game_file_conflict(self) -> None:
        write_asset_mod(self.mods, "a-pack", {"X_SF.upk": b"a"})
        write_asset_mod(self.mods, "b-pack", {"X_SF.upk": b"b"})
        scan = scan_mods(self.mods)
        report = analyze([scan.by_id["a-pack"], scan.by_id["b-pack"]])
        kinds = {c.kind for c in report.conflicts}
        self.assertIn(KIND_ASSET, kinds)
        self.assertTrue(any("X_SF.upk" in c.detail for c in report.conflicts))

    def test_different_game_files_do_not_conflict(self) -> None:
        write_asset_mod(self.mods, "a-pack", {"X_SF.upk": b"a"})
        write_asset_mod(self.mods, "b-pack", {"Y_SF.upk": b"b"})
        scan = scan_mods(self.mods)
        report = analyze([scan.by_id["a-pack"], scan.by_id["b-pack"]])
        self.assertNotIn(KIND_ASSET, {c.kind for c in report.conflicts})


class AssetFolderInstallTests(unittest.TestCase):
    """Drag-and-drop a folder of .upk (or a content pack) -> a generated mod."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.mods = self.root / "mods"
        self.mods.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _make_pack(self, name: str, *, with_catalog: bool = False, loose: bool = False) -> Path:
        pack = self.root / name
        base = pack if loose else pack / "assets"
        base.mkdir(parents=True)
        (base / "CH_Equip_NF_SPE_Head0063_SF.upk").write_bytes(b"nf")
        (base / "CH_Equip_NM_SPE_Head0063_SF.upk").write_bytes(b"nm")
        if with_catalog:
            (pack / "catalog.json").write_text('{"files": []}', encoding="utf-8")
            (pack / "README.txt").write_text("Community pack", encoding="utf-8")
        return pack

    def test_inspect_recognises_an_assets_folder(self) -> None:
        pack = self._make_pack("CrossoverPack", with_catalog=True)
        candidate = install_module.inspect(pack)
        self.assertEqual(candidate.kind, install_module.KIND_ASSET_FOLDER)
        self.assertIn("2 game file(s)", candidate.note)
        self.assertIn("catalog.json", candidate.note)

    def test_inspect_recognises_loose_upk(self) -> None:
        pack = self._make_pack("LoosePack", loose=True)
        candidate = install_module.inspect(pack)
        self.assertEqual(candidate.kind, install_module.KIND_ASSET_FOLDER)

    def test_install_writes_a_working_asset_mod(self) -> None:
        pack = self._make_pack("CrossoverPack", with_catalog=True)
        candidate = install_module.inspect(pack)
        folder = install_module.install(candidate, self.mods, "White Kat Heads")

        mod = load_mod_folder(folder)
        self.assertEqual(len(mod.patches), 1)
        self.assertEqual(
            set(mod.asset_targets()),
            {
                f"{COOKED}/CH_Equip_NF_SPE_Head0063_SF.upk",
                f"{COOKED}/CH_Equip_NM_SPE_Head0063_SF.upk",
            },
        )
        self.assertTrue((folder / "assets" / "CH_Equip_NF_SPE_Head0063_SF.upk").is_file())
        self.assertTrue((folder / "README.txt").is_file())  # provenance doc carried over
        self.assertFalse((folder / "catalog.json").is_file())  # but not the pack's manifest

    def test_db_import_can_require_a_companion_asset_mod(self) -> None:
        # A vanilla db and a modded copy -> a real delta to import.
        vanilla = build_db(self.root / "vanilla.db")
        modded = build_db(self.root / "modded.db")
        con = sqlite3.connect(modded)
        con.execute("UPDATE master_skill SET buy_money = 1 WHERE id = 'SKL_EXPUP_02'")
        con.commit()
        con.close()
        delta = dbdiff.compare(vanilla, modded)
        self.assertFalse(delta.empty)
        candidate = install_module.InstallCandidate(
            modded, install_module.KIND_DATABASE, "Crossover DB", delta.summary(), delta=delta
        )

        folders = install_module.install_database(
            candidate, self.mods, "Crossover DB", vanilla=vanilla,
            requires=["crossover-content-files"],
        )
        for folder in folders:
            data = json.loads((folder / "mod.json").read_text(encoding="utf-8"))
            self.assertEqual(data["requires"], ["crossover-content-files"])

    def test_folder_with_mod_json_is_still_copied_as_is(self) -> None:
        pack = self._make_pack("HasModJson")
        (pack / "mod.json").write_text(
            '{"id":"x","name":"x","description":"d","version":"1.0.0","author":"a",'
            '"patches":[{"type":"asset_file","source":"assets","target":"' + COOKED + '"}]}',
            encoding="utf-8",
        )
        candidate = install_module.inspect(pack)
        self.assertEqual(candidate.kind, install_module.KIND_FOLDER)


class DangerousTargetTests(unittest.TestCase):
    """What a mod must never be able to put in the game folder.

    The game loads DLLs from Binaries/Win64 when it starts, so a mod that drops
    one there gets its code run with the player's permissions on next launch.
    And masters.db has its own apply path with snapshots and a transaction -
    replacing the file wholesale skips all of that. Both were possible before:
    a non-.upk target was only a warning.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.game = build_game_tree(self.root)
        (self.game / "Binaries" / "Win64").mkdir(parents=True)
        self.mods = self.root / "mods"
        self.mods.mkdir()
        self.backups = self.root / "backups"
        self.backups.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _load_error(self, files: dict[str, bytes], target: str) -> str:
        folder = write_asset_mod(self.mods, "bad", files, target=target)
        with self.assertRaises(ModLoadError) as caught:
            load_mod_folder(folder)
        return caught.exception.reason

    # -- the rule itself -------------------------------------------------

    def test_program_files_are_refused(self) -> None:
        for name in ("version.dll", "loader.asi", "setup.exe", "run.bat",
                     "script.ps1", "x.vbs", "shortcut.lnk", "tweak.reg"):
            with self.subTest(name=name):
                self.assertTrue(
                    asset_runner.forbidden_target_reason(f"Binaries/Win64/{name}")
                )

    def test_the_check_ignores_case(self) -> None:
        self.assertTrue(asset_runner.forbidden_target_reason("Binaries/Win64/VERSION.DLL"))

    def test_a_trailing_dot_or_space_does_not_sneak_past(self) -> None:
        """Windows drops them, so "version.dll." lands on disk as version.dll."""
        self.assertTrue(asset_runner.forbidden_target_reason("Binaries/Win64/version.dll."))
        self.assertTrue(asset_runner.forbidden_target_reason("Binaries/Win64/version.dll "))
        self.assertTrue(asset_runner.forbidden_target_reason("Binaries/Win64/version.dll. ."))

    def test_a_colon_is_refused(self) -> None:
        """On NTFS "a.upk:b.exe" writes a hidden alternate stream."""
        self.assertTrue(asset_runner.forbidden_target_reason(f"{COOKED}/A.upk:payload.exe"))

    def test_the_database_and_its_backups_are_refused(self) -> None:
        for name in ("masters.db", "MASTERS.DB", "masters.db-wal", "masters.db-journal",
                     "masters.db.original", "masters.db.backup"):
            with self.subTest(name=name):
                self.assertTrue(
                    asset_runner.forbidden_target_reason(f"BrgGame/Content/{name}")
                )

    def test_ordinary_game_content_is_allowed(self) -> None:
        """The block must not break real packs."""
        for target in (f"{COOKED}/CHR_Hero_SF.upk", f"{COOKED}/Textures.tfc",
                       "BrgGame/Config/DefaultGame.ini", f"{COOKED}/sub/Deep_SF.upk",
                       f"{COOKED}/not.a.dll.upk"):
            with self.subTest(target=target):
                self.assertEqual(asset_runner.forbidden_target_reason(target), "")

    # -- where it is enforced -------------------------------------------

    def test_a_dll_in_a_pack_fails_at_load(self) -> None:
        """So the mod shows as broken, with the reason, instead of looking fine."""
        reason = self._load_error({"version.dll": b"MZ"}, target="Binaries/Win64")
        self.assertIn("program file", reason)

    def test_a_file_target_naming_the_database_fails_at_load(self) -> None:
        folder = write_mod(
            self.mods,
            "bad",
            {"patches": [{"type": "asset_file", "source": "fake.db",
                          "target": "BrgGame/Content/masters.db"}]},
        )
        (folder / "fake.db").write_bytes(b"not a database")
        with self.assertRaises(ModLoadError) as caught:
            load_mod_folder(folder)
        self.assertIn("database", caught.exception.reason)

    def test_one_bad_file_among_good_ones_still_fails(self) -> None:
        reason = self._load_error(
            {"A_SF.upk": b"a", "B_SF.upk": b"b", "sneaky.dll": b"MZ"}, target=COOKED
        )
        self.assertIn("sneaky.dll", reason)

    def test_a_file_added_after_load_is_caught_by_validation(self) -> None:
        folder = write_asset_mod(self.mods, "late", {"A_SF.upk": b"a"})
        mod = load_mod_folder(folder)
        (folder / "assets" / "version.dll").write_bytes(b"MZ")
        con = sqlite3.connect(":memory:")
        try:
            with self.assertRaises(ValidationError) as caught:
                mod.patches[0].validate(con, mod.id)
        finally:
            con.close()
        self.assertIn("program file", str(caught.exception))

    def test_the_runner_refuses_before_copying_anything(self) -> None:
        """Last line of defence, for anything that reached it unvalidated."""
        folder = write_asset_mod(self.mods, "late", {"A_SF.upk": b"a"})
        mod = load_mod_folder(folder)
        (folder / "assets" / "version.dll").write_bytes(b"MZ")

        report = asset_runner.apply_asset_patches([mod], self.game, self.backups)
        self.assertFalse(report.ok)
        self.assertIn("refused", report.error)
        self.assertFalse(
            (self.game / COOKED / "A_SF.upk").exists(),
            "a refused run must not half-install the good files either",
        )

    def test_save_refuses_and_leaves_the_database_alone(self) -> None:
        paths = AppPaths(self.root / "app").ensure()
        db = self.game / "BrgGame" / "Content" / "masters.db"
        before = db.read_bytes()
        folder = write_asset_mod(paths.mods_dir, "late", {"A_SF.upk": b"a"})
        manager = Manager(paths)
        manager.set_db_path(db)
        manager.set_enabled("late", True)
        (folder / "assets" / "version.dll").write_bytes(b"MZ")  # after load

        report = manager.save_mod_list()
        self.assertFalse(report.ok)
        self.assertFalse((self.game / COOKED / "version.dll").exists())
        self.assertEqual(db.read_bytes(), before, "the database must be untouched")


class BackupStoreSizeTests(unittest.TestCase):
    """Backups are only ever vanilla files, and at most one copy of each.

    A mod that adds a file needs no backup; a file no mod touches needs none;
    a mod replacing another mod's copy of a file backs nothing up, because only
    the vanilla file is worth keeping. So the store can never outgrow the
    vanilla files currently being replaced. It used to: restoring left the
    backup behind, and every on/off cycle added another full copy.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.game = build_game_tree(self.root)
        self.cooked = self.game / "BrgGame" / "CookedPCConsole"
        (self.cooked / "Stock_SF.upk").write_bytes(b"VANILLA" * 100)
        (self.cooked / "Untouched_SF.upk").write_bytes(b"NEVER" * 100)
        self.backups = self.root / "backups"
        self.backups.mkdir()
        self.mods = self.root / "mods"
        self.mods.mkdir()
        self.target = f"{COOKED}/Stock_SF.upk"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _store(self) -> list[Path]:
        store = self.backups / asset_runner.GAME_FILES_DIRNAME
        return sorted(store.glob("*.original")) if store.is_dir() else []

    def _load(self, *mod_ids):
        by_id = scan_mods(self.mods).by_id
        return [by_id[m] for m in mod_ids]

    def test_on_off_cycles_never_pile_up_copies(self) -> None:
        write_asset_mod(self.mods, "pack", {"Stock_SF.upk": b"MODDED"})
        mods = self._load("pack")
        for cycle in range(5):
            with self.subTest(cycle=cycle):
                asset_runner.apply_asset_patches(mods, self.game, self.backups)
                self.assertEqual(len(self._store()), 1, "one backup while the mod is on")
                asset_runner.restore_targets({self.target}, self.game, self.backups)
                self.assertEqual(self._store(), [], "none once the vanilla file is back")
                self.assertEqual(
                    (self.cooked / "Stock_SF.upk").read_bytes(), b"VANILLA" * 100
                )

    def test_a_mod_that_only_adds_files_backs_nothing_up(self) -> None:
        write_asset_mod(self.mods, "adds", {"Brand_New_SF.upk": b"NEW"})
        asset_runner.apply_asset_patches(self._load("adds"), self.game, self.backups)
        self.assertEqual(self._store(), [])

    def test_untouched_game_files_are_never_backed_up(self) -> None:
        write_asset_mod(self.mods, "pack", {"Stock_SF.upk": b"MODDED"})
        asset_runner.apply_asset_patches(self._load("pack"), self.game, self.backups)
        self.assertFalse(any("Untouched" in p.name for p in self._store()))

    def test_a_second_mod_on_the_same_file_backs_up_nothing_more(self) -> None:
        """Only vanilla is worth keeping - never another mod's copy."""
        write_asset_mod(self.mods, "a", {"Stock_SF.upk": b"OUTFIT_A"})
        write_asset_mod(self.mods, "b", {"Stock_SF.upk": b"OUTFIT_B"})
        asset_runner.apply_asset_patches(self._load("a"), self.game, self.backups)
        asset_runner.apply_asset_patches(self._load("a", "b"), self.game, self.backups)

        store = self._store()
        self.assertEqual(len(store), 1)
        self.assertEqual(store[0].read_bytes(), b"VANILLA" * 100, "and it is the vanilla one")
        self.assertEqual((self.cooked / "Stock_SF.upk").read_bytes(), b"OUTFIT_B")

    def test_a_failed_restore_keeps_the_backup(self) -> None:
        """If the copy back fails, that backup may be the only vanilla copy left."""
        write_asset_mod(self.mods, "pack", {"Stock_SF.upk": b"MODDED"})
        asset_runner.apply_asset_patches(self._load("pack"), self.game, self.backups)
        self.assertEqual(len(self._store()), 1)

        original = asset_runner._verified_copy

        def failing_copy(source, dest):
            raise OSError("disk full")

        asset_runner._verified_copy = failing_copy
        try:
            report = asset_runner.restore_targets({self.target}, self.game, self.backups)
        finally:
            asset_runner._verified_copy = original

        self.assertFalse(report.ok)
        self.assertEqual(len(self._store()), 1, "the backup must survive a failed restore")
        # ...and it is still recorded, so trying again later can use it.
        retry = asset_runner.restore_targets({self.target}, self.game, self.backups)
        self.assertTrue(retry.ok)
        self.assertEqual((self.cooked / "Stock_SF.upk").read_bytes(), b"VANILLA" * 100)

    def test_a_game_update_between_uses_is_captured_fresh(self) -> None:
        """Why the backup is retaken each time rather than kept forever.

        If the game patches a vanilla file while the mod is off, the next
        backup has to be the new version - restoring a stale one later would
        quietly roll the game back.
        """
        write_asset_mod(self.mods, "pack", {"Stock_SF.upk": b"MODDED"})
        mods = self._load("pack")
        asset_runner.apply_asset_patches(mods, self.game, self.backups)
        asset_runner.restore_targets({self.target}, self.game, self.backups)

        (self.cooked / "Stock_SF.upk").write_bytes(b"PATCHED_BY_UPDATE")  # a game update
        asset_runner.apply_asset_patches(mods, self.game, self.backups)
        asset_runner.restore_targets({self.target}, self.game, self.backups)
        self.assertEqual((self.cooked / "Stock_SF.upk").read_bytes(), b"PATCHED_BY_UPDATE")


class ManagerAssetTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.paths = AppPaths(self.root).ensure()
        self.game = build_game_tree(self.root)
        self.db = self.game / "BrgGame" / "Content" / "masters.db"
        self.cooked = self.game / "BrgGame" / "CookedPCConsole"
        write_asset_mod(self.paths.mods_dir, "pack", {"A_SF.upk": b"MODDED"})
        self.manager = Manager(self.paths)
        self.manager.set_db_path(self.db)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_game_root_is_derived_from_the_db_path(self) -> None:
        self.assertEqual(self.manager.asset_game_root, self.game.resolve())

    def test_save_applies_game_files(self) -> None:
        self.manager.set_enabled("pack", True)
        report = self.manager.save_mod_list()
        self.assertTrue(report.ok, report.error)
        self.assertIsNotNone(report.asset_report)
        self.assertEqual(report.asset_report.copied, [f"{COOKED}/A_SF.upk"])
        self.assertEqual((self.cooked / "A_SF.upk").read_bytes(), b"MODDED")

    def test_revert_removes_the_game_file(self) -> None:
        self.manager.set_enabled("pack", True)
        self.manager.save_mod_list()
        self.manager.revert(["pack"])
        self.assertFalse((self.cooked / "A_SF.upk").exists())

    def test_apply_refused_while_the_game_is_running(self) -> None:
        self.manager.set_enabled("pack", True)
        original = asset_runner.game_lock_reason
        asset_runner.game_lock_reason = lambda db_path=None: "LET IT DIE is running."
        try:
            report = self.manager.save_mod_list()
        finally:
            asset_runner.game_lock_reason = original
        self.assertFalse(report.ok)
        self.assertIn("running", report.error)
        self.assertFalse((self.cooked / "A_SF.upk").exists())

    def test_asset_failure_rolls_the_database_back(self) -> None:
        self.manager.set_enabled("pack", True)
        # A DB-changing mod so the transaction actually writes something.
        write_mod(
            self.paths.mods_dir,
            "cost",
            {"patches": [{"type": "update_set", "table": "master_skill",
                          "set": {"buy_money": 1}, "where": "buy_money > 1"}]},
        )
        self.manager.rescan()
        self.manager.set_enabled("cost", True)

        before = self.db.read_bytes()
        original = asset_runner.apply_asset_patches
        asset_runner.apply_asset_patches = lambda *a, **k: asset_runner.AssetApplyReport(
            ok=False, error="boom"
        )
        try:
            report = self.manager.save_mod_list()
        finally:
            asset_runner.apply_asset_patches = original
        self.assertFalse(report.ok)
        self.assertEqual(self.db.read_bytes(), before, "DB should be rolled back on asset failure")
        self.assertNotIn("cost", self.manager.state.applied)

    def test_restore_all_asset_backups(self) -> None:
        (self.cooked / "A_SF.upk").write_bytes(b"VANILLA")
        self.manager.set_enabled("pack", True)
        self.manager.save_mod_list()
        self.assertEqual((self.cooked / "A_SF.upk").read_bytes(), b"MODDED")

        report = self.manager.restore_all_asset_backups()
        self.assertTrue(report.ok)
        self.assertEqual((self.cooked / "A_SF.upk").read_bytes(), b"VANILLA")


if __name__ == "__main__":
    unittest.main()
