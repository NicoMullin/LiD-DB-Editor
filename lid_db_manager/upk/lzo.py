"""LZO1X decompression, in plain Python.

LET IT DIE stores its packages compressed in 128 KB blocks, and the blocks are
LZO1X - the algorithm Unreal Engine 3 used on PC. Python has no LZO in its
standard library, and a compiled extension would be one more thing to install,
so this is the decompressor written out by hand from the published format.

Writing back needs no real compressor. A block of literals is valid LZO1X and
decodes to itself, so ``store`` hands the game back the bytes it wants without
the work of matching - see bytepatch.py, where one chunk of a package is
rebuilt in place and everything else is left exactly as the game shipped it.

The literal-run encoding is FCH-independent and comes from the published
format; the same trick is used by Claudia-diva's LID-Patches (MIT), which is
where this approach was taken from.
"""

from __future__ import annotations


class LzoError(ValueError):
    """The compressed data does not decode. Nothing is returned."""


# The furthest back an M2 match reaches, and the shape of the M4 marker. These
# are the constants of the format, named as the reference implementation names
# them so the two can be read side by side.
_M2_MAX_OFFSET = 0x0800


def decompress(data: bytes, expected_size: int) -> bytes:
    """One LZO1X block, decoded to exactly ``expected_size`` bytes.

    The size is known up front - the package records it for every block - and
    is checked on the way out: a block that decodes to anything else is corrupt
    or not LZO at all, and is refused rather than returned half-right.
    """
    src = memoryview(data)
    end = len(src)
    out = bytearray()
    ip = 0

    def byte() -> int:
        nonlocal ip
        if ip >= end:
            raise LzoError("the block ends in the middle of an instruction")
        value = src[ip]
        ip += 1
        return value

    def long_length(base: int) -> int:
        """A length continued in following bytes: runs of zero add 255 each."""
        nonlocal ip
        total = 0
        while True:
            if ip >= end:
                raise LzoError("the block ends in the middle of a length")
            if src[ip] != 0:
                break
            total += 255
            ip += 1
        return total + base + byte()

    def literals(count: int) -> None:
        nonlocal ip
        if ip + count > end:
            raise LzoError("a literal run reaches past the end of the block")
        out.extend(src[ip:ip + count])
        ip += count

    def copy_match(distance: int, count: int) -> None:
        start = len(out) - distance
        if start < 0:
            raise LzoError("a match reaches back before the start of the block")
        if distance >= count:
            out.extend(out[start:start + count])
        else:
            # Overlapping: each byte copied may be one this copy just wrote.
            for i in range(count):
                out.append(out[start + i])

    # A first byte above 17 is a literal run with no instruction in front of it.
    t = byte()
    if t > 17:
        t -= 17
        if t < 4:
            literals(t)
            t = byte()
            state = "match"
        else:
            literals(t)
            state = "first_literal_run"
    else:
        ip -= 1
        state = "loop"

    while True:
        if state == "loop":
            t = byte()
            if t >= 16:
                state = "match"
                continue
            if t == 0:
                t = long_length(15)
            literals(t + 3)
            state = "first_literal_run"
            continue

        if state == "first_literal_run":
            t = byte()
            if t >= 16:
                state = "match"
                continue
            # Straight after a literal run, a short instruction is a three-byte
            # match further back than an ordinary M1 reaches.
            distance = 1 + _M2_MAX_OFFSET + (t >> 2) + (byte() << 2)
            copy_match(distance, 3)
            state = "match_done"
            continue

        if state == "match":
            if t >= 64:                                   # M2
                distance = 1 + ((t >> 2) & 7) + (byte() << 3)
                copy_match(distance, (t >> 5) - 1 + 2)
            elif t >= 32:                                 # M3
                length = t & 31
                if length == 0:
                    length = long_length(31)
                low, high = byte(), byte()
                distance = 1 + (low >> 2) + (high << 6)
                copy_match(distance, length + 2)
            elif t >= 16:                                 # M4, or the end marker
                distance = (t & 8) << 11
                length = t & 7
                if length == 0:
                    length = long_length(7)
                low, high = byte(), byte()
                distance += (low >> 2) + (high << 6)
                if distance == 0:
                    break                                 # end of stream
                copy_match(distance + 0x4000, length + 2)
            else:                                         # M1
                distance = 1 + (t >> 2) + (byte() << 2)
                copy_match(distance, 2)
            state = "match_done"
            continue

        if state == "match_done":
            # The low two bits of the byte two back say how many literals follow.
            t = src[ip - 2] & 3
            if t == 0:
                state = "loop"
                continue
            literals(t)
            t = byte()
            state = "match"
            continue

    if len(out) != expected_size:
        raise LzoError(
            f"the block decoded to {len(out):,} bytes, not the {expected_size:,} "
            "the package says it holds")
    return bytes(out)


#: Ends every LZO1X stream: an M4 marker with no following data.
_END = bytes([0x11, 0x00, 0x00])


def store(data: bytes) -> bytes:
    """``data`` as one LZO1X literal run - valid, and the same size plus a few.

    Compressing properly would save space in a file the game is about to read
    back into memory anyway; what matters here is that the block decodes to
    exactly these bytes, so a package can be rebuilt without a compressor.
    """
    if not data:
        return _END
    out = bytearray()
    size = len(data)
    if size >= 19:
        # Token 0, then (size - 18) written as a run of zero bytes worth 255
        # each and a final non-zero remainder.
        left = size - 18
        zeros, remainder = (left - 1) // 255, left - 255 * ((left - 1) // 255)
        out.append(0)
        out += b"\x00" * zeros
        out.append(remainder)
    elif size >= 4:
        out.append(size - 3)        # a token of 1..15 means that many literals
    else:
        out.append(17 + size)       # the short form, only valid at the start
    out += data
    out += _END
    return bytes(out)
