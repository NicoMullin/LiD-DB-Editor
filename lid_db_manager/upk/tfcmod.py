"""A TFC Installer mod folder, as a set of changes to the game's files.

The format and the install logic are FCH823's, from TFC Installer
(https://www.nexusmods.com/site/mods/587), ported with their permission for
LET IT DIE only. Anyone wanting the full tool should get it there.

A mod folder looks like:

    GameProfile.xml, ObjectDescriptors.*, ...      the tool's own settings
    Game/BrgGame/CookedPCConsole/*.upk.PackagePatch changes to named packages
    TexturePack/Let it Die.TFCMapping              which textures change
    TexturePack/Texture2D_0.tfc                    their big mips
    TexturePack/LocalMips_0.tfc                    their small ones

Installing it changes two kinds of package: those with a patch of their own,
and every package holding a copy of a texture the pack replaces - found with
texture_index. The pack's .tfc goes in beside them under the next free name,
Texture2D_N.tfc, and every changed texture is pointed at that N.

Each package's new bytes are always worked out from the stock package, never
from one already changed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import apply as A
from . import package as P
from . import packagepatch as PP
from . import texture2d as T2
from . import texturepack as TP
from .texture_index import TextureIndex

COOKED = "BrgGame/CookedPCConsole"
PATCH_SUFFIX = ".PackagePatch"
MAPPING_SUFFIX = ".TFCMapping"
LOCAL_CACHE = "LocalMips_0.tfc"


class TfcModError(ValueError):
    """The folder is not a TFC Installer mod this can install."""


@dataclass
class TfcMod:
    root: Path
    patches: dict[str, Path] = field(default_factory=dict)   # package name (lower) -> patch
    mapping: TP.Mapping | None = None
    mapping_file: Path | None = None
    caches: dict[int, Path] = field(default_factory=dict)    # pack's own index -> .tfc
    local_cache: Path | None = None

    @property
    def has_textures(self) -> bool:
        return self.mapping is not None and bool(self.mapping.entries)


def is_tfc_mod(folder: Path) -> bool:
    """Does this folder look like a TFC Installer mod?"""
    folder = Path(folder)
    if any(folder.rglob("*" + PATCH_SUFFIX)):
        return True
    pack = folder / "TexturePack"
    return pack.is_dir() and any(pack.glob("*" + MAPPING_SUFFIX))


def unsupported_content(folder: Path) -> list[str]:
    """What this mod carries beyond patches and a texture pack.

    TFC Installer mods can also copy files into the game, patch .ini files, and
    write into system folders such as Documents. None of that is supported here
    yet, and installing the rest without it would leave the mod half-done, so a
    mod carrying any of it is refused rather than installed in part.
    """
    folder = Path(folder)
    found = []
    game = folder / "Game"
    if game.is_dir():
        others = [p for p in game.rglob("*")
                  if p.is_file() and not p.name.endswith(PATCH_SUFFIX)]
        if others:
            kinds = sorted({"ini patches" if "ini" in p.suffix.lower() else "game files"
                            for p in others})
            shown = ", ".join(p.name for p in others[:3])
            found.append(f"{' and '.join(kinds)} to put into the game ({shown}"
                         f"{' ...' if len(others) > 3 else ''})")
    system = folder / "System"
    if system.is_dir() and any(p.is_file() for p in system.rglob("*")):
        found.append("files for system folders outside the game")
    return found


def load(folder: Path) -> TfcMod:
    folder = Path(folder)
    extra = unsupported_content(folder)
    if extra:
        raise TfcModError("it also carries " + "; ".join(extra) + ", which this cannot "
                          "install yet - use TFC Installer for this one")
    mod = TfcMod(folder)
    for patch in folder.rglob("*" + PATCH_SUFFIX):
        package = patch.name[:-len(PATCH_SUFFIX)]
        if package.lower() in mod.patches:
            raise TfcModError(f"two patches for {package} in one mod")
        mod.patches[package.lower()] = patch

    pack = folder / "TexturePack"
    mappings = sorted(pack.glob("*" + MAPPING_SUFFIX)) if pack.is_dir() else []
    if len(mappings) > 1:
        raise TfcModError("more than one texture mapping - only the main game's is "
                          "supported, not DLC ones")
    if mappings:
        mod.mapping_file = mappings[0]
        mod.mapping = TP.read_mapping(mappings[0])
        if mod.mapping.has_bulk_content:
            raise TfcModError("this texture pack uses bulk content, which is not supported")
        for entry in mod.mapping.entries:
            if entry.tfc_mips:
                cache = pack / f"{entry.tfc_name}_{entry.tfc_index}.tfc"
                if not cache.is_file():
                    raise TfcModError(f"the texture pack names {cache.name}, which is not "
                                      "in the folder")
                mod.caches[entry.tfc_index] = cache
            if entry.local_mips:
                local = pack / f"{entry.local_tfc_name}_{entry.local_tfc_index}.tfc"
                if not local.is_file():
                    raise TfcModError(f"the texture pack names {local.name}, which is not "
                                      "in the folder")
                mod.local_cache = local
    if not mod.patches and not mod.has_textures:
        raise TfcModError("no package patches and no texture pack - nothing to install")
    return mod


def packages_changed(mod: TfcMod, index: TextureIndex) -> list[str]:
    """Every package this mod changes, by file name as the game spells it."""
    wanted: dict[str, str] = {}
    for key, patch in mod.patches.items():
        wanted[key] = patch.name[:-len(PATCH_SUFFIX)]
    if mod.mapping is not None:
        for entry in mod.mapping.entries:
            for name in index.packages_holding(entry.texture_id):
                wanted.setdefault(name.lower(), name)
    return sorted(wanted.values(), key=str.lower)


def missing_textures(mod: TfcMod, index: TextureIndex) -> list[str]:
    """Textures the pack replaces that no package in the game holds."""
    if mod.mapping is None:
        return []
    return [e.texture_id for e in mod.mapping.entries
            if not index.packages_holding(e.texture_id)]


def cache_targets(mod: TfcMod, installed_as: dict[int, int]) -> list[tuple[Path, str]]:
    """(the pack's .tfc, where it goes) - under the numbers it was given."""
    return [(path, f"{COOKED}/Texture2D_{installed_as[i]}.tfc")
            for i, path in sorted(mod.caches.items())]


def transform(mod: TfcMod, package_name: str, stock: bytes,
              installed_as: dict[int, int]) -> bytes:
    """One package's new bytes, from its stock bytes.

    ``installed_as`` maps each of the pack's own cache numbers to the number it
    went into the game under - Texture2D_0.tfc in the pack might be
    Texture2D_3.tfc in the game, if three are there already.
    """
    package = P.read(stock)
    patch_file = mod.patches.get(package_name.lower())
    if patch_file is not None:
        try:
            flat, _ = A.apply(package, PP.read(patch_file))
        except (A.ApplyError, PP.PatchError) as problem:
            raise TfcModError(f"{package_name}: {problem}") from None
        package = P.read(flat)

    if mod.mapping is not None:
        local = mod.local_cache.read_bytes() if mod.local_cache else b""
        by_path = T2.textures_by_path(package)
        updates = []
        for entry in mod.mapping.entries:
            export = by_path.get(entry.texture_id.replace("/", "\\").lower())
            if export is None:
                continue
            installed = installed_as.get(entry.tfc_index, -1) if entry.tfc_mips else -1
            try:
                updates.append((export, T2.update(package, export, entry, installed, local)))
            except T2.TextureError as problem:
                raise TfcModError(f"{package_name}: {problem}") from None
        if updates:
            flat, _ = A.apply_textures(package, updates)
            package = P.read(flat)
    return package.data


def cache_numbers_present(cooked_dir: Path) -> set[int]:
    """Every N with a Texture2D_N.tfc in the game folder."""
    out = set()
    for existing in Path(cooked_dir).glob("Texture2D_*.tfc"):
        suffix = existing.stem[len("Texture2D_"):]
        if suffix.isdigit():
            out.add(int(suffix))
    return out


def allocate(count: int, taken: set[int]) -> list[int]:
    """The lowest ``count`` numbers not in ``taken``, which are then added to it."""
    out, n = [], 0
    while len(out) < count:
        if n not in taken:
            out.append(n)
            taken.add(n)
        n += 1
    return out
