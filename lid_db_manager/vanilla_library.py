"""The clean databases the manager measures everything else against.

A mod is "what this database has that vanilla does not", so anything worth
knowing - which mods are already in someone's file, what a reworked `masters.db`
actually changes - needs an untouched copy of the *right game build* to compare
with. Getting the build wrong is worse than having no copy at all: the game's
own patch then reads as a mod, and the manager would offer to bottle it up and
apply it on top of a later version.

Two places are searched, in this order:

    <bundled>/LiD Vanilla DB/<label>/masters.db   copies shipped with the manager
    <root>/LiD Vanilla DB/<label>/masters.db      copies the user dropped in

The folder name is only a label. The build number is read out of each database,
because a folder can be called anything and a file that says nothing about its
build cannot be matched to anyone's game.

Nothing here writes anything.
"""

from __future__ import annotations

import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

DIRNAME = "LiD Vanilla DB"
DB_NAME = "masters.db"


@dataclass(frozen=True)
class VanillaBuild:
    """One clean database on disk."""

    path: Path
    label: str  # the folder it sits in
    version: str  # TITLE_VERSION out of the file, "" when it does not say
    bundled: bool  # shipped with the manager rather than dropped in by hand

    @property
    def name(self) -> str:
        """What to call it in a list: the build it says it is, else its folder."""
        return self.version or self.label

    @property
    def description(self) -> str:
        where = "ships with the manager" if self.bundled else f"from {self.path.parent.name}"
        return f"{self.name} ({where})"


def database_version(path: Path) -> str:
    """The build a database says it is, or "" - never raises.

    A test fixture, a trimmed copy or a different game entirely will not have
    ``master_const_str``, and that is not an error here: it just means this copy
    cannot be matched by build.
    """
    try:
        con = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
    except sqlite3.Error:
        return ""
    try:
        row = con.execute(
            "SELECT value FROM master_const_str WHERE id = 'TITLE_VERSION'"
        ).fetchone()
        return str(row[0] or "").strip() if row else ""
    except sqlite3.Error:
        return ""
    finally:
        con.close()


def _bundled_dir() -> Path | None:
    """Where a frozen build keeps the copies that ship with it."""
    bundled = getattr(sys, "_MEIPASS", None)
    return Path(bundled) / DIRNAME if bundled else None


def search_dirs(root: Path | None = None) -> list[Path]:
    """Every folder that may hold vanilla databases, bundled copies first."""
    found: list[Path] = []
    bundled = _bundled_dir()
    if bundled is not None:
        found.append(bundled)
    if root is not None:
        found.append(Path(root) / DIRNAME)
    # A source checkout has no _MEIPASS, so its repository folder is both.
    seen: set[Path] = set()
    unique = []
    for directory in found:
        resolved = directory.resolve() if directory.exists() else directory
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(directory)
    return unique


def builds(root: Path | None = None) -> list[VanillaBuild]:
    """Every clean database available, newest-looking label last.

    One entry per build: if a dropped-in copy says it is the same build as a
    bundled one, the bundled copy wins - it is the one that was tested against
    the recipes and the shipped mods.
    """
    out: list[VanillaBuild] = []
    claimed: set[str] = set()
    for directory in search_dirs(root):
        bundled = _bundled_dir() is not None and directory == _bundled_dir()
        if not directory.is_dir():
            continue
        for child in sorted(p for p in directory.iterdir() if p.is_dir()):
            database = child / DB_NAME
            if not database.is_file():
                continue
            version = database_version(database)
            key = version or f"label:{child.name}"
            if key in claimed:
                continue
            claimed.add(key)
            out.append(
                VanillaBuild(
                    path=database, label=child.name, version=version, bundled=bundled
                )
            )
    return out


def for_version(version: str, root: Path | None = None) -> VanillaBuild | None:
    """The clean copy of exactly this build, if there is one."""
    wanted = (version or "").strip()
    if not wanted:
        return None
    for build in builds(root):
        if build.version == wanted:
            return build
    return None


def for_label(label: str, root: Path | None = None) -> VanillaBuild | None:
    """The clean copy sitting in a folder of this name, if there is one."""
    for build in builds(root):
        if build.label == label:
            return build
    return None


def best_for(db_path: Path | None, root: Path | None = None) -> VanillaBuild | None:
    """The clean copy matching this database's build.

    Returns None when nothing matches - deliberately, rather than falling back
    to the closest one. A diff against the wrong build turns the game's own
    patch into what looks like a mod, which is the one outcome worth refusing.
    """
    if not db_path or not Path(db_path).is_file():
        return None
    return for_version(database_version(Path(db_path)), root)
