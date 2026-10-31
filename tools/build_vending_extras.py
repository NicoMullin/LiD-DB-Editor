"""Build the Vending Machine mod's ammo refill and decal changes to BrgGame.upk.

The two features are 3bgdragon's, from Vending Machine Enhancement
(https://github.com/3bgdragon/let-it-die-vending-enhancement), used with their
permission: a "Refill ammo" entry that refills a gun's ammo for a fifth of what
the weapon costs, paid from the safe, and a "Decals" entry that opens the
mushroom stew's decal screen to put decals on or take them off.

Their tool writes each changed function with its new code inserted in the
middle and every jump after it moved along. Two mods doing that to the same
function cannot both have their way, and Timers changes ShopBase.TickASync and
Stew Multi Pull changes the stew screen's Tick and Render - the same functions.
So each insertion is turned into the form this manager can merge: the stock
statement where the code went in becomes a jump to the end of the function,
where their code runs, then the statement, then a jump back. Nothing before or
after moves. Their reviewed function bodies for Steam build 25923506 (game 1.92)
are the input, read from their repository:

    py -3 tools/build_vending_extras.py --upk PATH/TO/stock/BrgGame.upk
        --source PATH/TO/let-it-die-vending-enhancement

Each insertion is found by rebuilding their function from the stock one, and
the result is proved by rebuilding their function again from ours - byte for
byte - before anything is written. Writes one .PackagePatch for each choice:

    mods/Vending Machine/tfc/ammo, tfc/decals and tfc/both
"""
from __future__ import annotations

import argparse
import base64
import json
import struct
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
from upkpatch import HEADER, Kit, u16          # noqa: E402

OUT = REPO / "mods" / "Vending Machine" / "tfc"
PACKAGE = Path("Game") / "BrgGame" / "CookedPCConsole" / "BrgGame.upk.PackagePatch"
BUILD = 25923506
PRESETS = {"ammo": "ammo-en", "decals": "decals-en", "both": "all-en"}

parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
parser.add_argument("--upk", required=True, type=Path, help="the game's stock BrgGame.upk")
parser.add_argument("--source", required=True, type=Path,
                    help="a checkout of let-it-die-vending-enhancement")
parser.add_argument("--out", type=Path, default=OUT)
parser.add_argument("--listing", type=Path, help="also write the changed code, disassembled, here")
args = parser.parse_args()

reviewed = json.loads((args.source / "shared" / f"owned-functions-{BUILD}.json").read_text(encoding="utf-8"))
kit = Kit(REPO, args.upk)
if len(kit.pkg.exports) != reviewed["exportCount"]:
    sys.exit(f"this BrgGame.upk has {len(kit.pkg.exports)} exports, build {BUILD} has "
             f"{reviewed['exportCount']} - is it the stock package of game 1.92?")


def script(data: bytes) -> tuple[int, bytes]:
    mem, disk = struct.unpack_from("<ii", data, HEADER - 8)
    return mem, data[HEADER:HEADER + disk]


def targets(code: bytes):
    d = kit.Dis(code, kit.names, kit.objs)
    lines = d.run()
    if lines[-1][4] != "END" or d.pos != len(code):
        sys.exit("a script does not read back cleanly")
    return lines, d.targets


def moved(code: bytes, fields, where) -> bytes:
    out = bytearray(code)
    for at, _, t in fields:
        out[at:at + 2] = u16(where(t))
    return bytes(out)


def find_insertion(name, stock: bytes, theirs: bytes):
    """(disk, mem, extra memory, inserted code): where their code went into the stock script."""
    smem, s = script(stock)
    nmem, n = script(theirs)
    if stock[:HEADER - 8] != theirs[:HEADER - 8] or stock[HEADER + len(s):] != theirs[HEADER + len(n):]:
        sys.exit(f"{name}: more than its script changed")
    lines, fields = targets(s)
    extra_disk, extra = len(n) - len(s), nmem - smem
    found = []
    for d, m, *_ in lines:
        # Jumps to where the code went in land on the new code, as in theirs.
        shifted = moved(s, fields, lambda t, m=m: t + extra if t > m else t)
        if shifted[:d] + n[d:d + extra_disk] + shifted[d:] == n:
            found.append((d, m))
    if len(found) != 1:
        sys.exit(f"{name}: {len(found)} places fit their change, not one")
    d, m = found[0]
    return d, m, extra, n[d:d + extra_disk], lines


def span_at(lines, fields, m):
    """The statements from ``m`` a jump can replace: one, or more when one is too short.

    A jump is 3 bytes, and each object the statements name is 4 bytes on disk
    and 8 in memory, so the filler after the jump needs 5 bytes for each. A
    one-line ``super.TickASync(DeltaTime)`` names two and is only 11 bytes, so
    the next statement goes too - as long as nothing jumps into the middle.
    """
    first = next(i for i, line in enumerate(lines) if line[1] == m)
    for last in range(first, len(lines) - 1):
        disk = sum(line[2] for line in lines[first:last + 1])
        mem = sum(line[3] for line in lines[first:last + 1])
        refs, odd = divmod(mem - disk, 4)
        if not odd and disk >= 3 + 5 * refs:
            inside = [t for _, _, t in fields if m < t < m + mem]
            if inside:
                sys.exit(f"a jump lands at {inside[0]:#x}, inside the statements replaced at {m:#x}")
            return lines[first][0], disk, mem, refs
    sys.exit(f"nothing at {m:#x} is long enough to hold a jump")


def convert(name, stock: bytes, theirs: bytes):
    d, m, extra, inserted, lines = find_insertion(name, stock, theirs)
    smem, s = script(stock)
    fn = kit.fn(name, (len(lines), smem, len(s)))
    if next(line for line in lines if line[1] == m)[4] == "END":
        # Added on the end already: theirs is the merge-ready form as it is.
        fn.added = inserted
        return fn, (d, m, extra, 0, 0)
    _, s_fields = targets(s)
    d, dl, ml, refs = span_at(lines, s_fields, m)
    x = fn.added_at                                  # where the block will start
    _, ins_fields = targets(script(theirs)[1])
    ins_fields = [(at - d, k, t) for at, k, t in ins_fields if d <= at < d + len(inserted)]

    def here(t):                                     # their place -> ours
        if t < m:
            return t
        if t < m + extra:
            return x + (t - m)
        if t < m + extra + ml:
            return x + t - m                         # the moved statements, after their code
        return t - extra
    statements = bytes(fn.code[d:d + dl])
    _, t_fields = targets(statements + b"\x53")
    statements = moved(statements, t_fields, lambda t: x if t == m else t)
    block = moved(inserted, ins_fields, here) + statements + b"\x06" + u16(m + ml)
    to = fn.add(kit.asm().code(block))
    if to != x:
        sys.exit(f"{name}: the block is not where it was worked out for")
    # object constants are 4 bytes on disk and 8 in memory, so the filler
    # keeps both sizes of the statements it replaces
    fn.code[d:d + dl] = b"\x06" + u16(x) + (b"\x20" + bytes(4)) * refs + b"\x0b" * (dl - 3 - 5 * refs)
    fn.replaced.append((m, m + ml))
    return fn, (d, m, extra, dl, ml)


def prove(name, stock: bytes, theirs: bytes, ours: bytes, where):
    """Rebuild their function from ours: the same bytes, or nothing is written."""
    d, m, extra, dl, ml = where
    smem, s = script(stock)
    omem, o_code = script(ours)
    if o_code[:d] != s[:d]:
        sys.exit(f"{name}: changed before the statement it diverts")
    if not dl:
        if o_code != script(theirs)[1]:
            sys.exit(f"{name}: ours is not theirs")
        return                                       # added on the end, unchanged
    x = smem - 1
    length = len(script(theirs)[1]) - len(s)
    inserted = o_code[len(s) - 1:len(s) - 1 + length]
    _, fields = targets(o_code)
    fields = [(at - (len(s) - 1), k, t) for at, k, t in fields
              if len(s) - 1 <= at < len(s) - 1 + length]

    def back(t):                                     # our place -> theirs
        if t < m:
            return t
        if x <= t < x + extra + ml:
            return m + (t - x)
        return t + extra
    _, s_fields = targets(s)
    shifted = moved(s, s_fields, lambda t: t + extra if t > m else t)
    rebuilt = shifted[:d] + moved(inserted, fields, back) + shifted[d:]
    if rebuilt != script(theirs)[1]:
        sys.exit(f"{name}: ours does not rebuild theirs")
    # ...and after their code, the statements it went in front of, then back.
    statements = s[d:d + dl]
    _, t_fields = targets(statements + b"\x53")
    tail = moved(statements, t_fields, lambda t: x if t == m else t) + b"\x06" + u16(m + ml) + b"\x53"
    if o_code[len(s) - 1 + length:] != tail or o_code[d + dl:len(s) - 1] != s[d + dl:-1]:
        sys.exit(f"{name}: the moved statements or the rest of the stock script are not as they should be")


listing = []
for choice, preset in PRESETS.items():
    functions = []
    for row in reviewed["objects"]:
        if row["mod"] != "vending" or row["preset"] != preset:
            continue
        stock, theirs = base64.b64decode(row["off"]), base64.b64decode(row["on"])
        if stock == theirs:
            continue
        name = row["name"]
        if kit.pkg.full_path(row["index"] + 1) != name or kit.pkg.export_data(row["index"]) != stock:
            sys.exit(f"{name}: not the stock function this was reviewed against")
        fn, where = convert(name, stock, theirs)
        fn.finish()
        prove(name, stock, theirs, fn.new_data, where)
        functions.append(fn)
    print(f"-- {choice} ({preset}): {len(functions)} function(s)")
    kit.write_patch(functions, args.out / choice / PACKAGE)
    listing.append(f"## {choice}\n" + kit.listing(functions))
if args.listing:
    args.listing.write_text("\n".join(listing), encoding="utf-8")
