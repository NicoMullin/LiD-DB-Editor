"""Finding item artwork in a folder the player already has.

The important property is what this module does NOT do: no artwork ships with
the program, so with no folder set everything must still work and simply show
no pictures. The rest is matching - by the game's own ids where a map provides
them, by filename otherwise - and being unbothered by a folder full of junk.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lid_db_manager.icons import IconSource, blueprint_part, slug


def a_folder(tmp: Path, files: list[str], mapping: dict | None = None) -> Path:
    folder = tmp / "icons"
    folder.mkdir(parents=True, exist_ok=True)
    for name in files:
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x89PNG\r\n\x1a\n")  # enough to be a file
    if mapping is not None:
        (folder / "icon_map.json").write_text(json.dumps(mapping), encoding="utf-8")
    return folder


class NoArtworkAtAll(unittest.TestCase):
    """Nothing ships with the program, so this is the normal case."""

    def test_no_folder_is_not_an_error(self) -> None:
        source = IconSource(None)
        self.assertFalse(source.available)
        self.assertIsNone(source.for_id("PT_ARM_WP001_001"))

    def test_an_empty_string_is_treated_as_no_folder(self) -> None:
        self.assertFalse(IconSource("").available)

    def test_a_folder_that_is_not_there_is_not_an_error(self) -> None:
        source = IconSource(Path(tempfile.gettempdir()) / "no-such-folder-here")
        self.assertFalse(source.available)
        self.assertIsNone(source.for_id("PT_ARM_WP001_001"))


class MatchingByGameId(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_a_map_keyed_by_game_id_is_used(self) -> None:
        folder = a_folder(
            self.root, ["all_official/pt_arm_wp001_002.png"],
            {"gear_icons": {"PT_ARM_WP001_002": "all_official/pt_arm_wp001_002.png"}},
        )
        found = IconSource(folder).for_id("PT_ARM_WP001_002")
        self.assertIsNotNone(found)
        self.assertEqual(found.name, "pt_arm_wp001_002.png")

    def test_a_filename_that_is_the_id_works_without_a_map(self) -> None:
        folder = a_folder(self.root, ["weapons/pt_arm_wp001_002.png"])
        self.assertIsNotNone(IconSource(folder).for_id("PT_ARM_WP001_002"))

    def test_a_thumb_prefix_is_understood(self) -> None:
        folder = a_folder(self.root, ["thumbs/thumb_pt_arm_wp001_002.png"])
        self.assertIsNotNone(IconSource(folder).for_id("PT_ARM_WP001_002"))

    def test_an_id_with_no_picture_gives_nothing(self) -> None:
        folder = a_folder(self.root, ["weapons/pt_arm_wp001_002.png"])
        self.assertIsNone(IconSource(folder).for_id("PT_ARM_WP999_999"))


class Blueprints(unittest.TestCase):
    """A blueprint has no art of its own, so it borrows the gear's."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_a_blueprint_falls_back_to_the_part_it_makes(self) -> None:
        folder = a_folder(self.root, ["weapons/pt_arm_wp001_002.png"])
        self.assertIsNotNone(IconSource(folder).for_id("ITMP_ARM_WP001_002"))

    def test_the_unidentified_version_borrows_the_same_art(self) -> None:
        folder = a_folder(self.root, ["weapons/pt_arm_wp001_002.png"])
        self.assertIsNotNone(IconSource(folder).for_id("ITMP_ARM_WP001_002U"))

    def test_the_id_rule(self) -> None:
        self.assertEqual(blueprint_part("ITMP_ARM_WP001_002"), "PT_ARM_WP001_002")
        self.assertEqual(blueprint_part("ITMP_ARM_WP001_002U"), "PT_ARM_WP001_002")
        self.assertEqual(blueprint_part("ITMT_ALUMI_1"), "ITMT_ALUMI_1")


class MatchingByName(unittest.TestCase):
    """The fallback, for a folder of loose pictures with no map."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_a_name_matches_its_filename(self) -> None:
        folder = a_folder(self.root, ["weapons/battle_machete.png"])
        found = IconSource(folder).for_id("PT_ARM_WP001_002", "Battle Machete")
        self.assertIsNotNone(found)

    def test_slugging(self) -> None:
        self.assertEqual(slug("Battle Machete"), "battle_machete")
        self.assertEqual(slug("Death 'Roids (Blue)"), "death_roids_blue")
        self.assertEqual(slug("Fire & Ice"), "fire_and_ice")

    def test_the_id_wins_over_the_name(self) -> None:
        """An id is exact; a name is a guess, so it only fills gaps."""
        folder = a_folder(self.root, ["a/pt_arm_wp001_002.png", "b/battle_machete.png"])
        found = IconSource(folder).for_id("PT_ARM_WP001_002", "Battle Machete")
        self.assertEqual(found.name, "pt_arm_wp001_002.png")


class BeingHardToBreak(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_a_broken_map_is_ignored_not_fatal(self) -> None:
        folder = a_folder(self.root, ["weapons/pt_arm_wp001_002.png"])
        (folder / "icon_map.json").write_text("{ not json", encoding="utf-8")
        source = IconSource(folder)
        self.assertTrue(source.available)      # it still indexed the files
        self.assertIsNotNone(source.for_id("PT_ARM_WP001_002"))

    def test_a_map_pointing_at_a_missing_file_falls_through(self) -> None:
        folder = a_folder(
            self.root, ["weapons/pt_arm_wp001_002.png"],
            {"gear_icons": {"PT_ARM_WP001_002": "gone/missing.png"}},
        )
        found = IconSource(folder).for_id("PT_ARM_WP001_002")
        self.assertIsNotNone(found, "should fall back to matching the filename")

    def test_non_images_are_ignored(self) -> None:
        folder = a_folder(self.root, ["readme.txt", "notes.md"])
        self.assertFalse(IconSource(folder).available)

    def test_counting_what_has_art(self) -> None:
        folder = a_folder(self.root, ["weapons/pt_arm_wp001_002.png"])
        source = IconSource(folder)
        self.assertEqual(source.count_for(["PT_ARM_WP001_002", "PT_NOPE"]), 1)
