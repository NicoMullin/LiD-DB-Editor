"""Building a mod by editing values, rather than by writing SQL.

The whole thing rests on one decision: **this never writes a mod file.** It
copies the vanilla database, lets you change values in the copy, and then hands
that copy to the same import path a downloaded modded `masters.db` goes through.

That matters. It means the builder cannot invent a broken mod format, cannot
skip validation, and gets snapshots, load order, switchable parts and
untick-to-undo for free, because all of that already works on imported
databases. The builder's only job is to produce an edited file.

It also carries what has been learned about the game since, because several of
those lessons were edits that looked right and did nothing (see builder_data):
dates stored two different ways, floors stored twice, decals the Steam game
never loads, and prices far past anything the game uses, which crashed it.

Nothing here imports Qt - the window in ``ui/builder_window.py`` is a view onto
this.
"""

from __future__ import annotations

import re
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import builder_data, db_record, explain, install
from .sqlutil import quote_ident

# Rows read in one go. Above this the table is paged, so the quest list does not
# try to put 6,252 rows into a grid at once.
PAGE = 200

# The most schedule months one "keep it rotating" may add. Far more than anyone
# needs; it only stops a typo in the year from adding thousands of rows.
MAX_SCHEDULE_MONTHS = 600

SCHEDULE_TABLE = "master_automaticshop_schedule"
SCHEDULE_DATE = "%Y-%m-%d %H:%M:%S"


@dataclass
class Column:
    """One editable column, with everything the UI needs to show it well."""

    name: str
    meaning: str           # "the cost to unlock it"
    unit: str              # "KC"
    is_key: bool           # part of the row's identity, so never editable
    is_list: bool          # a comma-separated list; edit as text, not a number
    kind: str              # "int" | "real" | "text"
    described: bool        # False when nobody has written a meaning yet
    date: str = ""         # "" | "epoch" | "text" - see builder_data.DATE_COLUMNS

    @property
    def heading(self) -> str:
        """What to put at the top of the column."""
        words = self.meaning if self.described else self.name
        unit = "date, UTC" if self.date == "epoch" else "date" if self.date else self.unit
        return f"{words} ({unit})" if unit else words

    @property
    def is_number(self) -> bool:
        return self.kind in ("int", "real") and not self.is_list and not self.date

    def show(self, value) -> str:
        """The value as the editor shows it - a date as a date."""
        if value is None:
            return ""
        if self.date == "epoch":
            return show_epoch(value)
        return str(value)


@dataclass
class Row:
    key: tuple
    label: str             # "All-rounder, grade 2"
    values: dict           # column -> value
    added: bool = False    # a row this mod adds, rather than one it edits
    skipped: str = ""      # why the Steam game never loads it, if it does not


@dataclass
class TableView:
    table: str
    title: str
    about: str
    columns: list[Column]
    rows: list[Row]
    total: int             # rows in the table, not just this page
    tips: list[str]

    @property
    def editable(self) -> list[Column]:
        return [c for c in self.columns if not c.is_key]


@dataclass
class Risk:
    """A changed value past anything the stock game uses in that column."""

    table: str
    key: tuple
    column: str
    where: str             # "Shop prices - Mushroom Club decal draw"
    message: str

    def __str__(self) -> str:
        return f"{self.where}: {self.message}"


class BuildError(Exception):
    pass


# -- dates ------------------------------------------------------------------


# One date format for the whole program - the plain-English tab uses it too.
show_epoch = explain.show_epoch


_DATE_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d")


def _parse_date(typed: str) -> datetime | None:
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(typed, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def read_epoch(typed) -> int:
    """A date typed as 2027-01-31 (UTC), or the raw number of seconds."""
    if isinstance(typed, bool):
        raise BuildError("a date cannot be yes or no")
    if isinstance(typed, (int, float)):
        return int(typed)
    text = str(typed).strip()
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    when = _parse_date(text)
    if when is None:
        raise BuildError(
            f"\"{typed}\" is not a date. Type it as 2027-01-31, or 2027-01-31 10:00."
        )
    return int(when.timestamp())


def read_text_date(typed) -> str:
    """A date for a column that stores it as text: always '2027-01-31 00:00:00'."""
    when = _parse_date(str(typed).strip())
    if when is None:
        raise BuildError(
            f"\"{typed}\" is not a date. Type it as 2027-01-31, or 2027-01-31 10:00."
        )
    return when.strftime(SCHEDULE_DATE)


def _next_month(when: datetime) -> datetime:
    return when.replace(year=when.year + (when.month == 12),
                        month=1 if when.month == 12 else when.month + 1)


class ModBuilder:
    """A scratch copy of the database that remembers what you changed."""

    def __init__(self, vanilla: Path, scratch_dir: Path | None = None):
        vanilla = Path(vanilla)
        if not vanilla.is_file():
            raise BuildError(
                "Building a mod needs a clean copy of the database to start from. "
                "Save your mod list once - that writes masters.db.original - or "
                "point the manager at a clean masters.db."
            )
        self.vanilla = vanilla
        self._dir = Path(scratch_dir or tempfile.mkdtemp(prefix="lid-build-"))
        self._dir.mkdir(parents=True, exist_ok=True)
        self.scratch = self._dir / "masters.db"
        shutil.copy2(vanilla, self.scratch)
        self._con = sqlite3.connect(str(self.scratch))
        self._con.row_factory = sqlite3.Row
        # Names come from the vanilla file, so what a thing is called cannot
        # drift while you edit it. Rows this mod adds are not in there, so they
        # are named from the scratch copy instead.
        self._namer = explain.Namer(vanilla)
        self._fresh_namer: explain.Namer | None = None
        self._stock: sqlite3.Connection | None = None
        self._notes = explain.load_notes()
        # (table, key, column) -> value it had in vanilla, so an edit can be
        # taken back without re-reading the whole file.
        self._before: dict[tuple, object] = {}
        # Rows added rather than edited, as (table, key), in the order added.
        self._added: list[tuple[str, tuple]] = []
        self._schema: dict[str, tuple[list[str], list[Column]]] = {}
        self._ranges: dict[tuple[str, str], tuple | None] = {}

    # -- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        try:
            self._con.close()
            if self._stock is not None:
                self._stock.close()
                self._stock = None
        finally:
            self._namer.close()
            if self._fresh_namer is not None:
                self._fresh_namer.close()
                self._fresh_namer = None
            shutil.rmtree(self._dir, ignore_errors=True)

    def __enter__(self) -> "ModBuilder":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- what is in the database -------------------------------------------

    def _present(self) -> set[str]:
        return {r[0] for r in self._con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}

    def groups(self) -> list[tuple[str, str, list[str]]]:
        """The headings and their tables, with missing tables dropped.

        A table can be missing when the player's game version differs from the
        one these lists were written against. That is not worth an error - the
        heading simply has one less thing under it.
        """
        present = self._present()
        out = []
        for name, note, tables in builder_data.GROUPS:
            live = [t for t in tables if t in present]
            if live:
                out.append((name, note, live))
        rest = sorted(present - builder_data.grouped_tables())
        rest = [t for t in rest if not t.startswith("sqlite_") and not db_record.is_ours(t)]
        if rest:
            out.append((builder_data.EVERYTHING_ELSE,
                        builder_data.EVERYTHING_ELSE_NOTE, rest))
        return out

    def title_of(self, table: str) -> str:
        return (self._notes.get(table, {}).get("title") or table)

    def about_of(self, table: str) -> str:
        return self._notes.get(table, {}).get("about", "")

    def tips(self, table: str) -> list[str]:
        return list(builder_data.TIPS.get(table, []))

    def priced_in(self, unit: str) -> list[tuple[str, str, str]]:
        """Every column measured in one currency, as (table, column, meaning).

        Only tables this database actually has, so a click always lands.
        """
        present = self._present()
        return [c for c in currency_columns(unit, self._notes) if c[0] in present]

    def _describe(self, table: str) -> tuple[list[str], list[Column]]:
        if table in self._schema:
            return self._schema[table]
        info = list(self._con.execute(f"PRAGMA table_info({quote_ident(table)})"))
        if not info:
            raise BuildError(f"{table} is not a table in this database")
        entry = self._notes.get(table, {})
        described = entry.get("columns", {})
        keys = [r["name"] for r in info if r["pk"]]
        columns = []
        for r in info:
            name = r["name"]
            meaning, unit = described.get(name, ("", ""))
            declared = (r["type"] or "").upper()
            kind = ("int" if "INT" in declared
                    else "real" if any(x in declared for x in ("REAL", "FLOA", "DOUB"))
                    else "text")
            columns.append(Column(
                name=name,
                meaning=meaning or name,
                unit=unit,
                is_key=name in keys,
                is_list=(table, name) in builder_data.LIST_COLUMNS,
                kind=kind,
                described=bool(meaning),
                date=builder_data.DATE_COLUMNS.get((table, name), ""),
            ))
        # Described columns first, in the order they were written down, then
        # the rest. master_part has 91 columns and only a handful are worth
        # touching; leaving them in schema order buries those behind fifty
        # things nobody has a name for.
        order = {name: i for i, name in enumerate(described)}
        columns.sort(key=lambda c: (not c.described, order.get(c.name, 0)))
        self._schema[table] = (keys or ["rowid"], columns)
        return self._schema[table]

    def columns(self, table: str) -> list[Column]:
        return self._describe(table)[1]

    def keys_of(self, table: str) -> list[str]:
        return self._describe(table)[0]

    def _column(self, table: str, column: str) -> Column:
        match = next((c for c in self.columns(table) if c.name == column), None)
        if match is None:
            raise BuildError(f"{table} has no column called {column}")
        return match

    # -- reading -----------------------------------------------------------

    def _added_keys(self, table: str) -> set[tuple]:
        return {key for t, key in self._added if t == table}

    def _skipped_keys(self, table: str, keys: list[str]) -> tuple[set[tuple], str]:
        rule = builder_data.SKIPPED_ROWS.get(table)
        if not rule:
            return set(), ""
        condition, words = rule
        select = ", ".join(quote_ident(k) for k in keys)
        try:
            found = self._con.execute(
                f"SELECT {select} FROM {quote_ident(table)} WHERE {condition}"
            ).fetchall()
        except sqlite3.Error:
            # A database without those columns simply has nothing to flag.
            return set(), ""
        return {tuple(r) for r in found}, words

    def label(self, table: str, key: tuple, values: dict | None = None) -> str:
        """What a row is called - the game's own words where it has them."""
        keys, _ = self._describe(table)
        rule = self._notes.get(table, {}).get("row", {"kind": "key"})
        if rule.get("kind") == "columns" and values is not None:
            # Named from the row's own columns, so read them from the row as it
            # is now - a row this mod added is not in the vanilla file at all.
            skip = [None, ""] + list(rule.get("skip", []))
            parts = [explain._number(values.get(c)) for c in rule.get("columns", [])
                     if values.get(c) not in skip]
            if parts:
                return " / ".join(parts)
        namer = self._namer
        if key in self._added_keys(table):
            if self._fresh_namer is None:
                self._fresh_namer = explain.Namer(self.scratch)
            namer = self._fresh_namer
        return explain.row_label(namer, table, keys, key, rule)

    def view(self, table: str, *, search: str = "", offset: int = 0,
             limit: int = PAGE) -> TableView:
        keys, columns = self._describe(table)
        entry = self._notes.get(table, {})
        names = [*keys, *(c.name for c in columns if c.name not in keys)]
        select = ", ".join(quote_ident(c) for c in names)
        where, params = "", []
        if search:
            # Search every column as text. Slower than an index, but these are
            # small tables and it saves asking people which column to look in.
            terms = " OR ".join(
                f"CAST({quote_ident(c.name)} AS TEXT) LIKE ?" for c in columns
            )
            where = f" WHERE {terms}"
            params = [f"%{search}%"] * len(columns)
        total = self._con.execute(
            f"SELECT count(*) FROM {quote_ident(table)}{where}", params
        ).fetchone()[0]
        sql = (f"SELECT {select} FROM {quote_ident(table)}{where} "
               f"LIMIT ? OFFSET ?")
        found = self._con.execute(sql, [*params, limit, offset]).fetchall()
        added = self._added_keys(table)
        skipped, why = self._skipped_keys(table, keys)
        rows = []
        for r in found:
            key = tuple(r[i] for i in range(len(keys)))
            values = {c.name: r[names.index(c.name)] for c in columns}
            label = self.label(table, key, values)
            flag = why if key in skipped else ""
            if flag:
                label = f"{label} [{flag}]"
            if key in added:
                label = f"{label} (new)"
            rows.append(Row(key=key, label=label, values=values,
                            added=key in added, skipped=flag))
        return TableView(
            table=table,
            title=entry.get("title") or table,
            about=entry.get("about", ""),
            columns=columns,
            rows=rows,
            total=total,
            tips=self.tips(table),
        )

    def value(self, table: str, key: tuple, column: str):
        keys, _ = self._describe(table)
        row = self._con.execute(
            f"SELECT {quote_ident(column)} FROM {quote_ident(table)} WHERE "
            + " AND ".join(f"{quote_ident(k)} IS ?" for k in keys),
            key,
        ).fetchone()
        if row is None:
            raise BuildError(f"no such row in {self.title_of(table)}")
        return row[0]

    # -- how far is too far ------------------------------------------------

    def stock_range(self, table: str, column: str) -> tuple | None:
        """The lowest and highest number the STOCK game has in a column.

        Read from the vanilla file, not the scratch copy, so it does not move as
        you edit. None when the column holds no numbers there.
        """
        cell = (table, column)
        if cell in self._ranges:
            return self._ranges[cell]
        if self._stock is None:
            self._stock = sqlite3.connect(f"file:{self.vanilla.as_posix()}?mode=ro", uri=True)
        try:
            row = self._stock.execute(
                f"SELECT min({quote_ident(column)}), max({quote_ident(column)}) "
                f"FROM {quote_ident(table)} "
                f"WHERE typeof({quote_ident(column)}) IN ('integer', 'real')"
            ).fetchone()
        except sqlite3.Error:
            row = None
        found = tuple(row) if row and row[0] is not None else None
        self._ranges[cell] = found
        return found

    def range_note(self, table: str, column: str, value) -> str:
        """Why a value is worth a second look, or "" when it is ordinary.

        This warns and never refuses. Going past the stock game's numbers is
        sometimes the whole point - a grade 10 fighter works - but a price of
        1,000,000 where nothing in the game passes 200,000 crashed it.
        """
        match = self._column(table, column)
        if not match.is_number or isinstance(value, bool) or not isinstance(value, (int, float)):
            return ""
        found = self.stock_range(table, column)
        if not found:
            return ""
        low, high = found
        if value > high:
            if low == high:
                return (f"{explain._number(value)} - the stock game has "
                        f"{explain._number(high)} on every row here")
            return (f"{explain._number(value)} is more than anything the stock game "
                    f"uses here (highest {explain._number(high)})")
        if value < low:
            if low == high:
                return (f"{explain._number(value)} - the stock game has "
                        f"{explain._number(low)} on every row here")
            return (f"{explain._number(value)} is less than anything the stock game "
                    f"uses here (lowest {explain._number(low)})")
        return ""

    def risks(self, *, labelled: bool = True) -> list[Risk]:
        """Every changed value past the stock game's range, for the save step.

        Naming each row costs a lookup or two, so a caller that only wants the
        count can skip it.
        """
        out = []
        for table, key, column in self._before:
            try:
                note = self.range_note(table, column, self.value(table, key, column))
            except BuildError:
                continue
            if note:
                where = (f"{self.title_of(table)} - {self.label(table, key)}"
                         if labelled else self.title_of(table))
                out.append(Risk(table, key, column, where, note))
        return out

    # -- writing -----------------------------------------------------------

    def _write(self, table: str, key: tuple, column: str, value) -> None:
        """Change one cell, remembering what it was so it can go back."""
        keys, _ = self._describe(table)
        where = " AND ".join(f"{quote_ident(k)} IS ?" for k in keys)
        cell = (table, key, column)
        # A row this mod adds is a change in itself; its cells are not counted
        # again, and there is no vanilla value for them to go back to.
        fresh = key in self._added_keys(table)
        if not fresh and cell not in self._before:
            self._before[cell] = self.value(table, key, column)
        done = self._con.execute(
            f"UPDATE {quote_ident(table)} SET {quote_ident(column)} = ? WHERE {where}",
            [value, *key],
        )
        if not done.rowcount:
            raise BuildError(f"no such row in {self.title_of(table)}")
        # Back to where it started is not a change.
        if not fresh and self._before[cell] == value:
            del self._before[cell]

    def _mirror(self, table: str, key: tuple, column: str, old, value) -> None:
        """Make the same edit to the other copies of a table stored twice.

        Only where the copy has the column, is keyed the same way, and held the
        same value as the table being edited - if the copies already disagree,
        they were meant to, and one edit should not flatten both.
        """
        keys, _ = self._describe(table)
        present = self._present()
        for other in builder_data.mirrors_of(table):
            if other not in present:
                continue
            other_keys, other_columns = self._describe(other)
            if other_keys != keys or not any(c.name == column for c in other_columns):
                continue
            try:
                current = self.value(other, key, column)
            except BuildError:
                continue
            if current == old:
                self._write(other, key, column, value)

    def set_value(self, table: str, key: tuple, column: str, value, *,
                  commit: bool = True) -> None:
        """Change one cell in the scratch copy."""
        match = self._column(table, column)
        if match.is_key:
            raise BuildError(
                f"{column} is part of what identifies the row, so changing it "
                "would move the row rather than edit it"
            )
        value = self._coerce(match, value)
        key = tuple(key)
        old = self.value(table, key, column)
        self._write(table, key, column, value)
        if builder_data.mirrors_of(table):
            self._mirror(table, key, column, old, value)
        if commit:
            self._con.commit()

    def set_many(self, table: str, keys_: list[tuple], column: str, value) -> int:
        """The same value across many rows - "every revive costs 1"."""
        for key in keys_:
            self.set_value(table, key, column, value, commit=False)
        self._con.commit()
        return len(keys_)

    def scale(self, table: str, keys_: list[tuple], column: str, factor: float) -> int:
        """Multiply a column - "double every drop rate".

        Whole-number columns stay whole numbers, because writing 1.5 into a
        column the game reads as an integer is how you get a crash rather than
        a mod.
        """
        match = self._column(table, column)
        if match.kind == "text" or match.is_list:
            raise BuildError(f"{match.heading} is not a number, so it cannot be scaled")
        if match.date:
            raise BuildError(f"{match.heading} is a date, so it cannot be scaled")
        changed = 0
        for key in keys_:
            current = self.value(table, key, column)
            if not isinstance(current, (int, float)) or isinstance(current, bool):
                continue
            scaled = current * factor
            self.set_value(table, key, column,
                           int(round(scaled)) if match.kind == "int" else scaled,
                           commit=False)
            changed += 1
        self._con.commit()
        return changed

    def revert_cell(self, table: str, key: tuple, column: str) -> None:
        cell = (table, key, column)
        if cell in self._before:
            self.set_value(table, key, column, self._before[cell])

    def revert_all(self) -> None:
        """Back to vanilla: added rows removed, every edited cell put back."""
        for table, key in reversed(self._added):
            keys, _ = self._describe(table)
            self._con.execute(
                f"DELETE FROM {quote_ident(table)} WHERE "
                + " AND ".join(f"{quote_ident(k)} IS ?" for k in keys),
                key,
            )
        self._added.clear()
        for (table, key, column), value in list(self._before.items()):
            self._write(table, key, column, value)
        self._before.clear()
        self._con.commit()

    @staticmethod
    def _coerce(column: Column, value):
        """Turn what was typed into what the column holds."""
        if column.date == "epoch":
            return read_epoch(value)
        if column.date == "text":
            return read_text_date(value)
        if value is None or (isinstance(value, str) and value.strip() == ""):
            return "" if column.kind == "text" else 0
        if column.is_list or column.kind == "text":
            return str(value)
        try:
            if column.kind == "int":
                if isinstance(value, str):
                    value = value.strip().replace(",", "")
                return int(value)
            return float(value)
        except (TypeError, ValueError):
            raise BuildError(
                f"{column.heading} holds a whole number, and \"{value}\" is not one"
                if column.kind == "int" else
                f"{column.heading} holds a number, and \"{value}\" is not one"
            ) from None

    # -- adding rows -------------------------------------------------------

    def add_row(self, table: str, values: dict) -> tuple:
        """Put a new row in. Counted as one change, however many columns it has.

        Returns the new row's key.
        """
        keys, columns = self._describe(table)
        known = {c.name for c in columns}
        unknown = set(values) - known
        if unknown:
            raise BuildError(f"{table} has no column called {', '.join(sorted(unknown))}")
        names = list(values)
        placeholders = ", ".join("?" for _ in names)
        try:
            done = self._con.execute(
                f"INSERT INTO {quote_ident(table)} "
                f"({', '.join(quote_ident(c) for c in names)}) VALUES ({placeholders})",
                [values[c] for c in names],
            )
        except sqlite3.IntegrityError as exc:
            raise BuildError(f"{self.title_of(table)} already has that row ({exc})") from None
        self._con.commit()
        key = (done.lastrowid,) if keys == ["rowid"] else tuple(values.get(k) for k in keys)
        self._added.append((table, key))
        return key

    def copy_row(self, table: str, key: tuple, new_key: dict | None = None) -> tuple:
        """Add a copy of a row under a new key - a new grade, a new decal.

        The copy starts identical, so the only thing to decide is what
        identifies it. A table keyed on a plain row number needs nothing.
        """
        keys, columns = self._describe(table)
        row = self._con.execute(
            f"SELECT * FROM {quote_ident(table)} WHERE "
            + " AND ".join(f"{quote_ident(k)} IS ?" for k in keys),
            tuple(key),
        ).fetchone()
        if row is None:
            raise BuildError(f"no such row in {self.title_of(table)}")
        values = {c.name: row[c.name] for c in columns}
        if keys != ["rowid"]:
            new_key = dict(new_key or {})
            if set(new_key) != set(keys):
                raise BuildError(
                    "Say what identifies the copy: " + ", ".join(keys) + "."
                )
            by_name = {c.name: c for c in columns}
            fixed = {k: self._coerce(by_name[k], new_key[k]) for k in keys}
            if any(isinstance(v, str) and not v.strip() for v in fixed.values()):
                raise BuildError("A copy needs something in every part of its key.")
            if tuple(fixed[k] for k in keys) == tuple(key):
                raise BuildError("The copy needs a different key from the row it copies.")
            clash = self._con.execute(
                f"SELECT 1 FROM {quote_ident(table)} WHERE "
                + " AND ".join(f"{quote_ident(k)} IS ?" for k in keys),
                [fixed[k] for k in keys],
            ).fetchone()
            if clash:
                raise BuildError(f"{self.title_of(table)} already has a row with that key.")
            values.update(fixed)
        return self.add_row(table, values)

    # -- the vending machine -----------------------------------------------

    def tabs_in_use(self) -> list[str]:
        """The machine's lists, known ones first, then anything a mod added."""
        found = {r[0] for r in self._con.execute(
            f"SELECT DISTINCT lineup_id FROM {quote_ident(LINEUP_TABLE)}"
        )}
        return [t for t in LINEUP_TABS if t in found] + sorted(found - set(LINEUP_TABS))

    @staticmethod
    def lineup_name(lineup_id: str) -> str:
        return builder_data.LINEUP_NAMES.get(lineup_id, lineup_id)

    def tab_defaults(self, lineup_id: str) -> dict:
        """How the rows already on a list are set up.

        New rows copy this, so a new row is consistent with its neighbours -
        above all its currency_type, which decides which tab of the machine it
        shows on (see builder_data.CURRENCY_TYPES).
        """
        row = self._con.execute(
            f"SELECT currency_type, is_stable, stock, pack_count, "
            f"max(display_priority) AS top, count(*) AS n "
            f"FROM {quote_ident(LINEUP_TABLE)} WHERE lineup_id = ?", (lineup_id,)
        ).fetchone()
        if not row or not row["n"]:
            return {"currency_type": 0, "is_stable": 1, "stock": 1,
                    "pack_count": 1, "display_priority": 0}
        return {
            "currency_type": row["currency_type"],
            "is_stable": row["is_stable"],
            "stock": row["stock"],
            "pack_count": row["pack_count"],
            "display_priority": row["top"] or 0,
        }

    def tab_currency(self, lineup_id: str) -> tuple[str, str] | None:
        """(what the list charges in, the master_item column holding it)."""
        return builder_data.CURRENCY_TYPES.get(self.tab_defaults(lineup_id)["currency_type"])

    def catalogue(self, *, category: str = "", search: str = "",
                  limit: int = 400) -> list[Sellable]:
        """Everything the machine could be made to sell, named properly."""
        types: tuple[str, ...] = ()
        for name, kinds in SELLABLE_CATEGORIES.items():
            if not category or category == name:
                types += kinds
        if not types:
            return []
        rows = self._con.execute(
            "SELECT itemid, itemtype, name, rarity, buy_money, buy_recycle_point, "
            "buy_bloodnium FROM master_item WHERE itemtype IN "
            f"({', '.join('?' for _ in types)})", list(types)
        ).fetchall()
        sold = {}
        for r in self._con.execute(
            f"SELECT type_id, group_concat(DISTINCT lineup_id) AS tabs "
            f"FROM {quote_ident(LINEUP_TABLE)} GROUP BY type_id"
        ):
            sold[r["type_id"]] = r["tabs"] or ""
        wanted = search.lower().strip()
        out = []
        for r in rows:
            item_id = r["itemid"]
            if r["itemtype"] == "ITTP_RMAP":
                part = blueprint_part_id(item_id)
                made = self._namer.column("master_part", ["id"], (part,), "name")
                label = self._namer.text(made) or part
                unknown = " (unidentified)" if item_id.endswith("U") else ""
                name = f"{label}{unknown} - blueprint"
                category_name = "Blueprints"
            else:
                name = self._namer.text(r["name"]) or item_id
                category_name = next(
                    (k for k, v in SELLABLE_CATEGORIES.items() if r["itemtype"] in v),
                    "Other",
                )
            if wanted and wanted not in name.lower() and wanted not in item_id.lower():
                continue
            out.append(Sellable(
                item_id=item_id, name=name, category=category_name,
                rarity=r["rarity"] or 0, money=r["buy_money"] or 0,
                recycle=r["buy_recycle_point"] or 0,
                bloodnium=r["buy_bloodnium"] or 0,
                already=sold.get(item_id, ""),
            ))
        out.sort(key=lambda s: (s.category, s.name))
        return out[:limit]

    def stock_machine(self, item_ids: list[str], lineup_id: str,
                      price: int | None = None) -> int:
        """Put items on one of the machine's lists.

        A price of None leaves the item's own price alone. Otherwise the item's
        price is changed IN THE CURRENCY THAT LIST CHARGES - the lineup row
        carries no price of its own (every pack_ and discount column is 0 on all
        315 vanilla rows), so what the machine charges is the item's price.
        """
        if lineup_id not in self.tabs_in_use() and lineup_id not in LINEUP_TABS:
            raise BuildError(f"{lineup_id} is not one of the machine's lists")
        defaults = self.tab_defaults(lineup_id)
        currency = builder_data.CURRENCY_TYPES.get(defaults["currency_type"])
        if price is not None and currency is None:
            raise BuildError(
                f"What {self.lineup_name(lineup_id)} charges in is not known "
                f"(currency type {defaults['currency_type']}), so a price cannot be set."
            )
        next_id = (self._con.execute(
            f"SELECT max(goods_id) FROM {quote_ident(LINEUP_TABLE)}"
        ).fetchone()[0] or 0) + 1
        priority = defaults.pop("display_priority")
        added = 0
        for item_id in item_ids:
            exists = self._con.execute(
                f"SELECT 1 FROM {quote_ident(LINEUP_TABLE)} "
                f"WHERE lineup_id = ? AND type_id = ?", (lineup_id, item_id)
            ).fetchone()
            if exists:
                continue
            priority += 1
            self.add_row(LINEUP_TABLE, {
                "goods_id": next_id, "lineup_id": lineup_id, "entity_type": "ITEM",
                "type_id": item_id, "display_priority": priority,
                "freq": 0, "is_special": 0, "pack_money": 0, "pack_metal": 0,
                "pack_recycle_point": 0, "pack_bloodnium": 0,
                "money_discount_rate": 0, "metal_discount_rate": 0,
                "recycle_point_discount_rate": 0, "bloodnium_discount_rate": 0,
                **defaults,
            })
            next_id += 1
            added += 1
            if price is not None:
                self.set_value("master_item", (item_id,), currency[1], price)
        return added

    def lineup_size(self, lineup_id: str) -> int:
        return self._con.execute(
            f"SELECT count(*) FROM {quote_ident(LINEUP_TABLE)} WHERE lineup_id = ?",
            (lineup_id,),
        ).fetchone()[0]

    def _schedule_columns(self) -> set[str]:
        if SCHEDULE_TABLE not in self._present():
            return set()
        return {c.name for c in self.columns(SCHEDULE_TABLE)}

    def make_room(self, lineup_id: str) -> int:
        """Let the machine offer everything on a list, not a random part of it.

        Each schedule month says how many goods from a list are offered at once.
        Stock sets that to the list's size exactly (7 of 7, 28 of 28), so a new
        item on a full list would push another out. Raises the number on every
        month that uses the list. Returns how many values changed.
        """
        have = self._schedule_columns()
        size = self.lineup_size(lineup_id)
        changed = 0
        for slot, (low, high) in builder_data.SCHEDULE_SLOTS.items():
            if not {slot, low, high} <= have:
                continue
            months = self._con.execute(
                f"SELECT rowid, {quote_ident(low)}, {quote_ident(high)} "
                f"FROM {quote_ident(SCHEDULE_TABLE)} WHERE {quote_ident(slot)} = ?",
                (lineup_id,),
            ).fetchall()
            for rowid, least, most in months:
                if (most or 0) >= size:
                    continue
                if least == most:
                    self.set_value(SCHEDULE_TABLE, (rowid,), low, size, commit=False)
                    changed += 1
                self.set_value(SCHEDULE_TABLE, (rowid,), high, size, commit=False)
                changed += 1
        self._con.commit()
        return changed

    def schedule_end(self) -> str:
        """The last date the machine has a month for, as stored ('' if none)."""
        if "expire" not in self._schedule_columns():
            return ""
        row = self._con.execute(
            f"SELECT max(expire) FROM {quote_ident(SCHEDULE_TABLE)}"
        ).fetchone()
        return row[0] or ""

    def extend_schedule(self, until_year: int) -> int:
        """Add months to the machine's schedule, carrying the rotation on.

        The stock schedule has one row per month and ran out on 2026-08-01. New
        rows copy the last one and move to the next Bloodnium list each month
        (MON, TUE ... SUN, MON), until the end of ``until_year``. Returns how
        many months were added.
        """
        if "expire" not in self._schedule_columns():
            raise BuildError("This database has no vending machine schedule.")
        rows = self._con.execute(
            f"SELECT rowid, * FROM {quote_ident(SCHEDULE_TABLE)} ORDER BY expire"
        ).fetchall()
        if not rows:
            raise BuildError("The schedule is empty, so there is no month to carry on from.")
        last = rows[-1]
        when = _parse_date(str(last["expire"]))
        if when is None:
            raise BuildError(f"The schedule's last date, {last['expire']}, is not a date.")
        cycle: list[str] = []
        slot = "bloodnium_exchange_lineup_id"
        for r in rows:
            if slot in r.keys() and r[slot] and r[slot] not in cycle:
                cycle.append(r[slot])
        if (until_year - when.year) * 12 > MAX_SCHEDULE_MONTHS:
            raise BuildError(
                f"That is more than {MAX_SCHEDULE_MONTHS} months - pick an earlier year."
            )
        stop = datetime(until_year + 1, 1, 1, tzinfo=timezone.utc)
        template = {c: last[c] for c in last.keys() if c != "rowid"}
        current = template.get(slot)
        added = 0
        while True:
            when = _next_month(when)
            if when > stop:
                break
            if added >= MAX_SCHEDULE_MONTHS:
                raise BuildError(
                    f"That is more than {MAX_SCHEDULE_MONTHS} months - pick an earlier year."
                )
            values = dict(template, expire=when.strftime(SCHEDULE_DATE))
            if cycle and current in cycle:
                current = cycle[(cycle.index(current) + 1) % len(cycle)]
                values[slot] = current
            self.add_row(SCHEDULE_TABLE, values)
            added += 1
        return added

    # -- what you have changed ---------------------------------------------

    @property
    def dirty(self) -> bool:
        return bool(self._before) or bool(self._added)

    def change_count(self) -> int:
        return len(self._before) + len(self._added)

    def changed_tables(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for table, _, _ in self._before:
            out[table] = out.get(table, 0) + 1
        for table, _ in self._added:
            out[table] = out.get(table, 0) + 1
        return out

    def summary(self) -> list[str]:
        """One line per table, for the confirm step before saving."""
        return [f"{self.title_of(t)}: {n} value{'s' if n != 1 else ''}"
                for t, n in sorted(self.changed_tables().items(),
                                   key=lambda kv: -kv[1])]

    # -- handing it over ---------------------------------------------------

    def candidate(self) -> "install.InstallCandidate":
        """The edited database, checked the way a downloaded one would be."""
        if not self.dirty:
            raise BuildError("Nothing has been changed yet, so there is no mod to make.")
        return install.inspect(self.scratch, vanilla=self.vanilla)

    def save_as_mod(self, manager, name: str, description: str = "",
                    author: str = "", version: str = "1.0.0",
                    overwrite: bool = False) -> list:
        """Turn the edits into a mod, through the ordinary import path."""
        if not name.strip():
            raise BuildError("A mod needs a name.")
        return manager.install_database(
            self.candidate(),
            name.strip(),
            description=description.strip() or f"Built with the editor: {self.change_count()} value(s) changed.",
            author=author.strip(),
            version=version,
            overwrite=overwrite,
        )


@dataclass
class Sellable:
    """Something the vending machine can be made to sell."""

    item_id: str
    name: str
    category: str
    rarity: int
    money: int          # its Kill Coin price
    recycle: int        # its recycle-point price
    bloodnium: int      # its Bloodnium price
    already: str = ""   # the lists it is already sold on, if any


# What the machine sells, by the item type behind it. Vanilla only ever stocks
# materials and blueprints, plus four consumables on the COMMON list.
SELLABLE_CATEGORIES: dict[str, tuple[str, ...]] = {
    "Blueprints": ("ITTP_RMAP",),
    "Materials": ("ITTP_MATERIAL",),
    "Consumables": ("ITTP_HEAL", "ITTP_BIV", "ITTP_BACK", "ITTP_WOOD"),
}

# The vending machine's lists: the Kill Coin shop, the recycle-point exchange,
# and the seven monthly Bloodnium lists (not days - see builder_data).
LINEUP_TABS = ["COMMON", "RE", "MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]

LINEUP_TABLE = "master_automaticshop_lineup"


def blueprint_part_id(item_id: str) -> str:
    """`ITMP_ARM_WP001_001U` -> `PT_ARM_WP001_001`.

    A blueprint has no name of its own - every one of the 1,899 is called
    "RMAP" or "UNKNOWN_RMAP" - so the only way to show a useful name is to
    follow it back to the weapon or armour it makes. A trailing U marks the
    unidentified version of the same blueprint; both point at the same part.
    All 1,899 resolve by this rule.
    """
    if not item_id.startswith("ITMP_"):
        return item_id
    stem = item_id[5:]
    return "PT_" + (stem[:-1] if stem.endswith("U") else stem)


def currency_columns(unit: str, notes: dict | None = None) -> list[tuple[str, str, str]]:
    """Every column measured in one currency, as (table, column, meaning).

    Costs nothing to work out: the unit was written against each column when
    the table was described, so this is a filter rather than new knowledge.
    """
    notes = notes if notes is not None else explain.load_notes()
    out = []
    for table, entry in notes.items():
        for column, described in entry.get("columns", {}).items():
            if (isinstance(described, (tuple, list)) and len(described) == 2
                    and described[1] == unit):
                out.append((table, column, described[0]))
    return sorted(out)
