"""Noticing when the game replaces ``masters.db``.

Polls mtime and size, and only pays for a sha256 when one of those moved - so
the common case (nothing happened) costs two stat calls. When the hash differs
from the one stamped at the last "Save Mod List", the enabled mods are stale.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .sqlutil import sha256_file

STATUS_OK = "ok"  # DB matches the stamp: mods are on
STATUS_STALE = "stale"  # DB changed since the stamp: mods need re-applying
STATUS_UNSTAMPED = "unstamped"  # never saved a mod list for this DB
STATUS_MISSING = "missing"  # the DB file is gone


@dataclass
class DbStatus:
    state: str
    sha256: str = ""
    mtime: float = 0.0
    size: int = 0
    detail: str = ""

    @property
    def changed(self) -> bool:
        return self.state == STATUS_STALE

    @property
    def label(self) -> str:
        if self.state == STATUS_OK:
            return f"Up to date (sha {self.sha256[:8]})"
        if self.state == STATUS_STALE:
            return f"Database changed since last save (sha {self.sha256[:8]}) - mods are stale"
        if self.state == STATUS_UNSTAMPED:
            return "No mod list saved for this database yet"
        return self.detail or "Database file not found"


class DbWatcher:
    """Stateless-ish poll. Call ``check()`` on a timer; call ``stamp()`` on save."""

    def __init__(self, db_path: Path | str | None = None, expected_sha: str = "", expected_mtime: float = 0.0):
        self.db_path = Path(db_path) if db_path else None
        self.expected_sha = expected_sha
        self.expected_mtime = expected_mtime
        self._last_seen: tuple[float, int] | None = None
        self._last_sha = ""

    def set_db(self, db_path: Path | str | None, expected_sha: str = "", expected_mtime: float = 0.0) -> None:
        self.db_path = Path(db_path) if db_path else None
        self.expected_sha = expected_sha
        self.expected_mtime = expected_mtime
        self._last_seen = None
        self._last_sha = ""

    def stamp(self, sha256: str, mtime: float) -> None:
        """Called after a save: this is the state the mods are known to be on."""
        self.expected_sha = sha256
        self.expected_mtime = mtime
        self._last_seen = None
        self._last_sha = sha256

    def check(self) -> DbStatus:
        if self.db_path is None:
            return DbStatus(STATUS_MISSING, detail="No database selected")
        try:
            stat = os.stat(self.db_path)
        except OSError:
            return DbStatus(STATUS_MISSING, detail=f"Database file not found: {self.db_path}")

        fingerprint = (stat.st_mtime, stat.st_size)
        if self._last_seen == fingerprint and self._last_sha:
            sha = self._last_sha  # nothing moved, reuse the hash we already paid for
        else:
            try:
                sha = sha256_file(self.db_path)
            except OSError as exc:
                return DbStatus(STATUS_MISSING, detail=f"Database could not be read: {exc}")
            self._last_seen = fingerprint
            self._last_sha = sha

        if not self.expected_sha:
            return DbStatus(STATUS_UNSTAMPED, sha, stat.st_mtime, stat.st_size)
        if sha != self.expected_sha:
            return DbStatus(STATUS_STALE, sha, stat.st_mtime, stat.st_size)
        return DbStatus(STATUS_OK, sha, stat.st_mtime, stat.st_size)


class PollingWatcher(threading.Thread):
    """Background poller for the CLI. The GUI uses a QTimer over DbWatcher instead."""

    def __init__(self, watcher: DbWatcher, on_change: Callable[[DbStatus], None], interval: float = 5.0):
        super().__init__(daemon=True, name="lid-db-watchdog")
        self.watcher = watcher
        self.on_change = on_change
        self.interval = max(1.0, float(interval))
        self._stop = threading.Event()
        self._last_state = ""

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        while not self._stop.wait(self.interval):
            status = self.watcher.check()
            if status.state == self._last_state:
                continue
            self._last_state = status.state
            try:
                self.on_change(status)
            except Exception:
                pass
