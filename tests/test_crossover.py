"""Recognising the Crossover Content pack, and refusing to guess.

The interesting behaviour is all in the refusals. Attaching the wrong recorded
database changes to a pack would write rows that do not match the artwork, which
shows up in game as invisible or wrong-textured gear rather than as an error, so
every uncertain case has to fall back to "artwork only" with a reason attached
rather than doing its best.

These tests build tiny fake packs. The real pack is 234 MB and is not needed -
what is checked here is identification, not the content itself.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lid_db_manager import crossover, install


def a_pack(root: Path, version: str = "3.74", *, assets: int = 3,
           catalog_extra: dict | None = None, name: str = "pack") -> Path:
    folder = root / name
    (folder / "assets").mkdir(parents=True, exist_ok=True)
    for i in range(assets):
        (folder / "assets" / f"Thing{i}_SF.upk").write_bytes(b"upk")
    catalog = {
        "version": version,
        "files": [{"name": f"Thing{i}_SF.upk"} for i in range(assets)],
        "decals": [{"id": "SKL_A"}, {"id": "SKL_B"}],
        "quests": [{"qid": "Q1"}],
        "clones": [],
    }
    catalog.update(catalog_extra or {})
    (folder / "catalog.json").write_text(json.dumps(catalog), encoding="utf-8")
    return folder


def a_recipe(root: Path, pack: Path, version: str = "3.74",
             sql: str = "UPDATE master_item SET platform = 0;\n") -> dict:
    """A recipe folder whose fingerprint matches ``pack``."""
    folder = root / "recipes"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"crossover-{version}.sql").write_text(sql, encoding="utf-8")
    (folder / f"crossover-{version}.json").write_text(json.dumps({
        "pack": "LET IT DIE Crossover Content",
        "version": version,
        "catalog_sha256": crossover._sha256(pack / "catalog.json"),
        "sql": f"crossover-{version}.sql",
        "recorded": "2026-09-12",
        "counts": {"decals": 42, "quests": 25, "clones": 3, "packages": 234},
        "changes": {"inserts": 476, "updates": 120, "tables": 16},
    }), encoding="utf-8")
    return crossover.known_recipes(folder)


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)


class TellingThePackApart(Base):
    def test_an_ordinary_folder_is_not_the_pack(self) -> None:
        plain = self.root / "just-upks"
        plain.mkdir()
        (plain / "Something_SF.upk").write_bytes(b"upk")
        info = crossover.identify(plain)
        self.assertFalse(info.is_pack)
        self.assertFalse(info.complete)

    def test_an_unrelated_catalog_json_is_not_the_pack(self) -> None:
        """Plenty of things ship a catalog.json; only one has this shape."""
        other = self.root / "other"
        other.mkdir()
        (other / "catalog.json").write_text('{"name": "something else"}', encoding="utf-8")
        self.assertFalse(crossover.identify(other).is_pack)

    def test_broken_json_is_not_fatal(self) -> None:
        other = self.root / "other"
        other.mkdir()
        (other / "catalog.json").write_text("{ not json", encoding="utf-8")
        self.assertFalse(crossover.identify(other).is_pack)

    def test_the_pack_is_recognised_and_counted(self) -> None:
        pack = a_pack(self.root)
        info = crossover.identify(pack, recipes=a_recipe(self.root, pack))
        self.assertTrue(info.is_pack)
        self.assertEqual(info.version, "3.74")
        self.assertEqual(info.asset_count, 3)
        self.assertEqual(info.counts["decals"], 2)
        self.assertIn("3.74", info.suggested_name)

    def test_a_catalog_with_no_assets_folder_is_refused_with_a_reason(self) -> None:
        pack = a_pack(self.root)
        for file in (pack / "assets").iterdir():
            file.unlink()
        (pack / "assets").rmdir()
        info = crossover.identify(pack)
        self.assertFalse(info.is_pack)
        self.assertIn("Extract the whole download", info.reason)


class MatchingARecipe(Base):
    def test_a_matching_version_and_catalog_gives_the_recipe(self) -> None:
        pack = a_pack(self.root)
        info = crossover.identify(pack, recipes=a_recipe(self.root, pack))
        self.assertTrue(info.complete)
        self.assertIsNotNone(info.recipe)
        self.assertIn("UPDATE", info.recipe.sql())
        self.assertEqual(info.reason, "")

    def test_an_unknown_version_installs_artwork_only(self) -> None:
        pack = a_pack(self.root, version="3.75")
        known = a_recipe(self.root, a_pack(self.root / "other", version="3.74"), "3.74")
        info = crossover.identify(pack, recipes=known)
        self.assertTrue(info.is_pack)
        self.assertFalse(info.complete)
        self.assertIn("v3.75", info.reason)
        self.assertIn("v3.74", info.reason)

    def test_a_changed_catalog_is_refused_even_at_the_right_version(self) -> None:
        """The whole point of the fingerprint: same version, different content."""
        pack = a_pack(self.root)
        recipes = a_recipe(self.root, pack)
        catalog = json.loads((pack / "catalog.json").read_text(encoding="utf-8"))
        catalog["quests"].append({"qid": "Q_EXTRA"})
        (pack / "catalog.json").write_text(json.dumps(catalog), encoding="utf-8")
        info = crossover.identify(pack, recipes=recipes)
        self.assertTrue(info.is_pack)
        self.assertFalse(info.complete)
        self.assertIn("does not match", info.reason)

    def test_no_recipes_at_all_installs_artwork_only(self) -> None:
        pack = a_pack(self.root)
        info = crossover.identify(pack, recipes={})
        self.assertTrue(info.is_pack)
        self.assertFalse(info.complete)
        self.assertIn("only the artwork", info.reason)


class ReadingRecipesOffDisk(Base):
    def test_a_damaged_recipe_is_skipped_not_fatal(self) -> None:
        pack = a_pack(self.root)
        a_recipe(self.root, pack)
        (self.root / "recipes" / "crossover-9.9.json").write_text("{ broken", encoding="utf-8")
        found = crossover.known_recipes(self.root / "recipes")
        self.assertIn("3.74", found)
        self.assertNotIn("9.9", found)

    def test_a_recipe_whose_sql_is_missing_is_skipped(self) -> None:
        pack = a_pack(self.root)
        a_recipe(self.root, pack)
        (self.root / "recipes" / "crossover-3.74.sql").unlink()
        self.assertEqual(crossover.known_recipes(self.root / "recipes"), {})

    def test_a_missing_recipes_folder_is_not_an_error(self) -> None:
        self.assertEqual(crossover.known_recipes(self.root / "nope"), {})


class FindingAPackInsideAFolder(Base):
    """Releases have started bundling the pack beside other things.

    The folder a player actually downloads is then the one above the pack, so
    dropping it has to find the pack a level down - while leaving whatever else
    is in there alone, because a sibling folder of .upk can be a second mod with
    rules of its own.
    """

    def a_release(self, version: str = "3.74") -> Path:
        """A release folder: the pack in a subfolder, plus an unrelated mod."""
        release = self.root / "release"
        release.mkdir()
        pack = a_pack(release, version)          # release/pack/
        buttons = release / "buttons"
        buttons.mkdir()
        (buttons / "UI_ButtonGuide_STM_SF.upk").write_bytes(b"upk")
        (release / "README.txt").write_text("a release", encoding="utf-8")
        return pack

    def test_dropping_the_folder_above_finds_the_pack(self) -> None:
        pack = self.a_release()
        with mock.patch.object(crossover, "known_recipes",
                               return_value=a_recipe(self.root, pack)):
            found = install.inspect(pack.parent)
        self.assertEqual(found.kind, install.KIND_CONTENT_PACK)
        self.assertEqual(found.source, pack)
        self.assertIn("from pack/", found.note)

    def test_the_unrelated_sibling_mod_is_left_alone(self) -> None:
        """buttons/ is somebody else's mod with its own rules - never adopted."""
        pack = self.a_release()
        with mock.patch.object(crossover, "known_recipes",
                               return_value=a_recipe(self.root, pack)):
            found = install.inspect(pack.parent)
        copied = {p.name for p in found.source.rglob("*") if p.is_file()}
        self.assertNotIn("UI_ButtonGuide_STM_SF.upk", copied)

    def test_a_pack_at_the_top_level_is_still_used_directly(self) -> None:
        pack = a_pack(self.root)
        with mock.patch.object(crossover, "known_recipes",
                               return_value=a_recipe(self.root, pack)):
            found = install.inspect(pack)
        self.assertEqual(found.source, pack)
        self.assertNotIn("from", found.note)

    def test_subfolders_of_loose_upk_are_not_adopted(self) -> None:
        """Only a recognised pack is taken from below - never a pile of .upk."""
        release = self.root / "release"
        (release / "some-mod").mkdir(parents=True)
        (release / "some-mod" / "Thing_SF.upk").write_bytes(b"upk")
        with self.assertRaises(install.InstallError) as caught:
            install.inspect(release)
        self.assertIn("not a mod folder", str(caught.exception))

    def test_two_packs_inside_is_refused_rather_than_guessed(self) -> None:
        release = self.root / "release"
        release.mkdir()
        a_pack(release, name="first")
        a_pack(release, name="second")
        with self.assertRaises(install.InstallError) as caught:
            install.inspect(release)
        self.assertIn("more than one content pack", str(caught.exception))

    def test_a_nested_pack_of_an_unknown_version_still_says_why(self) -> None:
        self.a_release(version="9.9")
        with mock.patch.object(crossover, "known_recipes", return_value={}):
            found = install.inspect(self.root / "release")
        self.assertEqual(found.kind, install.KIND_ASSET_FOLDER)
        self.assertTrue(found.warnings)
        self.assertIn("from pack/", found.note)


class TheShippedRecipe(unittest.TestCase):
    """The recipe that actually ships, checked for shape rather than content."""

    def test_at_least_one_recipe_ships(self) -> None:
        self.assertTrue(crossover.known_recipes(), "no recorded recipes ship")

    def test_every_shipped_recipe_is_loadable_and_additive(self) -> None:
        for version, recipe in crossover.known_recipes().items():
            with self.subTest(version=version):
                sql = recipe.sql()
                # Checked as booleans: a failed assertIn would print the whole
                # 127 KB recipe into the test output.
                self.assertTrue("INSERT OR REPLACE INTO" in sql, "no inserts")
                self.assertFalse("DROP TABLE" in sql, "recipe drops a table")
                self.assertFalse("DELETE FROM" in sql, "recipe deletes rows")
                self.assertEqual(len(recipe.catalog_sha256), 64)
                self.assertTrue(recipe.summary)


if __name__ == "__main__":
    unittest.main()
