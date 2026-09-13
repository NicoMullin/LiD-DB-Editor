"""Finding artwork for an item, in a folder the player already has.

**Nothing ships with this program.** The game's artwork belongs to Grasshopper
Manufacture, not to us and not to whoever extracted it, so none of it is in this
repository or in any release. What this does instead is read a folder the player
points at - artwork they already have, or extracted from their own copy of the
game - and show it if it is there. With no folder set, everything still works;
there are simply no pictures.

The folder is expected to look like the one from the Let It Die Save Editor
(github.com/g3usyk/Let-It-Die-Save-Editor), which carries an ``icon_map.json``
keyed by the game's own ids - the same ids `masters.db` uses - so no guessing
about which picture belongs to which item is needed.

If that file is missing, names are matched instead: "Battle Machete" against
``battle_machete.png``. That is a fallback, not the main path.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

# Where to look, in order, for a game id. The save editor splits these by what
# the picture is of: the icon, the collectible card, the material thumbnail.
MAP_SECTIONS = ("gear_icons", "materials_thumbs", "decals_icons",
                "gear_cards", "materials_cards")

MAP_FILE = "icon_map.json"
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")


def slug(name: str) -> str:
    """"Battle Machete" -> "battle_machete"."""
    text = name.lower().strip().replace("&", "and")
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")


def blueprint_part(item_id: str) -> str:
    """A blueprint has no art of its own; it borrows the gear's.

    Kept here as well as in ``builder`` because this module is meant to stand
    alone - it knows nothing about building mods.
    """
    if not item_id.startswith("ITMP_"):
        return item_id
    stem = item_id[5:]
    return "PT_" + (stem[:-1] if stem.endswith("U") else stem)


class IconSource:
    """Looks up artwork by game id, or by name if there is no map."""

    def __init__(self, folder: Path | str | None):
        self.folder = Path(folder) if folder else None
        self._map: dict[str, str] = {}
        self._by_name: dict[str, Path] = {}
        self._cache: dict[str, Path | None] = {}
        if self.folder and self.folder.is_dir():
            self._load_map()

    @property
    def available(self) -> bool:
        return bool(self._map or self._by_name)

    def _load_map(self) -> None:
        """Read icon_map.json if it is there, else index the folder by name."""
        assert self.folder is not None
        found = self.folder / MAP_FILE
        if not found.is_file():
            found = self.folder.parent / MAP_FILE if self.folder.parent else found
        if found.is_file():
            try:
                data = json.loads(found.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
            for section in MAP_SECTIONS:
                part = data.get(section)
                if isinstance(part, dict):
                    # Earlier sections win, so an icon beats a card.
                    for key, value in part.items():
                        self._map.setdefault(str(key), str(value))
        # Index by filename too. This is what makes a folder of loose pictures
        # work at all, and it fills the gaps the map does not cover.
        for path in self.folder.rglob("*"):
            if path.suffix.lower() in IMAGE_SUFFIXES and path.is_file():
                self._by_name.setdefault(path.stem.lower(), path)

    def _resolve(self, relative: str) -> Path | None:
        assert self.folder is not None
        candidate = self.folder / relative
        if candidate.is_file():
            return candidate
        # A map may be written relative to the project root rather than to the
        # icons folder itself.
        candidate = self.folder.parent / relative if self.folder.parent else candidate
        return candidate if candidate.is_file() else None

    def for_id(self, game_id: str, name: str = "") -> Path | None:
        """Artwork for one row, or None when there is none to show."""
        if not self.folder or not game_id:
            return None
        key = f"{game_id}|{name}"
        if key in self._cache:
            return self._cache[key]
        found = self._look(game_id, name)
        self._cache[key] = found
        return found

    def _look(self, game_id: str, name: str) -> Path | None:
        for candidate_id in (game_id, blueprint_part(game_id)):
            relative = self._map.get(candidate_id)
            if relative:
                found = self._resolve(relative)
                if found is not None:
                    return found
        # Filenames sometimes are the id, lowercased, with or without a prefix.
        for candidate_id in (game_id, blueprint_part(game_id)):
            lowered = candidate_id.lower()
            for stem in (lowered, f"thumb_{lowered}", f"mat_{lowered}"):
                found = self._by_name.get(stem)
                if found is not None:
                    return found
        if name:
            found = self._by_name.get(slug(name))
            if found is not None:
                return found
        return None

    def count_for(self, ids: list[str]) -> int:
        """How many of these have artwork - for telling the player what they got."""
        return sum(1 for i in ids if self.for_id(i) is not None)
