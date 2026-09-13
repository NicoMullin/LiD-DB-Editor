"""Small pictures, made once and kept.

The artwork a player points us at is full-size renders - two megabytes each in
the case of the Save Editor's folder. Decoding four hundred of those to draw
them at 40 pixels would stall the window every time a list was filled.

So each one is scaled once and written to a cache folder in the manager's own
home directory, and after that the list draws from files a few kilobytes each.
The cache is keyed on the source file's path, size and modification time, so
replacing the artwork replaces the thumbnail too.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPixmap

SIZE = 40
FOLDER = "icon-cache"


class ThumbnailCache:
    """Scaled copies of whatever artwork the player has pointed us at."""

    def __init__(self, home: Path, size: int = SIZE):
        self.dir = Path(home) / FOLDER
        self.size = size
        self._memory: dict[Path, QIcon] = {}
        self._made = False

    def _ensure_dir(self) -> None:
        if not self._made:
            self.dir.mkdir(parents=True, exist_ok=True)
            self._made = True

    def _cache_path(self, source: Path) -> Path:
        try:
            stat = source.stat()
            stamp = f"{source}|{stat.st_size}|{int(stat.st_mtime)}|{self.size}"
        except OSError:
            stamp = f"{source}|{self.size}"
        digest = hashlib.sha1(stamp.encode("utf-8", "replace")).hexdigest()[:20]
        return self.dir / f"{digest}.png"

    def icon(self, source: Path | None) -> QIcon | None:
        """A small icon for one picture, making it the first time it is asked for."""
        if source is None:
            return None
        if source in self._memory:
            return self._memory[source]
        small = self._cache_path(source)
        pixmap = QPixmap()
        if small.is_file():
            pixmap.load(str(small))
        if pixmap.isNull():
            full = QPixmap()
            if not full.load(str(source)) or full.isNull():
                self._memory[source] = None  # type: ignore[assignment]
                return None
            pixmap = full.scaled(
                self.size, self.size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            try:
                self._ensure_dir()
                pixmap.save(str(small), "PNG")
            except OSError:
                pass  # a cache that cannot be written is not worth failing over
        made = QIcon(pixmap)
        self._memory[source] = made
        return made

    def clear(self) -> int:
        """Throw the cache away. Returns how many files went."""
        self._memory.clear()
        if not self.dir.is_dir():
            return 0
        gone = 0
        for path in self.dir.glob("*.png"):
            try:
                path.unlink()
                gone += 1
            except OSError:
                pass
        return gone
