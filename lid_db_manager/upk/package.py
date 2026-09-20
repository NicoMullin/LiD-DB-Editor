"""Reading a LET IT DIE package: the header, the compressed blocks, the tables.

Credit where it is due: this follows the reading of Unreal Engine 3 packages in
FCH823's TFC Installer (https://www.nexusmods.com/site/mods/587), used here
with their permission, cut down to the one game this manager is for. Their tool
supports forty-odd games; LET IT DIE is package version 861, licensee 19, which
is the engine's standard layout for that version with one difference of its own
- see bulk data in objects.py.

A package on disk is a short uncompressed header followed by compressed chunks.
``Package.read`` gives back the whole thing decompressed, as one flat buffer in
which every offset the header records points where it says. Patching works on
that buffer, and what is written back is written uncompressed.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

from . import lzo

TAG = 0x9E2A83C1
FILE_VERSION = 861
LICENSEE_VERSION = 19

# PackageFlags bit meaning "the body is stored compressed".
PKG_STORE_COMPRESSED = 0x02000000

COMPRESS_NONE = 0
COMPRESS_ZLIB = 1
COMPRESS_LZO = 2


class PackageError(ValueError):
    """The file is not a LET IT DIE package this can read."""


class Reader:
    """Little-endian reads with a cursor, the way the engine writes them."""

    def __init__(self, data: bytes, at: int = 0):
        self.data = data
        self.at = at

    def i32(self) -> int:
        value = struct.unpack_from("<i", self.data, self.at)[0]
        self.at += 4
        return value

    def u32(self) -> int:
        value = struct.unpack_from("<I", self.data, self.at)[0]
        self.at += 4
        return value

    def i64(self) -> int:
        value = struct.unpack_from("<q", self.data, self.at)[0]
        self.at += 8
        return value

    def u16(self) -> int:
        value = struct.unpack_from("<H", self.data, self.at)[0]
        self.at += 2
        return value

    def raw(self, count: int) -> bytes:
        if count < 0 or self.at + count > len(self.data):
            raise PackageError("a read runs past the end of the data")
        value = self.data[self.at:self.at + count]
        self.at += count
        return value

    def fstring(self) -> str:
        """An engine string: a length, then the text with its terminator.

        A negative length means UTF-16, which LET IT DIE's names never use but
        the format allows, so it is read rather than refused.
        """
        length = self.i32()
        if length == 0:
            return ""
        if length > 0:
            text = self.raw(length)
            return text[:-1].decode("latin-1")
        text = self.raw(-length * 2)
        return text[:-2].decode("utf-16-le")


@dataclass
class Generation:
    export_count: int
    name_count: int
    net_object_count: int


@dataclass
class CompressedChunk:
    """One run of the body: where it sits compressed, and where it belongs."""

    uncompressed_offset: int
    uncompressed_size: int
    compressed_offset: int
    compressed_size: int


@dataclass
class Summary:
    """The package header, in the order the engine writes it for version 861."""

    file_version: int
    licensee_version: int
    total_header_size: int
    folder_name: str
    package_flags: int
    name_count: int
    name_offset: int
    export_count: int
    export_offset: int
    import_count: int
    import_offset: int
    depends_offset: int
    import_export_guids_offset: int
    import_guids_count: int
    export_guids_count: int
    thumbnail_table_offset: int
    guid: bytes
    generations: list[Generation]
    engine_version: int
    cooker_version: int
    compression_flags: int
    chunks: list[CompressedChunk]
    # Where each of these sits in the header, so they can be rewritten in place.
    offsets_at: dict[str, int] = field(default_factory=dict)
    chunk_table_at: int = 0
    end: int = 0             # first byte after the chunk table

    @property
    def compressed(self) -> bool:
        return bool(self.chunks)


def read_summary(data: bytes) -> Summary:
    r = Reader(data)
    if r.u32() != TAG:
        raise PackageError("not an Unreal package - the first four bytes are wrong")
    file_version = r.u16()
    licensee_version = r.u16()
    if (file_version, licensee_version) != (FILE_VERSION, LICENSEE_VERSION):
        raise PackageError(
            f"package version {file_version}/{licensee_version} is not LET IT DIE's "
            f"{FILE_VERSION}/{LICENSEE_VERSION}")
    total_header_size = r.i32()
    folder_name = r.fstring()
    package_flags = r.u32()

    offsets_at: dict[str, int] = {}

    def offset(name: str) -> int:
        offsets_at[name] = r.at
        return r.i32()

    name_count = r.i32()
    name_offset = offset("name_offset")
    export_count = r.i32()
    export_offset = offset("export_offset")
    import_count = r.i32()
    import_offset = offset("import_offset")
    depends_offset = offset("depends_offset")
    import_export_guids_offset = offset("import_export_guids_offset")
    import_guids_count = r.i32()
    export_guids_count = r.i32()
    thumbnail_table_offset = offset("thumbnail_table_offset")
    guid = r.raw(16)
    generations = [Generation(r.i32(), r.i32(), r.i32()) for _ in range(r.i32())]
    engine_version = r.i32()
    cooker_version = r.i32()
    compression_flags = r.u32()
    chunk_table_at = r.at
    chunks = [CompressedChunk(r.i32(), r.i32(), r.i32(), r.i32()) for _ in range(r.i32())]

    return Summary(
        file_version=file_version, licensee_version=licensee_version,
        total_header_size=total_header_size, folder_name=folder_name,
        package_flags=package_flags,
        name_count=name_count, name_offset=name_offset,
        export_count=export_count, export_offset=export_offset,
        import_count=import_count, import_offset=import_offset,
        depends_offset=depends_offset,
        import_export_guids_offset=import_export_guids_offset,
        import_guids_count=import_guids_count, export_guids_count=export_guids_count,
        thumbnail_table_offset=thumbnail_table_offset, guid=guid,
        generations=generations, engine_version=engine_version,
        cooker_version=cooker_version, compression_flags=compression_flags,
        chunks=chunks, offsets_at=offsets_at, chunk_table_at=chunk_table_at,
        end=r.at,
    )


def decompress_chunk(data: bytes, chunk: CompressedChunk, method: int) -> bytes:
    """One chunk of the body, decoded.

    Each chunk is its own little header - the tag, a block size, the totals -
    then a size pair per block, then the blocks.
    """
    r = Reader(data, chunk.compressed_offset)
    if r.u32() != TAG:
        raise PackageError(f"a compressed chunk at {chunk.compressed_offset} has no tag")
    block_size = r.u32()
    total_compressed = r.u32()
    total_uncompressed = r.u32()
    if total_uncompressed != chunk.uncompressed_size:
        raise PackageError("a chunk's own header disagrees with the package about its size")
    count = (total_uncompressed + block_size - 1) // block_size
    sizes = [(r.u32(), r.u32()) for _ in range(count)]
    out = bytearray()
    for compressed_size, uncompressed_size in sizes:
        block = r.raw(compressed_size)
        if method == COMPRESS_LZO:
            out += lzo.decompress(block, uncompressed_size)
        elif method == COMPRESS_ZLIB:
            import zlib
            out += zlib.decompress(block)
        else:
            raise PackageError(f"compression method {method} is not one LET IT DIE uses")
    if len(out) != chunk.uncompressed_size:
        raise PackageError("a chunk decoded to the wrong size")
    del total_compressed
    return bytes(out)


# ---------------------------------------------------------------------------
# the three tables
# ---------------------------------------------------------------------------

@dataclass
class NameEntry:
    text: str
    flags: int          # 64-bit object flags, kept as they are


@dataclass
class ObjectImport:
    class_package: int  # name index
    class_package_number: int
    class_name: int
    class_name_number: int
    outer: int          # object reference: <0 import, >0 export, 0 none
    object_name: int
    object_name_number: int


@dataclass
class ObjectExport:
    class_index: int
    super_index: int
    outer: int
    object_name: int
    object_name_number: int
    archetype: int
    object_flags: int           # 64-bit
    serial_size: int
    serial_offset: int
    export_flags: int
    net_objects: list[int]
    package_guid: bytes
    package_flags: int
    at: int = 0                  # where this entry sits in the table
    serial_offset_at: int = 0    # where its serial offset sits, for rewriting


# One entry of each table. The patch format stores entries exactly as the
# package does, so both read through these.

def read_name_entry(r: Reader) -> NameEntry:
    text = r.fstring()
    flags = struct.unpack_from("<Q", r.raw(8))[0]
    return NameEntry(text, flags)


def read_import_entry(r: Reader) -> ObjectImport:
    return ObjectImport(r.i32(), r.i32(), r.i32(), r.i32(), r.i32(), r.i32(), r.i32())


def read_export_entry(r: Reader) -> ObjectExport:
    at = r.at
    class_index, super_index, outer = r.i32(), r.i32(), r.i32()
    object_name, object_name_number = r.i32(), r.i32()
    archetype = r.i32()
    object_flags = struct.unpack_from("<Q", r.raw(8))[0]
    serial_size = r.i32()
    serial_offset_at = r.at
    serial_offset = r.i32()
    export_flags = r.u32()
    net_objects = [r.i32() for _ in range(r.i32())]
    package_guid = r.raw(16)
    package_flags = r.u32()
    return ObjectExport(class_index, super_index, outer, object_name,
                        object_name_number, archetype, object_flags,
                        serial_size, serial_offset, export_flags,
                        net_objects, package_guid, package_flags,
                        at=at, serial_offset_at=serial_offset_at)


def read_names(data: bytes, summary: Summary) -> list[NameEntry]:
    r = Reader(data, summary.name_offset)
    return [read_name_entry(r) for _ in range(summary.name_count)]


def read_imports(data: bytes, summary: Summary) -> list[ObjectImport]:
    r = Reader(data, summary.import_offset)
    return [read_import_entry(r) for _ in range(summary.import_count)]


def read_exports(data: bytes, summary: Summary) -> list[ObjectExport]:
    r = Reader(data, summary.export_offset)
    return [read_export_entry(r) for _ in range(summary.export_count)]


# ---------------------------------------------------------------------------
# the whole package
# ---------------------------------------------------------------------------

@dataclass
class Package:
    summary: Summary
    data: bytes                       # the whole package, decompressed
    names: list[NameEntry]
    imports: list[ObjectImport]
    exports: list[ObjectExport]
    source: Path | None = None

    def name(self, index: int, number: int = 0) -> str:
        """A name as the engine prints it: ``Name`` or ``Name_N-1`` with a number."""
        text = self.names[index].text
        return f"{text}_{number - 1}" if number > 0 else text

    def object_name(self, reference: int) -> str:
        """The name of an object reference: <0 import, >0 export, 0 nothing."""
        if reference < 0:
            entry = self.imports[-reference - 1]
            return self.name(entry.object_name, entry.object_name_number)
        if reference > 0:
            entry = self.exports[reference - 1]
            return self.name(entry.object_name, entry.object_name_number)
        return ""

    def full_path(self, reference: int) -> str:
        """Outer.Outer.Name, as the patch format records references."""
        parts = []
        seen = set()
        while reference and reference not in seen:
            seen.add(reference)
            parts.append(self.object_name(reference))
            reference = (self.imports[-reference - 1].outer if reference < 0
                         else self.exports[reference - 1].outer)
        return ".".join(reversed(parts))

    def export_data(self, index: int) -> bytes:
        """The serialized bytes of one export, 0-based."""
        entry = self.exports[index]
        return self.data[entry.serial_offset:entry.serial_offset + entry.serial_size]


def uncompressed_header(raw: bytes, summary: Summary) -> bytes:
    """The header as it reads for the same package stored uncompressed.

    On disk a compressed package's header carries a table of its chunks, 16
    bytes each, and a flag saying the body is compressed. The body's offsets
    are all measured as if neither were there: the name table of a package
    with one chunk starts 16 bytes before the chunk data does. So the header
    that belongs in front of the decoded body is the one with the chunk table
    removed, the compression cleared, and everything after it moved up - and
    that comes out exactly as long as the body expects.
    """
    body_start = min(c.compressed_offset for c in summary.chunks)
    flags_at = summary.offsets_at["name_offset"] - 8
    header = bytearray(raw[:summary.chunk_table_at])
    flags = struct.unpack_from("<I", header, flags_at)[0]
    struct.pack_into("<I", header, flags_at, flags & ~PKG_STORE_COMPRESSED)
    struct.pack_into("<I", header, summary.chunk_table_at - 4, COMPRESS_NONE)
    header += struct.pack("<i", 0)
    header += raw[summary.end:body_start]
    if len(header) != summary.name_offset:
        raise PackageError(
            f"the header comes out {len(header)} bytes long without its chunk table, "
            f"but the name table starts at {summary.name_offset}")
    return bytes(header)


def decompressed(raw: bytes) -> tuple[Summary, bytes]:
    """The package as one flat buffer that is itself a valid uncompressed package.

    The body chunks are decoded into place behind a header rewritten to match
    (see ``uncompressed_header``), so what comes back can be written straight
    to disk. The returned Summary still describes the file as it was read.
    """
    summary = read_summary(raw)
    if not summary.chunks:
        return summary, raw
    method = summary.compression_flags or COMPRESS_LZO
    first = min(c.uncompressed_offset for c in summary.chunks)
    if first != summary.name_offset:
        raise PackageError("the body does not start where the name table does")
    size = max(c.uncompressed_offset + c.uncompressed_size for c in summary.chunks)
    flat = bytearray(size)
    flat[:first] = uncompressed_header(raw, summary)
    for chunk in summary.chunks:
        flat[chunk.uncompressed_offset:chunk.uncompressed_offset + chunk.uncompressed_size] = \
            decompress_chunk(raw, chunk, method)
    return summary, bytes(flat)


def read_tables(path) -> Package:
    """Just the name, import and export tables, reading as little as possible.

    The tables sit at the front of the body, before the depends table, so only
    the chunks up to there are read and decoded. Finding which of the game's
    thousands of packages hold a given texture needs only this, and it is a
    small fraction of the work of decompressing everything. ``data`` holds the
    decoded front of the package only; ``export_data`` does not work on it.
    """
    path = Path(path)
    with open(path, "rb") as handle:
        head = handle.read(65536)
        summary = read_summary(head) if len(head) >= 64 else read_summary(handle.read())
        if summary.end > len(head):
            handle.seek(0)
            head = handle.read(summary.end + 65536)
            summary = read_summary(head)
        if not summary.chunks:
            handle.seek(0)
            raw = handle.read(summary.depends_offset)
            return Package(summary, raw, read_names(raw, summary), read_imports(raw, summary),
                           read_exports(raw, summary), source=path)
        needed = [c for c in summary.chunks if c.uncompressed_offset < summary.depends_offset]
        last = max(c.compressed_offset + c.compressed_size for c in needed)
        handle.seek(0)
        raw = handle.read(last)
    method = summary.compression_flags or COMPRESS_LZO
    size = max(c.uncompressed_offset + c.uncompressed_size for c in needed)
    flat = bytearray(size)
    flat[:summary.name_offset] = uncompressed_header(raw, summary)
    for chunk in needed:
        flat[chunk.uncompressed_offset:chunk.uncompressed_offset + chunk.uncompressed_size] = \
            decompress_chunk(raw, chunk, method)
    flat = bytes(flat)
    return Package(summary, flat, read_names(flat, summary), read_imports(flat, summary),
                   read_exports(flat, summary), source=path)


def read(path_or_bytes) -> Package:
    raw = (Path(path_or_bytes).read_bytes()
           if not isinstance(path_or_bytes, (bytes, bytearray)) else bytes(path_or_bytes))
    summary, flat = decompressed(raw)
    return Package(
        summary=summary, data=flat,
        names=read_names(flat, summary),
        imports=read_imports(flat, summary),
        exports=read_exports(flat, summary),
        source=None if isinstance(path_or_bytes, (bytes, bytearray)) else Path(path_or_bytes),
    )
