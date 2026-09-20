"""Which of the game's packages hold which textures.

Unreal's cooker copies a texture into every package that uses it, so one
texture can live in many packages at once. A texture pack has to reach every
copy, or the old one still shows wherever the package holding it loads - which
is why a mod for Tetsuo also changes LastBoss_Escalator.upk. This follows TFC
Installer's second pass (FCH823's, https://www.nexusmods.com/site/mods/587,
used with their permission).

Finding them means reading the tables of all of the game's ~7,700 packages,
which takes a few minutes. So the answer is kept on disk and only the packages
that changed since - by size or time - are read again.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import package as P
from . import texture2d as T2

FORMAT = 1


@dataclass
class TextureIndex:
    by_texture: dict[str, list[str]]        # lower-case texture path -> package names
    unreadable: dict[str, str]              # package name -> why it could not be read

    def packages_holding(self, texture_path: str) -> list[str]:
        return list(self.by_texture.get(texture_path.replace("/", "\\").lower(), []))


def _stamp(path: Path) -> list[int]:
    stat = path.stat()
    return [stat.st_size, int(stat.st_mtime)]


def build(cooked_dir: Path, cache_file: Path | None = None,
          progress: Callable[[int, int, str], None] | None = None) -> TextureIndex:
    """The index for the packages in ``cooked_dir``, reusing ``cache_file``.

    ``progress`` is told (done, total, package name) as packages are read, so a
    first build - the slow one - can say how far along it is.
    """
    cooked_dir = Path(cooked_dir)
    cached: dict = {}
    if cache_file is not None and Path(cache_file).is_file():
        try:
            saved = json.loads(Path(cache_file).read_text(encoding="utf-8"))
            if saved.get("format") == FORMAT:
                cached = saved.get("packages", {})
        except (OSError, ValueError):
            cached = {}

    files = sorted(cooked_dir.glob("*.upk"))
    packages: dict[str, dict] = {}
    for done, path in enumerate(files, start=1):
        stamp = _stamp(path)
        previous = cached.get(path.name)
        if previous and previous.get("stamp") == stamp:
            packages[path.name] = previous
        else:
            try:
                textures = sorted(T2.textures_by_path(P.read_tables(path)))
                packages[path.name] = {"stamp": stamp, "textures": textures}
            except Exception as problem:            # noqa: BLE001 - recorded, not fatal
                packages[path.name] = {"stamp": stamp, "error": f"{problem}"}
        if progress is not None:
            progress(done, len(files), path.name)

    if cache_file is not None:
        Path(cache_file).parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(cache_file).with_suffix(".tmp")
        temporary.write_text(json.dumps({"format": FORMAT, "packages": packages}),
                             encoding="utf-8")
        temporary.replace(cache_file)

    by_texture: dict[str, list[str]] = {}
    unreadable: dict[str, str] = {}
    for name, record in packages.items():
        if "error" in record:
            unreadable[name] = record["error"]
            continue
        for texture in record["textures"]:
            by_texture.setdefault(texture, []).append(name)
    return TextureIndex(by_texture, unreadable)
