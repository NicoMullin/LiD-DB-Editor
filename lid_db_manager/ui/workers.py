"""Background threads.

Applying to a large masters.db and hashing it are both slow enough to freeze the
window, so both happen off the GUI thread and come back as signals.
"""

from __future__ import annotations

import traceback
from typing import Callable

from PySide6.QtCore import QMutex, QMutexLocker, QThread, Signal

from ..watchdog import DbStatus, DbWatcher


class TaskThread(QThread):
    """Runs one callable, then emits either its result or the traceback."""

    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, work: Callable[[], object], parent=None):
        super().__init__(parent)
        self._work = work

    def run(self) -> None:
        try:
            result = self._work()
        except Exception:
            self.failed.emit(traceback.format_exc())
            return
        self.succeeded.emit(result)


class WatchThread(QThread):
    """Polls the database on its own clock and reports state transitions."""

    statusChanged = Signal(object)

    def __init__(self, interval: float = 5.0, parent=None):
        super().__init__(parent)
        self._watcher = DbWatcher()
        self._mutex = QMutex()
        self._interval = max(1.0, float(interval))
        self._running = True
        self._last_state = ""

    def configure(self, db_path, sha256: str = "", mtime: float = 0.0) -> None:
        with QMutexLocker(self._mutex):
            self._watcher.set_db(db_path, sha256, mtime)
            self._last_state = ""

    def stamp(self, sha256: str, mtime: float) -> None:
        with QMutexLocker(self._mutex):
            self._watcher.stamp(sha256, mtime)
            self._last_state = ""

    def set_interval(self, seconds: float) -> None:
        with QMutexLocker(self._mutex):
            self._interval = max(1.0, float(seconds))

    def stop(self) -> None:
        self._running = False

    def poll_now(self) -> DbStatus:
        with QMutexLocker(self._mutex):
            return self._watcher.check()

    def run(self) -> None:
        while self._running:
            with QMutexLocker(self._mutex):
                interval = self._interval
                status = self._watcher.check()
                changed = status.state != self._last_state
                self._last_state = status.state
            if changed:
                self.statusChanged.emit(status)
            # Sleep in short slices so quitting the app is not a 5 second wait.
            slept = 0.0
            while self._running and slept < interval:
                self.msleep(200)
                slept += 0.2
