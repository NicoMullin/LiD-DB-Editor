"""A UnrealScript bytecode reader for LET IT DIE's packages (version 861).

Used by the tools that change a function in BrgGame.upk. Object references are
4 bytes on disk and 8 in memory; names are 8 in both. Jump targets and skip
counts are measured in memory bytes.

Every statement comes back as (disk, mem, disk_len, mem_len, text). Every
absolute jump target the script holds is collected in ``targets`` with the
disk position of its u16, and every object and name reference in ``objs`` and
``names``, so a caller can check a patch names everything it relies on.
"""
import struct

OBJ_DISK, OBJ_MEM = 4, 8


class Dis:
    def __init__(self, code: bytes, name_of=None, obj_of=None, base_mem=0):
        self.code = code
        self.pos = 0
        self.mem = base_mem
        self.name_of = name_of or (lambda i: f"name#{i}")
        self.obj_of = obj_of or (lambda i: f"obj#{i}")
        self.targets = []      # (disk pos of the u16, kind, target mem)
        self.objs = []         # (disk pos, ref)
        self.names = []        # (disk pos, index)

    def u8(self):
        v = self.code[self.pos]; self.pos += 1; self.mem += 1; return v

    def u16(self):
        v = struct.unpack_from("<H", self.code, self.pos)[0]; self.pos += 2; self.mem += 2; return v

    def target(self, kind):
        at = self.pos
        v = self.u16()
        self.targets.append((at, kind, v))
        return v

    def i32(self):
        v = struct.unpack_from("<i", self.code, self.pos)[0]; self.pos += 4; self.mem += 4; return v

    def f32(self):
        v = struct.unpack_from("<f", self.code, self.pos)[0]; self.pos += 4; self.mem += 4; return v

    def obj(self):
        v = struct.unpack_from("<i", self.code, self.pos)[0]
        self.objs.append((self.pos, v))
        self.pos += OBJ_DISK; self.mem += OBJ_MEM
        return self.obj_of(v) if v else "None"

    def name(self):
        i, n = struct.unpack_from("<ii", self.code, self.pos)
        self.names.append((self.pos, i))
        self.pos += 8; self.mem += 8
        return self.name_of(i) + (f"_{n - 1}" if n else "")

    def cstr(self):
        end = self.code.index(b"\0", self.pos)
        s = self.code[self.pos:end].decode("latin-1")
        n = end + 1 - self.pos
        self.pos += n; self.mem += n
        return s

    def wstr(self):
        start = self.pos
        while self.code[self.pos:self.pos + 2] != b"\0\0":
            self.pos += 2
        s = self.code[start:self.pos].decode("utf-16-le")
        self.pos += 2
        self.mem += self.pos - start
        return s

    def params(self):
        out = []
        while self.code[self.pos] != 0x16:
            out.append(self.expr())
        self.u8()
        return out

    def expr(self):
        t = self.u8()
        if t == 0x00: return f"L:{self.obj()}"
        if t == 0x01: return f"I:{self.obj()}"
        if t == 0x02: return f"D:{self.obj()}"
        if t == 0x04: return f"return {self.expr()}"
        if t == 0x05:
            p = self.obj(); size = self.u8(); return f"switch<{p},{size}>({self.expr()})"
        if t == 0x06: return f"jump 0x{self.target('jump'):x}"
        if t == 0x07:
            to = self.target("jumpifnot"); return f"jumpifnot 0x{to:x} ({self.expr()})"
        if t == 0x08: return "stop"
        if t == 0x09:
            line = self.u16(); dbg = self.u8(); return f"assert({line},{dbg}) {self.expr()}"
        if t == 0x0A:
            at = self.pos
            nxt = self.u16()
            if nxt == 0xFFFF:
                return "default:"
            self.targets.append((at, "case", nxt))
            return f"case {self.expr()}: (next 0x{nxt:x})"
        if t == 0x0B: return "nothing"
        if t == 0x0D: return f"goto {self.expr()}"
        if t == 0x0E: return f"eat<{self.obj()}> {self.expr()}"
        if t == 0x0F: return f"{self.expr()} = {self.expr()}"
        if t == 0x10:
            i = self.expr(); a = self.expr(); return f"{a}[{i}]"
        if t == 0x11:
            parts = [self.expr() for _ in range(5)]; return f"new({', '.join(parts)})"
        if t in (0x12, 0x19):
            left = self.expr(); skip = self.u16(); field = self.obj(); size = self.u8()
            right = self.expr()
            return f"{left}.{{skip=0x{skip:x},{field},{size}}}{right}" if t == 0x19 else \
                f"{left}::{{skip=0x{skip:x},{field},{size}}}{right}"
        if t == 0x13:
            c = self.obj(); return f"metacast<{c}>({self.expr()})"
        if t == 0x14: return f"{self.expr()} =b {self.expr()}"
        if t == 0x15: return "endparmvalue"
        if t == 0x16: return ")"
        if t == 0x17: return "self"
        if t == 0x18:
            s = self.u16(); return f"skip<0x{s:x}>({self.expr()})"
        if t == 0x1A:
            i = self.expr(); a = self.expr(); return f"{a}[{i}]"
        if t == 0x1B:
            n = self.name(); return f"{n}({', '.join(self.params())})"
        if t == 0x1C:
            f = self.obj(); return f"F:{f}({', '.join(self.params())})"
        if t == 0x1D: return f"{self.i32()}"
        if t == 0x1E: return f"{self.f32()}f"
        if t == 0x1F: return repr(self.cstr())
        if t == 0x20: return f"obj:{self.obj()}"
        if t == 0x21: return f"name:{self.name()}"
        if t == 0x22: return f"rot({self.i32()},{self.i32()},{self.i32()})"
        if t == 0x23: return f"vect({self.f32()},{self.f32()},{self.f32()})"
        if t == 0x24: return f"{self.u8()}b"
        if t == 0x25: return "0"
        if t == 0x26: return "1"
        if t == 0x27: return "true"
        if t == 0x28: return "false"
        if t == 0x29: return f"nativeparm {self.obj()}"
        if t == 0x2A: return "none"
        if t == 0x2C: return f"{self.u8()}"
        if t == 0x2D: return f"bool({self.expr()})"
        if t == 0x2E:
            c = self.obj(); return f"cast<{c}>({self.expr()})"
        if t == 0x2F:
            e = self.expr(); end = self.target("iterator"); return f"iterator {e} end=0x{end:x}"
        if t == 0x30: return "iteratorpop"
        if t == 0x31: return "iteratornext"
        if t in (0x32, 0x33):
            s = self.obj(); a = self.expr(); b = self.expr()
            return f"structcmp{'==' if t == 0x32 else '!='}<{s}>({a}, {b})"
        if t == 0x34: return repr(self.wstr())
        if t == 0x35:
            field = self.obj(); st = self.obj(); a = self.u8(); b = self.u8()
            return f"({self.expr()}).{field}[{a},{b}]"
        if t == 0x36: return f"len({self.expr()})"
        if t == 0x37:
            n = self.name(); return f"global.{n}({', '.join(self.params())})"
        if t == 0x38:
            k = self.u8(); return f"pcast{k:#x}({self.expr()})"
        if t == 0x39:
            a = self.expr(); i = self.expr(); n = self.expr(); self.u8()
            return f"{a}.insert({i},{n})"
        if t == 0x3A: return f"returnnothing<{self.obj()}>"
        if t in (0x3B, 0x3C, 0x3D, 0x3E):
            a = self.expr(); b = self.expr(); self.u8()
            return f"delcmp{t:#x}({a},{b})"
        if t == 0x3F: return "emptydelegate"
        if t == 0x40:
            a = self.expr(); i = self.expr(); n = self.expr(); self.u8()
            return f"{a}.remove({i},{n})"
        if t == 0x41:
            v = self.i32(); line = self.i32(); p = self.i32(); op = self.u8()
            return f"debuginfo({v},{line},{p},{op})"
        if t == 0x42:
            local = self.u8(); p = self.obj(); n = self.name()
            return f"delegatefn({local},{p},{n})"
        if t == 0x43:
            n = self.name(); p = self.obj(); return f"delegateprop({n},{p})"
        if t == 0x44: return f"{self.expr()} =d {self.expr()}"
        if t == 0x45:
            c = self.expr(); s1 = self.u16(); a = self.expr(); s2 = self.u16(); b = self.expr()
            return f"({c} ? {a} : {b})"
        if t == 0x46:
            a = self.expr(); s = self.u16(); v = self.expr(); self.u8()
            return f"{a}.find({v})"
        if t == 0x47:
            a = self.expr(); s = self.u16(); f = self.expr(); v = self.expr(); self.u8()
            return f"{a}.find({f},{v})"
        if t == 0x48: return f"O:{self.obj()}"
        if t == 0x49:
            s = self.u16(); e = self.expr(); self.u8(); return f"defaultparm({e})"
        if t == 0x4A: return "emptyparm"
        if t == 0x4B: return f"instdelegate {self.name()}"
        if t == 0x51: return f"interfacecontext({self.expr()})"
        if t == 0x52:
            c = self.obj(); return f"interfacecast<{c}>({self.expr()})"
        if t == 0x53: return "END"
        if t == 0x54:
            a = self.expr(); v = self.expr(); self.u8()
            return f"{a}.add({v})"
        if t in (0x55, 0x56):
            a = self.expr(); s = self.u16(); v = self.expr(); self.u8()
            return f"{a}.{ {0x55: 'additem', 0x56: 'removeitem'}[t]}({v})"
        if t == 0x57:
            a = self.expr(); s = self.u16(); i = self.expr(); v = self.expr(); self.u8()
            return f"{a}.insertitem({i},{v})"
        if t == 0x58:
            a = self.expr(); item = self.expr(); has = self.u8(); idx = self.expr()
            end = self.target("foreach")
            return f"foreach {a} -> {item} idx={idx if has else '-'} end=0x{end:x}"
        if t == 0x59:
            a = self.expr(); s = self.u16(); f = self.expr(); self.u8()
            return f"{a}.sort({f})"
        if t == 0x5A:
            s = self.u16(); return f"editoronly<0x{s:x}>"
        if 0x60 <= t <= 0x6F:
            idx = ((t - 0x60) << 8) | self.u8()
            return f"N{idx}({', '.join(self.params())})"
        if t >= 0x70:
            return f"N{t}({', '.join(self.params())})"
        raise ValueError(f"unknown token 0x{t:02x} at disk {self.pos - 1} mem {self.mem - 1}")

    def run(self):
        lines = []
        while self.pos < len(self.code):
            d, m = self.pos, self.mem
            text = self.expr()
            lines.append((d, m, self.pos - d, self.mem - m, text))
            if text == "END":
                break
        return lines
