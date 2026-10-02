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
KINDS = (KIND_INTEGER, KIND_NUMBER, KIND_CHOICE, KIND_TOGGLE)
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

    @property
    def is_number(self) -> bool:
        return self.kind in NUMBER_KINDS

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
    return isinstance(value, str) and bool(CHOICE_VALUE.match(value))


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
        if kind == KIND_CHOICE:
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
