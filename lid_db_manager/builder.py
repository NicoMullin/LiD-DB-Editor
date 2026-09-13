"""Building a mod by editing values, rather than by writing SQL.

The whole thing rests on one decision: **this never writes a mod file.** It
copies the vanilla database, lets you change values in the copy, and then hands
that copy to the same import path a downloaded modded `masters.db` goes through.

That matters. It means the builder cannot invent a broken mod format, cannot
skip validation, and gets snapshots, load order, switchable parts and
untick-to-undo for free, because all of that already works on imported
databases. The builder's only job is to produce an edited file.

Nothing here imports Qt - the window in ``ui/builder.py`` is a view onto this.
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import builder_data, explain, install
from .sqlutil import quote_ident

# Rows read in one go. Above this the table is paged, so the quest list does not
# try to put 6,252 rows into a grid at once.
PAGE = 200


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

    @property
    def heading(self) -> str:
        """What to put at the top of the column."""
        words = self.meaning if self.described else self.name
        return f"{words} ({self.unit})" if self.unit else words


@dataclass
class Row:
    key: tuple
    label: str             # "All-rounder, grade 2"
    values: dict           # column -> value


@dataclass
class TableView:
    table: str
    title: str
    about: str
    columns: list[Column]
    rows: list[Row]
    total: int             # rows in the table, not just this page

    @property
    def editable(self) -> list[Column]:
        return [c for c in self.columns if not c.is_key]


class BuildError(Exception):
    pass


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
        self._namer = explain.Namer(vanilla)
        self._notes = explain.load_notes()
        # (table, key, column) -> value it had in vanilla, so an edit can be
        # taken back without re-reading the whole file.
        self._before: dict[tuple, object] = {}
        # Rows added rather than edited, so they count towards the change total.
        self._added: list[tuple] = []
        self._schema: dict[str, tuple[list[str], list[Column]]] = {}

    # -- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        try:
            self._con.close()
        finally:
            self._namer.close()
            shutil.rmtree(self._dir, ignore_errors=True)

    def __enter__(self) -> "ModBuilder":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- what is in the database -------------------------------------------

    def groups(self) -> list[tuple[str, str, list[str]]]:
        """The headings and their tables, with missing tables dropped.

        A table can be missing when the player's game version differs from the
        one these lists were written against. That is not worth an error - the
        heading simply has one less thing under it.
        """
        present = {r[0] for r in self._con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        out = []
        for name, note, tables in builder_data.GROUPS:
            live = [t for t in tables if t in present]
            if live:
                out.append((name, note, live))
        rest = sorted(present - builder_data.grouped_tables())
        rest = [t for t in rest if not t.startswith("sqlite_")]
        if rest:
            out.append((builder_data.EVERYTHING_ELSE,
                        builder_data.EVERYTHING_ELSE_NOTE, rest))
        return out

    def title_of(self, table: str) -> str:
        return (self._notes.get(table, {}).get("title") or table)

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

    # -- reading -----------------------------------------------------------

    def view(self, table: str, *, search: str = "", offset: int = 0,
             limit: int = PAGE) -> TableView:
        keys, columns = self._describe(table)
        entry = self._notes.get(table, {})
        rule = entry.get("row", {"kind": "key"})
        select = ", ".join(quote_ident(c) for c in {*keys, *(c.name for c in columns)})
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
        rows = []
        for r in found:
            key = tuple(r[k] for k in keys)
            label = explain.row_label(self._namer, table, keys, key, rule)
            rows.append(Row(key=key, label=label,
                            values={c.name: r[c.name] for c in columns}))
        return TableView(
            table=table,
            title=entry.get("title") or table,
            about=entry.get("about", ""),
            columns=columns,
            rows=rows,
            total=total,
        )

    # -- writing -----------------------------------------------------------

    def set_value(self, table: str, key: tuple, column: str, value) -> None:
        """Change one cell in the scratch copy."""
        keys, columns = self._describe(table)
        match = next((c for c in columns if c.name == column), None)
        if match is None:
            raise BuildError(f"{table} has no column called {column}")
        if match.is_key:
            raise BuildError(
                f"{column} is part of what identifies the row, so changing it "
                "would move the row rather than edit it"
            )
        value = self._coerce(match, value)
        where = " AND ".join(f"{quote_ident(k)} IS ?" for k in keys)
        cell = (table, key, column)
        if cell not in self._before:
            row = self._con.execute(
                f"SELECT {quote_ident(column)} FROM {quote_ident(table)} WHERE {where}",
                key,
            ).fetchone()
            if row is None:
                raise BuildError(f"no such row in {table}")
            self._before[cell] = row[0]
        self._con.execute(
            f"UPDATE {quote_ident(table)} SET {quote_ident(column)} = ? WHERE {where}",
            [value, *key],
        )
        self._con.commit()
        # Back to where it started is not a change.
        if self._before[cell] == value:
            del self._before[cell]

    def set_many(self, table: str, keys_: list[tuple], column: str, value) -> int:
        """The same value across many rows - "every revive costs 1"."""
        for key in keys_:
            self.set_value(table, key, column, value)
        return len(keys_)

    def scale(self, table: str, keys_: list[tuple], column: str, factor: float) -> int:
        """Multiply a column - "double every drop rate".

        Whole-number columns stay whole numbers, because writing 1.5 into a
        column the game reads as an integer is how you get a crash rather than
        a mod.
        """
        keys, columns = self._describe(table)
        match = next((c for c in columns if c.name == column), None)
        if match is None:
            raise BuildError(f"{table} has no column called {column}")
        if match.kind == "text":
            raise BuildError(f"{match.heading} is not a number, so it cannot be scaled")
        changed = 0
        for key in keys_:
            where = " AND ".join(f"{quote_ident(k)} IS ?" for k in keys)
            row = self._con.execute(
                f"SELECT {quote_ident(column)} FROM {quote_ident(table)} WHERE {where}",
                key,
            ).fetchone()
            if row is None or not isinstance(row[0], (int, float)):
                continue
            scaled = row[0] * factor
            self.set_value(table, key, column,
                           int(round(scaled)) if match.kind == "int" else scaled)
            changed += 1
        return changed

    def revert_cell(self, table: str, key: tuple, column: str) -> None:
        cell = (table, key, column)
        if cell in self._before:
            self.set_value(table, key, column, self._before[cell])

    def revert_all(self) -> None:
        for (table, key, column), value in list(self._before.items()):
            self.set_value(table, key, column, value)

    @staticmethod
    def _coerce(column: Column, value):
        """Turn what was typed into what the column holds."""
        if value is None or (isinstance(value, str) and value.strip() == ""):
            return "" if column.kind == "text" else 0
        if column.is_list or column.kind == "text":
            return str(value)
        try:
            return int(value) if column.kind == "int" else float(value)
        except (TypeError, ValueError):
            raise BuildError(
                f"{column.heading} holds a number, and \"{value}\" is not one"
            ) from None

    # -- adding rows -------------------------------------------------------

    def add_row(self, table: str, values: dict) -> None:
        """Put a new row in. Counted as one change, however many columns it has."""
        keys, columns = self._describe(table)
        known = {c.name for c in columns}
        unknown = set(values) - known
        if unknown:
            raise BuildError(f"{table} has no column called {', '.join(sorted(unknown))}")
        names = list(values)
        placeholders = ", ".join("?" for _ in names)
        self._con.execute(
            f"INSERT INTO {quote_ident(table)} "
            f"({', '.join(quote_ident(c) for c in names)}) VALUES ({placeholders})",
            [values[c] for c in names],
        )
        self._con.commit()
        self._added.append((table, tuple(values.get(k) for k in keys)))

    # -- the vending machine -----------------------------------------------

    def tabs_in_use(self) -> list[str]:
        """The machine's tabs, known ones first, then anything a mod added."""
        found = {r[0] for r in self._con.execute(
            f"SELECT DISTINCT lineup_id FROM {quote_ident(LINEUP_TABLE)}"
        )}
        return [t for t in LINEUP_TABS if t in found] + sorted(found - set(LINEUP_TABS))

    def tab_defaults(self, lineup_id: str) -> dict:
        """How the rows already on a tab are set up.

        New rows copy this rather than asking the user to pick a currency_type,
        because what that number means is not written down anywhere - only that
        vanilla uses 0 on COMMON, 3 on RE and 4 on the daily tabs. Copying the
        tab keeps a new row consistent with its neighbours instead of guessing.
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
        """Put items on one of the machine's tabs.

        A price of None leaves the item's own price alone. Otherwise the item's
        price is changed - the lineup row carries no price of its own (every
        pack_ and discount column is 0 on all 315 vanilla rows), so what the
        machine charges IS the item's price.
        """
        if lineup_id not in self.tabs_in_use() and lineup_id not in LINEUP_TABS:
            raise BuildError(f"{lineup_id} is not one of the machine's tabs")
        defaults = self.tab_defaults(lineup_id)
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
                column = {0: "buy_money", 3: "buy_recycle_point"}.get(
                    defaults["currency_type"], "buy_money"
                )
                self.set_value("master_item", (item_id,), column, price)
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
    already: str = ""   # the tabs it is already sold on, if any


# What the machine sells, by the item type behind it. Vanilla only ever stocks
# materials and blueprints, plus four consumables on the COMMON tab.
SELLABLE_CATEGORIES: dict[str, tuple[str, ...]] = {
    "Blueprints": ("ITTP_RMAP",),
    "Materials": ("ITTP_MATERIAL",),
    "Consumables": ("ITTP_HEAL", "ITTP_BIV", "ITTP_BACK", "ITTP_WOOD"),
}

# The vending machine's tabs, in the order the game shows them.
LINEUP_TABS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN", "COMMON", "RE"]

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
            if isinstance(described, tuple) and len(described) == 2 and described[1] == unit:
                out.append((table, column, described[0]))
    return sorted(out)
