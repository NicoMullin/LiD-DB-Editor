"""Search, categories, tags and sort: the deciding, without a window.

Everything about what is on screen lives in browse.py as plain functions, so
this is where the behaviour is pinned down. The Qt side is in test_ui.py.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path

from fixtures import write_mod

from lid_db_manager import browse
from lid_db_manager.errors import ModLoadError
from lid_db_manager.mod_loader import load_mod_folder, scan_mods
from lid_db_manager.state import Settings

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class FakeMod:
    """Enough of a Mod for browse.py, which only ever reads these."""

    id: str = "m"
    name: str = "A Mod"
    description: str = ""
    author: str = "someone"
    category: str = ""
    tags: list[str] = field(default_factory=list)
    folder: Path = Path(".")


# ---------------------------------------------------------------------------
# Tidying what people type
# ---------------------------------------------------------------------------


class CleanTests(unittest.TestCase):
    def test_a_tag_comes_out_lower_case_and_hyphenated(self):
        self.assertEqual(browse.clean_tag("Coin Locker"), "coin-locker")
        self.assertEqual(browse.clean_tag("  TOKYO  death metro "), "tokyo-death-metro")
        self.assertEqual(browse.clean_tag("weapons/guns"), "weapons-guns")

    def test_a_tag_of_nothing_useful_comes_out_empty(self):
        for junk in ("", "   ", "---", "!!!", None):
            self.assertEqual(browse.clean_tag(junk), "")

    def test_a_tag_is_capped_rather_than_refused(self):
        self.assertEqual(len(browse.clean_tag("x" * 200)), browse.MAX_TAG_LENGTH)

    def test_two_spellings_of_one_tag_become_one_tag(self):
        self.assertEqual(browse.clean_tags(["Coins", "coins", "COINS"]), ["coins"])

    def test_tags_keep_the_order_they_were_written_in(self):
        self.assertEqual(browse.clean_tags(["zebra", "apple"]), ["zebra", "apple"])

    def test_a_string_of_tags_is_split_on_commas(self):
        self.assertEqual(browse.clean_tags("coins, storage ; prices"),
                         ["coins", "storage", "prices"])

    def test_blanks_are_dropped_not_kept_as_empty_tags(self):
        self.assertEqual(browse.clean_tags("coins,,  , storage,"), ["coins", "storage"])

    def test_the_number_of_tags_is_capped(self):
        self.assertEqual(len(browse.clean_tags([f"t{n}" for n in range(100)])),
                         browse.MAX_TAGS)

    def test_a_category_keeps_its_capitals_and_loses_its_stray_spacing(self):
        self.assertEqual(browse.clean_category("  Quality   of  life "), "Quality of life")
        self.assertEqual(browse.clean_category(None), "")

    def test_a_mod_with_no_category_is_filed_under_uncategorised(self):
        self.assertEqual(browse.category_of(FakeMod()), browse.UNCATEGORISED)
        self.assertEqual(browse.category_of(FakeMod(category="Gear")), "Gear")


# ---------------------------------------------------------------------------
# What is on screen
# ---------------------------------------------------------------------------


class MatchTests(unittest.TestCase):
    def setUp(self):
        self.coins = FakeMod(
            id="storage-limit", name="Storage Limit", author="KSFA",
            description="The Coin Locker expands to 10,000 instead of 2,000.",
            category="Economy", tags=["coins", "storage"])
        self.gun = FakeMod(
            id="weapon-ammo", name="Weapon Spare Ammo", author="KSFA",
            description="Every gun carries twice as much spare ammo.",
            category="Gear", tags=["weapons", "guns", "ammo"])

    def test_an_empty_filter_shows_everything(self):
        empty = browse.Filter()
        self.assertFalse(empty.on)
        self.assertTrue(browse.matches(self.coins, empty))
        self.assertTrue(browse.matches(self.gun, empty))

    def test_a_filter_of_only_spaces_is_not_a_filter(self):
        self.assertFalse(browse.Filter(text="   ").on)
        self.assertTrue(browse.matches(self.gun, browse.Filter(text="   ")))

    def test_search_looks_in_the_name(self):
        self.assertTrue(browse.matches(self.coins, browse.Filter(text="storage")))
        self.assertFalse(browse.matches(self.gun, browse.Filter(text="storage")))

    def test_search_looks_in_the_description(self):
        self.assertTrue(browse.matches(self.coins, browse.Filter(text="locker")))

    def test_search_looks_in_the_id_the_author_the_category_and_the_tags(self):
        for word in ("storage-limit", "ksfa", "economy", "coins"):
            self.assertTrue(browse.matches(self.coins, browse.Filter(text=word)), word)

    def test_search_ignores_case(self):
        self.assertTrue(browse.matches(self.coins, browse.Filter(text="COIN LoCkEr")))

    def test_every_word_has_to_appear_but_the_order_does_not_matter(self):
        # The point of splitting on words: "coin limit" is how somebody asks for
        # the Coin Locker mod, and it is not a phrase in anything it says.
        self.assertTrue(browse.matches(self.coins, browse.Filter(text="coin limit")))
        self.assertTrue(browse.matches(self.coins, browse.Filter(text="limit coin")))
        self.assertFalse(browse.matches(self.coins, browse.Filter(text="coin ammo")))

    def test_a_category_filter_is_exact_not_a_search(self):
        self.assertTrue(browse.matches(self.gun, browse.Filter(category="Gear")))
        self.assertTrue(browse.matches(self.gun, browse.Filter(category="gear")))
        self.assertFalse(browse.matches(self.gun, browse.Filter(category="Gea")))
        self.assertFalse(browse.matches(self.coins, browse.Filter(category="Gear")))

    def test_uncategorised_can_be_filtered_to_like_any_other(self):
        loose = FakeMod(name="Loose")
        self.assertTrue(browse.matches(loose, browse.Filter(category=browse.UNCATEGORISED)))
        self.assertFalse(browse.matches(self.gun, browse.Filter(category=browse.UNCATEGORISED)))

    def test_several_tags_mean_any_of_them_not_all(self):
        # Ticking a second tag is asking to see more mods, not fewer.
        both = browse.Filter(tags=frozenset({"coins", "guns"}))
        self.assertTrue(browse.matches(self.coins, both))
        self.assertTrue(browse.matches(self.gun, both))

    def test_a_tag_nothing_carries_shows_nothing(self):
        self.assertFalse(browse.matches(self.gun, browse.Filter(tags=frozenset({"nope"}))))

    def test_the_parts_of_a_filter_narrow_together(self):
        # Category AND tag AND text - unlike several tags, which widen.
        wrong_category = browse.Filter(category="Gear", tags=frozenset({"coins"}))
        self.assertFalse(browse.matches(self.coins, wrong_category))
        self.assertFalse(browse.matches(self.gun, wrong_category))
        right = browse.Filter(category="Gear", tags=frozenset({"guns"}), text="ammo")
        self.assertTrue(browse.matches(self.gun, right))

    def test_select_keeps_the_order_it_was_given(self):
        mods = [self.gun, self.coins]
        self.assertEqual(browse.select(mods, browse.Filter()), mods)
        self.assertEqual(browse.select(mods, browse.Filter(text="ksfa")), mods)

    def test_describe_says_what_is_on(self):
        filt = browse.Filter(text=" coins ", category="Economy",
                             tags=frozenset({"storage", "coins"}))
        self.assertEqual(filt.describe(), 'matching "coins", in Economy, tagged coins, storage')
        self.assertEqual(browse.Filter().describe(), "")


# ---------------------------------------------------------------------------
# What order it is in
# ---------------------------------------------------------------------------


class ArrangeTests(unittest.TestCase):
    def setUp(self):
        self.mods = [
            FakeMod(id="c", name="Cherry", category="Gear"),
            FakeMod(id="a", name="apple", category="Economy"),
            FakeMod(id="b", name="Banana", category="Economy"),
        ]

    def test_load_order_hands_back_exactly_what_it_was_given(self):
        out = browse.arrange(self.mods, browse.SORT_ORDER)
        self.assertEqual([m.id for m in out], ["c", "a", "b"])

    def test_a_sort_name_this_build_does_not_know_changes_nothing(self):
        out = browse.arrange(self.mods, "by-vibes")
        self.assertEqual([m.id for m in out], ["c", "a", "b"])

    def test_sorting_never_touches_the_list_it_was_given(self):
        before = list(self.mods)
        browse.arrange(self.mods, browse.SORT_NAME)
        self.assertEqual(self.mods, before)

    def test_by_name_ignores_case(self):
        out = browse.arrange(self.mods, browse.SORT_NAME)
        self.assertEqual([m.id for m in out], ["a", "b", "c"])

    def test_by_category_then_by_name_inside_it(self):
        out = browse.arrange(self.mods, browse.SORT_CATEGORY)
        self.assertEqual([m.id for m in out], ["a", "b", "c"])

    def test_uncategorised_sorts_last_not_alphabetically(self):
        mods = self.mods + [FakeMod(id="z", name="Aardvark")]
        out = browse.arrange(mods, browse.SORT_CATEGORY)
        self.assertEqual(out[-1].id, "z")

    def test_by_status_puts_what_needs_attention_first(self):
        status = {"c": "applied", "a": "failed", "b": "pending"}
        out = browse.arrange(self.mods, browse.SORT_STATUS,
                             status_of=lambda mod: status[mod.id])
        self.assertEqual([m.id for m in out], ["a", "b", "c"])

    def test_a_status_this_build_does_not_know_lands_before_applied(self):
        status = {"c": "applied", "a": "something-new", "b": "disabled"}
        out = browse.arrange(self.mods, browse.SORT_STATUS,
                             status_of=lambda mod: status[mod.id])
        self.assertEqual([m.id for m in out], ["a", "c", "b"])

    def test_newest_first_is_by_when_the_folder_was_written(self):
        times = {"c": 100.0, "a": 300.0, "b": 200.0}
        out = browse.arrange(self.mods, browse.SORT_NEWEST,
                             newest_of=lambda mod: times[mod.id])
        self.assertEqual([m.id for m in out], ["a", "b", "c"])

    def test_a_folder_that_cannot_be_read_sorts_oldest_rather_than_raising(self):
        out = browse.arrange([FakeMod(id="gone", folder=Path("nowhere-at-all"))],
                             browse.SORT_NEWEST)
        self.assertEqual([m.id for m in out], ["gone"])

    def test_every_sort_returns_every_mod(self):
        for sort in browse.SORTS:
            out = browse.arrange(self.mods, sort, status_of=lambda mod: "applied",
                                 newest_of=lambda mod: 0.0)
            self.assertEqual({m.id for m in out}, {"a", "b", "c"}, sort)


class CountTests(unittest.TestCase):
    def setUp(self):
        self.mods = [
            FakeMod(id="a", category="Economy", tags=["coins", "storage"]),
            FakeMod(id="b", category="Economy", tags=["coins"]),
            FakeMod(id="c", category="Gear", tags=["weapons"]),
            FakeMod(id="d"),
        ]

    def test_every_mod_is_counted_once(self):
        counts = browse.category_counts(self.mods)
        self.assertEqual(sum(counts.values()), len(self.mods))

    def test_categories_are_alphabetical_with_uncategorised_last(self):
        self.assertEqual(list(browse.category_counts(self.mods)),
                         ["Economy", "Gear", browse.UNCATEGORISED])

    def test_an_empty_category_is_not_offered(self):
        self.assertNotIn("Audio", browse.category_counts(self.mods))

    def test_tags_come_out_commonest_first_then_alphabetical(self):
        self.assertEqual(list(browse.tag_counts(self.mods)),
                         ["coins", "storage", "weapons"])

    def test_a_tag_is_counted_once_per_mod(self):
        self.assertEqual(browse.tag_counts(self.mods)["coins"], 2)

    def test_no_mods_means_no_choices_rather_than_an_error(self):
        self.assertEqual(browse.category_counts([]), {})
        self.assertEqual(browse.tag_counts([]), {})


# ---------------------------------------------------------------------------
# Reading them out of mod.json
# ---------------------------------------------------------------------------


class ModJsonTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.mods = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def load(self, **over):
        write_mod(self.mods, "m", {"patches": [{"type": "raw_sql", "sql": "SELECT 1;"}],
                                   **over})
        return load_mod_folder(self.mods / "m")

    def test_a_mod_that_says_nothing_has_no_category_and_no_tags(self):
        mod = self.load()
        self.assertEqual(mod.category, "")
        self.assertEqual(mod.tags, [])

    def test_they_are_read_and_tidied_on_the_way_in(self):
        mod = self.load(category="  Quality  of life ", tags=["Coins", "coins", "Coin Locker"])
        self.assertEqual(mod.category, "Quality of life")
        self.assertEqual(mod.tags, ["coins", "coin-locker"])

    def test_tags_that_are_not_a_list_of_strings_are_refused(self):
        for bad in ("coins", 7, [1, 2], [None]):
            with self.assertRaises(ModLoadError):
                self.load(tags=bad)

    def test_a_bare_sql_mod_loads_with_no_category_rather_than_failing(self):
        folder = self.mods / "loose"
        folder.mkdir()
        (folder / "mod.sql").write_text("SELECT 1;", encoding="utf-8")
        mod = load_mod_folder(folder)
        self.assertEqual(mod.category, "")
        self.assertEqual(browse.category_of(mod), browse.UNCATEGORISED)


class ShippedFilingTests(unittest.TestCase):
    """Every shipped mod is filed, because an unfiled one is one you cannot find."""

    @classmethod
    def setUpClass(cls):
        cls.mods = [m for m in scan_mods(PROJECT_ROOT / "mods").mods
                    if (m.folder / "mod.json").is_file()]

    def test_there_are_shipped_mods_to_check(self):
        self.assertGreater(len(self.mods), 10)

    def test_every_one_with_a_mod_json_has_a_category(self):
        unfiled = sorted(m.id for m in self.mods if not m.category)
        self.assertEqual(unfiled, [])

    def test_every_one_with_a_mod_json_has_at_least_one_tag(self):
        untagged = sorted(m.id for m in self.mods if not m.tags)
        self.assertEqual(untagged, [])

    def test_the_categories_used_are_ones_the_box_offers(self):
        # Free text is allowed for anybody's own mods; the shipped ones should
        # still agree with each other, or the box fills up with near-duplicates.
        for mod in self.mods:
            self.assertIn(mod.category, browse.SUGGESTED_CATEGORIES, mod.id)

    def test_the_tags_are_already_in_the_shape_tags_are_stored_in(self):
        for mod in self.mods:
            self.assertEqual(mod.tags, browse.clean_tags(mod.tags), mod.id)

    def test_a_search_for_coins_finds_the_coin_mods(self):
        found = {m.id for m in browse.select(self.mods, browse.Filter(text="coins"))}
        self.assertIn("storage-limit", found)
        self.assertIn("bank-limit", found)
        self.assertNotIn("armor-durability", found)

    def test_the_file_on_disk_keeps_the_tags_in_the_saved_shape(self):
        # Not just as loaded: the loader tidies, so a messy mod.json would pass
        # the test above while still reading badly to anybody opening the file.
        for mod in self.mods:
            raw = json.loads((mod.folder / "mod.json").read_text(encoding="utf-8-sig"))
            self.assertEqual(raw.get("tags"), mod.tags, mod.id)
            self.assertEqual(raw.get("category"), mod.category, mod.id)


class SortSettingTests(unittest.TestCase):
    """The sort is remembered; the filters deliberately are not."""

    def test_it_starts_on_load_order(self):
        self.assertEqual(Settings().mod_sort, browse.SORT_ORDER)

    def test_a_remembered_sort_is_read_back(self):
        self.assertEqual(Settings.from_dict({"mod_sort": "name"}).mod_sort, "name")

    def test_case_does_not_matter(self):
        self.assertEqual(Settings.from_dict({"mod_sort": "  NAME "}).mod_sort, "name")

    def test_a_sort_this_build_does_not_know_falls_back(self):
        # A state.json from a newer version must not leave the list sorted by
        # something that no longer exists.
        self.assertEqual(Settings.from_dict({"mod_sort": "by-vibes"}).mod_sort,
                         browse.SORT_ORDER)

    def test_nothing_about_the_filter_is_stored(self):
        stored = Settings().to_dict()
        self.assertIn("mod_sort", stored)
        for leaked in ("mod_search", "mod_category", "mod_tags", "mod_filter"):
            self.assertNotIn(leaked, stored)


if __name__ == "__main__":
    unittest.main()
