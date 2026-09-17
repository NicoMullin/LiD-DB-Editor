"""Where the game's masters.db probably is, to open the file picker there.

Only a starting point for "Locate masters.db" - the player still picks the file,
and nothing here is ever used as the database without them choosing it.

Steam can install a game into any of its library folders, on any drive. Steam
lists those folders in ``steamapps/libraryfolders.vdf`` inside its own install,
so that file is read rather than guessing at drives. The usual folder names on
each drive are tried as well, for a Steam whose own folder cannot be found.
"""

from __future__ import annotations

import os
import re
import string
import sys
from pathlib import Path

GAME_DIR = "LET IT DIE"
DB_REL = Path("BrgGame") / "Content" / "masters.db"
DEFAULT_STEAM = Path(r"C:\Program Files (x86)\Steam")

# Where people commonly put a second Steam library, relative to a drive root.
USUAL_LIBRARY_NAMES = ("SteamLibrary", "Steam", "Games/Steam", "Games/SteamLibrary",
                       "Program Files (x86)/Steam", "Program Files/Steam")

_PATH_LINE = re.compile(r'"path"\s+"((?:[^"\\]|\\.)*)"', re.IGNORECASE)


def steam_roots() -> list[Path]:
    """Steam's own install folder(s): from the registry, then the default."""
    found: list[Path] = []
    if sys.platform == "win32":
        try:
            import winreg

            for hive, key, value in (
                (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
            ):
                try:
                    with winreg.OpenKey(hive, key) as handle:
                        found.append(Path(str(winreg.QueryValueEx(handle, value)[0])))
                except OSError:
                    continue
        except ImportError:
            pass
    found.append(DEFAULT_STEAM)
    unique: list[Path] = []
    for path in found:
        if path not in unique:
            unique.append(path)
    return unique


def library_folders(steam_root: Path) -> list[Path]:
    """Every library Steam lists, its own folder first."""
    folders = [Path(steam_root)]
    vdf = Path(steam_root) / "steamapps" / "libraryfolders.vdf"
    try:
        text = vdf.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return folders
    for match in _PATH_LINE.finditer(text):
        path = Path(match.group(1).replace("\\\\", "\\"))
        if path not in folders:
            folders.append(path)
    return folders


def _drives() -> list[Path]:
    if sys.platform != "win32":
        return []
    return [Path(f"{letter}:/") for letter in string.ascii_uppercase if os.path.exists(f"{letter}:/")]


def candidate_libraries(roots: list[Path] | None = None, drives: list[Path] | None = None) -> list[Path]:
    libraries: list[Path] = []
    for root in (steam_roots() if roots is None else roots):
        for folder in library_folders(root):
            if folder not in libraries:
                libraries.append(folder)
    for drive in (_drives() if drives is None else drives):
        for name in USUAL_LIBRARY_NAMES:
            folder = Path(drive) / name
            if folder not in libraries:
                libraries.append(folder)
    return libraries


def find_game_database(roots: list[Path] | None = None, drives: list[Path] | None = None) -> Path | None:
    """The first masters.db found in a Steam library, or None."""
    for library in candidate_libraries(roots, drives):
        candidate = library / "steamapps" / "common" / GAME_DIR / DB_REL
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None
