"""One timestamped plain-text log per session, plus live listeners for the UI."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

LEVELS = ("INFO", "WARN", "ERROR")


@dataclass
class LogLine:
    when: datetime
    level: str
    message: str

    def format(self) -> str:
        return f"[{self.when.strftime('%H:%M:%S')}] {self.level:<5} {self.message}"

    def format_full(self) -> str:
        return f"{self.when.isoformat(timespec='seconds')} {self.level:<5} {self.message}"


class SessionLog:
    """Append-only log. Writes to disk immediately so a crash keeps the trail."""

    def __init__(self, logs_dir: Path | None = None, *, echo: bool = False):
        self.lines: list[LogLine] = []
        self.listeners: list[Callable[[LogLine], None]] = []
        self.echo = echo
        self._lock = threading.RLock()
        self.path: Path | None = None
        if logs_dir is not None:
            directory = Path(logs_dir)
            directory.mkdir(parents=True, exist_ok=True)
            self.path = directory / f"{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.log"
            self._write_header()

    def _write_header(self) -> None:
        from . import APP_NAME, __version__

        self._append_to_file(f"# {APP_NAME} {__version__} - session started {datetime.now()}")

    def _append_to_file(self, text: str) -> None:
        if self.path is None:
            return
        try:
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(text + "\n")
        except OSError:
            # Losing the log file must never take down an apply.
            self.path = None

    def add_listener(self, listener: Callable[[LogLine], None]) -> None:
        with self._lock:
            self.listeners.append(listener)

    def log(self, message: str, level: str = "INFO") -> LogLine:
        level = level if level in LEVELS else "INFO"
        line = LogLine(datetime.now(), level, message)
        with self._lock:
            self.lines.append(line)
            self._append_to_file(line.format_full())
            listeners = list(self.listeners)
        if self.echo:
            print(line.format())
        for listener in listeners:
            try:
                listener(line)
            except Exception:
                pass
        return line

    def info(self, message: str) -> LogLine:
        return self.log(message, "INFO")

    def warn(self, message: str) -> LogLine:
        return self.log(message, "WARN")

    def error(self, message: str) -> LogLine:
        return self.log(message, "ERROR")

    def text(self) -> str:
        with self._lock:
            return "\n".join(line.format() for line in self.lines)


def rotate_logs(logs_dir: Path, keep: int = 30) -> list[Path]:
    """Keep the newest ``keep`` session logs."""
    directory = Path(logs_dir)
    if not directory.is_dir():
        return []
    logs = sorted(directory.glob("*.log"), key=lambda p: p.name, reverse=True)
    removed = []
    for path in logs[max(keep, 1) :]:
        try:
            path.unlink()
            removed.append(path)
        except OSError:
            continue
    return removed
