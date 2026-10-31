"""Build the Animation Speed mod's change to BrgGame.upk (.PackagePatch).

A maintenance tool, not something a player runs. It writes

    mods/Animation Speed/tfc/Game/BrgGame/CookedPCConsole/BrgGame.upk.PackagePatch

Every action a fighter does - eating, kicking a chest open, picking something
up - is one animation played by BrgPawn_Base.PlayAnimation (or, for the upper
body only, PlayAnimationUpperPart), and the action ends when it does. Both pass
the animation slot a play rate of 1.0:

    AnimationDuration = mCustomAnimSlot[i].PlayCustomAnim(AnimName, 1.0, ...)

The change puts a rate from the database there instead, for your own fighter:

    AnimationDuration = 1.0
    if (meCharaType == Player
        && mGameInfo.mDatabase.GetConstFloat("LID_ANIM_" $ string(AnimName), AnimationDuration)
        && AnimationDuration > 0.0)
        ; // the rate from master_const_float
    else
        AnimationDuration = 1.0
    AnimationDuration = mCustomAnimSlot[i].PlayCustomAnim(AnimName, AnimationDuration, ...)

AnimationDuration is the function's own local, which the call overwrites with
the length it returns, as before. An animation with no LID_ANIM_ row plays as
it always did, and so does every animation of anyone else - enemies, Haters,
animals and NPCs play the same names. Which animations, and how fast, is all
in the mod's SQL; this change never needs rebuilding for a new one.

Every function is checked against the sizes it was written for, every changed
statement against its text, and the patch is applied to a copy of the package
to prove it lands where its export entry says. Reads the game's stock
BrgGame.upk and never changes it:

    py -3 tools/build_animation_speed.py --upk PATH/TO/stock/BrgGame.upk
"""
from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
from upkpatch import Kit, o, u16          # noqa: E402

PATCH_PATH = (REPO / "mods" / "Animation Speed" / "tfc" / "Game" / "BrgGame" / "CookedPCConsole"
              / "BrgGame.upk.PackagePatch")
KEY_PREFIX = "LID_ANIM_"
PLAYER = 0                      # EBrgCharacterType_Player

parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
parser.add_argument("--upk", required=True, type=Path, help="the game's stock BrgGame.upk")
parser.add_argument("--out", type=Path, default=PATCH_PATH)
parser.add_argument("--listing", type=Path, help="also write the changed code, disassembled, here")
args = parser.parse_args()

kit = Kit(REPO, args.upk)
ref = kit.ref_of


def f32(v): return b"\x1e" + struct.pack("<f", v)
def native(n, *a): return bytes([n]) + b"".join(a) + b"\x16"
def let(a, b): return b"\x0f" + a + b
def inst(path): return b"\x01" + o(ref(path))
def local(path): return b"\x00" + o(ref(path))
def b2i(e): return b"\x38\x3a" + e                     # byte -> int
def name2s(e): return b"\x38\x57" + e                  # name -> string
def cstr(text): return b"\x1f" + text.encode("latin-1") + b"\0"
EQ, CONCAT, FGT = 0x9A, 0x70, 0xB1


def context(outer: bytes, prop: str, inner: bytes) -> bytes:
    """outer.inner, where inner yields `prop`."""
    return b"\x19" + outer + u16(kit.mem_len(inner)) + o(ref(prop)) + b"\x00" + inner


def faster(path: str, sizes: tuple[int, int, int], at: int):
    fn = kit.fn(path, sizes)
    rate = local(f"{path}.AnimationDuration")
    d, m, dl, ml, _ = fn.stmt(at, f"L:{path}.AnimationDuration = ")
    play = fn.stmt_bytes(at, f"L:{path}.AnimationDuration = ")
    if "PlayCustomAnim(L:" + path + ".AnimName, 1.0f, " not in fn.by_mem[at][4]:
        sys.exit(f"{path} @{at:#x}: not the PlayCustomAnim(AnimName, 1.0, ...) this was written for")

    # the call is the end of the statement: <skip><return value>00 1c <PlayCustomAnim> <args>
    call = b"\x1c" + o(ref("Engine.AnimNodeSlot.PlayCustomAnim"))
    call_at = play.index(call)
    skip_at = call_at - 7
    (skip,) = struct.unpack_from("<H", play, skip_at)
    if play.count(call) != 1 or skip != kit.mem_len(play[call_at:]):
        sys.exit(f"{path} @{at:#x}: the call is not laid out as expected")
    one_at = call_at + len(call) + len(local(f"{path}.AnimName"))
    if play[one_at:one_at + 5] != f32(1.0):
        sys.exit(f"{path} @{at:#x}: no 1.0 rate where expected")
    new_call = play[call_at:one_at] + rate + play[one_at + 5:]
    new_play = (play[:skip_at] + u16(kit.mem_len(new_call)) + play[skip_at + 2:call_at] + new_call)

    key = native(CONCAT, cstr(KEY_PREFIX), name2s(local(f"{path}.AnimName")))
    database = context(inst("BrgPawn_Base.mGameInfo"), "BrgGameInfoNative.mDatabase",
                       inst("BrgGameInfoNative.mDatabase"))
    lookup = context(database, "BrgDatabase.GetConstFloat.ReturnValue",
                     b"\x1b" + struct.pack("<ii", kit.name_index("GetConstFloat"), 0) + key + rate + b"\x16")
    is_player = native(EQ, b2i(inst("BrgPawn_BaseNative.meCharaType")), b2i(b"\x24" + bytes([PLAYER])))

    fn.divert(at, f"L:{path}.AnimationDuration = ", kit.asm()
        .code(let(rate, f32(1.0)))
        .jumpifnot("play", is_player)
        .jumpifnot("one", lookup)
        .jumpifnot("one", native(FGT, rate, f32(0.0)))
        .jump("play")
        .label("one").code(let(rate, f32(1.0)))
        .label("play").code(new_play)
        .jump(m + ml))
    return fn


functions = [
    faster("BrgPawn_Base.PlayAnimation", (49, 1056, 744), 0x21B),
    faster("BrgPawn_Base.PlayAnimationUpperPart", (34, 773, 541), 0x192),
]
kit.write_patch(functions, args.out)
listing = kit.listing(functions)
print(listing)
if args.listing:
    args.listing.write_text(listing, encoding="utf-8")
