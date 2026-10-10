"""Write the Enemies Left mod's change to BrgGame.upk as a .PackagePatch.

A maintenance tool. It adds one line to the in-game HUD: how many enemies are
still alive on the floor, out of how many it started with - "Enemies left:
12 / 20". Everything it shows the game already counts:

  BrgAIDirector.GetAliveEnemyNum()      the living enemies (Haters and
                                        monsters), the count the game itself
                                        uses to judge a floor cleared
  BrgAIDirector.GetFloorStartEnemyNum() how many the floor started with

One function changes, BrgHUD.PostRender, by a trampoline: right after the UI
is drawn each frame, the line is drawn - but only while the game shows its HUD
at all (BrgUIManager.CheckVisibleHUD: not in menus, cutscenes, fades or death)
and only on a floor that has enemies and not in the hub, so never in the
Waiting Room.

Where it goes and how big is up to the player: the mod's SQL writes four
numbers into master_const_float, read every frame with the game's own
BrgDatabase.GetConstFloat:

  LID_ENEMY_HUD_X, LID_ENEMY_HUD_Y   the position, on the game's 1920 x 1080
  LID_ENEMY_HUD_SCALE                the text size (1 = the game's own)
  LID_ENEMY_HUD_ALIGN                0 left, 1 centred, 2 right of that point
  LID_ENEMY_HUD_COLOUR               the text's colour, as red * 65536 +
                                     green * 256 + blue (a float holds that
                                     exactly)

GetConstFloat hands its answer back through a variable, and PostRender has
none to spare, so the five land in BrgUIManager's mTestPosition,
mTestPositionAdd, mTestAngle, mTestTime and mTestCounter: floats the class
declares and no script ever reads or writes (left over from development).
They are written right before they are read, every frame.

The words come from master_text (TUB.TXT_LID_ENEMIES_LEFT, "#0" the living
and "#1" the start), written by the mod's SQL in every language the game has,
and drawn in the game's own text font with BrgUIFontRender.DrawShadowString,
which puts a black copy behind it. (DrawText's own UseShadowColor does not add
a shadow: it draws the text itself in the shadow colour.)

Reads the game's stock BrgGame.upk and never changes it:

    py -3 tools/build_enemies_left.py --upk PATH/TO/BrgGame.upk
"""
from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
OUT = REPO / "mods" / "Enemies Left" / "tfc"
PATCH_PATH = Path("Game") / "BrgGame" / "CookedPCConsole" / "BrgGame.upk.PackagePatch"
TEXT_KEY = "TUB.TXT_LID_ENEMIES_LEFT"
CONSTS = {"x": "LID_ENEMY_HUD_X", "y": "LID_ENEMY_HUD_Y",
          "scale": "LID_ENEMY_HUD_SCALE", "align": "LID_ENEMY_HUD_ALIGN",
          "colour": "LID_ENEMY_HUD_COLOUR"}
TEXT_FONT = 2                    # BrgUIManagerBase.mFont[2]: the game's text font

parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
parser.add_argument("--upk", required=True, type=Path, help="the game's stock BrgGame.upk")
parser.add_argument("--out", type=Path, default=OUT, help="writes " + PATCH_PATH.as_posix() + " here")
parser.add_argument("--listing", type=Path, help="also write the new code, disassembled, to this file")
args = parser.parse_args()

sys.path.insert(0, str(HERE))
from upkpatch import Kit, cstr, inst, local, o, u16      # noqa: E402

kit = Kit(REPO, args.upk)
ref = kit.ref_of
mem_len = kit.mem_len


def native(n, *a): return bytes([n]) + b"".join(a) + b"\x16"
def gt(a, b): return native(0x97, a, b)                  # int >
def not_(a): return native(0x81, a)                      # !bool
def feq(a, b): return native(0xB4, a, b)                 # float ==
def to_string(e): return b"\x38\x53" + e                 # int -> string
def to_int(e): return b"\x38\x44" + e                    # float -> int
def to_byte(e): return b"\x38\x3d" + e                   # int -> byte
def divide(a, b): return native(0x91, a, b)              # int /
def modulo(a, b): return native(0xFD, a, b)              # int %
def intc(v): return b"\x1d" + struct.pack("<i", v)      # an int constant
def let(a, b): return b"\x0f" + a + b
def fconst(v): return b"\x1e" + struct.pack("<f", v)
ZERO = b"\x25"


def context(obj, prop, inner):
    """obj.<inner>, where inner names `prop` (the form the game's compiler writes)."""
    return b"\x19" + obj + u16(mem_len(inner)) + o(prop) + b"\x00" + inner


def member(field, struct_ref, of):
    """of.field, written (the struct is changed in place)."""
    return b"\x35" + o(field) + o(struct_ref) + b"\x00\x01" + of


def call(obj, name, returns, *params):
    """obj.name(params...), a call by name, as the compiler writes it."""
    body = b"\x1b" + struct.pack("<ii", kit.name_index(name), 0) + b"".join(params) + b"\x16"
    return context(obj, returns, body)


def byte_field(obj, path):
    return context(obj, ref(path), inst(ref(path)))


# ------------------------------------------------------------------ the pieces
post = kit.fn("BrgHUD.PostRender", (39, 1186, 802))
ui = inst(ref("BrgHUD.mUIManager"))
ginfo = local(ref("BrgHUD.PostRender.GInfo"))
director = context(ginfo, ref("BrgGameInfoNative.mAIDirector"), inst(ref("BrgGameInfoNative.mAIDirector")))
database = context(ginfo, ref("BrgGameInfoNative.mDatabase"), inst(ref("BrgGameInfoNative.mDatabase")))
alive = call(director, "GetAliveEnemyNum", ref("BrgAIDirector.GetAliveEnemyNum.ReturnValue"), b"\x4a")
start = call(director, "GetFloorStartEnemyNum", ref("BrgAIDirector.GetFloorStartEnemyNum.ReturnValue"))
hud_shown = call(ui, "CheckVisibleHUD", ref("BrgUIManager.CheckVisibleHUD.ReturnValue"))
# Back in the hub the director still holds the last floor's numbers, so 0.2.1
# showed "Enemies left: 0 / 20" there. The game's own hub check, as its music
# and radio scripts make it: GameInfo.mStageManager.IsInHub().
stage = context(ginfo, ref("BrgGameInfoNative.mStageManager"), inst(ref("BrgGameInfoNative.mStageManager")))
in_hub = call(stage, "IsInHub", ref("BrgStageManager.IsInHub.ReturnValue"))

# the four numbers, each read into its own unused float of the UI manager
slots = {key: context(ui, ref(f"BrgUIManager.{field}"), inst(ref(f"BrgUIManager.{field}")))
         for key, field in (("x", "mTestPosition"), ("y", "mTestPositionAdd"),
                            ("scale", "mTestAngle"), ("align", "mTestTime"),
                            ("colour", "mTestCounter"))}
read_float = ref("BrgDatabase.GetConstFloat.ReturnValue")
reads = b"".join(call(database, "GetConstFloat", read_float, cstr(CONSTS[key]), slots[key])
                 for key in ("x", "y", "scale", "align", "colour"))

# the font: the game's text font at the chosen size, white, with a dark shadow
render_prop = ref("BrgUIManager.mFontRender")
renderer = context(ui, render_prop, inst(render_prop))
FR = "BrgGameBase.BrgUIFontRender"
state = context(renderer, ref(f"{FR}.mState"), inst(ref(f"{FR}.mState")))
state_struct = ref(f"{FR}.BrgUIFontRenderState")
fonts = context(ui, ref("BrgGameBase.BrgUIManagerBase.mFont"), inst(ref("BrgGameBase.BrgUIManagerBase.mFont")))
# mFont is a fixed-size array (Font mFont[N]), so its element is read with
# EX_ArrayElement (0x1A), as the game's own PostRenderToOffscreen does. 0.2.0
# used EX_DynArrayElement (0x10), which takes the array for a dynamic one: it
# read a font pointer as the array's length and, whenever that came out
# negative, "grew" the array through garbage and crashed the game on arriving
# on a floor - at random, as it depends on where the fonts sit in memory.
font = (let(member(ref(f"{FR}.BrgUIFontRenderState.mFont"), state_struct, state),
            b"\x1a" + b"\x2c" + bytes([TEXT_FONT]) + fonts)
        + let(member(ref(f"{FR}.BrgUIFontRenderState.mScaleX"), state_struct, state), slots["scale"])
        + let(member(ref(f"{FR}.BrgUIFontRenderState.mScaleY"), state_struct, state), slots["scale"]))
colour = to_int(slots["colour"])
colours = call(
    renderer, "SetColor", 0,
    to_byte(divide(colour, intc(65536))),
    to_byte(modulo(divide(colour, intc(256)), intc(256))),
    to_byte(modulo(colour, intc(256))),
    b"\x24\xff",
) + b"".join(
    let(byte_field(renderer, f"{FR}.{field}"), b"\x24" + bytes([value]))
    for field, value in (("mShadowColorR", 0), ("mShadowColorG", 0), ("mShadowColorB", 0),
                         ("mShadowColorA", 255))
)

# the words: the game's own text call, as the stew menu makes it
top = kit.fn("BrgUIMenu_SkillExchange.SetGachaTopMenuType", (34, 1697, 1125))
buy = top.stmt_bytes(0x2F0, "(I:BrgUIMenu_SkillExchange.mGachaUIInfoArray[0])")
d = kit.Dis(buy, kit.names, kit.objs)
assert d.u8() == 0x0F
d.expr()
buy = buy[d.pos:]                                   # BrgUIManager.GetLocalizeTextST('...BUY')
assert buy[0] == 0x12 and buy[1] == 0x20 and buy[13] == 0x1B
words_call = (buy[13:22] + cstr(TEXT_KEY) + to_string(alive) + to_string(start)
              + b"\x4a" * 4 + b"\x16")
words = b"\x12" + buy[1:6] + u16(mem_len(words_call)) + buy[8:12] + buy[12:13] + words_call

draw = call(
    renderer, "DrawShadowString", 0,
    slots["x"], slots["y"], words,
    feq(slots["align"], fconst(2.0)),               # right of the point
    feq(slots["align"], fconst(1.0)),               # centred on it
    b"\x4a\x4a",
)

# ------------------------------------------------------------------ the change
drawn = post.stmt_bytes(0x3D9, "I:BrgHUD.mUIManager.{skip=0xb,None,0}RenderProcess(self)")
back = 0x3D9 + 0x20                                  # the statement after it
post.stmt(back, "cast<BrgGameInfo>(")
post.divert(0x3D9, "I:BrgHUD.mUIManager.", kit.asm()
            .code(drawn)
            .jumpifnot("done", hud_shown)
            .jumpifnot("done", native(0x77, director, b"\x2a"))     # mAIDirector != none
            .jumpifnot("done", gt(start, ZERO))
            .jumpifnot("done", not_(in_hub))
            .code(reads + font + colours + draw)
            .label("done")
            .jump(back))

out_file = args.out / PATCH_PATH
kit.write_patch([post], out_file)
if args.listing:
    args.listing.write_text(kit.listing([post]), encoding="utf-8")
