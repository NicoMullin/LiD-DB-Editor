"""Database backups.

Taken only when the user clicks "Save Mod List" - never on every apply::

    <db folder>/masters.db.original          written once, never touched again
    <db folder>/masters.db.backup            rolling, overwritten each save
    <root>/backups/2026-09-06_01-30-00.db    dated, last N kept

The ``.original`` is the important one. Every other backup is a copy of the
database *at the time of that save* - so from the second save onwards they are
copies of an already-modded file. The ``.original`` is written on the first
backup only and never overwritten, so there is always a way back to the
database as it was before this tool ever touched it, without re-downloading it.

Copies go through SQLite's online-backup API so a WAL-mode database is captured
consistently instead of byte-copied mid-write. If that fails for any reason the
copy falls back to a plain file copy.
"""

from __future__ import annotations

import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

BACKUP_SUFFIX = ".backup"
ORIGINAL_SUFFIX = ".original"
# Only timestamp-named files rotate; nothing else in backups/ is ever deleted.
DATED_GLOB = "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]_*.db"


@dataclass
class BackupResult:
    rolling: Path | None = None
    dated: Path | None = None
    original: Path | None = None  # set only on the save that created it
    removed: list[Path] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.removed is None:
            self.removed = []


def _is_intact(path: Path) -> bool:
    """True when the file opens as a SQLite database and passes a quick check."""
    try:
        con = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
    except sqlite3.Error:
        return False
    try:
        return con.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    except sqlite3.Error:
        return False
    finally:
        con.close()


def copy_database(source: Path, destination: Path) -> None:
    """Consistent copy of a SQLite database, byte-for-byte where that is safe.

    A plain file copy is preferred because it leaves the copy with the same
    checksum as the original, so a backup can be compared against a known-good
    vanilla hash. That is only sound when nothing is pending in a side journal -
    with a -wal or -journal file present the on-disk file is not the whole
    story, so the copy goes through SQLite's online-backup API instead.
    """
    source, destination = Path(source), Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.unlink()

    sidecars = [source.with_name(source.name + s) for s in ("-wal", "-journal", "-shm")]
    if not any(s.exists() for s in sidecars):
        shutil.copy2(source, destination)
        # Accept it if the copy is sound - or if the source was never a
        # database, in which case copying the bytes was the right thing anyway.
        if _is_intact(destination) or not _is_intact(source):
            return
        destination.unlink()

    source_con = destination_con = None
    try:
        source_con = sqlite3.connect(str(source))
        destination_con = sqlite3.connect(str(destination))
        source_con.backup(destination_con)
    except sqlite3.Error:
        # Not a SQLite file, locked, or otherwise unhappy - fall back to bytes.
        _close_quietly(destination_con)
        destination_con = None
        if destination.exists():
            destination.unlink()
        shutil.copy2(source, destination)
    finally:
        # sqlite3 connections are not closed by their context manager, and a
        # connection left open keeps a file handle on the copy.
        _close_quietly(source_con)
        _close_quietly(destination_con)


def _close_quietly(connection: sqlite3.Connection | None) -> None:
    if connection is None:
        return
    try:
        connection.close()
    except sqlite3.Error:
        pass


def rolling_backup_path(db_path: Path) -> Path:
    db_path = Path(db_path)
    return db_path.with_name(db_path.name + BACKUP_SUFFIX)


def original_backup_path(db_path: Path) -> Path:
    db_path = Path(db_path)
    return db_path.with_name(db_path.name + ORIGINAL_SUFFIX)


def dated_backup_path(backups_dir: Path, when: datetime | None = None) -> Path:
    """A free dated filename. Two saves in the same second get -2, -3, ..."""
    stamp = (when or datetime.now()).strftime("%Y-%m-%d_%H-%M-%S")
    candidate = Path(backups_dir) / f"{stamp}.db"
    counter = 2
    while candidate.exists():
        candidate = Path(backups_dir) / f"{stamp}-{counter}.db"
        counter += 1
    return candidate


def rotate(backups_dir: Path, keep: int = 5) -> list[Path]:
    """Delete all but the newest ``keep`` dated backups. Returns what went."""
    directory = Path(backups_dir)
    if not directory.is_dir():
        return []
    dated = sorted(directory.glob(DATED_GLOB), key=lambda p: p.name, reverse=True)
    removed = []
    for path in dated[max(keep, 1) :]:
        try:
            path.unlink()
            removed.append(path)
        except OSError:
            continue
    return removed


def take_backups(db_path: Path, backups_dir: Path, keep: int = 5) -> BackupResult:
    """Write the original (first time only), refresh the rolling backup,
    add a dated one, then rotate the dated ones."""
    db_path = Path(db_path)
    result = BackupResult()

    # Written once and never again: the only copy guaranteed to predate any
    # mod this tool applied. Everything below it is a snapshot of "now".
    original = original_backup_path(db_path)
    if not original.exists():
        copy_database(db_path, original)
        result.original = original

    rolling = rolling_backup_path(db_path)
    copy_database(db_path, rolling)
    result.rolling = rolling

    dated = dated_backup_path(backups_dir)
    copy_database(db_path, dated)
    result.dated = dated

    result.removed = rotate(backups_dir, keep)
    return result


@dataclass
class BackupEntry:
    """One restorable copy of the database."""

    path: Path
    kind: str  # "original", "rolling" or "dated"

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def size_mb(self) -> float:
        try:
            return self.path.stat().st_size / (1024 * 1024)
        except OSError:
            return 0.0

    @property
    def modified(self) -> datetime | None:
        try:
            return datetime.fromtimestamp(self.path.stat().st_mtime)
        except OSError:
            return None

    def label(self) -> str:
        when = self.modified
        stamp = when.strftime("%Y-%m-%d %H:%M") if when else "unknown date"
        suffix = {
            "original": "  (ORIGINAL - before any mod was applied)",
            "rolling": "  (most recent save)",
        }.get(self.kind, "")
        return f"{self.name}  -  {stamp}, {self.size_mb:.1f} MB{suffix}"


def list_backups(backups_dir: Path, db_path: Path | None = None) -> list[BackupEntry]:
    """Every restorable copy: original, then rolling, then dated newest first.

    The ``.original`` and ``.backup`` files live beside the database rather than
    in ``backups/``, so they have to be looked for separately - leaving them out
    is why "no backups yet" used to show up with one sitting right there.
    """
    entries: list[BackupEntry] = []

    if db_path is not None:
        original = original_backup_path(db_path)
        if original.is_file():
            entries.append(BackupEntry(original, "original"))
        rolling = rolling_backup_path(db_path)
        if rolling.is_file():
            entries.append(BackupEntry(rolling, "rolling"))

    directory = Path(backups_dir)
    if directory.is_dir():
        entries += [
            BackupEntry(path, "dated")
            for path in sorted(directory.glob(DATED_GLOB), key=lambda p: p.name, reverse=True)
        ]
    return entries


def restore_backup(backup_path: Path, db_path: Path) -> None:
    """Put a backup back over the live database."""
    backup_path, db_path = Path(backup_path), Path(db_path)
    if not backup_path.is_file():
        raise FileNotFoundError(f"backup not found: {backup_path}")
    copy_database(backup_path, db_path)
