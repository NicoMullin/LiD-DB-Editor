"""Applying a .PackagePatch to a package, and writing the result.

The logic is FCH823's, from TFC Installer (https://www.nexusmods.com/site/mods/587),
ported with their permission. Two rules carry the whole thing:

- **Where an object goes.** A replacement no bigger than what it replaces is
  written over it, in place. A bigger one is appended to the end of the
  package, in the order the patch lists them, and the space it left is cleared.
  That is exactly what TFC Installer does, so a package patched here matches
  one patched there, byte for byte, once both are decompressed.

- **Offsets inside objects.** Objects with bulk data - mesh buffers, texture
  mips, audio - record where that data sits in the package as an absolute
  offset. A patch stores each of those relative to the object's own start, and
  lists where they are; applying it adds back wherever the object ends up.

Before anything is written, the patch's own checks run: it names what it
expects to find at the names and objects it relies on, and a package where any
of those differ is refused. That is what keeps a patch made for one version of
a package from being forced onto another.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from .package import Package
from .packagepatch import PackagePatch


class ApplyError(ValueError):
    """The patch does not fit this package. Nothing was changed."""


@dataclass
class Placement:
    export_index: int
    offset: int
    size: int
    moved: bool


def _path(package: Package, reference: int) -> str:
    """A reference's full path the way the patch format writes it."""
    return package.full_path(reference).replace(".", "\\")


def problems(package: Package, patch: PackagePatch) -> list[str]:
    """Why this patch does not fit this package - an empty list if it does."""
    found: list[str] = []
    names = package.names
    for ref in patch.name_references:
        if ref.index < len(names):
            if names[ref.index].text != ref.text:
                found.append(f"name #{ref.index} is {names[ref.index].text!r}, "
                             f"the patch expects {ref.text!r}")
        elif not any(e.index == ref.index and e.is_new for e in patch.names.entries):
            found.append(f"name #{ref.index} ({ref.text!r}) is not in the package")

    for ref in patch.object_references:
        if ref.index == 0:
            continue
        if ref.index < 0:
            slot = -ref.index - 1
            if slot < len(package.imports):
                actual = _path(package, ref.index)
                if actual.lower() != ref.full_path.lower():
                    found.append(f"import #{slot} is {actual!r}, the patch expects "
                                 f"{ref.full_path!r}")
            elif not any(e.index == slot and e.is_new for e in patch.imports.entries):
                found.append(f"import #{slot} ({ref.full_path!r}) is not in the package")
        else:
            slot = ref.index - 1
            if slot < len(package.exports):
                actual = _path(package, ref.index)
                if actual.lower() != ref.full_path.lower():
                    found.append(f"export #{slot} is {actual!r}, the patch expects "
                                 f"{ref.full_path!r}")
            elif not any(e.index == slot and e.is_new for e in patch.exports.entries):
                found.append(f"export #{slot} ({ref.full_path!r}) is not in the package")
    return found


def unsupported(patch: PackagePatch) -> str:
    """Why this patch cannot be applied here yet, or ""."""
    changed = [label for label, table in (("names", patch.names),
                                          ("imports", patch.imports),
                                          ("exports", patch.exports)) if table.entries]
    if changed:
        return ("it adds to or changes the package's own tables ("
                + ", ".join(changed) + "), which this does not support yet - "
                "install it with TFC Installer instead")
    return ""


def apply(package: Package, patch: PackagePatch) -> tuple[bytes, list[Placement]]:
    """The package's decompressed bytes with the patch applied.

    Raises ApplyError, having changed nothing, if the patch does not fit.
    """
    reason = unsupported(patch)
    if reason:
        raise ApplyError(reason)
    for label, have, expect in (("names", len(package.names), patch.names.original_count),
                                ("imports", len(package.imports), patch.imports.original_count),
                                ("exports", len(package.exports), patch.exports.original_count)):
        if have < expect:
            raise ApplyError(f"the package has {have} {label} but the patch was made "
                             f"against one with {expect}")
    found = problems(package, patch)
    if found:
        more = f" (and {len(found) - 5} more)" if len(found) > 5 else ""
        raise ApplyError("the package is not the one this patch was made for: "
                         + "; ".join(found[:5]) + more)

    data = bytearray(package.data)
    placements: list[Placement] = []
    for update in patch.objects:
        if not 0 <= update.export_index < len(package.exports):
            raise ApplyError(f"the patch replaces export #{update.export_index}, "
                             "which the package does not have")
        entry = package.exports[update.export_index]
        new = bytearray(update.data)
        if len(new) <= entry.serial_size:
            start, moved = entry.serial_offset, False
        else:
            start, moved = len(data), True

        for record in update.offset_records:
            fmt = "<q" if record.is_64_bit else "<i"
            width = 8 if record.is_64_bit else 4
            if not 0 <= record.position <= len(new) - width:
                raise ApplyError(f"an offset in export #{update.export_index} points "
                                 "outside its own data")
            stored = struct.unpack_from(fmt, new, record.position)[0]
            struct.pack_into(fmt, new, record.position, stored + start)

        old_start, old_size = entry.serial_offset, entry.serial_size
        if moved:
            data[old_start:old_start + old_size] = bytes(old_size)
            data += new
        else:
            data[start:start + old_size] = bytes(new) + bytes(old_size - len(new))

        _place_entry(data, entry, start, len(new))
        placements.append(Placement(update.export_index, start, len(new), moved))
    return bytes(data), placements


def _place_entry(data: bytearray, entry, start: int, size: int) -> None:
    """The export table entry is fixed-size, so size and offset change in place."""
    struct.pack_into("<i", data, entry.serial_offset_at - 4, size)
    struct.pack_into("<i", data, entry.serial_offset_at, start)
    entry.serial_size, entry.serial_offset = size, start


def apply_textures(package: Package, textures) -> tuple[bytes, list[Placement]]:
    """The package with each (export index, Texture) written in.

    Placed by the same rule as a patch's objects. A texture's bytes depend on
    where it lands - its bulk data records absolute offsets - so its size is
    worked out first, the place chosen, and only then is it built.
    """
    data = bytearray(package.data)
    placements: list[Placement] = []
    for export_index, texture in textures:
        entry = package.exports[export_index]
        size = len(texture.build(0))
        if size <= entry.serial_size:
            start, moved = entry.serial_offset, False
        else:
            start, moved = len(data), True
        new = texture.build(start)
        old_start, old_size = entry.serial_offset, entry.serial_size
        if moved:
            data[old_start:old_start + old_size] = bytes(old_size)
            data += new
        else:
            data[start:start + old_size] = new + bytes(old_size - len(new))
        _place_entry(data, entry, start, len(new))
        placements.append(Placement(export_index, start, len(new), moved))
    return bytes(data), placements


