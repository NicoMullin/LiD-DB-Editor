"""Recognising the LET IT DIE Crossover Content pack.

The pack is a folder of 234 artwork packages plus its own installer. Dropped on
the manager as-is it reads as an ordinary asset folder: the artwork gets copied
and the database half - the decal pool entries and blueprint quests that make
the artwork reachable in game - is silently dropped, so nothing appears.

This module closes that gap. It identifies the pack, checks it is a release we
have a recorded recipe for, and hands back the SQL that goes with it, so the
manager can build one mod holding both halves.

Why a recorded recipe rather than running the pack's installer: the manager
rebuilds masters.db from vanilla plus the enabled mods every time the list
changes, so anything written to the game out-of-band is lost on the next toggle.
The content has to be a mod in the stack to survive. The recipes are recorded
from the pack's own installer by ``tools/build_crossover_recipe.py``; see that
file for how, and the README for why the pack itself is not redistributed.

Nothing here touches Qt, the game, or the network.
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

RECIPES_DIRNAME = "recipes"


def _recipes_dir() -> Path:
    """Where the recorded recipes live, source tree or frozen build.

    PyInstaller keeps pure Python in an archive, so ``__file__`` does not point
    at a real directory in a frozen build; the recipes are added as data files
    under the package name instead (see build.py).
    """
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        return Path(bundled) / "lid_db_manager" / RECIPES_DIRNAME
    return Path(__file__).resolve().parent / RECIPES_DIRNAME

# What the pack folder must contain to be the pack at all. The catalog is the
# authority on its content; the installer is what we are deliberately replacing.
CATALOG_NAME = "catalog.json"
ASSETS_DIRNAME = "assets"

PACK_NAME = "LET IT DIE Crossover Content"
PACK_SOURCE = "the pack's own release page"


@dataclass(frozen=True)
class Recipe:
    """A recorded set of database changes for one release of the pack."""

    version: str
    catalog_sha256: str
    sql_path: Path
    recorded: str = ""
    # The game build the recording was taken from. Carried into the mod it
    # builds, so a later game patch shows up as a warning instead of silently
    # applying rows measured against a database that no longer exists.
    game_version: str = ""
    counts: dict = field(default_factory=dict)
    changes: dict = field(default_factory=dict)

    def sql(self) -> str:
        return self.sql_path.read_text(encoding="utf-8")

    @property
    def summary(self) -> str:
        decals = self.counts.get("decals", 0)
        quests = self.counts.get("quests", 0)
        return f"{decals} decal-pool entries and {quests} blueprint quests"


@dataclass
class PackInfo:
    """What a folder turned out to be, and whether we can install it whole."""

    folder: Path
    is_pack: bool = False
    version: str = ""
    catalog_sha256: str = ""
    asset_count: int = 0
    counts: dict = field(default_factory=dict)
    recipe: Recipe | None = None
    reason: str = ""

    @property
    def complete(self) -> bool:
        """True when both halves can be installed: artwork and database."""
        return self.is_pack and self.recipe is not None

    @property
    def suggested_name(self) -> str:
        return f"{PACK_NAME} v{self.version}" if self.version else PACK_NAME


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def known_recipes(directory: Path | None = None) -> dict[str, Recipe]:
    """Every recorded recipe that ships with the manager, keyed by version."""
    folder = Path(directory) if directory is not None else _recipes_dir()
    found: dict[str, Recipe] = {}
    if not folder.is_dir():
        return found
    for meta_file in sorted(folder.glob("crossover-*.json")):
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            sql_path = folder / meta["sql"]
            if not sql_path.is_file():
                continue
            recipe = Recipe(
                version=str(meta["version"]),
                catalog_sha256=str(meta["catalog_sha256"]),
                sql_path=sql_path,
                recorded=str(meta.get("recorded", "")),
                game_version=str(meta.get("game_version", "")).strip(),
                counts=dict(meta.get("counts", {})),
                changes=dict(meta.get("changes", {})),
            )
        except (OSError, ValueError, KeyError):
            continue  # a damaged recipe is skipped, never fatal
        found[recipe.version] = recipe
    return found


def _read_catalog(folder: Path) -> dict | None:
    catalog = folder / CATALOG_NAME
    if not catalog.is_file():
        return None
    try:
        data = json.loads(catalog.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    # Distinguish this pack's catalog from any other file of that name.
    if not isinstance(data, dict) or not all(
        isinstance(data.get(key), list) for key in ("files", "decals", "quests")
    ):
        return None
    return data


def identify(folder: Path, recipes: dict[str, Recipe] | None = None) -> PackInfo:
    """Work out whether a folder is the pack, and whether we can install it whole.

    Never raises for an ordinary folder - a plain pile of .upk is simply "not
    the pack", which the caller handles as an asset folder like any other.
    """
    folder = Path(folder)
    info = PackInfo(folder=folder)
    data = _read_catalog(folder)
    if data is None:
        return info

    assets = folder / ASSETS_DIRNAME
    if not assets.is_dir():
        info.reason = (
            f"This looks like the {PACK_NAME} catalogue, but there is no "
            f"{ASSETS_DIRNAME}/ folder beside it. Extract the whole download, "
            "not just part of it."
        )
        return info

    info.is_pack = True
    info.version = str(data.get("version", "")).strip()
    info.catalog_sha256 = _sha256(folder / CATALOG_NAME)
    info.asset_count = sum(1 for p in assets.iterdir() if p.is_file())
    info.counts = {
        "decals": len(data["decals"]),
        "quests": len(data["quests"]),
        "clones": len(data.get("clones", [])),
        "packages": len(data["files"]),
    }

    available = known_recipes() if recipes is None else recipes
    if not available:
        info.reason = (
            "No recorded database changes ship with this manager, so only the "
            "artwork can be installed."
        )
        return info

    match = available.get(info.version)
    if match is None:
        shipped = ", ".join("v" + v for v in sorted(available))
        info.reason = (
            f"This is {PACK_NAME} v{info.version or '(no version)'}, and this "
            f"manager only has recorded database changes for {shipped}. The "
            "artwork can still be installed on its own, but the new content "
            "will not appear in game until the manager is updated."
        )
        return info

    # The catalogue fully determines the database changes, so a hash that does
    # not match means this is not the release the recipe was recorded from -
    # repackaged, edited, or corrupted. Guessing here would write the wrong rows.
    if match.catalog_sha256 != info.catalog_sha256:
        info.reason = (
            f"This says it is v{info.version}, but its catalogue does not match "
            f"the v{info.version} release the recorded database changes came "
            "from, so they may not fit it. Re-download the pack from "
            f"{PACK_SOURCE}. The artwork can still be installed on its own."
        )
        return info

    info.recipe = match
    return info
