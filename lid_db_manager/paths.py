"""Where the tool keeps its own files.

Everything lives next to the source tree by default::

    <root>/mods/         mod folders (user-extensible)
    <root>/snapshots/    per-mod pre-apply snapshots
    <root>/logs/         one plain-text log per session
    <root>/backups/      dated .db snapshots taken on "Save Mod List"
    <root>/state.json    enabled mods, modpacks, last-seen DB hash

Set ``LID_DB_MANAGER_HOME`` to move all of it somewhere else - useful when the
tool is installed read-only, or frozen into an .exe living in Program Files.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

ENV_HOME = "LID_DB_MANAGER_HOME"


def _default_root() -> Path:
    override = os.environ.get(ENV_HOME)
    if override:
        return Path(override).expanduser().resolve()
    if getattr(sys, "frozen", False):  # PyInstaller one-file build
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class AppPaths:
    root: Path

    @staticmethod
    def default() -> "AppPaths":
        return AppPaths(_default_root())

    @property
    def mods_dir(self) -> Path:
        return self.root / "mods"

    @property
    def snapshots_dir(self) -> Path:
        return self.root / "snapshots"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    @property
    def backups_dir(self) -> Path:
        return self.root / "backups"

    @property
    def state_file(self) -> Path:
        return self.root / "state.json"

    def ensure(self) -> "AppPaths":
        for directory in (self.mods_dir, self.snapshots_dir, self.logs_dir, self.backups_dir):
            directory.mkdir(parents=True, exist_ok=True)
        return self
