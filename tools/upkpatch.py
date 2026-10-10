"""Shared machinery for writing BrgGame.upk .PackagePatch files.

A .PackagePatch holds only the changed functions, not the game's package. LiD
DB Editor applies it to the player's own BrgGame.upk (the format is FCH823's,
from TFC Installer). It names every object and name the new code relies on, so
it is refused, not forced, on a package where any of them moved.

    kit = Kit(manager_checkout, stock_upk)
    fn = kit.fn("BrgUIMenu_Insurance.GetVIPPassPrice", (4, 115, 83))
    fn.poke_once(0x4B, "return (", old_bytes, new_bytes)
    kit.write_patch([fn.finish_()], out_path)

Every change is checked before anything is written: a function must have the
stock sizes it was written against, a statement must start with the text it
was written against, and the patch is applied to a copy of the package to
prove it lands where its export entry says.
"""
from __future__ import annotations

import hashlib
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
HEADER = 48                     # a function's data before its script
REFERENCE_VERSION = 12          # the reference layout TFC Installer writes

o = lambda ref: struct.pack("<i", ref)   # noqa: E731
u16 = lambda v: struct.pack("<H", v)     # noqa: E731


def inst(ref): return b"\x01" + o(ref)
def local(ref): return b"\x00" + o(ref)
def intb(v): return b"\x2c" + bytes([v])
def byte(v): return b"\x24" + bytes([v])
def cstr(text): return b"\x1f" + text.encode("latin-1") + b"\0"


def fstring(text: str) -> bytes:
    encoded = text.encode("latin-1") + b"\0"
    return struct.pack("<i", len(encoded)) + encoded


class Kit:
    """The stock package, read once, and what is built from it."""

    def __init__(self, manager: Path, upk: Path):
        sys.path.insert(0, str(Path(manager).resolve()))
        sys.path.insert(0, str(HERE))
        from lid_db_manager.upk import apply as A
        from lid_db_manager.upk import package as P
        from lid_db_manager.upk import packagepatch as PP
        from ue3dis import Dis
        self.A, self.P, self.PP, self.Dis = A, P, PP, Dis
        self.upk = Path(upk)
        raw = self.upk.read_bytes()
        print(f"{self.upk.name}: SHA-1 {hashlib.sha1(raw).hexdigest()}")
        self.summary, self.flat = P.decompressed(raw)
        self.pkg = P.Package(self.summary, self.flat, P.read_names(self.flat, self.summary),
                             P.read_imports(self.flat, self.summary),
                             P.read_exports(self.flat, self.summary), source=self.upk)

    # -- reading ----------------------------------------------------------

    def names(self, i): return self.pkg.names[i].text
    def objs(self, i): return self.pkg.full_path(i)

    def ref_of(self, path: str) -> int:
        """The object reference (export > 0, import < 0) with this full path."""
        pkg = self.pkg
        hits = [i + 1 for i in range(len(pkg.exports)) if pkg.full_path(i + 1) == path]
        hits += [-(i + 1) for i in range(len(pkg.imports)) if pkg.full_path(-(i + 1)) == path]
        if len(hits) != 1:
            sys.exit(f"{path}: {len(hits)} matches - is this BrgGame.upk?")
        return hits[0]

    def name_index(self, name: str) -> int:
        hits = [i for i, n in enumerate(self.pkg.names) if n.text == name]
        if len(hits) != 1:
            sys.exit(f"{name}: {len(hits)} names - is this BrgGame.upk?")
        return hits[0]

    def virtual_call(self, name: str) -> bytes:
        """name() on self, with no parameters."""
        return b"\x1b" + struct.pack("<ii", self.name_index(name), 0) + b"\x16"

    def mem_len(self, chunk: bytes) -> int:
        d = self.Dis(chunk, self.names, self.objs)
        while d.pos < len(chunk):
            d.expr()
        return d.mem

    def asm(self) -> "Asm":
        return Asm(self)

    def fn(self, path: str, stock_sizes: tuple[int, int, int]) -> "Fn":
        return Fn(self, path, stock_sizes)

    # -- writing ----------------------------------------------------------

    def _class_of(self, ref: int) -> tuple[str, str]:
        pkg = self.pkg
        if ref < 0:
            imp = pkg.imports[-ref - 1]
            return pkg.name(imp.class_name, imp.class_name_number), \
                pkg.name(imp.class_package, imp.class_package_number)
        class_index = pkg.exports[ref - 1].class_index
        return (pkg.object_name(class_index) if class_index else "Class"), ""

    def _path_of(self, ref: int) -> str:
        return self.pkg.full_path(ref).replace(".", "\\")

    def write_patch(self, functions: list["Fn"], out_path: Path) -> str:
        """Write the .PackagePatch, after checking that applying it gives what it should."""
        pkg = self.pkg
        for fn in functions:
            if fn.new_data is None:
                fn.finish()
        used_objects = {fn.ref for fn in functions}
        used_names: set[int] = set()
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
            class_name, class_package = self._class_of(ref)
            out += struct.pack("<i", ref) + fstring(self._path_of(ref))
            if ref < 0:
                out += fstring(self._path_of(ref))
            out += fstring(class_name)
            if ref < 0:
                out += fstring(class_package)
        patch_bytes = bytes(out)

        # check it before writing it
        A, P, PP = self.A, self.P, self.PP
        patch = PP.read(patch_bytes)
        fresh = P.Package(self.summary, self.flat, pkg.names, pkg.imports,
                          P.read_exports(self.flat, self.summary), source=self.upk)
        if A.problems(fresh, patch):
            sys.exit("the patch does not fit: " + "; ".join(A.problems(fresh, patch)))
        applied, _ = A.apply(fresh, patch)
        expected = bytearray(self.flat)
        entries = P.read_exports(self.flat, self.summary)
        for fn in functions:
            # no bigger: written over the old one in place; bigger: added on the end
            e = entries[fn.ref - 1]
            old_at, old_size, new = e.serial_offset, e.serial_size, fn.new_data
            if len(new) <= old_size:
                expected[old_at:old_at + old_size] = new + bytes(old_size - len(new))
                at = old_at
            else:
                expected[old_at:old_at + old_size] = bytes(old_size)
                at = len(expected)
                expected += new
            A._place_entry(expected, e, at, len(new))
        if applied != bytes(expected):
            sys.exit("applying the patch does not give the expected package")
        re_exports = P.read_exports(applied, self.summary)
        for fn in functions:
            e = re_exports[fn.ref - 1]
            if bytes(applied[e.serial_offset:e.serial_offset + e.serial_size]) != fn.new_data:
                sys.exit(f"{fn.path}: not where its export entry says")

        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(patch_bytes)
        digest = hashlib.sha256(patch_bytes).hexdigest()
        print(f"checks {len(name_refs)} name(s), {len(object_refs)} object(s); wrote {out_path} "
              f"({len(patch_bytes):,} bytes), SHA-256 {digest}")
        return digest

    def listing(self, functions: list["Fn"]) -> str:
        """The changed and added statements, disassembled."""
        lines = []
        for fn in functions:
            lines.append(f"# {fn.path}")
            changed = {m for m, _ in fn.replaced} | fn.poked
            for d, m, dl, ml, t in fn.new_lines:
                if m >= fn.added_at or m in changed:
                    lines.append(f"{m:04x} {t}")
        return "\n".join(lines) + "\n"


class Asm:
    """Statements with forward labels; jump targets are memory offsets."""

    def __init__(self, kit: Kit):
        self.kit = kit
        self.parts = []

    def code(self, b): self.parts.append(("code", b)); return self
    def label(self, name): self.parts.append(("label", name)); return self
    def jump(self, to): self.parts.append(("jump", to, None)); return self
    def jumpifnot(self, to, cond): self.parts.append(("jumpifnot", to, cond)); return self

    def assemble(self, at: int) -> bytes:
        mem_len = self.kit.mem_len
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

    def __init__(self, kit: Kit, path: str, stock_sizes: tuple[int, int, int]):
        self.kit = kit
        self.ref = kit.ref_of(path)
        self.path = path
        self.data = kit.pkg.export_data(self.ref - 1)
        self.mem, self.disk = struct.unpack_from("<ii", self.data, HEADER - 8)
        self.code = bytearray(self.data[HEADER:HEADER + self.disk])
        self.tail = self.data[HEADER + self.disk:]
        d = kit.Dis(bytes(self.code), kit.names, kit.objs)
        self.lines = d.run()
        if (len(self.lines), self.mem, self.disk) != tuple(stock_sizes) or self.lines[-1][4] != "END":
            sys.exit(f"{path} is not the one this was written for: {len(self.lines)} statements, "
                     f"{self.mem}/{self.disk} bytes. Is this the stock BrgGame.upk of game 1.89?")
        self.targets = {t for _, _, t in d.targets}
        self.by_mem = {line[1]: line for line in self.lines}
        self.added = b""
        self.added_at = self.mem - 1            # the END token's place
        self.replaced = []
        self.poked: set[int] = set()
        self.new_data = None

    def stmt(self, at, starts_with):
        line = self.by_mem.get(at)
        if not line or not line[4].startswith(starts_with):
            sys.exit(f"{self.path} @{at:#x}: expected `{starts_with}`, found `{line and line[4]}`")
        return line

    def stmt_bytes(self, at, starts_with):
        d, m, dl, ml, _ = self.stmt(at, starts_with)
        return bytes(self.code[d:d + dl])

    def add(self, asm: Asm) -> int:
        at = self.added_at + self.kit.mem_len(self.added)
        self.added += asm.assemble(at)
        return at

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

    def poke(self, at, starts_with, old: bytes, new: bytes, count: int = 1):
        """Change every occurrence of `old` in the statement at `at` - exactly
        `count` of them, or nothing is written."""
        d, m, dl, ml, _ = self.stmt(at, starts_with)
        body = bytes(self.code[d:d + dl])
        if body.count(old) != count or len(old) != len(new):
            sys.exit(f"{self.path} @{at:#x}: {old.hex()} is there {body.count(old)} time(s), "
                     f"not {count}")
        self.code[d:d + dl] = body.replace(old, new)
        self.poked.add(m)

    def finish(self):
        kit = self.kit
        for lo, hi in self.replaced:
            if any(lo < t < hi for t in self.targets):
                sys.exit(f"{self.path}: a jump lands inside the replaced statement at {lo:#x}")
        code = bytes(self.code[:-1]) + self.added + b"\x53"
        d = kit.Dis(code, kit.names, kit.objs)
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
