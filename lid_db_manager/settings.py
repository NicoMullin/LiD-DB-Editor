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

**Only numbers ever reach the database.** A value is checked against the mod's
own limits and turned into digits before it is written into anything, so a
setting cannot carry SQL - there is no way to type a quote into a number.

A placeholder may ask for a number style, because game text is written the way
each language writes numbers:

    {{percent}}         100000      the plain number, for SQL
    {{percent:comma}}   100,000     English
    {{percent:dot}}     100.000     German, Spanish, French, Italian, Portuguese
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from .errors import ModLoadError

KIND_INTEGER = "integer"
KIND_NUMBER = "number"
KINDS = (KIND_INTEGER, KIND_NUMBER)

STYLE_PLAIN = ""
STYLE_COMMA = "comma"
STYLE_DOT = "dot"
STYLES = (STYLE_PLAIN, STYLE_COMMA, STYLE_DOT)

# Lower-case, so "multiplier" and "Multiplier" can never be two settings.
ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
# Deliberately looser than ID_PATTERN, so a mistyped name is caught and named
# rather than left in the SQL as literal braces.
PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z][A-Za-z0-9_]*)\s*(?::\s*([A-Za-z]*)\s*)?\}\}")

# The game's integer columns are 32-bit. Anything a setting allows has to fit,
# including once it is multiplied into a value that is already large.
LARGEST_ALLOWED = 2_000_000_000


@dataclass(frozen=True)
class ModSetting:
    """One number a mod lets the player choose."""

    id: str
    label: str
    kind: str
    default: int | float
    minimum: int | float
    maximum: int | float
    step: int | float = 1
    unit: str = ""
    help: str = ""

    def coerce(self, value: Any) -> int | float:
        """``value`` as this setting's kind of number, or ValueError saying why.

        The message is written to be shown to a player as it stands.
        """
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

    def display(self, value: int | float) -> str:
        """How a value reads in the mod list: "x2", "10,000 KC", "100,000%"."""
        text = format_number(value, STYLE_COMMA)
        unit = self.unit.strip()
        if unit.lower() == "x":
            return f"x{text}"
        if unit == "%":
            return f"{text}%"
        return f"{text} {unit}".strip()


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


def parse_settings(raw: Any, mod_ref: str) -> list[ModSetting]:
    """The ``settings`` array of a mod.json, checked."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ModLoadError(mod_ref, "'settings' must be an array")
    found: list[ModSetting] = []
    seen: set[str] = set()
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
                id=setting_id,
                label=str(entry.get("label") or setting_id).strip(),
                kind=kind,
                default=default,
                minimum=minimum,
                maximum=maximum,
                step=step,
                unit=str(entry.get("unit") or "").strip(),
                help=str(entry.get("help") or "").strip(),
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
    declared = {setting.id for setting in settings}
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
                f"{where} asks for {{{{{name}:{style}}}}}; the styles are 'comma' and 'dot'",
            )
        used.add(name)
    return used


def render_text(text: str, values: dict[str, int | float]) -> str:
    """Text with every placeholder replaced by its value, in the style asked."""

    def substitute(match: re.Match) -> str:
        name = match.group(1)
        if name not in values:
            return match.group(0)
        return format_number(values[name], (match.group(2) or "").lower())

    return PLACEHOLDER.sub(substitute, text)


def render(obj: Any, values: dict[str, int | float]) -> Any:
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
) -> tuple[dict[str, int | float], list[str]]:
    """Defaults with the player's choices laid over them.

    A stored value that is no longer allowed - the mod's limits changed since,
    or state.json was edited by hand - falls back to the default and says so,
    rather than being written into the database.
    """
    values: dict[str, int | float] = {}
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
