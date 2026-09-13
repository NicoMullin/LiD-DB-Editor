"""The vetted list, and the patch that is the only way to reach it.

Changing a hash inside the game executable is allowed because it is the only
way a mod can replace a package the game checks. It is dangerous for exactly
the same reason, so it is not something a mod can ask for in its own words -
it can only name a recording that ships with the manager.

Almost everything here is a refusal. The one capability is tested in
test_exe_checksums.py; what is tested here is that nothing else can get at it.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lid_db_manager import patch, vetted
from lid_db_manager.errors import ModLoadError, ValidationError

GOOD = {
    "mod": "Colored PlayStation Buttons",
    "version": "1.2",
    "target": vetted.GAME_EXE,
    "package": "UI_ButtonGuide_STM_SF.upk",
    "checksum_before": "1b26d222e55b5da895839a0d412497670d94be10",
    "checksum_after": "1ba2f780f15180912dedf706119f96670d6e6e18",
    "asset_sha256": "21304459fb70462dde5896f49b3f30127039ec09ad236ee686c86dc431deb79c",
    "code_fingerprint": "865385568ef7f241653ed15a73920121ada7ea856de466e545648e7727787fb5",
}


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.recipes = self.root / "recipes"
        self.recipes.mkdir()

    def write(self, name: str, **changes) -> Path:
        data = dict(GOOD)
        for key, value in changes.items():
            if value is None:
                data.pop(key, None)
            else:
                data[key] = value
        path = self.recipes / f"{name}.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path


class LoadingRecordings(Base):
    def test_a_good_one_loads(self) -> None:
        self.write("exe-buttons-1.2")
        found = vetted.known_exe_recipes(self.recipes)
        self.assertEqual(list(found), ["exe-buttons-1.2"])
        self.assertEqual(found["exe-buttons-1.2"].package, "UI_ButtonGuide_STM_SF.upk")

    def test_only_exe_prefixed_files_are_read(self) -> None:
        """crossover-*.json lives in the same folder and is a different thing."""
        self.write("crossover-3.74")
        self.assertEqual(vetted.known_exe_recipes(self.recipes), {})

    def test_a_recording_naming_another_file_is_refused(self) -> None:
        self.write("exe-sneaky", target="Binaries/Win64/something-else.dll")
        self.assertEqual(vetted.known_exe_recipes(self.recipes), {})

    def test_malformed_hashes_are_refused(self) -> None:
        for field in ("checksum_before", "checksum_after", "asset_sha256",
                      "code_fingerprint"):
            with self.subTest(field=field):
                self.write("exe-bad", **{field: "nonsense"})
                self.assertEqual(vetted.known_exe_recipes(self.recipes), {})

    def test_a_missing_field_is_refused(self) -> None:
        for field in ("package", "checksum_before", "checksum_after",
                      "asset_sha256", "code_fingerprint"):
            with self.subTest(field=field):
                self.write("exe-bad", **{field: None})
                self.assertEqual(vetted.known_exe_recipes(self.recipes), {})

    def test_an_unchanged_hash_is_refused(self) -> None:
        self.write("exe-pointless", checksum_after=GOOD["checksum_before"])
        self.assertEqual(vetted.known_exe_recipes(self.recipes), {})

    def test_broken_json_is_skipped_not_fatal(self) -> None:
        self.write("exe-buttons-1.2")
        (self.recipes / "exe-broken.json").write_text("{ nope", encoding="utf-8")
        self.assertEqual(list(vetted.known_exe_recipes(self.recipes)), ["exe-buttons-1.2"])

    def test_an_unknown_name_says_what_is_available(self) -> None:
        self.write("exe-buttons-1.2")
        with self.assertRaises(vetted.VettedError) as caught:
            vetted.exe_recipe("exe-whatever", self.recipes)
        self.assertIn("exe-buttons-1.2", str(caught.exception))


class TheGate(Base):
    """A mod folder cannot reach the capability except by a vetted name."""

    def a_mod(self, patch_data: dict) -> Path:
        folder = self.root / "mod"
        folder.mkdir(exist_ok=True)
        return folder

    def build(self, patch_data: dict, recipes: dict | None = None):
        folder = self.a_mod(patch_data)
        real = vetted.exe_recipe  # bind before patching, or the mock calls itself
        with mock.patch.object(
            vetted, "exe_recipe",
            side_effect=lambda name, directory=None: real(name, self.recipes),
        ):
            return patch.ExeChecksumPatch(patch_data, folder, 0)

    def test_an_unknown_recipe_will_not_load(self) -> None:
        self.write("exe-buttons-1.2")
        with self.assertRaises(ModLoadError) as caught:
            self.build({"type": "exe_checksum_entry", "recipe": "exe-made-up"})
        self.assertIn("not a recording this manager ships", str(caught.exception))

    def test_no_recipe_name_will_not_load(self) -> None:
        with self.assertRaises(ModLoadError):
            self.build({"type": "exe_checksum_entry"})

    def test_a_mod_may_not_supply_its_own_hashes(self) -> None:
        """The whole design: the mod names a recording, it does not describe one."""
        self.write("exe-buttons-1.2")
        for sneaky in ("package", "checksum_before", "checksum_after", "target"):
            with self.subTest(field=sneaky):
                with self.assertRaises(ModLoadError) as caught:
                    self.build({
                        "type": "exe_checksum_entry",
                        "recipe": "exe-buttons-1.2",
                        sneaky: "anything at all",
                    })
                self.assertIn("may not set", str(caught.exception))

    def test_it_declares_the_executable_so_two_mods_conflict(self) -> None:
        self.write("exe-buttons-1.2")
        built = self.build({"type": "exe_checksum_entry", "recipe": "exe-buttons-1.2"})
        self.assertEqual(built.asset_targets(), {vetted.GAME_EXE})

    def test_it_writes_nothing_to_the_database(self) -> None:
        self.write("exe-buttons-1.2")
        built = self.build({"type": "exe_checksum_entry", "recipe": "exe-buttons-1.2"})
        self.assertEqual(built.tables(), set())
        self.assertEqual(built.targets(), set())
        with sqlite3.connect(":memory:") as con:
            self.assertEqual(built.apply(con, "m").rows_changed, 0)


class ValidatingAgainstTheModFolder(Base):
    def build_in(self, folder: Path):
        real = vetted.exe_recipe  # bind before patching, or the mock calls itself
        with mock.patch.object(
            vetted, "exe_recipe",
            side_effect=lambda name, directory=None: real(name, self.recipes),
        ):
            return patch.ExeChecksumPatch(
                {"type": "exe_checksum_entry", "recipe": "exe-buttons-1.2"}, folder, 0
            )

    def test_without_the_package_it_refuses(self) -> None:
        """Telling the game to expect a file the mod does not ship is nonsense."""
        self.write("exe-buttons-1.2")
        folder = self.root / "mod"
        folder.mkdir()
        built = self.build_in(folder)
        with sqlite3.connect(":memory:") as con, self.assertRaises(ValidationError) as caught:
            built.validate(con, "m")
        self.assertIn("does not contain that file", str(caught.exception))

    def test_a_different_build_of_the_package_refuses(self) -> None:
        self.write("exe-buttons-1.2")
        folder = self.root / "mod"
        (folder / "assets").mkdir(parents=True)
        (folder / "assets" / "UI_ButtonGuide_STM_SF.upk").write_bytes(b"not that file")
        built = self.build_in(folder)
        with sqlite3.connect(":memory:") as con, self.assertRaises(ValidationError) as caught:
            built.validate(con, "m")
        self.assertIn("not the one the recording was made from", str(caught.exception))


class RecognisingAModRelease(Base):
    """Matched on the release's own manifest, hashed whole."""

    ATLAS = b"pretend this is a button atlas"

    def a_release(self, *, manifest: dict | None = None,
                  atlas: bytes | None = None) -> tuple[Path, dict]:
        folder = self.root / "buttons"
        folder.mkdir(exist_ok=True)
        body = manifest if manifest is not None else {"file": GOOD["package"], "version": "1.2"}
        (folder / vetted.MANIFEST_NAME).write_text(json.dumps(body), encoding="utf-8")
        (folder / GOOD["package"]).write_bytes(
            self.ATLAS if atlas is None else atlas
        )
        return folder, body

    def matching_recipe(self, folder: Path, atlas: bytes | None = None) -> dict:
        self.write(
            "exe-buttons-1.2",
            manifest_sha256=vetted.sha256_of(folder / vetted.MANIFEST_NAME),
            asset_sha256=hashlib.sha256(
                self.ATLAS if atlas is None else atlas
            ).hexdigest(),
        )
        return vetted.known_exe_recipes(self.recipes)

    def test_a_matching_release_is_recognised(self) -> None:
        folder, _ = self.a_release()
        found = vetted.identify_mod_folder(folder, self.matching_recipe(folder))
        self.assertIsNotNone(found)
        self.assertEqual(found.package, GOOD["package"])

    def test_an_ordinary_folder_is_not(self) -> None:
        plain = self.root / "plain"
        plain.mkdir()
        (plain / "Something_SF.upk").write_bytes(b"upk")
        self.assertIsNone(vetted.identify_mod_folder(plain, {}))

    def test_a_changed_manifest_is_not_recognised(self) -> None:
        """A byte different means it is not the release that was recorded."""
        folder, body = self.a_release()
        recipes = self.matching_recipe(folder)
        body["version"] = "1.3"
        (folder / vetted.MANIFEST_NAME).write_text(json.dumps(body), encoding="utf-8")
        self.assertIsNone(vetted.identify_mod_folder(folder, recipes))

    def test_a_different_package_is_refused(self) -> None:
        """The hash written to the exe is this file's, so it must be this file."""
        folder, _ = self.a_release()
        recipes = self.matching_recipe(folder)
        (folder / GOOD["package"]).write_bytes(b"some other atlas entirely")
        self.assertIsNone(vetted.identify_mod_folder(folder, recipes))

    def test_a_missing_package_is_refused(self) -> None:
        folder, _ = self.a_release()
        recipes = self.matching_recipe(folder)
        (folder / GOOD["package"]).unlink()
        self.assertIsNone(vetted.identify_mod_folder(folder, recipes))

    def test_a_recording_with_no_manifest_hash_never_matches(self) -> None:
        folder, _ = self.a_release()
        self.write("exe-buttons-1.2", manifest_sha256="")
        self.assertIsNone(
            vetted.identify_mod_folder(folder, vetted.known_exe_recipes(self.recipes))
        )


class TheShippedModJson(unittest.TestCase):
    """What actually gets installed is a committed file, not generated JSON."""

    def test_every_recording_has_one_that_agrees_with_it(self) -> None:
        found = vetted.known_exe_recipes()
        self.assertTrue(found)
        for name, recipe in found.items():
            with self.subTest(name=name):
                self.assertTrue(recipe.mod_json_path.is_file(),
                                f"{name} has no mod.json beside it")
                data = recipe.mod_json()   # raises if it names another recording
                kinds = [p.get("type") for p in data["patches"]]
                self.assertIn("asset_file", kinds)
                self.assertIn(vetted.ExeChecksumType, kinds)

    def test_one_naming_a_different_recording_is_refused(self) -> None:
        found = vetted.known_exe_recipes()
        recipe = next(iter(found.values()))
        data = recipe.mod_json()
        data["patches"][-1]["recipe"] = "exe-something-else"
        with tempfile.TemporaryDirectory() as tmp:
            fake = vetted.ExeRecipe(**{**recipe.__dict__, "name": recipe.name})
            path = Path(tmp) / f"{recipe.name}.mod.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            with mock.patch.object(vetted, "_recipes_dir", return_value=Path(tmp)):
                with self.assertRaises(vetted.VettedError) as caught:
                    fake.mod_json()
        self.assertIn("does not name this recording", str(caught.exception))


class TheShippedRecordings(unittest.TestCase):
    def test_every_one_that_ships_is_well_formed(self) -> None:
        found = vetted.known_exe_recipes()
        self.assertTrue(found, "no vetted executable recordings ship")
        for name, recipe in found.items():
            with self.subTest(name=name):
                self.assertTrue(name.startswith(vetted.EXE_PREFIX))
                self.assertEqual(recipe.target, vetted.GAME_EXE)
                self.assertEqual(len(recipe.checksum_before), 40)
                self.assertEqual(len(recipe.checksum_after), 40)
                self.assertEqual(len(recipe.code_fingerprint), 64)
                self.assertNotEqual(recipe.checksum_before, recipe.checksum_after)


if __name__ == "__main__":
    unittest.main()
