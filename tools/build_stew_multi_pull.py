"""Write the Stew Multi-Pull mod's change to BrgGame.upk as a .PackagePatch.

A maintenance tool. It adds "Purchase xN" to the Mushroom Club's stew menu.
That repeats the game's own single pull N times. Each pull is a normal one: it
costs the normal price, takes the next decal from the save's queue and is
saved. The stew animation plays for the first pull and the closing animation
once at the end, and every decal gets its own result card.

The mod lets the player choose N, so one patch is written for each choice, as
tfc/x<N>/Game/BrgGame/CookedPCConsole/BrgGame.upk.PackagePatch; mod.json picks
the folder with "source": "tfc/x{{count}}".

Two functions in BrgUIMenu_SkillExchange change, both by trampolines: an
existing statement is replaced by a jump to new code on the end of the
function, and that code jumps back. No existing jump target moves.

  SetGachaTopMenuType  the normal stew gets three entries: Purchase,
                       Purchase xN, Check Lineup
  Tick  state 23 (the menu)       the new entry; Check Lineup becomes entry 2
        state 14 (stew animation) skipped on repeat pulls
        state 16 (closing animation, then the top menu)
                                  skipped between pulls, which go straight on

The pull count lives in mReturnState, a byte the class declares and no script
uses, so the package's tables do not change.

Reads the game's stock BrgGame.upk and never changes it:

    py -3 tools/build_stew_multi_pull.py --upk PATH/TO/BrgGame.upk

The stock BrgGame.upk is the one Steam installs, or the copy the manager keeps
in backups/game_files once a mod has changed it.
"""
from __future__ import annotations

import argparse
import hashlib
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
OUT = REPO / "mods" / "Stew Multi Pull" / "tfc"
PATCH_PATH = Path("Game") / "BrgGame" / "CookedPCConsole" / "BrgGame.upk.PackagePatch"
COUNTS = (5, 10, 15, 20, 25)    # the mod's choice of pulls; one patch each, in tfc/x<N>
HEADER = 48                     # a function's data before its script
NO_ANIMATION = 11               # StartMatinee has no case for it: nothing plays, and it counts as finished
STATE_PULL = 13                 # GetStateAfterGacha's answer when the next pull can go ahead
REFERENCE_VERSION = 12          # the reference layout TFC Installer writes

parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
parser.add_argument("--upk", required=True, type=Path, help="the game's stock BrgGame.upk")
parser.add_argument("--out", type=Path, default=OUT, help="writes x<N>/" + PATCH_PATH.as_posix() + " here")
parser.add_argument("--counts", type=int, nargs="+", default=list(COUNTS), help="pulls per Purchase xN")
parser.add_argument("--listing", type=Path, help="also write the new code, disassembled, into this folder")
args = parser.parse_args()
if not all(2 <= n <= 255 for n in args.counts):
    sys.exit("every count must be 2 to 255")

sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HERE))
from lid_db_manager.upk import apply as A                   # noqa: E402
from lid_db_manager.upk import package as P                 # noqa: E402
from lid_db_manager.upk import packagepatch as PP           # noqa: E402
from ue3dis import Dis                                      # noqa: E402

raw = args.upk.read_bytes()
print(f"{args.upk.name}: SHA-1 {hashlib.sha1(raw).hexdigest()}")
summary, flat = P.decompressed(raw)
pkg = P.Package(summary, flat, P.read_names(flat, summary), P.read_imports(flat, summary),
                P.read_exports(flat, summary), source=args.upk)
names = lambda i: pkg.names[i].text   # noqa: E731
objs = lambda i: pkg.full_path(i).replace("BrgUIMenu_SkillExchange.", "")   # noqa: E731


def export(path: str) -> int:
    hits = [i + 1 for i in range(len(pkg.exports)) if pkg.full_path(i + 1) == path]
    if len(hits) != 1:
        sys.exit(f"{path}: {len(hits)} matches - is this BrgGame.upk?")
    return hits[0]


CLS = "BrgUIMenu_SkillExchange"
RS = export(f"{CLS}.mReturnState")
TEMPSTATE = export(f"{CLS}.Tick.tempState")
DEBUGMATINEE = export(f"{CLS}.Tick.debugMatinee")
GET_STATE_AFTER = export(f"{CLS}.GetStateAfterGacha")
START_MATINEE = export(f"{CLS}.StartMatinee")

o = lambda ref: struct.pack("<i", ref)      # noqa: E731
u16 = lambda v: struct.pack("<H", v)         # noqa: E731


def mem_len(chunk: bytes) -> int:
    d = Dis(chunk, names, objs)
    while d.pos < len(chunk):
        d.expr()
    return d.mem


# ------------------------------------------------------------ expression bits
def inst(ref): return b"\x01" + o(ref)
def local(ref): return b"\x00" + o(ref)
def intb(v): return b"\x2c" + bytes([v])
def byte(v): return b"\x24" + bytes([v])
def to_int(e): return b"\x38\x3a" + e                   # byte -> int
def to_byte(e): return b"\x38\x3d" + e                  # int -> byte
def to_string(e): return b"\x38\x53" + e                # int -> string
def native(n, *a): return bytes([n]) + b"".join(a) + b"\x16"
def let(a, b): return b"\x0f" + a + b
def fcall(ref, *a): return b"\x1c" + o(ref) + b"".join(a) + b"\x16"
def concat(a, b): return native(0x70, a, b)
def gt(a, b): return native(0x97, a, b)
def lt(a, b): return native(0x96, a, b)
def eq(a, b): return native(0x9A, a, b)
def ne(a, b): return native(0x9B, a, b)
def minus(a, b): return native(0x93, a, b)
def not_(a): return native(0x81, a)
ZERO, ONE = b"\x25", b"\x26"
rs = inst(RS)
count = to_int(rs)


class Asm:
    """Statements with forward labels; jump targets are memory offsets."""

    def __init__(self):
        self.parts = []

    def code(self, b): self.parts.append(("code", b)); return self
    def label(self, name): self.parts.append(("label", name)); return self
    def jump(self, to): self.parts.append(("jump", to, None)); return self
    def jumpifnot(self, to, cond): self.parts.append(("jumpifnot", to, cond)); return self

    def assemble(self, at: int) -> bytes:
        labels, pos = {}, at
        for p in self.parts:
            if p[0] == "label":
                labels[p[1]] = pos
            elif p[0] == "code":
                pos += mem_len(p[1])
            else:
                pos += 3 + (mem_len(p[2]) if p[2] else 0)
        out = b""
        for p in self.parts:
            if p[0] == "code":
                out += p[1]
            elif p[0] in ("jump", "jumpifnot"):
                to = labels[p[1]] if isinstance(p[1], str) else p[1]
                out += (b"\x06" if p[0] == "jump" else b"\x07") + u16(to) + (p[2] or b"")
        return out


class Fn:
    """One function's script, with code added before its END token."""

    def __init__(self, path, stock_sizes):
        self.ref = export(path)
        self.path = path
        self.data = pkg.export_data(self.ref - 1)
        self.mem, self.disk = struct.unpack_from("<ii", self.data, HEADER - 8)
        self.code = bytearray(self.data[HEADER:HEADER + self.disk])
        self.tail = self.data[HEADER + self.disk:]
        d = Dis(bytes(self.code), names, objs)
        self.lines = d.run()
        if (len(self.lines), self.mem, self.disk) != stock_sizes or self.lines[-1][4] != "END":
            sys.exit(f"{path} is not the one this was written for: {len(self.lines)} statements, "
                     f"{self.mem}/{self.disk} bytes. Is this the stock BrgGame.upk of game 1.89?")
        self.targets = {t for _, _, t in d.targets}
        self.by_mem = {line[1]: line for line in self.lines}
        self.added = b""
        self.added_at = self.mem - 1            # the END token's place
        self.replaced = []

    def stmt(self, at, starts_with):
        line = self.by_mem.get(at)
        if not line or not line[4].startswith(starts_with):
            sys.exit(f"{self.path} @{at:#x}: expected `{starts_with}`, found `{line and line[4]}`")
        return line

    def stmt_bytes(self, at, starts_with):
        d, m, dl, ml, _ = self.stmt(at, starts_with)
        return bytes(self.code[d:d + dl])

    def next_at(self):
        return self.added_at + mem_len(self.added)

    def add(self, asm: Asm) -> int:
        at = self.next_at()
        self.added += asm.assemble(at)
        return at

    def after(self, at, starts_with):
        _, m, _, ml, _ = self.stmt(at, starts_with)
        return m + ml

    def divert(self, at, starts_with, asm: Asm):
        """Replace the statement at `at` with a jump to `asm`, added on the end."""
        d, m, dl, ml, _ = self.stmt(at, starts_with)
        to = self.add(asm)
        refs, extra = divmod(ml - dl, 4)
        pad = dl - 3 - 5 * refs
        if pad < 0 or extra:
            sys.exit(f"{self.path} @{at:#x}: statement too short to replace")
        # an object constant is 4 bytes on disk and 8 in memory, so the filler
        # keeps both sizes of the statement it replaces
        self.code[d:d + dl] = b"\x06" + u16(to) + (b"\x20" + o(0)) * refs + b"\x0b" * pad
        self.replaced.append((m, m + ml))

    def poke(self, at, starts_with, offset, value: bytes):
        d, *_ = self.stmt(at, starts_with)
        self.code[d + offset:d + offset + len(value)] = value

    def finish(self):
        for lo, hi in self.replaced:
            if any(lo < t < hi for t in self.targets):
                sys.exit(f"{self.path}: a jump lands inside the replaced statement at {lo:#x}")
        code = bytes(self.code[:-1]) + self.added + b"\x53"
        d = Dis(code, names, objs)
        lines = d.run()
        if lines[-1][4] != "END" or d.pos != len(code):
            sys.exit(f"{self.path}: the new script does not read back cleanly")
        starts = {line[1] for line in lines}
        for _, kind, t in d.targets:
            if t not in starts:
                sys.exit(f"{self.path}: a {kind} to {t:#x} does not land on a statement")
        if d.mem > 0xFFFF:
            sys.exit(f"{self.path}: too long for 16-bit jumps")
        self.new_lines = lines
        self.used_names = {i for _, i in d.names}
        self.used_objects = {r for _, r in d.objs if r}
        self.new_data = self.data[:HEADER - 8] + struct.pack("<ii", d.mem, len(code)) + code + self.tail
        print(f"{self.path}: {self.mem}/{self.disk} -> {d.mem}/{len(code)} script bytes (memory/disk)")
        return self.new_data


def split_let(stmt: bytes):
    """`a = b` -> (a, b)."""
    d = Dis(stmt, names, objs)
    assert d.u8() == 0x0F
    d.expr()
    return stmt[1:d.pos], stmt[d.pos:]


# ------------------------------------------------------- SetGachaTopMenuType
def build(N: int) -> bytes:
    top = Fn(f"{CLS}.SetGachaTopMenuType", (34, 1697, 1125))
    three = top.stmt_bytes(0x2D0, "I:mGachaMenuSelectItemNum = 3")
    grow = top.stmt_bytes(0x2DC, "I:mGachaUIInfoArray.add(I:mGachaMenuSelectItemNum)")
    text0 = top.stmt_bytes(0x2F0, "(I:mGachaUIInfoArray[0]).SkillExchange_GachaUIInfo.mText")
    slot1, lineup = split_let(top.stmt_bytes(0xDB, "(I:mGachaUIInfoArray[1]).SkillExchange_GachaUIInfo.mText"))
    slot2, _ = split_let(top.stmt_bytes(0x3B1, "(I:mGachaUIInfoArray[2]).SkillExchange_GachaUIInfo.mText"))
    _, buy = split_let(text0)


    def localize(key: str, *params: bytes) -> bytes:
        """BrgUIManager.GetLocalizeTextST(key, params...), built like the BUY call it copies."""
        assert buy[0] == 0x12 and buy[1] == 0x20 and buy[13] == 0x1B
        left, retval, size, fn_name = buy[1:6], buy[8:12], buy[12:13], buy[13:22]
        call = (fn_name + b"\x1f" + key.encode("latin-1") + b"\0"
                + b"".join(params) + b"\x4a" * (6 - len(params)) + b"\x16")
        return b"\x12" + left + u16(mem_len(call)) + retval + size + call


    if localize("MSHR_KAIKAN.TXT_GACHA_BUY") != buy:
        sys.exit("could not rebuild the game's own GetLocalizeTextST call")
    # "Purchase x10": the game's own Buy text, then its own unused "x#0" text
    # (MSHR_KAIKAN.TXT_GACHA_TIMES, "#0倍" in Japanese), so every language reads right
    multi = concat(concat(buy, b"\x1f \x00"), localize("MSHR_KAIKAN.TXT_GACHA_TIMES", to_string(intb(N))))
    # the box stew's three-entry layout: bases and cursor guides v00, v01, v02
    layout = b"".join(top.stmt_bytes(at, "(I:mGachaUIInfoArray[") for at in (0x413, 0x476, 0x4D9, 0x53C, 0x59F, 0x603))
    top.stmt(0x66D, "jumpifnot 0x69e")                        # after the switch: clamp the cursor
    top.divert(0x5C, "I:mGachaMenuSelectItemNum = 2", Asm()
               .code(three + grow + text0)
               .code(let(slot1, multi))                                 # Purchase xN
               .code(let(slot2, lineup))
               .code(layout)
               .jump(0x66D))

    # ----------------------------------------------------------------------- Tick
    tick = Fn(f"{CLS}.Tick", (659, 15362, 11146))
    SOUND_OK = "I:BrgUIBase.mUIManager.{skip=0x14,None,0}AddPlaySoundCue(obj:SN_SE_SYS_MomokoShop.SN_SE_SYS_MomokoShop_OK"
    sound_ok = tick.stmt_bytes(0x1BD2, SOUND_OK)
    pull = tick.stmt_bytes(0x1BFB, "StartWait(0.0f, pcast0x3a(F:GetStateAfterGacha()))")
    hide_menu = tick.stmt_bytes(0x1C16, "F:SetVisibleGachaMenu(false)")
    is_box = tick.stmt_bytes(0x2928, "jumpifnot 0x2992 (bool((I:mCurrentGachaProperty).SkillExchange_GachaProperty.mIsBOXGacha")[3:]
    wait_temp = tick.stmt_bytes(0x2492, "StartWait(0.0f, pcast0x3a(L:Tick.tempState))")
    close_anim = tick.stmt_bytes(0x290F, "F:StartMatinee(8b)")
    to_top = tick.stmt_bytes(0x2992, "StartWait(0.0f, 3)")
    MENU_DONE = 0x1C77
    tick.stmt(MENU_DONE, "jump 0x1df9")

    # state 23, case 0 (Buy): clear the count. It goes after the sound, so the
    # new case's `mReturnState = N` is the only one the sound follows.
    tick.divert(0x1BD2, SOUND_OK, Asm().code(sound_ok + let(rs, byte(0))).jump(0x1BFB))
    # state 23: Lineup becomes entry 2, and a new case 1 joins the chain before it
    tick.poke(0x1C24, "case 1b: (next 0x1c71)", 3, byte(2))
    new_case = tick.add(Asm()
                        .code(b"\x0a" + u16(0x1C24) + byte(1))          # case 1:
                        .code(let(rs, byte(N)) + sound_ok + pull + hide_menu)
                        .jump(MENU_DONE))
    tick.poke(0x1BCD, "case 0b: (next 0x1c24)", 1, u16(new_case))

    # state 14: keep `debugMatinee = 10`, then on a repeat pull of a multi-pull
    # (0 < count < N, not a box stew) override the animation with one that does
    # not exist, so nothing plays. (Keeping the super-rare animation for 4- and
    # 5-star decals was tried in 0.2.0: in game it zoomed the camera out
    # mid-run, the previous pull's closing animation never having played.)
    back = tick.after(0x2763, "L:Tick.debugMatinee = 10b")
    tick.divert(0x2763, "L:Tick.debugMatinee = 10b", Asm()
                .code(tick.stmt_bytes(0x2763, "L:Tick.debugMatinee = 10b"))
                .jumpifnot("done", gt(count, ZERO))
                .jumpifnot("done", lt(count, intb(N)))
                .jumpifnot("done", not_(is_box))
                .code(let(local(DEBUGMATINEE), byte(NO_ANIMATION)))
                .label("done")
                .jump(back))

    # state 16, first tick: no closing animation while more pulls will follow
    back = tick.after(0x290F, "F:StartMatinee(8b)")
    tick.divert(0x290F, "F:StartMatinee(8b)", Asm()
                .jumpifnot("close", gt(count, ONE))
                .jumpifnot("close", not_(is_box))
                .jumpifnot("close", eq(to_int(fcall(GET_STATE_AFTER)), intb(STATE_PULL)))
                .code(fcall(START_MATINEE, byte(NO_ANIMATION)))
                .jump(back)
                .label("close")
                .code(close_anim)
                .jump(back))

    # state 16, normal stew: the next pull instead of the top menu. If it cannot
    # go ahead, GetStateAfterGacha's own answer (no KC, decals full, ...) shows.
    back = tick.after(0x2992, "StartWait(0.0f, 3)")
    tick.divert(0x2992, "StartWait(0.0f, 3)", Asm()
                .jumpifnot("last", gt(count, ONE))
                .code(let(rs, to_byte(minus(count, ONE))))
                .code(let(local(TEMPSTATE), fcall(GET_STATE_AFTER)))
                .jumpifnot("go", ne(to_int(local(TEMPSTATE)), intb(STATE_PULL)))
                .code(let(rs, byte(0)))
                .label("go")
                .code(wait_temp)
                .jump(back)
                .label("last")
                .code(let(rs, byte(0)) + to_top)
                .jump(back))

    functions = [top, tick]
    for fn in functions:
        fn.finish()


    # ---------------------------------------------------------------- the patch
    def fstring(text: str) -> bytes:
        encoded = text.encode("latin-1") + b"\0"
        return struct.pack("<i", len(encoded)) + encoded


    def class_of(ref: int) -> tuple[str, str]:
        if ref < 0:
            imp = pkg.imports[-ref - 1]
            return pkg.name(imp.class_name, imp.class_name_number), \
                pkg.name(imp.class_package, imp.class_package_number)
        class_index = pkg.exports[ref - 1].class_index
        return (pkg.object_name(class_index) if class_index else "Class"), ""


    def path_of(ref: int) -> str:
        return pkg.full_path(ref).replace(".", "\\")


    used_objects = {fn.ref for fn in functions}
    used_names = set()
    for fn in functions:
        used_objects |= fn.used_objects
        used_names |= fn.used_names
    object_refs = sorted(used_objects, key=lambda r: (r < 0, abs(r)))
    name_refs = sorted(used_names)

    out = bytearray()
    out += struct.pack("<i", 2)
    for table in (pkg.names, pkg.imports, pkg.exports):
        out += struct.pack("<ii", len(table), 0)
    out += struct.pack("<i", len(functions))
    for fn in functions:
        out += struct.pack("<ii", fn.ref - 1, 0)
        out += struct.pack("<i", len(fn.new_data)) + fn.new_data
    out += struct.pack("<i", REFERENCE_VERSION)
    out += struct.pack("<i", len(name_refs))
    for i in name_refs:
        out += struct.pack("<i", i) + fstring(pkg.names[i].text)
    out += struct.pack("<i", len(object_refs))
    for ref in object_refs:
        class_name, class_package = class_of(ref)
        out += struct.pack("<i", ref) + fstring(path_of(ref))
        if ref < 0:
            out += fstring(path_of(ref))
        out += fstring(class_name)
        if ref < 0:
            out += fstring(class_package)
    patch_bytes = bytes(out)

    # ------------------------------------------------- check it before writing it
    patch = PP.read(patch_bytes)
    # apply() moves the export entries it is given, so it gets its own copy of
    # the table and `pkg` stays the stock package for the next count
    fresh = P.Package(summary, flat, pkg.names, pkg.imports, P.read_exports(flat, summary),
                      source=args.upk)
    if A.problems(fresh, patch):
        sys.exit("the patch does not fit: " + "; ".join(A.problems(fresh, patch)))
    applied, _ = A.apply(fresh, patch)
    expected = bytearray(flat)
    entries = P.read_exports(flat, summary)
    for fn in functions:
        e = entries[fn.ref - 1]
        expected[e.serial_offset:e.serial_offset + e.serial_size] = bytes(e.serial_size)
    for fn in functions:
        at = len(expected)
        expected += fn.new_data
        A._place_entry(expected, entries[fn.ref - 1], at, len(fn.new_data))
    if applied != bytes(expected):
        sys.exit("applying the patch does not give the expected package")
    # and the applied package reads back with both new functions in it
    re_exports = P.read_exports(applied, summary)
    for fn in functions:
        e = re_exports[fn.ref - 1]
        if bytes(applied[e.serial_offset:e.serial_offset + e.serial_size]) != fn.new_data:
            sys.exit(f"{fn.path}: not where its export entry says")

    out_file = args.out / f"x{N}" / PATCH_PATH
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_bytes(patch_bytes)
    print(f"x{N}: checks {len(name_refs)} name(s), {len(object_refs)} object(s); wrote {out_file} "
          f"({len(patch_bytes):,} bytes), SHA-256 {hashlib.sha256(patch_bytes).hexdigest()}")

    if args.listing:
        args.listing.mkdir(parents=True, exist_ok=True)
        with open(args.listing / f"x{N}.txt", "w", encoding="utf-8") as f:
            for fn in functions:
                f.write(f"# {fn.path}\n")
                changed = {m for m, _ in fn.replaced}
                for d, m, dl, ml, t in fn.new_lines:
                    if m >= fn.added_at or m in changed or m in (0x1BCD, 0x1C24):
                        f.write(f"{m:04x} {t}\n")
    return patch_bytes


for n in args.counts:
    build(n)
