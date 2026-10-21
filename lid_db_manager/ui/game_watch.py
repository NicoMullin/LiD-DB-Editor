"""Notices LET IT DIE starting and closing, however it was started.

A timer on the window looks for the game's process every few seconds (a
process list is cheap to read). When the game has gone, the save is what it
wrote last as it quit, so ``gameClosed`` waits until the save files have not
changed for one more look - a game still writing its save as it shuts down
is never caught halfway.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

INTERVAL_MS = 3000


class GameWatch(QObject):
    gameStarted = Signal()
    gameClosed = Signal()

    def __init__(self, is_running, save_files, parent=None, interval_ms: int = INTERVAL_MS):
        """``is_running()``: is the game running; ``save_files()``: the saves to see settle."""
        super().__init__(parent)
        self._is_running = is_running
        self._save_files = save_files
        self._was_running = False
        self._settling = False
        self._last = None
        # Parented, so it stops with the window (see first-run-timer-flake).
        self.timer = QTimer(self)
        self.timer.setInterval(interval_ms)
        self.timer.timeout.connect(self.look)

    def start(self) -> None:
        self._was_running = bool(self._is_running())
        self.timer.start()

    @property
    def running(self) -> bool:
        return self._was_running

    def _files_now(self) -> tuple:
        state = []
        for path in self._save_files():
            try:
                stat = Path(path).stat()
                state.append((str(path), stat.st_mtime_ns, stat.st_size))
            except OSError:
                state.append((str(path), None, None))
        return tuple(state)

    def look(self) -> None:
        running = bool(self._is_running())
        if running:
            if not self._was_running:
                self._was_running = True
                self.gameStarted.emit()
            self._settling = False
            return
        if self._was_running:
            self._was_running = False
            self._settling = True
            self._last = self._files_now()
            return
        if self._settling:
            now = self._files_now()
            if now == self._last:
                self._settling = False
                self.gameClosed.emit()
            else:
                self._last = now
