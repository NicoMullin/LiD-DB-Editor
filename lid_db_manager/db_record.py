"""The manager's own note inside ``masters.db``: which mods are in this file.

One small table, written in the same transaction as the mods themselves, so the
two can never disagree about an apply that failed halfway. It travels with the
database - copied to another PC, shared with a friend, restored from a backup -
which ``state.json`` does not.

The game never asks for this table, and every part of the manager that compares
a database with stock skips it by name (see ``is_ours``), so a database that is
vanilla apart from this note still reads as vanilla.

It is a suggestion, never the truth. Somebody can hand-edit the cells after it
was written, or edit the note itself; the adoption scan checks every mod it
names against the actual cells before believing a word of it.
"""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import __version__
from .sqlutil import quote_ident

TABLE = "_lid_mod_manager"

# Bumped whenever what is stored changes shape. A newer release reads every
# older format; an older release leaves a newer note alone rather than guess.
FORMAT_VERSION = 1

_CREATE = (
    f"CREATE TABLE IF NOT EXISTS {quote_ident(TABLE)} ("
    '"position" INTEGER PRIMARY KEY, '
    '"mod_id" TEXT NOT NULL, '
    "\"name\" TEXT NOT NULL DEFAULT '', "
    "\"version\" TEXT NOT NULL DEFAULT '', "
    "\"mod_values\" TEXT NOT NULL DEFAULT '{}', "
    "\"applied_at\" TEXT NOT NULL DEFAULT '', "
    '"format" INTEGER NOT NULL, '
    "\"manager_version\" TEXT NOT NULL DEFAULT '')"
)


def is_ours(table: str) -> bool:
    """True for the manager's own table - never a change, never a mod."""
    return str(table).lower() == TABLE


@dataclass
class RecordedMod:
    """One mod the note says is in the database, in load order."""

    mod_id: str
    name: str = ""
    version: str = ""
    values: dict = field(default_factory=dict)
    applied_at: str = ""


@dataclass
class Record:
    mods: list[RecordedMod] = field(default_factory=list)
    manager_version: str = ""
    # Set when the note was there but could not be used, and why.
    unreadable: str = ""

    def get(self, mod_id: str) -> RecordedMod | None:
        for entry in self.mods:
            if entry.mod_id == mod_id:
                return entry
        return None


def _clean_values(text) -> dict:
    """Only numbers survive. Anything else in there is somebody's hand edit."""
    try:
        data = json.loads(text or "{}")
    except (TypeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(key): value
        for key, value in data.items()
        if isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    }


def write(con: sqlite3.Connection, mods: list[RecordedMod]) -> None:
    """Replace the note with ``mods``, inside the caller's transaction.

    No mods means no note at all: a database with every mod taken off is
    exactly the shape the game shipped it in.
    """
    if not mods:
        con.execute(f"DROP TABLE IF EXISTS {quote_ident(TABLE)}")
        return
    con.execute(_CREATE)
    con.execute(f"DELETE FROM {quote_ident(TABLE)}")
    now = datetime.now().isoformat(timespec="seconds")
    con.executemany(
        f"INSERT INTO {quote_ident(TABLE)} "
        '("position", "mod_id", "name", "version", "mod_values", "applied_at", '
        '"format", "manager_version") VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        [
            (
                position,
                entry.mod_id,
                entry.name,
                entry.version,
                json.dumps(entry.values, sort_keys=True),
                entry.applied_at or now,
                FORMAT_VERSION,
                __version__,
            )
            for position, entry in enumerate(mods, start=1)
        ],
    )


def read(db_path: Path) -> Record | None:
    """The note in ``db_path``, or None when there is none.

    Never raises: a note that cannot be read comes back with ``unreadable``
    saying why, and the scan carries on without it.
    """
    try:
        con = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        return read_from(con)
    finally:
        con.close()


def read_from(con: sqlite3.Connection) -> Record | None:
    try:
        exists = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND lower(name) = ?", (TABLE,)
        ).fetchone()
    except sqlite3.Error:
        return None
    if not exists:
        return None
    try:
        rows = con.execute(
            'SELECT "mod_id", "name", "version", "mod_values", "applied_at", "format", '
            f'"manager_version" FROM {quote_ident(TABLE)} ORDER BY "position"'
        ).fetchall()
    except sqlite3.Error as exc:
        return Record(unreadable=f"the manager's note in this database could not be read ({exc})")

    record = Record()
    for mod_id, name, version, values, applied_at, fmt, manager_version in rows:
        try:
            fmt = int(fmt)
        except (TypeError, ValueError):
            continue
        if fmt > FORMAT_VERSION:
            return Record(
                manager_version=str(manager_version or ""),
                unreadable=(
                    "this database was last saved by a newer version of the manager "
                    f"({manager_version or 'unknown'}); its note was left unread"
                ),
            )
        mod_id = str(mod_id or "").strip()
        if not mod_id or record.get(mod_id) is not None:
            continue
        record.manager_version = str(manager_version or "")
        record.mods.append(
            RecordedMod(
                mod_id=mod_id,
                name=str(name or ""),
                version=str(version or ""),
                values=_clean_values(values),
                applied_at=str(applied_at or ""),
            )
        )
    return record


def from_applied(applied: dict) -> list[RecordedMod]:
    """The note for what ``state.applied`` says is in the database, in order."""
    return [
        RecordedMod(
            mod_id=mod_id,
            name=entry.name,
            version=entry.version,
            values=dict(entry.values),
            applied_at=entry.applied_at,
        )
        for mod_id, entry in applied.items()
    ]
