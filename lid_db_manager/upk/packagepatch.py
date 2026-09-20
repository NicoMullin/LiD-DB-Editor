"""Reading a .PackagePatch - the format TFC Installer mods are distributed in.

The format is FCH823's, from TFC Installer (https://www.nexusmods.com/site/mods/587),
and this reader is a port of theirs, used with their permission. Anyone wanting
the full tool, for any of the forty-odd games it supports, should get it there.

A patch does not hold a finished package. It holds the changes to one:

    version
    names     the table's original length, then each changed or added entry
    imports   the same
    exports   the same
    objects   for each export being replaced: its index, the places inside its
              new data that hold file offsets, and the new data itself
    (version 2 and later)
    references  what the patch expects to find at the names and objects it
                relies on, by index - checked before anything is applied, so a
                patch that no longer fits the package is refused, not half-done
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .package import (NameEntry, ObjectExport, ObjectImport, PackageError, Reader,
                      read_export_entry, read_import_entry, read_name_entry)

LATEST_VERSION = 2


class PatchError(ValueError):
    """The patch file is not one this can read."""


@dataclass
class TableEntryUpdate:
    index: int          # 0-based position in the table
    value: object       # NameEntry, ObjectImport or ObjectExport
    is_new: bool        # appended past the table's original end, not replaced


@dataclass
class TableUpdate:
    original_count: int
    entries: list[TableEntryUpdate] = field(default_factory=list)


@dataclass
class OffsetRecord:
    """A place inside an object's data that holds a file offset.

    Objects that keep bulk data - texture mips, mostly - record where in the
    package that data sits, as an absolute offset. Move the object and those
    numbers are wrong, so each one is listed here to be fixed up. LET IT DIE's
    bulk data offsets are 64-bit.
    """

    position: int       # within the object's own data
    is_64_bit: bool


@dataclass
class ObjectUpdate:
    export_index: int
    offset_records: list[OffsetRecord]
    data: bytes


@dataclass
class NameReference:
    index: int
    text: str


@dataclass
class ObjectReference:
    index: int          # <0 import, >0 export - 1-based, as the engine counts
    object_id: str
    full_path: str
    class_name: str
    class_package: str = ""

    @property
    def is_import(self) -> bool:
        return self.index < 0


@dataclass
class PackagePatch:
    version: int
    names: TableUpdate
    imports: TableUpdate
    exports: TableUpdate
    objects: list[ObjectUpdate]
    reference_version: int = 0
    name_references: list[NameReference] = field(default_factory=list)
    object_references: list[ObjectReference] = field(default_factory=list)
    source: Path | None = None


def _table(r: Reader, read_entry) -> TableUpdate:
    table = TableUpdate(r.i32())
    for _ in range(r.i32()):
        index = r.i32()
        value = read_entry(r)
        is_new = r.raw(1)[0] != 0
        table.entries.append(TableEntryUpdate(index, value, is_new))
    return table


def _object_reference(r: Reader, version: int) -> ObjectReference:
    index = r.i32()
    first = r.fstring()
    is_import = index < 0
    full_path = r.fstring() if is_import and version >= 12 else first
    class_name = r.fstring()
    class_package = r.fstring() if is_import and version >= 11 else ""
    return ObjectReference(index, first, full_path, class_name, class_package)


def read(path_or_bytes) -> PackagePatch:
    raw = (Path(path_or_bytes).read_bytes()
           if not isinstance(path_or_bytes, (bytes, bytearray)) else bytes(path_or_bytes))
    r = Reader(raw)
    try:
        version = r.i32()
        if not 1 <= version <= LATEST_VERSION:
            raise PatchError(f"patch format version {version} is not one this reads "
                             f"(1 to {LATEST_VERSION})")
        names = _table(r, read_name_entry)
        imports = _table(r, read_import_entry)
        exports = _table(r, read_export_entry)
        objects = []
        for _ in range(r.i32()):
            export_index = r.i32()
            records = []
            for _ in range(r.i32()):
                position = r.i64()
                records.append(OffsetRecord(position, r.raw(1)[0] != 0))
            data = r.raw(r.i32())
            objects.append(ObjectUpdate(export_index, records, data))
        patch = PackagePatch(version, names, imports, exports, objects)
        if version >= 2:
            patch.reference_version = r.i32()
            patch.name_references = [NameReference(r.i32(), r.fstring())
                                     for _ in range(r.i32())]
            patch.object_references = [_object_reference(r, patch.reference_version)
                                       for _ in range(r.i32())]
    except (PackageError, IndexError) as problem:
        raise PatchError(f"the patch ends early or is malformed: {problem}") from None
    except Exception as problem:     # struct.error on a truncated read
        raise PatchError(f"the patch could not be read: {problem}") from None
    if r.at != len(raw):
        raise PatchError(f"{len(raw) - r.at:,} bytes are left over after the patch - "
                         "it is not the format this expects")
    if not isinstance(path_or_bytes, (bytes, bytearray)):
        patch.source = Path(path_or_bytes)
    return patch
