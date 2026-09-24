"""Switching the game's own file check off, one package at a time.

The executable carries a list of file names, each with the SHA-1 the game
expects that file to have. Replace a listed file and the game refuses it at
startup with an error box naming the package and nothing else to go on.

There are two ways past that. The precise one is to write the correct hash for
the file being installed, which is what ``exe_checksums`` does for a vetted
recipe. The other is this: **a file with no entry is never checked at all**, so
changing one byte of an entry's *name* takes that package out of the list and
leaves it freely replaceable, by any mod, for good.

Nothing here touches program code. The name lives in a PE resource, the edit is
one byte inside it, and every write is proved afterwards: same length, same code
fingerprint, only the bytes that were meant to change, all of them inside the
list, and the list still reads back with as many entries as before.

The change is reversible without keeping any record of it. Every name the game
lists ends in ``.upk``, ``.ini`` or ``.usf``, and only the last character is
touched - so the first two characters of the extension say what the last one
was. That is also what makes a switched-off entry recognisable on sight.
"""

from __future__ import annotations

from dataclasses import dataclass

from .exe_checksums import (
    HASH_BYTES,
    ExeFormatError,
    checksum_resource,
    code_fingerprint,
    read_entries,
)

# What the last character of a name becomes. The alternate is for the case that
# would otherwise be a no-op - a name already ending in the mark. Upper case,
# and deliberately the same as the standalone LiD File Check tool uses, so that
# a file switched off by either one comes out byte for byte the same and both
# read each other's work.
OFF_MARK = ord("X")
OFF_MARK_ALT = ord("Y")

# The stem of every extension the game lists, and the character we took off it.
# Reading the original back out of a switched-off name needs nothing else.
ORIGINAL_LAST = {"up": "k", "in": "i", "us": "f"}

# Names read back lower case, so the marks are compared that way.
OFF_SUFFIXES = tuple(
    f".{stem}{chr(mark).lower()}"
    for stem in ORIGINAL_LAST
    for mark in (OFF_MARK, OFF_MARK_ALT)
)


@dataclass(frozen=True)
class Listing:
    """One appearance of a name in the list.

    A name can be listed more than once - 228 are, on the builds seen so far -
    and the game may look up either, so switching a package off has to change
    every one of them. ``read_entries`` keys by name and cannot address a
    duplicate, which is why this walks the list itself.
    """

    name: str  # lower case, as the list holds it
    last_at: int  # absolute offset of the last character of the name


def listings(raw: bytes) -> list[Listing]:
    """Every appearance of every name in the list, in the order they are held."""
    at, length = checksum_resource(raw)
    end = at + length
    out: list[Listing] = []
    cursor = at
    while cursor < end:
        stop = raw.find(b"\0", cursor, end)
        if stop < 0:
            break
        name = raw[cursor:stop]
        cursor = stop + 1
        if not name:
            continue
        if name == b"+++":
            break
        if cursor + HASH_BYTES > end:
            raise ExeFormatError("the file list ends mid-entry")
        try:
            out.append(Listing(name.decode("ascii").lower(), stop - 1))
        except UnicodeDecodeError:
            raise ExeFormatError("a name in the file list is not plain text") from None
        cursor += HASH_BYTES
    if not out:
        raise ExeFormatError("the file list is empty")
    return out


def is_checked(raw: bytes, package: str) -> bool:
    """Does the game still verify this file?

    A package the list does not name is not verified, which is the whole of the
    mechanism: this asks the same question the game asks.
    """
    return package.strip().lower() in read_entries(raw)


def checked_names(raw: bytes) -> list[str]:
    """Every file the game still verifies.

    A switched-off name is still an entry in the list - that is the point, the
    length never changes - but it no longer names a file the game will look up,
    so it does not belong here.
    """
    return sorted(name for name in read_entries(raw) if not name.endswith(OFF_SUFFIXES))


def switched_off_names(raw: bytes) -> list[str]:
    """The names that have been switched off, read back off the file itself.

    No stock copy is needed and no record is kept: the game names only ``.upk``,
    ``.ini`` and ``.usf`` files, so any other ending is our own handiwork.
    """
    return sorted(
        {listing.name for listing in listings(raw) if listing.name.endswith(OFF_SUFFIXES)}
    )


def original_name(name: str) -> str:
    """A switched-off name as the game wrote it, or the name unchanged."""
    if not name.endswith(OFF_SUFFIXES):
        return name
    return name[:-1] + ORIGINAL_LAST[name[-3:-1]]


def _rewritten(raw: bytes, meant: dict[int, int], what: str) -> bytes:
    """Splice the bytes in, and prove that is all that happened.

    These checks are not belt-and-braces. They are the reason this is allowed
    near an executable at all: any one of them failing means nothing is
    returned, so nothing can be written.
    """
    if not meant:
        return raw
    at, length = checksum_resource(raw)
    out = bytearray(raw)
    for offset, value in meant.items():
        if not at <= offset < at + length:
            raise ExeFormatError("a change landed outside the file list")
        out[offset] = value
    changed = bytes(out)

    if len(changed) != len(raw):
        raise ExeFormatError("the change altered the file's length")
    if code_fingerprint(changed) != code_fingerprint(raw):
        raise ExeFormatError("the change altered the game's code")
    differs = {i for i in range(at, at + length) if raw[i] != changed[i]}
    if differs != {o for o, v in meant.items() if raw[o] != v}:
        raise ExeFormatError("bytes changed that were not meant to")
    if raw[:at] != changed[:at] or raw[at + length:] != changed[at + length:]:
        raise ExeFormatError("a change landed outside the file list")
    if len(listings(changed)) != len(listings(raw)):
        raise ExeFormatError("the file list no longer reads back correctly")
    return changed


def switch_off(raw: bytes, packages) -> tuple[bytes, list[str]]:
    """The executable with those packages taken out of the list.

    Returns the new bytes and the names that were actually switched off - a
    package the list never named, or one already switched off, is not an error
    and is simply not in that list.
    """
    wanted = {str(p).strip().lower() for p in packages if str(p).strip()}
    if not wanted:
        return raw, []
    meant: dict[int, int] = {}
    done: set[str] = set()
    for listing in listings(raw):
        if listing.name not in wanted:
            continue
        current = raw[listing.last_at]
        meant[listing.last_at] = OFF_MARK if current != OFF_MARK else OFF_MARK_ALT
        done.add(listing.name)
    changed = _rewritten(raw, meant, "switch off")
    # One parse, then every name is looked up in it. Asking is_checked per name
    # re-parsed the whole list each time, which on a real game is 8,038 parses
    # of 400 KB - a minute of it, for an answer one parse already holds.
    still = read_entries(changed).keys() & done
    if still:
        raise ExeFormatError(f"{sorted(still)[0]} is still in the file list")
    return changed, sorted(done)


def switch_on(raw: bytes, packages=None) -> tuple[bytes, list[str]]:
    """The executable with switched-off packages put back in the list.

    ``packages`` names them as the game does (``foo.upk``); None means every
    one that is switched off. What the last character was is read off the
    extension, so nothing has to have been recorded when they were taken out.
    """
    if packages is None:
        wanted = None
    else:
        wanted = {str(p).strip().lower() for p in packages if str(p).strip()}
        if not wanted:
            return raw, []
    meant: dict[int, int] = {}
    done: set[str] = set()
    for listing in listings(raw):
        if not listing.name.endswith(OFF_SUFFIXES):
            continue
        was = original_name(listing.name)
        if wanted is not None and was not in wanted:
            continue
        meant[listing.last_at] = ord(was[-1])
        done.add(was)
    changed = _rewritten(raw, meant, "switch on")
    missing = done - read_entries(changed).keys()  # one parse - see switch_off
    if missing:
        raise ExeFormatError(
            f"{sorted(missing)[0]} did not go back into the file list"
        )
    return changed, sorted(done)
