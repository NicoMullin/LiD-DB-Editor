"""Pointing a texture in a package at a texture pack's mips.

The logic is FCH823's, from TFC Installer (https://www.nexusmods.com/site/mods/587),
ported with their permission, for LET IT DIE only.

A Texture2D export is:

    NetIndex
    property tags, ending in None        size, format, which cache it streams from
    source art                           a bulk data block, empty in cooked games
    mip count, then per mip:             a bulk data block and its width and height
    trailing data                        kept exactly as it is

A mip's bulk data either lives in a .tfc (the big ones, streamed in) or inside
the package straight after its own header (the small ones). Updating a texture
rewrites the mip list from the pack's mapping: big mips point into the pack's
.tfc, small ones carry the pack's pixels inline, and the properties that
describe the texture are brought into line.

Every bulk data block records an absolute file offset - even an empty one, and
even for data that sits right beside it - so the bytes of a texture depend on
where in the package it ends up. ``build`` takes that position as an argument.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from .package import Package
from .texturepack import MipEntry, TextureEntry

BULK_SEPARATE_FILE = 0x01
BULK_COMPRESSED_ZLIB = 0x02
BULK_COMPRESSED_LZO = 0x10

#: The name TFC Installer gives the caches it installs: Texture2D_0.tfc and on.
TFC_BASE_NAME = "Texture2D"


class TextureError(ValueError):
    """This texture cannot be updated here. Nothing was changed."""


# ---------------------------------------------------------------------------
# property tags
# ---------------------------------------------------------------------------

@dataclass
class Tag:
    """One property tag, kept as its parts so it can be changed and rewritten."""

    name: str
    name_ref: tuple[int, int]
    type: str
    type_ref: tuple[int, int]
    array_index: int
    extra: bytes        # struct or enum name, or a bool's single byte
    value: bytes

    def serialize(self) -> bytes:
        size = 0 if self.type == "BoolProperty" else len(self.value)
        return (struct.pack("<iiiiii", *self.name_ref, *self.type_ref, size, self.array_index)
                + self.extra + (b"" if self.type == "BoolProperty" else self.value))


def read_tags(package: Package, data: bytes, at: int) -> tuple[list[Tag], int, bytes]:
    """The property tags from ``at``: (tags, where the terminator ends, the terminator)."""
    tags = []
    while True:
        name_ref = struct.unpack_from("<ii", data, at)
        name = package.name(*name_ref)
        if name == "None":
            return tags, at + 8, data[at:at + 8]
        type_ref = struct.unpack_from("<ii", data, at + 8)
        kind = package.name(*type_ref)
        size, array_index = struct.unpack_from("<ii", data, at + 16)
        at += 24
        if kind in ("StructProperty", "ByteProperty"):
            extra, at = data[at:at + 8], at + 8
        elif kind == "BoolProperty":
            extra, at = data[at:at + 1], at + 1
            size = 0
        else:
            extra = b""
        value, at = data[at:at + size], at + size
        tags.append(Tag(name, name_ref, kind, type_ref, array_index, extra, value))


def _find(tags: list[Tag], name: str) -> Tag | None:
    return next((t for t in tags if t.name == name and t.array_index == 0), None)


def _name_index(package: Package, text: str) -> int | None:
    for i, entry in enumerate(package.names):
        if entry.text == text:
            return i
    return None


def _set_int(package: Package, tags: list[Tag], name: str, value: int, create: bool) -> None:
    tag = _find(tags, name)
    if tag is not None:
        tag.value = struct.pack("<i", value)
        return
    if not create:
        return
    name_i, type_i = _name_index(package, name), _name_index(package, "IntProperty")
    if name_i is None or type_i is None:
        raise TextureError(f"this texture has no {name} property, and adding one would "
                           "need a new entry in the package's name table")
    tags.append(Tag(name, (name_i, 0), "IntProperty", (type_i, 0), 0, b"",
                    struct.pack("<i", value)))


def _set_name(package: Package, tags: list[Tag], name: str, text: str, number: int) -> None:
    value_i = _name_index(package, text)
    tag = _find(tags, name)
    if value_i is None:
        raise TextureError(f"setting {name} to {text}_{number - 1} would need a new entry "
                           "in the package's name table")
    value = struct.pack("<ii", value_i, number)
    if tag is not None:
        tag.value = value
        return
    name_i, type_i = _name_index(package, name), _name_index(package, "NameProperty")
    if name_i is None or type_i is None:
        raise TextureError(f"this texture has no {name} property, and adding one would "
                           "need a new entry in the package's name table")
    tags.append(Tag(name, (name_i, 0), "NameProperty", (type_i, 0), 0, b"", value))


def _remove(tags: list[Tag], name: str) -> None:
    tags[:] = [t for t in tags if t.name != name]


# ---------------------------------------------------------------------------
# the native part
# ---------------------------------------------------------------------------

@dataclass
class Bulk:
    flags: int
    element_count: int
    size_on_disk: int
    offset: int
    inline: bytes = b""     # the data itself, when it lives in the package

    @property
    def is_local(self) -> bool:
        return not self.flags & BULK_SEPARATE_FILE

    def header(self, offset: int) -> bytes:
        # LET IT DIE's one difference from stock UE3: 64-bit size and offset.
        return struct.pack("<Iiqq", self.flags, self.element_count, self.size_on_disk, offset)


@dataclass
class Mip:
    data: Bulk
    size_x: int
    size_y: int


@dataclass
class Texture:
    net_index: bytes
    tags: list[Tag]
    terminator: bytes
    source_art: Bulk
    mips: list[Mip]
    trailing: bytes

    def build(self, start: int) -> bytes:
        """The export's bytes, for an export that begins at ``start`` in the file."""
        out = bytearray(self.net_index)
        for tag in self.tags:
            out += tag.serialize()
        out += self.terminator

        def block(bulk: Bulk) -> None:
            nonlocal out
            here = start + len(out) + 24          # where inline data would begin
            offset = here if bulk.is_local else bulk.offset
            out += bulk.header(offset)
            if bulk.is_local:
                out += bulk.inline

        block(self.source_art)
        out += struct.pack("<i", len(self.mips))
        for mip in self.mips:
            block(mip.data)
            out += struct.pack("<ii", mip.size_x, mip.size_y)
        out += self.trailing
        return bytes(out)


def _read_bulk(data: bytes, at: int) -> tuple[Bulk, int]:
    flags, count, size, offset = struct.unpack_from("<Iiqq", data, at)
    at += 24
    bulk = Bulk(flags, count, size, offset)
    if bulk.is_local and size > 0:
        bulk.inline = data[at:at + size]
        at += size
    return bulk, at


def read(package: Package, export_index: int) -> Texture:
    data = package.export_data(export_index)
    tags, at, terminator = read_tags(package, data, 4)
    source_art, at = _read_bulk(data, at)
    (count,) = struct.unpack_from("<i", data, at)
    at += 4
    mips = []
    for _ in range(count):
        bulk, at = _read_bulk(data, at)
        size_x, size_y = struct.unpack_from("<ii", data, at)
        at += 8
        mips.append(Mip(bulk, size_x, size_y))
    return Texture(data[:4], tags, terminator, source_art, mips, data[at:])


# ---------------------------------------------------------------------------
# applying a texture pack entry
# ---------------------------------------------------------------------------

def _tfc_flags(mip: MipEntry) -> int:
    flags = BULK_SEPARATE_FILE
    if mip.compression == 2:
        flags |= BULK_COMPRESSED_LZO
    elif mip.compression == 1:
        flags |= BULK_COMPRESSED_ZLIB
    return flags


def update(package: Package, export_index: int, entry: TextureEntry,
           tfc_index: int, local_cache: bytes) -> Texture:
    """The texture with the pack's mips in place of its own.

    ``tfc_index`` is the number the pack's .tfc was installed under - the N of
    Texture2D_N.tfc - and ``local_cache`` the bytes of LocalMips_0.tfc.
    """
    texture = read(package, export_index)
    size_x, size_y = entry.size

    mips: list[Mip] = []
    for mip in entry.tfc_mips:
        mips.append(Mip(Bulk(_tfc_flags(mip), mip.element_count, mip.size_on_disk,
                             mip.offset), mip.size_x, mip.size_y))
    for mip in entry.local_mips:
        if mip.compression != 0:
            raise TextureError(f"{entry.texture_id} has a compressed local mip, which "
                               "this does not support")
        pixels = local_cache[mip.offset:mip.offset + mip.size_on_disk]
        if len(pixels) != mip.size_on_disk:
            raise TextureError(f"{entry.texture_id} points past the end of its local "
                               "mip cache")
        mips.append(Mip(Bulk(0, mip.element_count, mip.size_on_disk, 0, pixels),
                        mip.size_x, mip.size_y))
    texture.mips = mips

    tags = texture.tags
    if entry.mip_count == 1:
        # A single mip is a UI image: it never streams, so it forgets its cache.
        _set_int(package, tags, "OriginalSizeX", size_x, create=False)
        _set_int(package, tags, "OriginalSizeY", size_y, create=False)
        _remove(tags, "TextureFileCacheName")
        _remove(tags, "FirstResourceMemMip")
        _remove(tags, "MipTailBaseIdx")
        return texture

    _set_int(package, tags, "SizeX", size_x, create=False)
    _set_int(package, tags, "SizeY", size_y, create=False)
    _set_int(package, tags, "OriginalSizeX", size_x, create=False)
    _set_int(package, tags, "OriginalSizeY", size_y, create=False)
    if entry.tfc_mips:
        _set_name(package, tags, "TextureFileCacheName", TFC_BASE_NAME, tfc_index + 1)
    else:
        _remove(tags, "TextureFileCacheName")

    first_local = next((i for i, m in enumerate(mips)
                        if m.data.is_local and m.data.size_on_disk > 0), 0)
    last_used = next((i for i in range(len(mips) - 1, -1, -1)
                      if mips[i].data.size_on_disk > 0), len(mips) - 1)
    if _find(tags, "FirstResourceMemMip") is not None:
        _set_int(package, tags, "FirstResourceMemMip", first_local, create=False)
    if _find(tags, "MipTailBaseIdx") is not None:
        _set_int(package, tags, "MipTailBaseIdx", last_used, create=False)
    return texture


def textures_by_path(package: Package) -> dict[str, int]:
    """Every Texture2D export in the package, by its path, lower case."""
    out = {}
    for i, export in enumerate(package.exports):
        if package.object_name(export.class_index) == "Texture2D":
            out[package.full_path(i + 1).replace(".", "\\").lower()] = i
    return out
