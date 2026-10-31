"""Two mods' changes to one UnrealScript function, combined.

A .PackagePatch replaces a whole function, so two mods changing the same one
used to be a clash: the later one in the load order won and the other's change
was lost. Timers and Vending Machine both change ShopBase.TickASync; Stew Multi
Pull and Vending Machine both change the stew screen's Tick and Render.

Most changes to a function here are made the same way, and that way can be
combined. A statement in the stock script is swapped for a jump of the same
size, to code added at the end of the function, which does its work and jumps
back. Every other statement stays exactly where it was. Two such changes can
both be had, as long as they do not swap the same statement: take each mod's
swapped statements, then the first mod's added code, then the second's - with
the second's jumps into its own added code moved along by the length of the
first's. The stock function is needed to tell which statements each one
swapped, which is why this runs where the game's own package is to hand.

Anything else - a change that moves the stock code, a different header, the
same statement swapped by both - is not combined, and the caller does what it
always did.
"""

from __future__ import annotations

import struct

from .ue3script import Dis

HEADER = 48             # a function's data before its script: ..., memory size, disk size
END = 0x53


def _split(data: bytes) -> tuple[bytes, int, bytes, bytes] | None:
    """(header, memory size, script, the rest) - or None if it does not read as a function."""
    if len(data) < HEADER:
        return None
    mem, disk = struct.unpack_from("<ii", data, HEADER - 8)
    if not 0 < disk <= len(data) - HEADER:
        return None
    return data[:HEADER - 8], mem, data[HEADER:HEADER + disk], data[HEADER + disk:]


def _read(code: bytes) -> Dis | None:
    """The script read to its END, or None if it does not read cleanly."""
    d = Dis(code)
    try:
        lines = d.run()
    except (ValueError, IndexError, struct.error):
        return None
    if not lines or lines[-1][4] != "END" or d.pos != len(code):
        return None
    d.lines = lines
    return d


def _fits(code: bytes, disk: int, mem: int) -> bool:
    """Do these bytes read as statements of exactly this size, on disk and in memory?"""
    swapped = Dis(code + bytes([END]))
    try:
        swapped.run()
    except (ValueError, IndexError, struct.error):
        return False
    return swapped.pos == disk + 1 and swapped.mem == mem + 1


def _changes(stock: Dis, mem: int, code: bytes) -> list[tuple[int, int]] | None:
    """The stretches of stock statements (disk start, disk length) this version swaps, or None.

    None when the version is not stock-plus-additions: its stock part must
    keep every statement's size, and whatever it puts in place of swapped
    statements must read as statements of exactly that size. One jump can
    stand for more than one statement - a short one has no room for it - so a
    stretch runs on until what replaced it reads cleanly to its end.
    """
    s = stock.code
    if len(code) < len(s) or code[-1] != END or mem < stock.mem:
        return None
    lines = stock.lines[:-1]
    changed = []
    i = 0
    while i < len(lines):
        d, _, dl, ml, _ = lines[i]
        if code[d:d + dl] == s[d:d + dl]:
            i += 1
            continue
        disk, size = 0, 0
        for j in range(i, len(lines)):
            disk += lines[j][2]
            size += lines[j][3]
            if _fits(code[d:d + disk], disk, size):
                break
        else:
            return None
        changed.append((d, disk))
        i = j + 1
    return changed


def merge(stock: bytes, first: bytes, second: bytes) -> bytes | None:
    """``first`` and ``second``'s changes to the ``stock`` function, both kept - or None."""
    parts = [_split(x) for x in (stock, first, second)]
    if None in parts:
        return None
    (head, s_mem, s, rest), (head_a, a_mem, a, rest_a), (head_b, b_mem, b, rest_b) = parts
    if not head == head_a == head_b or not rest == rest_a == rest_b:
        return None
    base = _read(s)
    if base is None or base.mem != s_mem:
        return None
    read_a, read_b = _read(a), _read(b)
    if read_a is None or read_b is None or read_a.mem != a_mem or read_b.mem != b_mem:
        return None
    swapped_a, swapped_b = _changes(base, a_mem, a), _changes(base, b_mem, b)
    if swapped_a is None or swapped_b is None:
        return None
    if any(da < db + lb and db < da + la for da, la in swapped_a for db, lb in swapped_b):
        return None             # the same statement, changed by both

    end = len(s) - 1                    # where the added code starts, on disk...
    added_at = s_mem - 1                # ...and in memory
    shift = a_mem - s_mem               # the memory the first one's added code takes
    in_b = [(d, d + dl) for d, dl in swapped_b] + [(end, len(b) - 1)]
    moved = bytearray(b)
    for at, _, target in read_b.targets:
        if target >= added_at and any(lo <= at < hi for lo, hi in in_b):
            if target + shift > 0xFFFF:
                return None
            moved[at:at + 2] = struct.pack("<H", target + shift)

    code = bytearray(s[:-1])
    for d, dl in swapped_a:
        code[d:d + dl] = a[d:d + dl]
    for d, dl in swapped_b:
        code[d:d + dl] = moved[d:d + dl]
    code += a[end:-1] + moved[end:-1] + bytes([END])
    mem = a_mem + b_mem - s_mem
    if mem > 0xFFFF:
        return None

    # Every jump has to land on the start of a statement, as it did in each.
    merged = _read(bytes(code))
    if merged is None or merged.mem != mem:
        return None
    starts = {line[1] for line in merged.lines}
    if any(target not in starts for _, _, target in merged.targets):
        return None
    return bytes(head) + struct.pack("<ii", mem, len(code)) + bytes(code) + bytes(rest)
