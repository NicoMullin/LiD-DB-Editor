"""The table of expected file hashes inside the game's executable.

LET IT DIE ships a list of SHA-1 hashes for its own data files, stored as a
resource inside ``BrgGame-Steam.exe``. On this build it covers 8,266 names -
7,678 of the 7,882 packages on disk, plus the .ini and .usf files. Replace a
listed file and the game notices.

Most artwork mods never meet this, because the files they replace are among the
204 the table does not list - equipment added after the table was built. A mod
that changes a *listed* file has no such luck: the game has to be told the new
hash, or it refuses the file.

This module is the read-only half of that plus one narrow write:

    checksum_resource()   find the table inside the executable
    read_entries()        every name in it, with the hash and where it sits
    code_fingerprint()    SHA-256 of the .text section - the executable's code
    apply_entry()         replace exactly one 20-byte hash, and prove it

``apply_entry`` is the only function here that produces changed bytes, and it
is deliberately incapable of changing anything else: it writes 20 bytes at an
offset it worked out from the file's own structure, only when those bytes
already hold the value the caller said to expect, and it re-checks afterwards
that the length is unchanged, that every byte outside those 20 is untouched,
and that the code fingerprint still matches. A hash in a data table is not
code, and this proves it stayed that way.

Nothing here writes to disk. See patch.py for the patch that uses it and
recipes/ for the vetted recordings that are the only way to reach it.
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass

# The resource the hash table lives in: type 10 (RT_RCDATA), id 1010.
RESOURCE_TYPE = 10
RESOURCE_ID = 1010
# Each entry is a NUL-terminated name followed by a raw SHA-1.
HASH_BYTES = 20
# A marker that divides the table into sections. Only the first is file hashes.
SECTION_MARKER = b"+++"
CODE_SECTION = b".text"


class ExeFormatError(ValueError):
    """The executable is not the shape this expects."""


@dataclass(frozen=True)
class Entry:
    """One file the executable carries a hash for."""

    name: str
    sha1: str
    at: int  # absolute offset of the hash within the executable


def _u16(raw: bytes, at: int) -> int:
    return struct.unpack_from("<H", raw, at)[0]


def _u32(raw: bytes, at: int) -> int:
    return struct.unpack_from("<I", raw, at)[0]


def _pe_offset(raw: bytes) -> int:
    """Where the PE header starts, checking this is a Windows executable."""
    if len(raw) < 0x40 or raw[:2] != b"MZ":
        raise ExeFormatError("not a Windows executable")
    pe = _u32(raw, 0x3C)
    if len(raw) < pe + 24 or raw[pe:pe + 4] != b"PE\0\0":
        raise ExeFormatError("not a valid PE executable")
    return pe


def _sections(raw: bytes) -> list[tuple[bytes, int, int, int]]:
    """(name, virtual address, virtual size, file offset) for each section."""
    pe = _pe_offset(raw)
    optional_size = _u16(raw, pe + 20)
    count = _u16(raw, pe + 6)
    table = pe + 24 + optional_size
    out = []
    for index in range(count):
        at = table + index * 40
        if at + 40 > len(raw):
            raise ExeFormatError("section table runs past the end of the file")
        out.append((
            raw[at:at + 8].rstrip(b"\0"),
            _u32(raw, at + 12),   # virtual address
            _u32(raw, at + 16),   # virtual size
            _u32(raw, at + 20),   # raw data offset
        ))
    return out


def _file_offset(raw: bytes, rva: int, size: int = 1) -> int:
    """Turn a virtual address into an offset in the file on disk."""
    for _name, virtual, length, start in _sections(raw):
        if virtual <= rva and rva + size <= virtual + length:
            at = start + rva - virtual
            if at + size <= len(raw):
                return at
    raise ExeFormatError("a resource points outside the file")


def code_fingerprint(raw: bytes) -> str:
    """SHA-256 of the executable's code section.

    This is the check that makes a resource edit defensible: the same value
    before and after means the game's code is byte-for-byte what it was, and
    only data moved. A file with no single unambiguous .text section is
    refused rather than guessed at.
    """
    found = []
    for name, _virtual, _length, start in _sections(raw):
        if name == CODE_SECTION:
            pe = _pe_offset(raw)
            at = pe + 24 + _u16(raw, pe + 20)
            for index in range(_u16(raw, pe + 6)):
                entry = at + index * 40
                if raw[entry:entry + 8].rstrip(b"\0") == CODE_SECTION:
                    size = _u32(raw, entry + 16)   # size of raw data
                    if start + size > len(raw):
                        raise ExeFormatError("code section runs past the end of the file")
                    found.append(hashlib.sha256(raw[start:start + size]).hexdigest())
                    break
            break
    if len(found) != 1:
        raise ExeFormatError("the executable has no single code section")
    return found[0]


def checksum_resource(raw: bytes) -> tuple[int, int]:
    """(offset, length) of the hash table inside the executable."""
    pe = _pe_offset(raw)
    optional = pe + 24
    magic = _u16(raw, optional)
    if magic not in (0x10B, 0x20B):
        raise ExeFormatError("unsupported executable format")
    # The data directories follow the optional header; the resource one is
    # third, and PE32+ puts it 16 bytes further along than PE32.
    directories = optional + (112 if magic == 0x20B else 96)
    resource_rva = _u32(raw, directories + 16)
    resource_size = _u32(raw, directories + 20)
    if not resource_rva or not resource_size:
        raise ExeFormatError("the executable has no resource directory")
    base = _file_offset(raw, resource_rva, resource_size)

    def entries(relative: int) -> list[tuple[int, int]]:
        if relative + 16 > resource_size:
            raise ExeFormatError("malformed resource directory")
        at = base + relative
        count = _u16(raw, at + 12) + _u16(raw, at + 14)  # named + id entries
        if relative + 16 + count * 8 > resource_size:
            raise ExeFormatError("malformed resource directory entries")
        return [(_u32(raw, at + 16 + i * 8), _u32(raw, at + 20 + i * 8)) for i in range(count)]

    def descend(relative: int, key: int) -> int:
        matches = [where for name, where in entries(relative) if name == key]
        if len(matches) != 1 or not matches[0] & 0x80000000:
            raise ExeFormatError(f"no single resource directory for {key}")
        return matches[0] & 0x7FFFFFFF

    language = entries(descend(descend(0, RESOURCE_TYPE), RESOURCE_ID))
    if len(language) != 1 or language[0][1] & 0x80000000:
        raise ExeFormatError("the hash table's language entry is ambiguous")
    relative = language[0][1]
    if relative + 16 > resource_size:
        raise ExeFormatError("malformed resource data entry")
    data = base + relative
    size = _u32(raw, data + 4)
    return _file_offset(raw, _u32(raw, data), size), size


def read_entries(raw: bytes) -> dict[str, Entry]:
    """Every file hash in the table, keyed by lower-case name.

    Only the first section is returned. The table can carry a ``+++`` marker
    after which the entries mean something else; on the build this was written
    against there is no marker at all, but reading past one would silently mix
    two different kinds of entry together.
    """
    at, length = checksum_resource(raw)
    end = at + length
    out: dict[str, Entry] = {}
    cursor = at
    while cursor < end:
        stop = raw.find(b"\0", cursor, end)
        if stop < 0:
            break
        name = raw[cursor:stop]
        cursor = stop + 1
        if not name:
            continue
        if name == SECTION_MARKER:
            break
        if cursor + HASH_BYTES > end:
            raise ExeFormatError("the hash table ends mid-entry")
        try:
            key = name.decode("ascii").lower()
        except UnicodeDecodeError:
            raise ExeFormatError("a name in the hash table is not plain text") from None
        # A duplicate name would make "the entry for X" meaningless, and the
        # write path refuses anything it cannot address unambiguously.
        if key not in out:
            out[key] = Entry(key, raw[cursor:cursor + HASH_BYTES].hex(), cursor)
        else:
            out[key] = Entry(key, out[key].sha1, -1)
        cursor += HASH_BYTES
    if not out:
        raise ExeFormatError("the hash table is empty")
    return out


def apply_entry(raw: bytes, package: str, before: str, after: str) -> bytes:
    """The executable with one file's expected hash changed, and nothing else.

    ``before`` is what that entry must currently hold - a wrong value means
    this is not the build the change was recorded against, or something already
    changed it, and either way nothing is written. Every check here is a
    refusal, never a repair.
    """
    before, after = before.lower(), after.lower()
    for label, value in (("before", before), ("after", after)):
        if len(value) != HASH_BYTES * 2 or not all(c in "0123456789abcdef" for c in value):
            raise ExeFormatError(f"the {label} hash is not a SHA-1")
    if before == after:
        raise ExeFormatError("the before and after hashes are the same")

    entry = read_entries(raw).get(package.lower())
    if entry is None:
        raise ExeFormatError(f"this executable carries no hash for {package}")
    if entry.at < 0:
        raise ExeFormatError(f"{package} appears more than once in the hash table")
    if entry.sha1 == after:
        # Already carries the change. Almost always this mod, applied before and
        # still in place while the manager's record of it went missing - moving
        # or reinstalling the manager does that. Adopting it silently would be
        # worse than stopping: the saved "original" would be the modified file,
        # so switching the mod off later would restore the modification.
        raise ExeFormatError(
            f"the game executable already expects the modified {package}, so this "
            "change is in place - but the manager has no saved copy of the original "
            "to put back. Something applied it outside this manager, or its backups "
            "were lost. Restore the game files (Steam: Properties > Installed Files "
            "> Verify integrity) and enable the mod again, so the manager has a way "
            "back."
        )
    if entry.sha1 != before:
        raise ExeFormatError(
            f"the hash for {package} is {entry.sha1}, not the expected {before}. "
            "This game build is not the one this change was recorded against."
        )

    at = entry.at
    changed = raw[:at] + bytes.fromhex(after) + raw[at + HASH_BYTES:]

    # Prove the edit did what it said. These are not belt-and-braces: they are
    # the reason this is allowed to touch an executable at all.
    if len(changed) != len(raw):
        raise ExeFormatError("the edit changed the file's length")
    if changed[:at] != raw[:at] or changed[at + HASH_BYTES:] != raw[at + HASH_BYTES:]:
        raise ExeFormatError("the edit changed bytes outside the hash")
    if code_fingerprint(changed) != code_fingerprint(raw):
        raise ExeFormatError("the edit changed the executable's code")
    if read_entries(changed)[package.lower()].sha1 != after:
        raise ExeFormatError("the hash did not end up as asked")
    return changed
