"""Changing a few bytes inside a package, leaving the rest of it alone.

Some of what a mod wants to change is not in ``masters.db`` at all: it is a
number compiled into UnrealScript bytecode inside a package. The wait before a
dead enemy drops its reward is one - the script reads it from a table whose
neighbours come from a config file, but that entry's number is in the bytecode.

A package is a small uncompressed header, then the body in chunks, each chunk
LZO1X in blocks (see package.py). Rewriting the whole thing uncompressed works
and is what a rebuilt package does, but BrgGame.upk is 179 MB compressed and
365 MB without, and this is a one-byte change. So instead:

    find the chunk holding the bytes, decode it, change them, write the new
    chunk at the end of the file, and point the table entry at it

Everything else in the file is byte for byte as the game shipped it, and it
grows by one chunk. The old chunk is left where it is: the table no longer
names it, and an update replaces the file anyway.

The approach, and the trick of writing the new chunk as a run of literals
rather than compressing it, are from Claudia-diva's LID-Patches (MIT).

Matching is on the bytes *around* the value, never on the value itself, so a
site is still found once it has been changed, and a mod can be re-applied or
measured without first putting the file back.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from . import lzo
from . import package as P


class SiteError(ValueError):
    """The bytes to change could not be found, or not unambiguously."""


@dataclass
class Site:
    """Where a signature sits, and what it takes to write the chunk back."""

    chunk_index: int
    chunk: P.CompressedChunk
    raw: bytes          # the chunk, decoded
    at: int             # where the match ENDS inside raw - the value's first byte
    block_size: int
    entry_at: int       # where this chunk's 16-byte table entry sits in the file


def _chunk_block_size(data: bytes, chunk: P.CompressedChunk) -> int:
    """The block size a chunk was written with, from its own little header."""
    r = P.Reader(data, chunk.compressed_offset)
    if r.u32() != P.TAG:
        raise SiteError("a chunk in this package has no tag - is it a package?")
    return r.u32()


def find(data: bytes, signature: bytes, *, follows: bytes = b"") -> Site:
    """The one place ``signature`` appears in the package body.

    Chunks are decoded one at a time and the search stops at the first hit, so
    the usual case reads a fraction of a large file. ``follows`` is checked
    against the bytes just past the value, which is what tells a genuine match
    from a coincidence when the signature is short.
    """
    summary = P.read_summary(data)
    if not summary.compressed:
        raise SiteError("this package is not compressed - nothing to rebuild")
    for index, chunk in enumerate(summary.chunks):
        raw = P.decompress_chunk(data, chunk, summary.compression_flags or P.COMPRESS_LZO)
        at = raw.find(signature)
        if at < 0:
            continue
        if raw.find(signature, at + 1) >= 0:
            raise SiteError("those bytes appear more than once in one chunk, so "
                            "there is no telling which was meant")
        value_at = at + len(signature)
        if follows and raw[value_at + 1:value_at + 1 + len(follows)] != follows:
            raise SiteError("the bytes after the one to change are not what this "
                            "mod expects - the game build may have moved on")
        return Site(chunk_index=index, chunk=chunk, raw=raw, at=value_at,
                    block_size=_chunk_block_size(data, chunk),
                    entry_at=summary.chunk_table_at + 4 + index * 16)
    raise SiteError("those bytes are not in this package")


def encode_chunk(raw: bytes, block_size: int) -> bytes:
    """One chunk ready to write: its header, the block table, then the blocks."""
    blocks = [raw[i:i + block_size] for i in range(0, len(raw), block_size)]
    stored = [lzo.store(block) for block in blocks]
    head = struct.pack("<4I", P.TAG, block_size,
                       sum(len(b) for b in stored), len(raw))
    table = b"".join(struct.pack("<II", len(s), len(b))
                     for s, b in zip(stored, blocks))
    return head + table + b"".join(stored)


def rewrite(data: bytes, site: Site, raw: bytes) -> bytes:
    """The package with ``site``'s chunk replaced by ``raw``.

    The new chunk goes on the end and the table entry is repointed at it. Where
    the chunk *belongs* once decoded does not move, so nothing else in the file
    has to be touched - no offset in the header changes.
    """
    if len(raw) != site.chunk.uncompressed_size:
        raise SiteError("a rebuilt chunk has to be the same size as the one it "
                        "replaces")
    blob = encode_chunk(raw, site.block_size)
    out = bytearray(data)
    out += blob
    struct.pack_into("<4I", out, site.entry_at,
                     site.chunk.uncompressed_offset, site.chunk.uncompressed_size,
                     len(data), len(blob))
    return bytes(out)


def write_chunks(original: bytes, flat_before: bytes, flat_after: bytes) -> bytes | None:
    """The package rebuilt as chunks, reusing every chunk that did not change.

    A patched package can be written out flat - the game reads one happily -
    but flat is two to three times the size, and a change usually touches a
    handful of chunks out of dozens. Glados rewrites three of Cafe_KIS's
    sixty-five; the other sixty-two are already exactly the bytes the game
    shipped, so they are copied across compressed and untouched.

    Anything the patch added goes on the end of the last chunk rather than into
    new ones. The chunk table then keeps its size, the header never moves, and
    no offset anywhere else has to be corrected.

    Returns None when this cannot be done and the caller should write the flat
    form instead: a package that was not compressed to begin with, or one whose
    header the patch changed, which would need the file header rewritten too.
    """
    summary = P.read_summary(original)
    if not summary.compressed:
        return None
    head = summary.chunks[0].uncompressed_offset
    if flat_after[:head] != flat_before[:head]:
        return None                      # the summary itself moved; not ours to fix
    last = len(summary.chunks) - 1
    tail_size = len(flat_after) - summary.chunks[last].uncompressed_offset
    if tail_size < 0:
        return None                      # it shrank past the last chunk's start

    body_at = summary.chunks[0].compressed_offset
    out = bytearray(original[:body_at])  # summary and chunk table, as they were
    entries = []
    for number, chunk in enumerate(summary.chunks):
        at = chunk.uncompressed_offset
        size = tail_size if number == last else chunk.uncompressed_size
        new = flat_after[at:at + size]
        old = flat_before[at:at + chunk.uncompressed_size]
        if number != last and new == old:
            blob = original[chunk.compressed_offset:
                            chunk.compressed_offset + chunk.compressed_size]
        else:
            blob = encode_chunk(new, _chunk_block_size(original, chunk))
        entries.append((at, size, len(out), len(blob)))
        out += blob

    for number, (at, size, offset, length) in enumerate(entries):
        struct.pack_into("<4I", out, summary.chunk_table_at + 4 + number * 16,
                         at, size, offset, length)
    return bytes(out)
