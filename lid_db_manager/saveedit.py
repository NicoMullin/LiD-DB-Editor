"""Editing a save: the engine under the Saves tab's editor.

A save is read whole (savegame.py has the container), changed as a JSON
document, and written back the way the game writes it. The game's JSON has
two quirks - every "/" in a string written "\\/", and every float with four
places ("0.0000") - and with both kept an untouched save comes back byte for
byte the same (lid-save-file-format), so whatever differs in a written save is
only what was edited. The game reads plainer JSON too (other editors write
it, and saves made that way load in the game); this keeps its own form anyway.

Every change goes through ``set``, which keeps a value's type - some numbers
are stored as text, and stay text - and remembers what it was, for the list
of changes shown before anything is written. Writing always backs the save up
first, reads the new file back before it replaces anything, and refuses while
the game runs (it writes its save when it quits).

What the fields mean, and their limits, were worked out against the game's
own masters.db and saves, with the help of the LET IT DIE save editors made by
oberlinx, g3usyk, mrzacarias and the lid-utils author, used with their
permission. The limits come from the database in use, so a mod that raises
one (a bigger bank, say) is honoured.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import savegame

MONEY_MAX = 999_999_999            # what the game's counters hold (Kill Coins, Death Metals, Re points)
FREE_CONTINUES_MAX = 9_999


class EditError(Exception):
    """Nothing was written; the message says why, for a player."""


# -- the game's own JSON ---------------------------------------------------------

def _string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False).replace("/", "\\/")


def encode(value) -> str:
    """``value`` as the game writes JSON: compact, "\\/" in strings, floats to four places."""
    parts: list[str] = []
    _encode(value, parts)
    return "".join(parts)


def _encode(value, out: list) -> None:
    if isinstance(value, dict):
        out.append("{")
        first = True
        for key, item in value.items():
            if not first:
                out.append(",")
            first = False
            out.append(_string(str(key)))
            out.append(":")
            _encode(item, out)
        out.append("}")
    elif isinstance(value, list):
        out.append("[")
        for i, item in enumerate(value):
            if i:
                out.append(",")
            _encode(item, out)
        out.append("]")
    elif isinstance(value, str):
        out.append(_string(value))
    elif value is True:
        out.append("true")
    elif value is False:
        out.append("false")
    elif value is None:
        out.append("null")
    elif isinstance(value, int):
        out.append(str(value))
    elif isinstance(value, float):
        out.append(f"{value:.4f}")
    else:
        raise EditError(f"cannot write a {type(value).__name__} into a save")


# -- a save being edited ---------------------------------------------------------

@dataclass(frozen=True)
class Change:
    label: str
    path: tuple
    before: object
    after: object


class SaveDocument:
    """One save, read and ready to change."""

    def __init__(self, raw: bytes, source: Path | None = None) -> None:
        self.source = Path(source) if source else None
        self.raw = raw
        self.version, self.payload = savegame.read_container(raw)
        try:
            self.doc = json.loads(self.payload)
        except ValueError:
            raise EditError("the save's contents are not readable - it may be damaged") from None
        if not isinstance(self.doc, dict) or not isinstance(self.doc.get("soul"), dict):
            raise EditError("this is not a save the game wrote (it has no soul section)")
        self._original = json.loads(self.payload)
        self._labels: dict[tuple, str] = {}
        self._before: dict[tuple, object] = {}  # each labelled value as it was when first set
        self._notes: list[Change] = []      # changes made to whole records (added, removed), in words

    @classmethod
    def read(cls, path: Path | str) -> "SaveDocument":
        try:
            return cls(Path(path).read_bytes(), path)
        except OSError as exc:
            raise EditError(f"could not read the save: {exc}") from None
        except savegame.SaveError as exc:
            raise EditError(str(exc)) from None

    # -- reading and changing values ---------------------------------------------

    @property
    def uid(self) -> str:
        user = self.doc.get("user") or {}
        return str(user.get("uid", self.doc["soul"].get("uid", "")))

    def get(self, path: tuple, default=None):
        node = self.doc
        for key in path:
            if isinstance(node, dict) and key in node:
                node = node[key]
            elif isinstance(node, list) and isinstance(key, int) and 0 <= key < len(node):
                node = node[key]
            else:
                return default
        return node

    def number(self, path: tuple, default: int = 0) -> int:
        value = self.get(path, default)
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def set(self, path: tuple, value, label: str = "") -> None:
        """Change one value, keeping the type the save stores it as (a number kept as
        text stays text). A field the save does not have is added as a number."""
        parent = self.get(path[:-1])
        if not isinstance(parent, (dict, list)):
            raise EditError(f"the save has no {'/'.join(map(str, path[:-1]))}")
        key = path[-1]
        old = parent.get(key) if isinstance(parent, dict) else parent[key]
        if isinstance(old, bool):
            new = bool(value)
        elif isinstance(old, str):
            new = str(int(value)) if isinstance(value, (int, float)) and not isinstance(value, bool) else str(value)
        elif isinstance(old, float):
            new = float(value)
        elif isinstance(old, int) or old is None:
            try:
                new = int(value)
            except (TypeError, ValueError):
                raise EditError(f"{label or path[-1]} must be a whole number, not {value!r}") from None
        else:
            new = value
        if label:
            # kept now, not looked up in the save as read: a fighter deleted earlier in the
            # list moves the ones after it, so the same path held another value then
            self._before.setdefault(tuple(path), old)
            self._labels[tuple(path)] = label
        parent[key] = new

    def changes(self) -> list[Change]:
        """Every value that differs from the save as it was read."""
        found = []
        for path, label in self._labels.items():
            before = self._before[path] if path in self._before else _get(self._original, path)
            after = self.get(path)
            if before != after or type(before) is not type(after):
                found.append(Change(label, path, before, after))
        return found + list(self._notes)

    def note(self, label: str, before="", after="") -> None:
        """A change ``set`` does not make - records added or removed - for the list of changes."""
        self._notes.append(Change(label, (), before, after))

    @property
    def changed(self) -> bool:
        return bool(self.changes()) or self.encoded() != self.payload

    def encoded(self) -> bytes:
        return encode(self.doc).encode("utf-8")

    def to_bytes(self) -> bytes:
        """The save as it is now, in the game's container; checked to read back."""
        payload = self.encoded()
        data = savegame.write_container(self.version, payload)
        version, again = savegame.read_container(data)
        if version != self.version or json.loads(again) != self.doc:
            raise EditError("the changed save did not read back right - nothing was written")
        return data


def _get(doc, path: tuple):
    node = doc
    for key in path:
        if isinstance(node, dict) and key in node:
            node = node[key]
        elif isinstance(node, list) and isinstance(key, int) and 0 <= key < len(node):
            node = node[key]
        else:
            return None
    return node


def write_save(target: Path, data: bytes, backups_dir: Path, lock_reason=None) -> Path:
    """``data`` in place of the save at ``target``: backed up first, swapped in once written.
    Returns the backup."""
    if lock_reason is None:
        from . import asset_runner
        lock_reason = asset_runner.game_lock_reason
    reason = lock_reason()
    if reason:
        raise EditError(f"{reason} Close the game first: it writes its save when it quits.")
    target = Path(target)
    folder = Path(backups_dir) / "saves" / (target.parent.name or "save")
    folder.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d-%H%M%S")
    backup, n = folder / f"{target.stem}-{stamp}-before-edit{target.suffix}", 1
    while backup.exists():
        n += 1
        backup = folder / f"{target.stem}-{stamp}-before-edit-{n}{target.suffix}"
    try:
        shutil.copy2(target, backup)
    except OSError as exc:
        raise EditError(f"could not back the save up: {exc}") from None
    temporary = target.with_name(target.name + ".new")
    try:
        temporary.write_bytes(data)
        if temporary.read_bytes() != data:
            raise EditError("the new save did not come out the same - nothing was written")
        os.replace(temporary, target)
    except OSError as exc:
        raise EditError(f"could not write the save: {exc} (the backup is at {backup})") from None
    finally:
        temporary.unlink(missing_ok=True)
    return backup


# -- limits, from the game's database ----------------------------------------

@dataclass
class Limits:
    """What the game can hold, from the masters.db in use (a mod may raise these)."""

    bank: dict = field(default_factory=dict)        # KC bank level -> how many Kill Coins it holds
    tank: dict = field(default_factory=dict)        # SPL tank level -> how much Splithium it holds
    bloodnium: int = 999_999

    @classmethod
    def read(cls, db_path: Path | str | None) -> "Limits":
        limits = cls()
        if not db_path or not Path(db_path).is_file():
            return limits
        try:
            con = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
        except sqlite3.Error:
            return limits
        try:
            for table, into in (("master_safe_level", limits.bank), ("master_spirit_tank_level", limits.tank)):
                try:
                    into.update({int(lv): int(cap) for lv, cap in con.execute(
                        f'SELECT level, "limit" FROM {table}') if lv is not None and cap is not None})
                except sqlite3.Error:
                    pass
            try:
                row = con.execute("SELECT value FROM master_const_int WHERE id = 'BLOODNIUM_POINT_MAX'").fetchone()
                if row and int(row[0]) > 0:
                    limits.bloodnium = int(row[0])
            except (sqlite3.Error, TypeError, ValueError):
                pass
        finally:
            con.close()
        return limits

    def top_level(self, table: dict) -> int:
        return max(table) if table else 99

    def cap(self, table: dict, level: int) -> int:
        """What a bank at ``level`` holds (MONEY_MAX when the database has no table)."""
        if not table:
            return MONEY_MAX
        level = min(max(int(level), min(table)), max(table))
        return table.get(level, MONEY_MAX)


# -- the Account page's fields -----------------------------------------------

@dataclass(frozen=True)
class Field:
    key: str
    label: str
    path: tuple
    help: str = ""


ACCOUNT_FIELDS = (
    Field("kill_coins", "Kill Coins", ("soul", "free_money"), "Held in the Kill Coin Bank; no more than its level holds."),
    Field("bank_level", "Kill Coin Bank level", ("soul", "safe_level")),
    Field("splithium", "Splithium", ("soul", "spirit"), "Held in the SPL tank; no more than its level holds."),
    Field("tank_level", "SPL tank level", ("soul", "spirit_tank_level")),
    Field("bloodnium", "Bloodnium", ("soul", "bloodnium_point")),
    Field("re_points", "Re points", ("soul", "recycle_point"), "Anything earned over the most the game holds is lost."),
    Field("death_metals", "Death Metals", ("user", "free_medal"),
          "The free Death Metals; bought ones (paid) are left as they are."),
    Field("free_continues", "Free continues a day", ("soul", "free_continue_max_count"),
          "How many free continues the continue screen offers each day. The game's daily reset sets it back."),
)


def account_bounds(save: SaveDocument, limits: Limits, values: dict | None = None) -> dict:
    """field key -> (lowest, highest) for the Account page, the banks' caps from their levels."""
    values = values or {}
    bank_level = values.get("bank_level", save.number(("soul", "safe_level"), 1))
    tank_level = values.get("tank_level", save.number(("soul", "spirit_tank_level"), 1))
    return {
        "kill_coins": (0, limits.cap(limits.bank, bank_level)),
        "bank_level": (1, limits.top_level(limits.bank)),
        "splithium": (0, limits.cap(limits.tank, tank_level)),
        "tank_level": (1, limits.top_level(limits.tank)),
        "bloodnium": (0, limits.bloodnium),
        "re_points": (0, MONEY_MAX),
        "death_metals": (0, MONEY_MAX),
        "free_continues": (0, FREE_CONTINUES_MAX),
    }


def set_account(save: SaveDocument, values: dict, limits: Limits) -> None:
    """The Account page's values into the save, each kept within what the game holds."""
    bounds = account_bounds(save, limits, values)
    by_key = {f.key: f for f in ACCOUNT_FIELDS}
    for key, value in values.items():
        f = by_key[key]
        low, high = bounds[key]
        current = save.get(f.path)
        if current is not None and save.number(f.path) == int(value):
            continue                    # unchanged: left as it is, even outside the limits
        value = max(low, min(int(value), high))
        if current is None and value == 0:
            continue                    # not in this save, and nothing to add
        if current is not None and save.number(f.path) == value:
            continue
        save.set(f.path, value, f.label)
        if key == "free_continues":
            # all of them there to use today, as the continue screen counts them
            save.set(("soul", "free_continue_count"), value, "Free continues left today")
