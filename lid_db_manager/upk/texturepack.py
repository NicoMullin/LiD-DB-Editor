"""Reading a TFC Installer texture pack: the .TFCMapping and the caches beside it.

The format is FCH823's, from TFC Installer (https://www.nexusmods.com/site/mods/587),
ported with their permission.

A texture pack is a folder holding:

    Let it Die.TFCMapping   which textures change, and where their new mips are
    Texture2D_0.tfc         the big mips, compressed, which the game streams in
    LocalMips_0.tfc         the small mips, stored inside each package instead

The .tfc is copied into the game untouched, under the next free name. Nothing
in it is decoded here: each texture is simply pointed at the right place in it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .package import PackageError, Reader

CURRENT_VERSION = 3
LEGACY_VERSION = 1


class MappingError(ValueError):
    """The mapping file is not one this can read."""


@dataclass
class MipEntry:
    compression: int        # 2 = LZO for mips in the .tfc, 0 for local mips
    offset: int             # where the mip starts in its cache file
    size_on_disk: int       # its stored (compressed) size
    element_count: int      # its size once decompressed
    size_x: int
    size_y: int


@dataclass
class TextureEntry:
    """One texture the pack replaces, by its path inside the game."""

    texture_id: str         # e.g. wp_assaultrifle3102\Textures\TX_WP_AssaultRifle3102_D
    tfc_name: str = ""
    tfc_index: int = -1
    tfc_mips: list[MipEntry] = field(default_factory=list)
    local_tfc_name: str = ""
    local_tfc_index: int = -1
    local_mips: list[MipEntry] = field(default_factory=list)

    @property
    def object_name(self) -> str:
        """The texture's own name, the last part of its path."""
        return self.texture_id.replace("/", "\\").rsplit("\\", 1)[-1]

    @property
    def mip_count(self) -> int:
        return len(self.tfc_mips) + len(self.local_mips)

    @property
    def size(self) -> tuple[int, int]:
        """The full-size mip's dimensions."""
        first = (self.tfc_mips or self.local_mips)[0]
        return first.size_x, first.size_y


@dataclass
class Mapping:
    version: int
    has_bulk_content: bool
    entries: list[TextureEntry]


def _mips(r: Reader, version: int, count: int) -> list[MipEntry]:
    out = []
    for _ in range(count):
        compression = r.i32() if version != LEGACY_VERSION else 0
        offset = r.i32()
        if version == LEGACY_VERSION and compression == 0:
            element_count = r.i32()
            size_on_disk = element_count
        else:
            size_on_disk = r.i32()
            element_count = r.i32()
        out.append(MipEntry(compression, offset, size_on_disk, element_count,
                            r.u32(), r.u32()))
    return out


def read_mapping(path_or_bytes) -> Mapping:
    raw = (Path(path_or_bytes).read_bytes()
           if not isinstance(path_or_bytes, (bytes, bytearray)) else bytes(path_or_bytes))
    r = Reader(raw)
    try:
        first = r.i32()
        version = r.i32() if first == -1 else LEGACY_VERSION
        if first != -1:
            r.at = 0
        if version > CURRENT_VERSION:
            raise MappingError(f"texture mapping version {version} is newer than this "
                               f"reads ({CURRENT_VERSION})")
        has_bulk = bool(r.raw(1)[0]) if version >= 3 else False
        entries = []
        for _ in range(r.i32()):
            entry = TextureEntry(r.fstring())
            count = r.i32()
            if count > 0:
                entry.tfc_name = r.fstring()
                entry.tfc_index = r.i32()
                entry.tfc_mips = _mips(r, version, count)
            count = r.i32()
            if count > 0:
                entry.local_tfc_name = r.fstring()
                entry.local_tfc_index = r.i32()
                entry.local_mips = _mips(r, version, count)
            if entry.mip_count == 0:
                raise MappingError(f"{entry.texture_id} has no mips at all")
            entries.append(entry)
    except (PackageError, IndexError) as problem:
        raise MappingError(f"the texture mapping ends early: {problem}") from None
    if r.at != len(raw):
        raise MappingError(f"{len(raw) - r.at:,} bytes are left over after the texture "
                           "mapping - it is not the format this expects")
    return Mapping(version, has_bulk, entries)
