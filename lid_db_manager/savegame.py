"""The game's save file, for the one change the manager makes to it: a new game day.

LET IT DIE keeps its save at ``<game>/Savedata/<steam id>/brggame.sav``: a
small container ("BRG\\0", a version, the total size, "ZLIB", then zlib chunks
each led by its plain and packed sizes, and a 0 to end the list) holding plain
JSON. The game decides once a day - at 10:00 UTC, its daily reset - that a new
game day has begun: free continues refill, daily things come round again. It
knows the day it last did that from ``user.last_login_date``, the day's start
as a Unix time.

Testing anything daily otherwise means waiting for the reset or winding the PC
clock, which upsets everything else that counts time. Setting that one number
back a day makes the game's next start a new game day, with nothing else in
the save touched: the number is changed where it stands in the JSON text, so
every other byte of the save's contents stays as the game wrote it.

The save as it was is copied to the manager's backups first, every time, and
the new file is written beside the save and swapped in only once it reads back
right.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import struct
import time
import zlib
from dataclasses import dataclass
from pathlib import Path

SAVE_NAME = "brggame.sav"
MAGIC = b"BRG\0"
ALGORITHM = b"ZLIB"
DAY = 86400
# The game's day turns at 10:00 UTC: it counts days from ten hours before now.
DAY_TURNS_AT = 10 * 3600
MARKER = re.compile(rb'"last_login_date":(-?\d+)')


class SaveError(Exception):
    """The save could not be read or changed; the message says why, for a player."""


@dataclass(frozen=True)
class NewDay:
    """What starting a new game day did."""

    save: Path
    before: int             # the day the game last began, as it was
    after: int              # ...and as it is now
    backup: Path | None     # the save as it was (None when nothing needed changing)

    @property
    def changed(self) -> bool:
        return self.after != self.before


def find_saves(game_root: Path | str | None) -> list[Path]:
    """Every save under the game folder, the most recently played first."""
    if not game_root:
        return []
    folder = Path(game_root) / "Savedata"
    if not folder.is_dir():
        return []
    saves = [p for p in folder.glob(f"*/{SAVE_NAME}") if p.is_file()]
    return sorted(saves, key=lambda p: p.stat().st_mtime, reverse=True)


def game_day(moment: float) -> int:
    """The start (00:00 UTC of its date) of the game day ``moment`` falls in."""
    return (int(moment) - DAY_TURNS_AT) // DAY * DAY


def read_container(raw: bytes) -> tuple[int, bytes]:
    """The save's version and its JSON text."""
    if len(raw) < 16 or raw[:4] != MAGIC or raw[12:16] != ALGORITHM:
        raise SaveError("this is not a LET IT DIE save (it does not start BRG ... ZLIB)")
    version, total = struct.unpack_from("<II", raw, 4)
    at, parts = 16, []
    while True:
        if at + 4 > len(raw):
            raise SaveError("the save ends early - it may be damaged")
        (plain,) = struct.unpack_from("<I", raw, at)
        if plain == 0:
            break
        if at + 8 > len(raw):
            raise SaveError("the save ends early - it may be damaged")
        (packed,) = struct.unpack_from("<I", raw, at + 4)
        try:
            part = zlib.decompress(raw[at + 8:at + 8 + packed])
        except zlib.error as exc:
            raise SaveError(f"part of the save could not be unpacked ({exc}) - it may be damaged") from None
        if len(part) != plain:
            raise SaveError("part of the save is not the size it says - it may be damaged")
        parts.append(part)
        at += 8 + packed
    payload = b"".join(parts)
    if len(payload) != total:
        raise SaveError("the save is not the size it says - it may be damaged")
    return version, payload


def write_container(version: int, payload: bytes, chunks: int = 4) -> bytes:
    """The container around ``payload``, in ``chunks`` near-equal parts as the game writes it."""
    size = max(1, -(-len(payload) // chunks))
    out = [MAGIC, struct.pack("<II", version, len(payload)), ALGORITHM]
    for start in range(0, len(payload), size):
        part = payload[start:start + size]
        packed = zlib.compress(part, 6)
        out.append(struct.pack("<II", len(part), len(packed)) + packed)
    out.append(struct.pack("<I", 0))
    return b"".join(out)


def _marker(payload: bytes) -> int:
    """The day the game last began, checked two ways: read as JSON, and found
    exactly once in the text, so the change lands on that number and no other."""
    try:
        document = json.loads(payload)
        value = document["user"]["last_login_date"]
    except (ValueError, KeyError, TypeError):
        raise SaveError("the save has no last_login_date where the game keeps it") from None
    if isinstance(value, bool) or not isinstance(value, int):
        raise SaveError(f"the save's last_login_date is {value!r}, not a time")
    found = MARKER.findall(payload)
    if len(found) != 1 or int(found[0]) != value:
        raise SaveError("last_login_date is in the save more than once - left alone to be safe")
    return value


def start_new_game_day(save: Path | str, backups_dir: Path | str, now: float | None = None) -> NewDay:
    """Make the game's next start a new game day, backing the save up first.

    Nothing is written when the save already says the last game day began
    before today - the next start is a new day as it is.
    """
    save = Path(save)
    now = time.time() if now is None else now
    try:
        raw = save.read_bytes()
    except OSError as exc:
        raise SaveError(f"could not read the save: {exc}") from None
    version, payload = read_container(raw)
    before = _marker(payload)
    yesterday = game_day(now) - DAY
    if before <= yesterday:
        return NewDay(save, before, before, None)

    folder = Path(backups_dir) / "saves" / save.parent.name
    folder.mkdir(parents=True, exist_ok=True)
    backup = folder / f"{save.stem}-{time.strftime('%Y-%m-%d-%H%M%S', time.localtime(now))}{save.suffix}"
    shutil.copy2(save, backup)
    if backup.read_bytes() != raw:
        raise SaveError(f"the backup at {backup} did not come out the same - the save was left alone")

    changed = MARKER.sub(b'"last_login_date":%d' % yesterday, payload, count=1)
    new = write_container(version, changed)
    # Read back before it replaces anything: the same document but that number.
    expected = json.loads(payload)
    expected["user"]["last_login_date"] = yesterday
    again_version, again = read_container(new)
    if again_version != version or json.loads(again) != expected:
        raise SaveError("the changed save did not read back right - the save was left alone")
    temporary = save.with_name(save.name + ".new")
    try:
        temporary.write_bytes(new)
        os.replace(temporary, save)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise SaveError(f"could not write the save: {exc} (the backup is at {backup})") from None
    return NewDay(save, before, yesterday, backup)
