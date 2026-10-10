"""Values a player can change without editing a mod.

Most mods come down to one number - how many times longer a weapon lasts, what
a draw costs, how much the bank holds. Shipping one mod per number ("x2", "x5",
"x10") makes the list longer and still never has the one the player wanted, so
a mod can instead say which of its numbers are up to the player:

    "settings": [
      {"id": "multiplier", "label": "Durability multiplier", "type": "integer",
       "default": 2, "min": 1, "max": 100, "unit": "x"}
    ]

and use it wherever it would have written the number:

    "sql": "UPDATE master_part SET dur = dur * {{multiplier}} WHERE type = 'PTTP_ARM';"

The mod has to say so itself. Guessing which ``2`` in someone's SQL is "the
setting" would sooner or later change the wrong one, and that fails silently.

**Only the mod's own values ever reach the database.** A number is checked
against the mod's own limits and turned into digits before it is written into
anything, so a setting cannot carry SQL - there is no way to type a quote into a
number. The two other kinds are no looser:

    {"id": "reveal", "type": "toggle", "default": false}        -> 1 or 0
    {"id": "skill", "type": "choice", "default": "",
     "options": [{"value": "SKL_HPUP_01", "label": "Tank"}, ...]}

A toggle is written as 1 or 0. A choice is written as one of the values the mod
itself lists, and those are held to letters, digits and underscores, so what a
player picks can be put between quotes in SQL and never close them. A long list
can live in a file of its own beside mod.json (``"options_from": "list.json"``)
and be shared by several settings.

A fourth kind picks any number of decals out of the player's own masters.db -
including decals another mod added - in a window with a search and sorting:

    {"id": "stew_decals", "type": "decals", "default_label": "Every 3-star decal and up",
     "default_sql": "SELECT sklid FROM master_skillgacha_odds WHERE ..."}

Its placeholder belongs inside ``IN (...)``: ``WHERE s.id IN ({{stew_decals}})``.
What the player ticked is written as quoted ids, each held to letters, digits
and underscores like a choice's value. Until they tick anything it is the mod's
own ``default_sql``, so the default keeps up with the database (and with any
mod that adds to it) instead of being a list frozen when the mod was written.
A decal the player picked that is no longer in masters.db simply matches no row.

A fifth kind picks any number of entries off the mod's own list, in the same
window - quests to rotate, say:

    {"id": "quests", "type": "pick", "noun": "quest", "default_label": "All of them",
     "options": [{"value": "Q_1", "label": "Defeat 50 Haters", "group": "Weekly",
                  "help": "First clear: ..."}, ...]}

Its options are written like a choice's (``options`` or ``options_from``); its
``default`` is a list of their values, or every one when left out. Like a
decal list, its placeholder belongs inside ``IN (...)`` and is written as
quoted values - the player's ticks, or the default's - and only values the mod
lists ever reach the SQL.

A sixth holds a shop's list - what it sells, how many at once, in which
currency, for how much, and how many times a day - edited in a window that
lists every item and mushroom in the player's masters.db:

    {"id": "goods", "type": "goods", "noun": "good", "default_label": "...",
     "default": "ITMT_WOOD_4/1/k/0/0,MSR_053/5/d/8/1"}

Each entry is ``id/bundle/currency/price/a day``: currency ``k`` (Kill Coins)
or ``d`` (Death Metals), a price of 0 for the game's own price for that many,
and 0 a day for no limit. Its placeholder belongs inside ``VALUES``, as rows of
(place, 'id', bundle, currency 0 or 1, price, a day); an empty list is one row
with a NULL id, for the mod to skip. Nothing but letters, digits, underscores
and those separators is accepted, so it carries no SQL either.

A seventh holds a list of rewards to draw from, each with a weight - the daily
login bonus, say - edited in a window that lists every reward in the player's
masters.db (master_reward):

    {"id": "rewards", "type": "rewards", "noun": "reward", "default_label": "...",
     "default": "RWD_MONEY_3000/1,RWD_SKILL_011/3"}

Each entry is ``reward id/weight``: a reward with weight 3 comes up three times
as often as one with weight 1. Its placeholder belongs inside ``VALUES``, as
rows of ('id', weight); an empty list is one row with a NULL id, for the mod
to skip.

A placeholder may ask for a number style, because game text is written the way
each language writes numbers:

    {{percent}}         100000      the plain number, for SQL
    {{percent:comma}}   100,000     English
    {{percent:dot}}     100.000     German, Spanish, French, Italian, Portuguese
    {{skill:label}}     Tank        what the player saw (choices and toggles)

Settings may name a ``group``; the Configuration tab puts a heading over each.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import ModLoadError

KIND_INTEGER = "integer"
KIND_NUMBER = "number"
KIND_CHOICE = "choice"
KIND_TOGGLE = "toggle"
KIND_DECALS = "decals"
KIND_PICK = "pick"
KIND_GOODS = "goods"
KIND_REWARDS = "rewards"
KINDS = (KIND_INTEGER, KIND_NUMBER, KIND_CHOICE, KIND_TOGGLE, KIND_DECALS, KIND_PICK, KIND_GOODS,
         KIND_REWARDS)
# The kinds whose value is a list picked in a window of their own.
LIST_KINDS = (KIND_DECALS, KIND_PICK, KIND_GOODS, KIND_REWARDS)
NUMBER_KINDS = (KIND_INTEGER, KIND_NUMBER)

STYLE_PLAIN = ""
STYLE_COMMA = "comma"
STYLE_DOT = "dot"
STYLE_LABEL = "label"
STYLES = (STYLE_PLAIN, STYLE_COMMA, STYLE_DOT, STYLE_LABEL)

# What a choice's value may be made of. It is written into SQL as it stands, so
# nothing that could end a quoted string or start a new statement is allowed.
CHOICE_VALUE = re.compile(r"^[A-Za-z0-9_]{0,64}$")
# More than anyone would scroll through, but a mistake (a whole table pasted
# into one list) is refused rather than built into a box nobody can use.
MOST_OPTIONS = 5000
# A decal setting's value: the ids picked, comma-separated, or "" for the mod's
# own default. Every id is held to what a choice's value may be.
DECAL_LIST = re.compile(r"^[A-Za-z0-9_]{1,64}(?:,[A-Za-z0-9_]{1,64})*$")
MOST_DECALS = MOST_OPTIONS
# What a mod's default_sql must look like: one SELECT, nothing after it.
DEFAULT_SQL = re.compile(r"^\s*select\b[^;]*$", re.IGNORECASE)
# A goods list's value: entries "id/bundle/currency/price/a day", comma-separated.
# The currency is k (Kill Coins) or d (Death Metals); a price of 0 is the game's
# own price for that many; "a day" of 0 is no limit. Nothing but letters,
# digits, underscores and the separators, so it can never carry SQL.
GOODS_ENTRY = r"[A-Za-z0-9_]{1,64}/[0-9]{1,3}/[kd]/[0-9]{1,10}/[0-9]{1,2}"
GOODS_LIST = re.compile(rf"^{GOODS_ENTRY}(?:,{GOODS_ENTRY})*$")
MOST_GOODS = 500
MOST_IN_A_BUNDLE = 999
MOST_A_DAY = 20
CURRENCIES = {"k": 0, "d": 1}
# A reward list's value: entries "reward id/weight", comma-separated. A weight
# of 3 comes up three times as often as 1.
REWARD_ENTRY = r"[A-Za-z0-9_]{1,64}/[0-9]{1,3}"
REWARD_LIST = re.compile(rf"^{REWARD_ENTRY}(?:,{REWARD_ENTRY})*$")
MOST_REWARDS = 1000
MOST_WEIGHT = 100

_TRUE_WORDS = {"1", "on", "true", "yes"}
_FALSE_WORDS = {"0", "off", "false", "no"}

# Lower-case, so "multiplier" and "Multiplier" can never be two settings.
ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
# Deliberately looser than ID_PATTERN, so a mistyped name is caught and named
# rather than left in the SQL as literal braces.
PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z][A-Za-z0-9_]*)\s*(?::\s*([A-Za-z]*)\s*)?\}\}")

# The game's integer columns are 32-bit. Anything a setting allows has to fit,
# including once it is multiplied into a value that is already large.
LARGEST_ALLOWED = 2_000_000_000


@dataclass(frozen=True)
class SettingOption:
    """One entry in a choice's list."""

    value: str
    label: str
    group: str = ""
    help: str = ""


@dataclass(frozen=True)
class GoodsEntry:
    """One line of a goods list: what is sold, how many at once, for what."""

    id: str
    bundle: int = 1         # how many one purchase gives
    currency: str = "k"     # k Kill Coins, d Death Metals
    price: int = 0          # for the bundle; 0 is the game's own price for that many
    a_day: int = 0          # how many times a day it can be bought; 0 is no limit

    def text(self) -> str:
        return f"{self.id}/{self.bundle}/{self.currency}/{self.price}/{self.a_day}"


def parse_goods(value: Any, label: str) -> list[GoodsEntry]:
    """A goods list's entries, each checked; ValueError (for a player) if not.

    An entry listed twice exactly is kept once. "" is an empty list.
    """
    if isinstance(value, (list, tuple)):
        value = ",".join(e.text() if isinstance(e, GoodsEntry) else str(e) for e in value)
    if not isinstance(value, str):
        raise ValueError(f"{label} is a list of goods, not {value!r}")
    text = ",".join(part.strip() for part in value.split(",")) if value.strip() else ""
    if not text:
        return []
    if not GOODS_LIST.match(text):
        raise ValueError(f"{label}: that is not a list of goods (id/bundle/k or d/price/a day)")
    entries: list[GoodsEntry] = []
    for part in text.split(","):
        good, bundle, currency, price, a_day = part.split("/")
        entry = GoodsEntry(good, int(bundle), currency, int(price), int(a_day))
        if not 1 <= entry.bundle <= MOST_IN_A_BUNDLE:
            raise ValueError(f"{label}: {good} comes in bundles of 1 to {MOST_IN_A_BUNDLE}")
        if entry.price > LARGEST_ALLOWED:
            raise ValueError(f"{label}: {good}'s price is more than the game can hold")
        if entry.a_day > MOST_A_DAY:
            raise ValueError(f"{label}: {good} can be bought at most {MOST_A_DAY} times a day")
        if entry not in entries:
            entries.append(entry)
    if len(entries) > MOST_GOODS:
        raise ValueError(f"{label}: {len(entries):,} goods is more than the {MOST_GOODS:,} allowed")
    return entries


@dataclass(frozen=True)
class RewardEntry:
    """One line of a reward list: a reward, and how often it comes up."""

    id: str
    weight: int = 1         # 3 comes up three times as often as 1

    def text(self) -> str:
        return f"{self.id}/{self.weight}"


def parse_rewards(value: Any, label: str) -> list[RewardEntry]:
    """A reward list's entries, each checked; ValueError (for a player) if not.

    A reward listed twice is kept once, at its first weight. "" is an empty list.
    """
    if isinstance(value, (list, tuple)):
        value = ",".join(e.text() if isinstance(e, RewardEntry) else str(e) for e in value)
    if not isinstance(value, str):
        raise ValueError(f"{label} is a list of rewards, not {value!r}")
    text = ",".join(part.strip() for part in value.split(",")) if value.strip() else ""
    if not text:
        return []
    if not REWARD_LIST.match(text):
        raise ValueError(f"{label}: that is not a list of rewards (reward id/weight)")
    entries: list[RewardEntry] = []
    seen: set[str] = set()
    for part in text.split(","):
        reward, weight = part.split("/")
        if not 1 <= int(weight) <= MOST_WEIGHT:
            raise ValueError(f"{label}: {reward}'s weight is 1 to {MOST_WEIGHT}")
        if reward not in seen:
            seen.add(reward)
            entries.append(RewardEntry(reward, int(weight)))
    if len(entries) > MOST_REWARDS:
        raise ValueError(f"{label}: {len(entries):,} rewards is more than the {MOST_REWARDS:,} allowed")
    return entries


@dataclass(frozen=True)
class ModSetting:
    """One value a mod lets the player choose."""

    id: str
    label: str
    kind: str
    default: int | float | str
    minimum: int | float
    maximum: int | float
    step: int | float = 1
    unit: str = ""
    help: str = ""
    # A choice's list, in the order it is shown.
    options: tuple[SettingOption, ...] = ()
    # A heading the Configuration tab puts over this setting and the ones
    # after it in the same group.
    group: str = ""
    # A decal setting's default: the mod's own query for the decals it means,
    # and how that reads to a player.
    default_sql: str = ""
    default_label: str = ""
    # A pick setting's default (the values it means until the player ticks
    # any) and what one entry is called ("quest").
    default_picks: tuple[str, ...] = ()
    noun: str = ""
    # A goods list's default: the entries it means until the player edits it.
    default_goods: str = ""
    # A reward list's default, the same way.
    default_rewards: str = ""

    @property
    def is_number(self) -> bool:
        return self.kind in NUMBER_KINDS

    @property
    def is_list(self) -> bool:
        return self.kind in LIST_KINDS

    def decal_ids(self, value: str) -> list[str]:
        """The ids a decal or pick setting's value holds ([] for the default)."""
        return [part for part in str(value).split(",") if part]

    def picks(self, value: str) -> list[str]:
        """A pick setting's values in force: the player's ticks, or the default."""
        return self.decal_ids(value) or list(self.default_picks)

    def goods(self, value: str) -> list[GoodsEntry]:
        """A goods list's entries in force: the player's list, or the default."""
        if str(value or "").strip():
            return parse_goods(value, self.label)
        return parse_goods(self.default_goods, self.label)

    def rewards(self, value: str) -> list[RewardEntry]:
        """A reward list's entries in force: the player's list, or the default."""
        if str(value or "").strip():
            return parse_rewards(value, self.label)
        return parse_rewards(self.default_rewards, self.label)

    def sql(self, value: int | float | str) -> str:
        """What ``{{name}}`` becomes in SQL. Only the list settings differ from
        render_text's plain value: quoted ids, or the mod's default (its query
        for decals, its listed values for a pick).

        A goods list becomes rows for ``VALUES``: (place, 'id', bundle,
        currency 0 or 1, price, a day). An empty one is a single row whose id is
        NULL, since ``VALUES`` cannot be empty - a mod skips it by its id.
        A reward list becomes rows of ('id', weight) the same way."""
        if self.kind == KIND_REWARDS:
            entries = self.rewards(value)
            if not entries:
                return "(NULL, 0)"
            return ", ".join(f"('{e.id}', {e.weight})" for e in entries)
        if self.kind == KIND_GOODS:
            entries = self.goods(value)
            if not entries:
                return "(0, NULL, 1, 0, 0, 0)"
            return ", ".join(
                f"({n}, '{e.id}', {e.bundle}, {CURRENCIES[e.currency]}, {e.price}, {e.a_day})"
                for n, e in enumerate(entries, start=1)
            )
        if self.kind == KIND_PICK:
            return ",".join(f"'{pick}'" for pick in self.picks(value))
        if self.kind != KIND_DECALS:
            return str(value)
        ids = self.decal_ids(value)
        if not ids:
            return self.default_sql
        return ",".join(f"'{decal}'" for decal in ids)

    def option(self, value: Any) -> SettingOption | None:
        for option in self.options:
            if option.value == value:
                return option
        return None

    def coerce(self, value: Any) -> int | float | str:
        """``value`` as this setting allows it, or ValueError saying why.

        The message is written to be shown to a player as it stands.
        """
        if self.kind == KIND_TOGGLE:
            return _as_toggle(value, self.label)
        if self.kind == KIND_DECALS:
            return _as_decals(value, self.label)
        if self.kind == KIND_GOODS:
            return ",".join(e.text() for e in parse_goods(value, self.label))
        if self.kind == KIND_REWARDS:
            return ",".join(e.text() for e in parse_rewards(value, self.label))
        if self.kind == KIND_PICK:
            # Checked like decal ids, then held to the mod's own list: one the
            # mod no longer lists is dropped (and none left is the default).
            ids = _as_decals(value, self.label).split(",")
            return ",".join(i for i in ids if self.option(i) is not None)
        if self.kind == KIND_CHOICE:
            if isinstance(value, str) and self.option(value) is not None:
                return value
            raise ValueError(f"{value!r} is not one of the choices for {self.label}")
        if isinstance(value, bool):
            raise ValueError(f"{self.label} must be a number")
        if isinstance(value, str):
            text = value.strip().replace(",", "").replace(" ", "")
            try:
                value = float(text)
            except ValueError:
                raise ValueError(f"{self.label} must be a number, not {value!r}") from None
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"{self.label} must be a number")
        if self.kind == KIND_INTEGER:
            if float(value) != int(value):
                raise ValueError(f"{self.label} must be a whole number")
            value = int(value)
        else:
            value = float(value)
        if value < self.minimum or value > self.maximum:
            raise ValueError(
                f"{self.label} must be between {self.display(self.minimum)} "
                f"and {self.display(self.maximum)}"
            )
        return value

    def display(self, value: int | float | str) -> str:
        """How a value reads in the mod list: "x2", "10,000 KC", "On", "Tank"."""
        if self.kind == KIND_TOGGLE:
            return "On" if value else "Off"
        if self.kind == KIND_DECALS:
            count = len(self.decal_ids(value))
            if not count:
                return self.default_label or "The mod's own choice"
            return f"{count:,} decal{'s' if count != 1 else ''} picked"
        if self.kind == KIND_GOODS:
            if not str(value or "").strip():
                return self.default_label or "The mod's own list"
            count = len(parse_goods(value, self.label))
            noun = self.noun or "good"
            return f"{count:,} {noun}{'s' if count != 1 else ''}"
        if self.kind == KIND_REWARDS:
            if not str(value or "").strip():
                return self.default_label or "The mod's own list"
            count = len(parse_rewards(value, self.label))
            noun = self.noun or "reward"
            return f"{count:,} {noun}{'s' if count != 1 else ''}"
        if self.kind == KIND_PICK:
            count = len(self.decal_ids(value))
            if not count:
                return self.default_label or "The mod's own choice"
            noun = self.noun or "item"
            return f"{count:,} of {len(self.options):,} {noun}s picked"
        if self.kind == KIND_CHOICE:
            option = self.option(value)
            return option.label if option is not None else str(value)
        text = format_number(value, STYLE_COMMA)
        unit = self.unit.strip()
        if unit.lower() == "x":
            return f"x{text}"
        if unit == "%":
            return f"{text}%"
        return f"{text} {unit}".strip()


def is_setting_value(value: Any) -> bool:
    """Something a setting could have been given: a finite number, or text a
    choice's value could be. Values read back from a file a person could have
    edited are passed through this, and anything else is dropped."""
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return math.isfinite(value)
    return isinstance(value, str) and bool(
        CHOICE_VALUE.match(value) or DECAL_LIST.match(value) or GOODS_LIST.match(value)
        or REWARD_LIST.match(value))


def _as_decals(value: Any, label: str) -> str:
    """Picked decal ids as a decal setting holds them: comma-separated, each
    one checked, the first of any duplicate kept. "" is the mod's default."""
    if isinstance(value, str):
        parts = [part.strip() for part in value.split(",")] if value.strip() else []
    elif isinstance(value, (list, tuple)):
        parts = [part.strip() if isinstance(part, str) else part for part in value]
    else:
        raise ValueError(f"{label} is a list of decals, not {value!r}")
    ids: list[str] = []
    for part in parts:
        if not isinstance(part, str) or not CHOICE_VALUE.match(part) or not part:
            raise ValueError(f"{label}: {part!r} is not a decal id")
        if part not in ids:
            ids.append(part)
    if len(ids) > MOST_DECALS:
        raise ValueError(f"{label}: {len(ids):,} decals is more than the {MOST_DECALS:,} allowed")
    return ",".join(ids)


def _pick_setting(entry: dict, options: tuple[SettingOption, ...], common: dict,
                  mod_ref: str, setting_id: str) -> ModSetting:
    """A pick setting: its list, and the values it means until the player ticks any."""
    listed = [option.value for option in options]
    if "" in listed:
        raise ModLoadError(mod_ref, f"setting {setting_id!r}: a pick's options all need a value")
    raw = entry.get("default", listed)
    if not isinstance(raw, list) or not raw or not all(isinstance(v, str) for v in raw):
        raise ModLoadError(
            mod_ref, f"setting {setting_id!r}: a pick's 'default' is a list of its options' values"
        )
    unknown = [v for v in raw if v not in listed]
    if unknown:
        raise ModLoadError(
            mod_ref, f"setting {setting_id!r}: 'default' lists {unknown[0]!r}, which is not one of its options"
        )
    noun = str(entry.get("noun") or "item").strip()
    if not noun.replace(" ", "").isalpha():
        raise ModLoadError(mod_ref, f"setting {setting_id!r}: 'noun' is one word for an entry, like \"quest\"")
    return ModSetting(
        kind=KIND_PICK, default="", minimum=0, maximum=0, options=options,
        default_picks=tuple(dict.fromkeys(raw)),
        default_label=str(entry.get("default_label") or "").strip(),
        noun=noun, **common,
    )


def _as_toggle(value: Any, label: str) -> int:
    """On or off, as 1 or 0 - the way it is written into SQL."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)) and value in (0, 1):
        return int(value)
    if isinstance(value, str):
        word = value.strip().lower()
        if word in _TRUE_WORDS:
            return 1
        if word in _FALSE_WORDS:
            return 0
    raise ValueError(f"{label} is either on or off, not {value!r}")


def format_number(value: int | float, style: str = STYLE_PLAIN) -> str:
    """A number as text, in one of the STYLES. Only ever digits and separators."""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if style == STYLE_PLAIN:
        return str(value)
    grouped = f"{value:,}"
    if style == STYLE_COMMA:
        return grouped
    # dot: thousands with ".", and a decimal comma for the rare non-whole value.
    return grouped.replace(",", "\0").replace(".", ",").replace("\0", ".")


def _read_options_file(name: Any, mod_dir: Path | None, mod_ref: str, setting_id: str) -> Any:
    """The list in a file beside mod.json, named by ``options_from``."""
    if not isinstance(name, str) or not name.strip():
        raise ModLoadError(mod_ref, f"setting {setting_id!r}: 'options_from' must be a file name")
    if mod_dir is None:
        raise ModLoadError(mod_ref, f"setting {setting_id!r}: 'options_from' needs a mod folder")
    folder = Path(mod_dir).resolve()
    path = (folder / name.strip()).resolve()
    if path.parent != folder:
        raise ModLoadError(
            mod_ref, f"setting {setting_id!r}: 'options_from' must name a file in the mod's own folder"
        )
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise ModLoadError(mod_ref, f"setting {setting_id!r}: could not read {name}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ModLoadError(
            mod_ref, f"setting {setting_id!r}: {name} is not valid JSON (line {exc.lineno}): {exc.msg}"
        ) from exc


def _parse_options(raw: Any, mod_ref: str, setting_id: str) -> tuple[SettingOption, ...]:
    if not isinstance(raw, list) or not raw:
        raise ModLoadError(mod_ref, f"setting {setting_id!r} is a choice, so it needs a list of options")
    if len(raw) > MOST_OPTIONS:
        raise ModLoadError(
            mod_ref, f"setting {setting_id!r} has {len(raw):,} options; the most allowed is {MOST_OPTIONS:,}"
        )
    options: list[SettingOption] = []
    values: set[str] = set()
    labels: set[str] = set()
    for position, entry in enumerate(raw, start=1):
        if not isinstance(entry, dict):
            raise ModLoadError(mod_ref, f"setting {setting_id!r}: option #{position} is not a JSON object")
        value = entry.get("value")
        if not isinstance(value, str) or not CHOICE_VALUE.match(value):
            raise ModLoadError(
                mod_ref,
                f"setting {setting_id!r}: option #{position} needs a 'value' made of letters, "
                "digits and underscores (up to 64), or empty for none",
            )
        label = str(entry.get("label") or "").strip()
        if not label:
            raise ModLoadError(mod_ref, f"setting {setting_id!r}: option {value!r} needs a 'label'")
        if value in values:
            raise ModLoadError(mod_ref, f"setting {setting_id!r} lists the value {value!r} twice")
        if label.lower() in labels:
            raise ModLoadError(
                mod_ref,
                f"setting {setting_id!r} has two options labelled {label!r}; a player "
                "could not tell them apart",
            )
        values.add(value)
        labels.add(label.lower())
        options.append(
            SettingOption(
                value=value,
                label=label,
                group=str(entry.get("group") or "").strip(),
                help=str(entry.get("help") or "").strip(),
            )
        )
    return tuple(options)


def parse_settings(raw: Any, mod_ref: str, mod_dir: Path | None = None) -> list[ModSetting]:
    """The ``settings`` array of a mod.json, checked.

    ``mod_dir`` is where an ``options_from`` file is looked for.
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ModLoadError(mod_ref, "'settings' must be an array")
    found: list[ModSetting] = []
    seen: set[str] = set()
    # Several settings commonly share one list: read and check it once.
    shared_lists: dict[str, tuple[SettingOption, ...]] = {}
    for position, entry in enumerate(raw, start=1):
        if not isinstance(entry, dict):
            raise ModLoadError(mod_ref, f"setting #{position} is not a JSON object")
        setting_id = str(entry.get("id") or "").strip()
        if not ID_PATTERN.match(setting_id):
            raise ModLoadError(
                mod_ref,
                f"setting #{position} needs an 'id' made of lower-case letters, digits "
                "and underscores, starting with a letter",
            )
        if setting_id in seen:
            raise ModLoadError(mod_ref, f"two settings are called {setting_id!r}")
        kind = str(entry.get("type") or KIND_INTEGER).strip().lower()
        if kind not in KINDS:
            raise ModLoadError(
                mod_ref, f"setting {setting_id!r} has type {kind!r}; use one of {', '.join(KINDS)}"
            )
        label = str(entry.get("label") or setting_id).strip()
        common = {
            "id": setting_id,
            "label": label,
            "help": str(entry.get("help") or "").strip(),
            "group": str(entry.get("group") or "").strip(),
        }
        if kind == KIND_TOGGLE:
            try:
                default = _as_toggle(entry.get("default", False), label)
            except ValueError:
                raise ModLoadError(
                    mod_ref, f"setting {setting_id!r}: a toggle's 'default' is true or false"
                ) from None
            found.append(ModSetting(kind=kind, default=default, minimum=0, maximum=1, **common))
            seen.add(setting_id)
            continue
        if kind == KIND_GOODS:
            try:
                default_goods = ",".join(
                    e.text() for e in parse_goods(entry.get("default", ""), label))
            except ValueError as exc:
                raise ModLoadError(mod_ref, f"setting {setting_id!r}: 'default' - {exc}") from None
            noun = str(entry.get("noun") or "good").strip()
            if not noun.replace(" ", "").isalpha():
                raise ModLoadError(
                    mod_ref, f"setting {setting_id!r}: 'noun' is one word for an entry, like \"good\"")
            found.append(
                ModSetting(
                    kind=kind, default="", minimum=0, maximum=0, default_goods=default_goods,
                    default_label=str(entry.get("default_label") or "").strip(), noun=noun,
                    **common,
                )
            )
            seen.add(setting_id)
            continue
        if kind == KIND_REWARDS:
            try:
                default_rewards = ",".join(
                    e.text() for e in parse_rewards(entry.get("default", ""), label))
            except ValueError as exc:
                raise ModLoadError(mod_ref, f"setting {setting_id!r}: 'default' - {exc}") from None
            noun = str(entry.get("noun") or "reward").strip()
            if not noun.replace(" ", "").isalpha():
                raise ModLoadError(
                    mod_ref, f"setting {setting_id!r}: 'noun' is one word for an entry, like \"reward\"")
            found.append(
                ModSetting(
                    kind=kind, default="", minimum=0, maximum=0, default_rewards=default_rewards,
                    default_label=str(entry.get("default_label") or "").strip(), noun=noun,
                    **common,
                )
            )
            seen.add(setting_id)
            continue
        if kind == KIND_DECALS:
            default_sql = entry.get("default_sql")
            if not isinstance(default_sql, str) or not DEFAULT_SQL.match(default_sql):
                raise ModLoadError(
                    mod_ref,
                    f"setting {setting_id!r} is a decal list, so it needs a 'default_sql': one "
                    "SELECT of the decal ids it means before a player picks any",
                )
            try:
                default = _as_decals(entry.get("default", ""), label)
            except ValueError as exc:
                raise ModLoadError(mod_ref, f"setting {setting_id!r}: 'default' - {exc}") from None
            found.append(
                ModSetting(
                    kind=kind, default=default, minimum=0, maximum=0,
                    default_sql=" ".join(default_sql.split()),
                    default_label=str(entry.get("default_label") or "").strip(),
                    **common,
                )
            )
            seen.add(setting_id)
            continue
        if kind in (KIND_CHOICE, KIND_PICK):
            if "options" in entry and "options_from" in entry:
                raise ModLoadError(
                    mod_ref, f"setting {setting_id!r}: give 'options' or 'options_from', not both"
                )
            if "options_from" in entry:
                key = str(entry["options_from"]).strip()
                if key not in shared_lists:
                    shared_lists[key] = _parse_options(
                        _read_options_file(entry["options_from"], mod_dir, mod_ref, setting_id),
                        mod_ref,
                        setting_id,
                    )
                options = shared_lists[key]
            else:
                options = _parse_options(entry.get("options"), mod_ref, setting_id)
            if kind == KIND_PICK:
                found.append(_pick_setting(entry, options, common, mod_ref, setting_id))
                seen.add(setting_id)
                continue
            default = entry.get("default", options[0].value)
            if not isinstance(default, str) or all(o.value != default for o in options):
                raise ModLoadError(
                    mod_ref, f"setting {setting_id!r}: 'default' must be the value of one of its options"
                )
            found.append(
                ModSetting(kind=kind, default=default, minimum=0, maximum=0, options=options, **common)
            )
            seen.add(setting_id)
            continue

        def number(key: str, required: bool) -> int | float | None:
            value = entry.get(key)
            if value is None:
                if required:
                    raise ModLoadError(mod_ref, f"setting {setting_id!r} needs a '{key}'")
                return None
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ModLoadError(mod_ref, f"setting {setting_id!r}: '{key}' must be a number")
            if kind == KIND_INTEGER and float(value) != int(value):
                raise ModLoadError(
                    mod_ref, f"setting {setting_id!r} is a whole number, so '{key}' must be one"
                )
            if abs(value) > LARGEST_ALLOWED:
                raise ModLoadError(
                    mod_ref,
                    f"setting {setting_id!r}: '{key}' is larger than the game's numbers can hold",
                )
            return int(value) if kind == KIND_INTEGER else float(value)

        default = number("default", True)
        minimum = number("min", True)
        maximum = number("max", True)
        step = number("step", False) or 1
        if minimum > maximum:
            raise ModLoadError(mod_ref, f"setting {setting_id!r}: 'min' is above 'max'")
        if not minimum <= default <= maximum:
            raise ModLoadError(
                mod_ref, f"setting {setting_id!r}: 'default' is outside 'min' and 'max'"
            )
        if step <= 0:
            raise ModLoadError(mod_ref, f"setting {setting_id!r}: 'step' must be above zero")
        found.append(
            ModSetting(
                kind=kind,
                default=default,
                minimum=minimum,
                maximum=maximum,
                step=step,
                unit=str(entry.get("unit") or "").strip(),
                **common,
            )
        )
        seen.add(setting_id)
    return found


def placeholders_in(obj: Any) -> list[tuple[str, str]]:
    """Every (name, style) a JSON value or text asks for, in order."""
    found: list[tuple[str, str]] = []
    if isinstance(obj, str):
        for match in PLACEHOLDER.finditer(obj):
            found.append((match.group(1), (match.group(2) or "").lower()))
    elif isinstance(obj, dict):
        for value in obj.values():
            found.extend(placeholders_in(value))
    elif isinstance(obj, list):
        for value in obj:
            found.extend(placeholders_in(value))
    return found


def check_placeholders(obj: Any, settings: list[ModSetting], mod_ref: str, where: str) -> set[str]:
    """Refuse a placeholder naming no setting, or a style that does not exist.

    Returns the setting names used, so the caller can warn about unused ones.
    """
    declared = {setting.id: setting for setting in settings}
    used: set[str] = set()
    for name, style in placeholders_in(obj):
        if name not in declared:
            known = ", ".join(sorted(declared)) or "none are declared"
            raise ModLoadError(
                mod_ref, f"{where} uses {{{{{name}}}}}, but there is no setting called that ({known})"
            )
        if style not in STYLES:
            raise ModLoadError(
                mod_ref,
                f"{where} asks for {{{{{name}:{style}}}}}; the styles are 'comma', 'dot' and 'label'",
            )
        is_number = declared[name].is_number
        if (style in (STYLE_COMMA, STYLE_DOT) and not is_number) or (style == STYLE_LABEL and is_number):
            raise ModLoadError(
                mod_ref,
                f"{where} asks for {{{{{name}:{style}}}}}, which does not suit a "
                f"{declared[name].kind} setting",
            )
        used.add(name)
    return used


def render_values(settings: list[ModSetting], values: dict) -> dict:
    """``values`` plus what each ``{{name:label}}`` reads as.

    This is what render and render_text are handed, so a label can be filled in
    without them needing the settings.
    """
    filled = dict(values)
    for setting in settings:
        if not setting.is_number and setting.id in values:
            filled[f"{setting.id}:{STYLE_LABEL}"] = setting.display(values[setting.id])
        if setting.is_list and setting.id in values:
            filled[setting.id] = setting.sql(values[setting.id])
    return filled


def render_text(text: str, values: dict) -> str:
    """Text with every placeholder replaced by its value, in the style asked."""

    def substitute(match: re.Match) -> str:
        name = match.group(1)
        style = (match.group(2) or "").lower()
        if style == STYLE_LABEL:
            return str(values.get(f"{name}:{STYLE_LABEL}", match.group(0)))
        if name not in values:
            return match.group(0)
        value = values[name]
        if isinstance(value, str):
            return value
        return format_number(value, style)

    return PLACEHOLDER.sub(substitute, text)


def render(obj: Any, values: dict) -> Any:
    """A JSON value with its placeholders filled in.

    A string that is *nothing but* one unstyled placeholder becomes the number
    itself, so ``"set": {"price": "{{price}}"}`` writes a number, not text.
    """
    if isinstance(obj, str):
        whole = PLACEHOLDER.fullmatch(obj.strip())
        if whole and not whole.group(2) and whole.group(1) in values:
            return values[whole.group(1)]
        return render_text(obj, values)
    if isinstance(obj, dict):
        return {key: render(value, values) for key, value in obj.items()}
    if isinstance(obj, list):
        return [render(value, values) for value in obj]
    return obj


def resolve_values(
    settings: list[ModSetting], stored: dict[str, Any] | None
) -> tuple[dict[str, int | float | str], list[str]]:
    """Defaults with the player's choices laid over them.

    A stored value that is no longer allowed - the mod's limits changed since,
    or state.json was edited by hand - falls back to the default and says so,
    rather than being written into the database.
    """
    values: dict[str, int | float | str] = {}
    problems: list[str] = []
    stored = stored or {}
    for setting in settings:
        if setting.id not in stored:
            values[setting.id] = setting.default
            continue
        try:
            values[setting.id] = setting.coerce(stored[setting.id])
        except ValueError as exc:
            values[setting.id] = setting.default
            problems.append(f"{exc}; using the default, {setting.display(setting.default)}")
    return values, problems


def parse_when(
    raw: Any, settings: list[ModSetting], mod_ref: str, where: str
) -> dict[str, tuple[int | float | str, ...]]:
    """A part's ``"when"``: which setting values it applies under.

    ``"when": {"vip": true}`` keeps a part to when the toggle ``vip`` is on;
    ``{"cost": ["kc5000", "kc10000"]}`` to either of two choices; naming more
    than one setting needs all of them to match. Every name must be one of the
    mod's settings and every value one that setting can take - a typo would
    otherwise leave the part off for good, silently.
    """
    if not isinstance(raw, dict) or not raw:
        raise ModLoadError(
            mod_ref, f"{where}: 'when' must name at least one setting, like {{\"vip\": true}}"
        )
    declared = {setting.id: setting for setting in settings}
    conditions: dict[str, tuple[int | float | str, ...]] = {}
    for name, wanted in raw.items():
        setting = declared.get(name)
        if setting is None:
            raise ModLoadError(
                mod_ref, f"{where}: 'when' names {name!r}, which is not one of this mod's settings"
            )
        listed = wanted if isinstance(wanted, list) else [wanted]
        if not listed:
            raise ModLoadError(mod_ref, f"{where}: 'when' gives {name!r} no value to match")
        allowed = []
        for value in listed:
            try:
                allowed.append(setting.coerce(value))
            except ValueError as exc:
                raise ModLoadError(mod_ref, f"{where}: 'when' - {exc}") from None
        conditions[name] = tuple(allowed)
    return conditions


def when_holds(conditions: dict[str, tuple], values: dict) -> bool:
    """Whether the chosen ``values`` meet every condition of a part's ``when``."""
    return all(values.get(name) in allowed for name, allowed in conditions.items())
