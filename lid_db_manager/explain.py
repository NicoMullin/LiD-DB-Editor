"""Saying what a mod changes in plain English.

A diff can tell you that `master_safe_level.limit` went from 50000 to 100000 on
99 rows. That is true and almost useless. This turns it into "the most Kill
Coins the Buffalo Bank can hold, doubled, for levels 1-99".

Three things do the work, in order of how much hand-written knowledge they need:

1. **The game's own text.** Rows point at it - `master_quest.name` holds
   `'QUEST_NAME.TXT_...'` - so quests, skills and fighter types name themselves
   with nothing written by hand.
2. **The game's own descriptions.** A skill's description reads "...by #0%",
   where `#0` is that row's `val0`. Filling it in with the old and new values
   lets the game explain its own numbers, which is the only honest way to
   describe a column whose meaning changes from row to row.
3. **``explain_data.TABLES``**, for plain columns with no description to
   borrow - what `limit` means on the Buffalo Bank, and in what units.

Anything not covered falls through to its real column name and a note saying so.
Nothing here guesses.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from .explain_data import NAMES, TABLES, VALUES
from .sqlutil import quote_ident

# English. The game stores it under "int" (international), not "eng".
LANGUAGE = "int"
# Rows listed by name before it turns into a count.
NAME_LIMIT = 6
# The game's own line separator inside a description.
PARAGRAPH = "//"


@dataclass
class Change:
    """One sentence about one column, plus an example of it."""

    sentence: str
    example: str = ""
    quotes: list[str] = field(default_factory=list)  # the game's own wording


@dataclass
class TableExplanation:
    table: str
    title: str
    about: str = ""
    changes: list[Change] = field(default_factory=list)

    @property
    def described(self) -> bool:
        return self.table in TABLES


@dataclass
class Explanation:
    tables: list[TableExplanation] = field(default_factory=list)
    note: str = ""

    @property
    def empty(self) -> bool:
        return not self.tables


def load_notes(home: Path | None = None) -> dict:
    """The built-in table notes, with a home-folder file merged over them.

    Lets a player or a mod author describe a table the program has never heard
    of without editing the program. A broken file is ignored rather than fatal -
    a bad description must never stop a mod being read.
    """
    notes = {name: dict(entry) for name, entry in TABLES.items()}
    if home is None:
        return notes
    path = Path(home) / "table-notes.json"
    if not path.is_file():
        return notes
    try:
        extra = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return notes
    if not isinstance(extra, dict):
        return notes
    for table, entry in extra.items():
        if not isinstance(entry, dict):
            continue
        merged = notes.get(str(table), {})
        merged.update(entry)
        # JSON gives ["meaning", "unit"]; the code expects a pair.
        columns = merged.get("columns")
        if isinstance(columns, dict):
            merged["columns"] = {
                c: (tuple(v) if isinstance(v, list) else v) for c, v in columns.items()
            }
        notes[str(table)] = merged
    return notes


class Namer:
    """Looks names and descriptions up in the game's own text."""

    def __init__(self, db_path: Path | str | None):
        self._con: sqlite3.Connection | None = None
        if db_path and Path(db_path).is_file():
            try:
                self._con = sqlite3.connect(
                    f"file:{Path(db_path).as_posix()}?mode=ro", uri=True
                )
                self._con.row_factory = sqlite3.Row
            except sqlite3.Error:
                self._con = None
        self._cache: dict[tuple, str | None] = {}

    def close(self) -> None:
        if self._con is not None:
            self._con.close()
            self._con = None

    def text(self, pointer: str | None) -> str | None:
        """'QUEST_NAME.TXT_X' -> the English string, or None."""
        if not self._con or not isinstance(pointer, str) or "." not in pointer:
            return None
        key = ("text", pointer)
        if key in self._cache:
            return self._cache[key]
        section, text_id = pointer.split(".", 1)
        try:
            row = self._con.execute(
                "SELECT txt FROM master_text WHERE lang=? AND sct=? AND id=?",
                (LANGUAGE, section, text_id),
            ).fetchone()
        except sqlite3.Error:
            row = None
        value = " ".join(row[0].split()) if row and row[0] else None
        self._cache[key] = value
        return value

    def column(self, table: str, key_columns: list[str], key: tuple, column: str):
        """One column of one row, addressed by its key."""
        if not self._con or not key_columns or len(key_columns) != len(key):
            return None
        cache_key = ("col", table, column, key)
        if cache_key in self._cache:
            return self._cache[cache_key]
        where = " AND ".join(f"{quote_ident(c)} IS ?" for c in key_columns)
        try:
            row = self._con.execute(
                f"SELECT {quote_ident(column)} FROM {quote_ident(table)} WHERE {where}", key
            ).fetchone()
        except sqlite3.Error:
            row = None
        value = row[0] if row else None
        self._cache[cache_key] = value
        return value

    def row_values(self, table: str, key_columns: list[str], key: tuple, columns: list[str]):
        return [self.column(table, key_columns, key, c) for c in columns]

    def parameters(self, table: str, key_column: str, key, order: str, value: str) -> dict:
        """A row's numbered parameters, as {number: value}.

        Quest names and descriptions are written with #0 and #1 in them, and
        the actual words live in a separate table, one row per placeholder.
        """
        if not self._con:
            return {}
        cache_key = ("params", table, key)
        if cache_key in self._cache:
            return self._cache[cache_key]
        try:
            rows = self._con.execute(
                f"SELECT {quote_ident(order)}, {quote_ident(value)} FROM {quote_ident(table)} "
                f"WHERE {quote_ident(key_column)} = ?", (key,)
            ).fetchall()
        except sqlite3.Error:
            rows = []
        found = {r[0]: r[1] for r in rows}
        self._cache[cache_key] = found
        return found


# -- wording helpers -------------------------------------------------------


def _number(value) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        return f"{value:,.4g}"
    if value is None:
        return "nothing"
    text = str(value)
    if text in VALUES:
        return VALUES[text]
    if not text.strip():
        # An emptied column reads as "set to , for 155 rows" otherwise.
        return "nothing"
    return text if len(text) <= 60 else text[:57] + "..."


def _with_unit(value, unit: str) -> str:
    return f"{_number(value)} {unit}".strip() if unit else _number(value)


def ranges(numbers) -> str:
    """[1,2,3,7] -> '1-3, 7'. Turns 99 changed levels into four characters."""
    values = sorted({n for n in numbers if isinstance(n, int)})
    if not values:
        return ""
    out: list[str] = []
    start = previous = values[0]
    for n in values[1:]:
        if n == previous + 1:
            previous = n
            continue
        out.append(f"{start}-{previous}" if start != previous else str(start))
        start = previous = n
    out.append(f"{start}-{previous}" if start != previous else str(start))
    return ", ".join(out)


def _fill(text: str, values: list) -> str:
    """Substitute the game's #0..#9 placeholders with a row's values."""
    def swap(match):
        index = int(match.group(1))
        if index < len(values) and values[index] is not None:
            return _number(values[index])
        return match.group(0)

    return re.sub(r"#(\d)", swap, text)


def _fill_named(namer: "Namer", text: str, values: dict) -> str:
    """Same, but the values are kept in another table, keyed by placeholder.

    A value may be a pointer at the game's own text ('ATTR.TXT_1' -> 'Piercing')
    or a plain number, so try the text first and fall back to the value itself.
    """
    def swap(match):
        value = values.get(int(match.group(1)))
        if value is None:
            return match.group(0)
        return namer.text(value) or _number(value)

    # The game writes a literal # as ## in its text.
    return " ".join(re.sub(r"#(\d)", swap, text).replace("##", "#").split())


def _label_from_text(namer: "Namer", wording: str, rule: dict, subject, key_column: str) -> str:
    """A row's name, with any #0 placeholders filled from a parameter table.

    Without this, every quest in a series reads as the same name, because the
    part that tells them apart is the placeholder.
    """
    params = rule.get("params")
    if wording and "#" in wording and params:
        found = namer.parameters(
            params["table"], params.get("key", key_column), subject,
            params.get("order", "no"), params.get("value", "val"),
        )
        return _fill_named(namer, wording, found)
    return _clean_label(wording)


def plural(word: str, count: int) -> str:
    """'entry' -> 'entries', 'bonus' -> 'bonuses'. Enough for our nouns."""
    if count == 1:
        return word
    if word.endswith("y") and not word.endswith(("ay", "ey", "oy", "uy")):
        return word[:-1] + "ies"
    if word.endswith(("s", "x", "z", "ch", "sh")):
        return word + "es"
    return word + "s"


def _readable(text: str) -> str:
    return " ".join(text.replace(PARAGRAPH, " ").split())


def _clean_label(text: str) -> str:
    """Drop the game's #0 placeholders from a name it fills in at runtime.

    Quest names read "Naked Victory #0" in the file; the game substitutes a
    number when it shows them. Leaving the marker in a summary just looks like
    a bug, and the number is not ours to guess.
    """
    return " ".join(re.sub(r"#\d", "", text).replace("()", "").split()).strip(" -:")


# -- row labels ------------------------------------------------------------


def _fighter_tier(namer: Namer, key: tuple) -> str:
    parts = [namer.text(f"FTYPE_NAME.TXT_{key[0]}") or str(key[0])]
    if len(key) > 1:
        parts.append(f"grade {_number(key[1])}")
    if len(key) > 2 and key[2]:
        parts.append(f"limit break {_number(key[2])}")
    return ", ".join(parts)


def row_label(namer: Namer, table: str, key_columns: list[str], key: tuple, rule: dict) -> str:
    kind = (rule or {}).get("kind", "key")
    if kind in ("level", "number") and key:
        return f"{rule.get('word', 'level')} {_number(key[0])}"
    if kind == "labelled" and key:
        # A key that is several numbers: "grade 1, level 5".
        # A word may be left blank for a key part that speaks for itself, so
        # join the two halves rather than always putting a space between them.
        words = rule.get("words", [])
        return ", ".join(
            f"{words[i]} {_number(v)}".strip() if i < len(words) else _number(v)
            for i, v in enumerate(key)
        )
    if kind == "item" and key:
        # Most items name themselves. Blueprints do not: all 1,899 are called
        # "RMAP" or "UNKNOWN_RMAP" in the game's text, so the only useful name
        # is the weapon or armour they make. A trailing U is the unidentified
        # version of the same blueprint.
        pointer = namer.column(table, key_columns, key, rule.get("column", "name"))
        wording = _clean_label(namer.text(pointer) or "")
        item_id = str(key[0])
        if wording and wording not in ("RMAP", "UNKNOWN_RMAP"):
            return wording
        if item_id.startswith("ITMP_"):
            stem = item_id[5:]
            part = "PT_" + (stem[:-1] if stem.endswith("U") else stem)
            made = namer.text(namer.column("master_part", ["id"], (part,), "name"))
            if made:
                unknown = " (unidentified)" if item_id.endswith("U") else ""
                return f"{_clean_label(made)}{unknown} blueprint"
        return wording or item_id
    if kind == "columns" and key:
        # Some tables are keyed on a plain row number, which tells the player
        # nothing. Name the row from its own columns instead - the date a login
        # bonus lands on, rather than "12,970". "skip" drops the fillers the
        # game uses for "not applicable", like a date of 0000-00-00.
        skip = [None, ""] + list(rule.get("skip", []))
        parts = []
        for column in rule.get("columns", []):
            value = (key[key_columns.index(column)] if column in key_columns
                     else namer.column(table, key_columns, key, column))
            if value not in skip:
                parts.append(_number(value))
        return " / ".join(parts) or _number(key[0])
    if kind == "via" and key:
        # Follow an id into another table and use that row's name, so a decal's
        # odds row reads as the decal rather than as its id. The id may be a key
        # column or an ordinary one - the vending machine keys on a row number
        # and holds the item it sells in a separate column.
        def value_of(column):
            if column in key_columns:
                return key[key_columns.index(column)]
            return namer.column(table, key_columns, key, column)

        # Both sides may be several columns - a floor is keyed on id + area.
        here = rule.get("column")
        here = here if isinstance(here, list) else [here]
        there = rule.get("other_key", "id")
        there = there if isinstance(there, list) else [there]
        reference = tuple(value_of(c) for c in here)
        other = rule.get("table", "")
        pointer = namer.column(other, there, reference, rule.get("text_column", "name"))
        label = (_label_from_text(namer, namer.text(pointer) or "", rule, reference[0], there[0])
                 or _number(reference[0]))
        # Tables keyed on (thing, attribute) need the attribute too, or every
        # row of a part's six damage types reads as the same part name.
        extras = [value_of(c) for c in rule.get("with", [])]
        extras = [e for e in extras if e not in (None, "")]
        if extras:
            label += " (" + ", ".join(_number(e) for e in extras) + ")"
        return label
    if kind == "fighter_tier" and key:
        return _fighter_tier(namer, key)
    if kind == "shop_product" and key:
        product = str(key[0])
        name = NAMES.get(product)
        if name:
            return name
        # PRD_CONTINUE_2 is a different product from PRD_CONTINUE; keep the
        # number so eight revives do not all read as "Revive".
        base = re.sub(r"_(\d+)$", "", product)
        suffix = product[len(base):].lstrip("_")
        friendly = NAMES.get(base)
        return f"{friendly} {suffix}" if friendly and suffix else (friendly or product)
    if kind == "text" and key:
        pointer = namer.column(table, key_columns, key, rule.get("column", "name"))
        wording = namer.text(pointer) or ""
        return _label_from_text(namer, wording, rule, key[0], key_columns[0]) or str(key[0])
    # Skip empty key parts - a blank condition column should not read as the
    # word "nothing" in the middle of a row's name.
    parts = [_number(k) for k in key if k not in (None, "")]
    return " / ".join(parts) or "(row)"


def _label_list(labels: list[str], word: str, examples: bool = True) -> str:
    """Join names readably. Names contain commas, so separate with semicolons.

    A table marked "no_examples" is counted but never quoted from - the
    banned-word list is the reason it exists, since printing a sample of it
    would put slurs in front of the player to no purpose.
    """
    if not examples:
        return f"{len(labels):,} {word}"
    if len(labels) == 1:
        return labels[0]
    shown = "; ".join(labels[:NAME_LIMIT])
    if len(labels) > NAME_LIMIT:
        shown += f"; and {len(labels) - NAME_LIMIT} more"
    return f"{len(labels):,} {word}: {shown}"


# -- the explanation itself ------------------------------------------------


def _how_changed(items: list[tuple], unit: str) -> tuple[str, str]:
    """(verb, tail) for "<verb> <the thing><tail>".

    The verb belongs to the mod, not to the thing being changed - "Sets the
    points needed ... to 0" rather than "The points needed ... is now 0", which
    disagrees whenever the description is plural.
    """
    after = {new for _, _, new in items}
    if len(after) == 1:
        return "Sets", f" to {_with_unit(next(iter(after)), unit)}"
    numeric = [(b, n) for _, b, n in items if isinstance(b, (int, float)) and isinstance(n, (int, float)) and b]
    if len(numeric) == len(items) and numeric:
        ratios = {round(n / b, 4) for b, n in numeric}
        if len(ratios) == 1:
            ratio = next(iter(ratios))
            if ratio == 2:
                return "Doubles", ""
            if ratio == 0.5:
                return "Halves", ""
            if ratio > 0:
                return "Multiplies", f" by {ratio:g}"
    if numeric and len(numeric) == len(items):
        if all(n > b for b, n in numeric):
            return "Raises", ""
        if all(n < b for b, n in numeric):
            return "Lowers", ""
    return "Changes", ""


def _describe_values(namer: Namer, table: str, td, rule: dict, items: list[tuple], column: str) -> Change | None:
    """Use the game's own description for a column whose meaning varies by row.

    ``val0`` is the EXP bonus on one skill and something else on the next, so
    the only honest description is the one the game already writes - with the
    old and new numbers filled into it.
    """
    pointer_column = rule.get("column", "desc")
    value_columns = list(rule.get("values", []))
    if column not in value_columns:
        return None
    quotes: list[str] = []
    for key, before, after in items[:NAME_LIMIT]:
        pointer = namer.column(table, td.key_columns, key, pointer_column)
        description = namer.text(pointer)
        name = row_label(namer, table, td.key_columns, key, td_rule(td))
        if not description:
            quotes.append(f"{name}: {column} {_number(before)} to {_number(after)}")
            continue
        values = namer.row_values(table, td.key_columns, key, value_columns)
        if "#" in description:
            after_values = list(values)
            after_values[value_columns.index(column)] = after
            quotes.append(f"{name}: “{_readable(_fill(description, values))}”")
            quotes.append(f"    becomes “{_readable(_fill(description, after_values))}”")
        else:
            quotes.append(f"{name}: {column} {_number(before)} to {_number(after)}")
            quotes.append(f"    in game: “{_readable(description)}”")
    more = "" if len(items) <= NAME_LIMIT else f" (and {len(items) - NAME_LIMIT} more)"
    return Change(
        sentence=f"Changes what {len(items)} of them do{more}, in the game's own words:",
        quotes=quotes,
    )


def _explain_text(td, rule: dict, items: list[tuple], described) -> Change:
    """Game text, counted per line rather than per language.

    One changed name is ten changed rows - one for each language the game
    ships. Saying "1,528 rows" is technically true and tells a player nothing.
    """
    lang_at = td.key_columns.index(rule["lang"])
    label_at = td.key_columns.index(rule["label"]) if rule.get("label") in td.key_columns else 0
    lines = {tuple(v for i, v in enumerate(key) if i != lang_at) for key, _, _ in items}
    languages = {key[lang_at] for key, _, _ in items}
    meaning = (described or ("the wording shown in game", ""))[0]
    sentence = (
        f"Changes {meaning}, for {len(lines)} {plural('line', len(lines))} of text, "
        f"in {len(languages)} {plural('language', len(languages))}."
    )
    english = [i for i in items if i[0][lang_at] == LANGUAGE] or items
    key, before, after = english[0]
    example = f"{key[label_at]}: “{_readable(str(before))}” → “{_readable(str(after))}”"
    return Change(sentence=sentence, example=example)


def td_rule(td) -> dict:
    return getattr(td, "_row_rule", {}) or {}


def explain_delta(delta, db_path=None, home=None) -> Explanation:
    """Turn a mod's delta into sentences. Never raises - this is a display."""
    explanation = Explanation()
    if delta is None:
        return explanation
    notes = load_notes(home)
    namer = Namer(db_path)
    try:
        for td in delta.tables:
            entry = notes.get(td.table, {})
            rule = entry.get("row", {"kind": "key"})
            td._row_rule = rule
            out = TableExplanation(
                table=td.table,
                title=entry.get("title") or td.table,
                about=entry.get("about", ""),
            )
            columns = entry.get("columns", {})
            describes = entry.get("describes")
            # Some tables are counted but never quoted from - see _label_list.
            examples = not entry.get("no_examples")

            by_column: dict[str, list[tuple]] = {}
            for update in td.updates:
                for column, new in update.changes.items():
                    by_column.setdefault(column, []).append(
                        (update.key, update.before.get(column), new)
                    )

            for column, items in by_column.items():
                if rule.get("kind") == "game_text" and rule.get("lang") in td.key_columns:
                    out.changes.append(_explain_text(td, rule, items, columns.get(column)))
                    continue
                if describes:
                    change = _describe_values(namer, td.table, td, describes, items, column)
                    if change is not None:
                        out.changes.append(change)
                        continue
                meaning, unit = columns.get(column, (None, ""))
                if meaning is None:
                    meaning = f"“{column}” (nobody has described this one yet)"
                labels = [row_label(namer, td.table, td.key_columns, k, rule) for k, _, _ in items]
                if rule.get("kind") in ("level", "number"):
                    word = rule.get("word", "level")
                    span = ranges([k[0] for k, _, _ in items])
                    which = f"for {plural(word, len(items))} {span}"
                else:
                    which = "for " + _label_list(
                        labels, plural(entry.get("word", "row"), len(labels)), examples)
                verb, tail = _how_changed(items, unit)
                sentence = f"{verb} {meaning}{tail}, {which}."
                key, before, after = items[0]
                example = (
                    f"{labels[0]}: {_with_unit(before, unit)} → {_with_unit(after, unit)}"
                    if examples else ""
                )
                out.changes.append(Change(sentence=sentence, example=example))

            if td.inserts:
                word = entry.get("word", "row")
                out.changes.append(Change(
                    sentence=f"Adds {len(td.inserts):,} new {plural(word, len(td.inserts))}."
                ))
            if td.deletes:
                labels = [row_label(namer, td.table, td.key_columns, k, rule) for k in td.deletes]
                out.changes.append(
                    Change(sentence="Removes " + _label_list(
                        labels, plural(entry.get("word", "row"), len(labels)), examples) + ".")
                )
            if entry.get("unused"):
                out.changes.insert(0, Change(
                    sentence="The offline game never reads this table, so changing "
                             "it is unlikely to do anything."
                ))
            elif entry.get("empty"):
                # Weaker than "unused", and deliberately so: an empty table is
                # not proof the game ignores it, only that vanilla fills in
                # nothing. Adding rows here may well do something.
                out.changes.insert(0, Change(
                    sentence="Vanilla leaves this table empty. Whether the game "
                             "acts on rows added to it is not known."
                ))
            if getattr(td, "is_new_table", False):
                out.changes.insert(0, Change(
                    sentence="Adds this table, which the game does not normally have."
                ))
            if out.changes:
                explanation.tables.append(out)
    finally:
        namer.close()

    undescribed = [t.table for t in explanation.tables if not t.described]
    if undescribed:
        explanation.note = (
            f"{len(undescribed)} table(s) here have no description yet, so their column "
            "names are shown as they are. They can be described in table-notes.json."
        )
    return explanation
