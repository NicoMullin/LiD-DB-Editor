"""Finding one mod among many: search, categories, tags and sort order.

Twenty mods fit on a screen and a hundred do not, and the list is sorted by load
order - which is the right default, because load order is what decides which mod
wins, but a poor way to find "the one about coins". So a mod can say what kind of
thing it is, and the list can be narrowed to that.

    "category": "Economy",
    "tags": ["coins", "storage"]

A **category** is one answer to "what kind of mod is this", so a mod has exactly
one. **Tags** cut across categories - a mod can be about coins and about the
shop - so a mod has as many as it likes. Both are free text: the shipped ones
come from SUGGESTED_CATEGORIES, and anything else somebody types is just as
valid, because a taxonomy written here would be wrong for the next mod.

Everything in this module is a plain function over Mod objects, with no Qt and no
manager, so the behaviour that decides whether a mod is on screen can be tested
without building a window.

Sorting never touches the load order. It is a way of looking at the list, not a
way of changing it - see ``arrange``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

# A mod that says nothing is not guessed about; it is shown under this instead,
# so the category list accounts for every mod and the counts always add up.
UNCATEGORISED = "Uncategorised"

# What the Category box offers. Not a restriction - a category nobody here
# thought of is written through exactly as typed.
SUGGESTED_CATEGORIES = (
    "Economy",
    "Gear",
    "Quality of life",
    "Game content",
    "Visuals",
    "Audio",
    "Examples",
)

SORT_ORDER = "order"
SORT_NAME = "name"
SORT_CATEGORY = "category"
SORT_STATUS = "status"
SORT_NEWEST = "newest"

SORT_LABELS = {
    SORT_ORDER: "Load order",
    SORT_NAME: "Name",
    SORT_CATEGORY: "Category",
    SORT_STATUS: "Status",
    SORT_NEWEST: "Newest first",
}
SORTS = tuple(SORT_LABELS)

# Sorting by status puts what needs attention first, not what starts with "a".
# Anything unrecognised lands between the two - a new status is more likely to
# mean "something to look at" than "nothing to do".
STATUS_RANK = {"failed": 0, "pending": 1, "applied": 3, "disabled": 4}
STATUS_RANK_UNKNOWN = 2


def status_rank(status) -> int:
    return STATUS_RANK.get(str(status).lower(), STATUS_RANK_UNKNOWN)


# Tags are compared, counted and matched, so two spellings of one tag would read
# as two tags. One shape: lower case, words joined by single hyphens.
_TAG_JUNK = re.compile(r"[^a-z0-9]+")
MAX_TAG_LENGTH = 40
MAX_TAGS = 20
MAX_CATEGORY_LENGTH = 40


def clean_tag(raw) -> str:
    """One tag in the one shape tags are held in, or "" if nothing is left."""
    return _TAG_JUNK.sub("-", str(raw or "").strip().lower()).strip("-")[:MAX_TAG_LENGTH]


def clean_tags(values) -> list[str]:
    """A tag list, cleaned, with duplicates and blanks dropped, order kept."""
    if isinstance(values, str):
        values = values.replace(";", ",").split(",")
    out: list[str] = []
    for value in values or []:
        tag = clean_tag(value)
        if tag and tag not in out:
            out.append(tag)
    return out[:MAX_TAGS]


def clean_category(raw) -> str:
    """A category as typed, with its spacing tidied. "" means uncategorised."""
    text = " ".join(str(raw or "").split())
    return text[:MAX_CATEGORY_LENGTH]


def category_of(mod) -> str:
    """The category to file this mod under, never "" - see UNCATEGORISED."""
    return getattr(mod, "category", "") or UNCATEGORISED


def tags_of(mod) -> list[str]:
    return list(getattr(mod, "tags", ()) or ())


# ---------------------------------------------------------------------------
# What is on screen
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Filter:
    """What the list is narrowed to. Empty everywhere means "show everything"."""

    text: str = ""
    category: str = ""
    # Several ticked tags mean "any of these", not "all of them": ticking a
    # second tag while browsing is asking to see more, not less.
    tags: frozenset[str] = field(default_factory=frozenset)

    @property
    def on(self) -> bool:
        return bool(self.text.strip() or self.category or self.tags)

    def cleared(self) -> "Filter":
        return Filter()

    def with_text(self, text: str) -> "Filter":
        return replace(self, text=text)

    def with_category(self, category: str) -> "Filter":
        return replace(self, category=category)

    def with_tags(self, tags) -> "Filter":
        return replace(self, tags=frozenset(clean_tags(tags)))

    def describe(self) -> str:
        """What is being filtered on, for the line under the list."""
        parts = []
        if self.text.strip():
            parts.append(f'matching "{self.text.strip()}"')
        if self.category:
            parts.append(f"in {self.category}")
        if self.tags:
            parts.append("tagged " + ", ".join(sorted(self.tags)))
        return ", ".join(parts)


def haystack(mod) -> str:
    """Everything about a mod that a search should look through, lower case.

    Deliberately wide: somebody searching "coin" means "show me the coin ones"
    and does not care whether the word is in the name or the description. The
    author and the id are in there too, so a pack can be found by who made it.
    """
    pieces = [
        getattr(mod, "name", ""),
        getattr(mod, "id", ""),
        getattr(mod, "description", ""),
        getattr(mod, "author", ""),
        category_of(mod),
        " ".join(tags_of(mod)),
    ]
    return " ".join(str(piece) for piece in pieces).lower()


def matches(mod, filt: Filter) -> bool:
    """Whether this mod belongs on screen under this filter.

    The words of a search all have to appear, in any order and anywhere - so
    "coin limit" finds the Coin Locker mod without needing the phrase.
    """
    if filt.category and category_of(mod).lower() != filt.category.lower():
        return False
    if filt.tags and not filt.tags & set(tags_of(mod)):
        return False
    words = filt.text.lower().split()
    if words:
        found = haystack(mod)
        if not all(word in found for word in words):
            return False
    return True


def select(mods, filt: Filter) -> list:
    return [mod for mod in mods if matches(mod, filt)]


# ---------------------------------------------------------------------------
# What order it is in
# ---------------------------------------------------------------------------


def arrange(mods, sort: str, *, order_of=None, status_of=None, newest_of=None) -> list:
    """The same mods in the order asked for. Never changes anybody's load order.

    ``mods`` arrives in load order already (enabled first, then the rest), and
    SORT_ORDER hands that straight back. Every other sort is a way of looking at
    the list; the number in column 0 still says where a mod really applies,
    which is why the list stops letting you drag that number around while a
    sort is on. Ties fall back to the name, so the order is never arbitrary.
    """
    mods = list(mods)
    if sort == SORT_ORDER or sort not in SORT_LABELS:
        return mods

    def name(mod) -> str:
        return str(getattr(mod, "name", "")).lower()

    if sort == SORT_NAME:
        return sorted(mods, key=name)
    if sort == SORT_CATEGORY:
        return sorted(mods, key=lambda mod: (category_of(mod).lower(), name(mod)))
    if sort == SORT_STATUS:
        status = status_of or (lambda mod: "")
        return sorted(mods, key=lambda mod: (status_rank(status(mod)), name(mod)))
    # Newest first, by when the mod folder was written - which is when it was
    # imported, so a mod just dropped on the window comes to the top.
    newest = newest_of or _folder_time
    return sorted(mods, key=lambda mod: (-newest(mod), name(mod)))


def _folder_time(mod) -> float:
    try:
        return float(mod.folder.stat().st_mtime)
    except (OSError, AttributeError):
        return 0.0


# ---------------------------------------------------------------------------
# What there is to choose from
# ---------------------------------------------------------------------------


def category_counts(mods) -> dict[str, int]:
    """Every category in use, with how many mods are in it, in display order.

    Built from the mods themselves rather than from SUGGESTED_CATEGORIES, so a
    category somebody invented is offered and an empty one is not.
    UNCATEGORISED sorts last however it would sort alphabetically, because it is
    not a category so much as the absence of one.
    """
    counts: dict[str, int] = {}
    for mod in mods:
        name = category_of(mod)
        counts[name] = counts.get(name, 0) + 1
    return {
        name: counts[name]
        for name in sorted(counts, key=lambda n: (n == UNCATEGORISED, n.lower()))
    }


def tag_counts(mods) -> dict[str, int]:
    """Every tag in use, with how many mods carry it, commonest first."""
    counts: dict[str, int] = {}
    for mod in mods:
        for tag in tags_of(mod):
            counts[tag] = counts.get(tag, 0) + 1
    return {
        tag: counts[tag] for tag in sorted(counts, key=lambda t: (-counts[t], t))
    }
