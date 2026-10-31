"""Two mods changing the same UnrealScript function, both kept.

A .PackagePatch replaces whole functions, so two mods changing one function
used to be a clash the later one won: Timers and Vending Machine's ammo refill
both change the shop's TickASync, and Stew Multi Pull and its decal entry both
change the stew screen's Tick and Render. Where each mod only swaps stock
statements for jumps to code it adds at the end, the two are merged instead.
"""

from __future__ import annotations

import os
import struct
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from lid_db_manager.upk import apply as A
from lid_db_manager.upk import packagepatch as PP
from lid_db_manager.upk import scriptmerge as SM
from lid_db_manager.upk.ue3script import Dis
from test_package_stacking import a_package_patch, a_package_patch_mod

PROJECT_ROOT = Path(__file__).resolve().parent.parent
HEAD, TAIL = b"H" * 40, b"tail"


def let(local: int, value: int) -> bytes:
    """local = value: 8 bytes on disk, 12 in memory (one object)."""
    return b"\x0f\x00" + struct.pack("<i", local) + b"\x2c" + bytes([value])


def jump(to: int) -> bytes:
    return b"\x06" + struct.pack("<H", to)


def jumpifnot(to: int) -> bytes:
    return b"\x07" + struct.pack("<H", to) + b"\x27"


RETURN, END = b"\x04\x0b", b"\x53"


def function(code: bytes) -> bytes:
    d = Dis(code)
    d.run()
    return HEAD + struct.pack("<ii", d.mem, len(code)) + code + TAIL


def jump_out(to: int) -> bytes:
    """A jump in place of one let: the same 8 bytes on disk and 12 in memory."""
    return jump(to) + b"\x20" + bytes(4)


# stock: let 1 (mem 0), let 2 (mem 12), return (mem 24), END (mem 26)
STOCK = function(let(1, 1) + let(2, 2) + RETURN + END)
ADDED_AT = 26


def first_mod() -> bytes:
    """Swaps the first let: sets local 3, does the let, jumps back."""
    return function(jump_out(ADDED_AT) + let(2, 2) + RETURN
                    + let(3, 3) + let(1, 1) + jump(12) + END)


def second_mod() -> bytes:
    """Swaps the second let, with a jump inside its own added code."""
    added = jumpifnot(ADDED_AT + 4) + b"\x0b" + let(4, 4) + let(2, 2) + jump(24)
    return function(let(1, 1) + jump_out(ADDED_AT) + RETURN + added + END)


class Merging(unittest.TestCase):
    def test_both_changes_are_kept(self) -> None:
        merged = SM.merge(STOCK, first_mod(), second_mod())
        shift = 27                      # the first one's added code, in memory
        added_b = jumpifnot(ADDED_AT + 4 + shift) + b"\x0b" + let(4, 4) + let(2, 2) + jump(24)
        self.assertEqual(merged, function(
            jump_out(ADDED_AT) + jump_out(ADDED_AT + shift) + RETURN
            + let(3, 3) + let(1, 1) + jump(12) + added_b + END))

    def test_either_order_works(self) -> None:
        merged = SM.merge(STOCK, second_mod(), first_mod())
        self.assertIsNotNone(merged)
        self.assertNotEqual(merged, SM.merge(STOCK, first_mod(), second_mod()))
        self.assertEqual(SM.merge(STOCK, STOCK, first_mod()), first_mod())

    def test_the_same_statement_changed_by_both_is_not(self) -> None:
        other = function(jump_out(ADDED_AT) + let(2, 2) + RETURN + let(5, 5) + let(1, 1)
                         + jump(12) + END)
        self.assertIsNone(SM.merge(STOCK, first_mod(), other))

    def test_a_change_that_moves_the_stock_code_is_not(self) -> None:
        inserted = function(let(9, 9) + let(1, 1) + let(2, 2) + RETURN + END)
        self.assertIsNone(SM.merge(STOCK, inserted, first_mod()))
        self.assertIsNone(SM.merge(STOCK, first_mod(), inserted))

    def test_nor_one_with_another_header_or_tail(self) -> None:
        other = HEAD[:-1] + b"X" + first_mod()[40:]
        self.assertIsNone(SM.merge(STOCK, other, second_mod()))
        self.assertIsNone(SM.merge(STOCK, first_mod()[:-1] + b"X", second_mod()))

    def test_one_jump_can_stand_for_two_short_statements(self) -> None:
        """super.TickASync(DeltaTime) is 11 bytes holding two objects - no room
        for the 13 a jump and its filler need - so the next one goes with it."""
        call = b"\x1c" + bytes(4) + b"\x00" + bytes(4) + b"\x16"     # 11 disk, 19 mem
        stock = function(call + let(2, 2) + RETURN + END)           # added at 33
        out = jump(33) + (b"\x20" + bytes(4)) * 3 + b"\x0b"         # 3 objects, as replaced
        self.assertEqual(len(out), 19)
        mod = function(out + RETURN + let(7, 7) + call + let(2, 2) + jump(31) + END)
        merged = SM.merge(stock, mod, function(call + let(2, 2) + RETURN + END))
        self.assertEqual(merged, mod)


class FakeEntry(SimpleNamespace):
    pass


class FakePackage:
    """Just enough package for apply(): one export, its table entry at the front."""

    def __init__(self, body: bytes):
        self.data = struct.pack("<iii", 0, len(body), 16) + bytes(4) + body
        self.exports = [FakeEntry(serial_offset=16, serial_size=len(body), serial_offset_at=8)]
        self.names, self.imports = [], []

    def export_data(self, index: int) -> bytes:
        e = self.exports[index]
        return self.data[e.serial_offset:e.serial_offset + e.serial_size]


class ApplyingOnTopOfAnother(unittest.TestCase):
    def patch(self, body: bytes) -> PP.PackagePatch:
        return PP.read(a_package_patch([(0, body)]))

    def test_with_the_stock_function_to_hand_both_land(self) -> None:
        package = FakePackage(first_mod())
        flat, _ = A.apply(package, self.patch(second_mod()), lambda i: STOCK)
        self.assertEqual(FakePackage.export_data(SimpleNamespace(data=flat, exports=package.exports), 0),
                         SM.merge(STOCK, first_mod(), second_mod()))

    def test_without_it_the_later_one_wins_as_before(self) -> None:
        package = FakePackage(first_mod())
        flat, _ = A.apply(package, self.patch(second_mod()))
        self.assertIn(second_mod(), flat)

    def test_and_where_they_do_not_combine(self) -> None:
        clash = function(jump_out(ADDED_AT) + let(2, 2) + RETURN + let(5, 5) + let(1, 1)
                         + jump(12) + END)
        package = FakePackage(first_mod())
        flat, _ = A.apply(package, self.patch(clash), lambda i: STOCK)
        self.assertIn(clash, flat)


class TheConflictList(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.mods = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def clashes(self, mods, stock_body=None) -> list:
        from lid_db_manager.conflict import KIND_OBJECT, analyze
        return [c for c in analyze(mods, stock_body=stock_body).conflicts if c.kind == KIND_OBJECT]

    def test_two_that_combine_are_not_a_clash(self) -> None:
        one = a_package_patch_mod(self.mods, "one", a_package_patch([(5, first_mod())]))
        two = a_package_patch_mod(self.mods, "two", a_package_patch([(5, second_mod())]))
        stock = lambda package, index: STOCK if (package, index) == ("brggame.upk", 5) else None  # noqa: E731
        self.assertEqual(self.clashes([one, two], stock), [])
        # without the game's own function to compare with, it still is
        self.assertEqual(len(self.clashes([one, two])), 1)

    def test_two_that_do_not_still_are(self) -> None:
        clash = function(jump_out(ADDED_AT) + let(2, 2) + RETURN + let(5, 5) + let(1, 1)
                         + jump(12) + END)
        one = a_package_patch_mod(self.mods, "one", a_package_patch([(5, first_mod())]))
        two = a_package_patch_mod(self.mods, "two", a_package_patch([(5, clash)]))
        self.assertEqual(len(self.clashes([one, two], lambda package, index: STOCK)), 1)


class OnlyGameFolderPatches(unittest.TestCase):
    """The TFC edition of Vending Machine Enhancement carries its own tool's
    templates, named by checksum, beside Game/. A player dropped it in whole and
    the manager went looking for a game file called '4c5e3353...'."""

    def test_patches_outside_game_are_not_read(self) -> None:
        from lid_db_manager.upk import tfcmod
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            cooked = folder / "Game" / "BrgGame" / "CookedPCConsole"
            cooked.mkdir(parents=True)
            (cooked / "BrgGame.upk.PackagePatch").write_bytes(a_package_patch([]))
            runtime = folder / "runtime" / "build25923506"
            runtime.mkdir(parents=True)
            (runtime / ("4c5e3353" * 8 + ".PackagePatch")).write_bytes(a_package_patch([]))
            self.assertEqual(list(tfcmod.load(folder).patches), ["brggame.upk"])


VENDING = PROJECT_ROOT / "mods" / "Vending Machine"
STOCK_UPK = Path(os.environ.get("LID_STOCK_UPK", ""))
SIBLINGS = PROJECT_ROOT.parent
PATCH = Path("Game") / "BrgGame" / "CookedPCConsole" / "BrgGame.upk.PackagePatch"


class TheVendingMachineParts(unittest.TestCase):
    def test_each_choice_replaces_its_functions(self) -> None:
        shop = {"BrgUIMenu_ShopBase\\AddSellCurrentFighterItemSelectPanel",
                "BrgUIMenu_ShopBase\\SellItem", "BrgUIMenu_ShopBase\\TickASync"}
        menu = {"BrgUIMenu_ItemVendingMachine\\GetMenuDefinition",
                "BrgUIMenu_ItemVendingMachine\\SetupTopMenu", "BrgUIMenu_ItemVendingMachine\\TickASync"}
        stew = {"BrgUIMenu_ItemVendingMachine\\Tick", "BrgUIMenu_ItemVendingMachine\\Render",
                "BrgUIMenu_SkillExchange\\StartSkillExchange", "BrgUIMenu_SkillExchange\\Tick",
                "BrgUIMenu_SkillExchange\\Render"}
        for choice, wanted in (("ammo", shop | menu), ("decals", menu | stew), ("both", shop | menu | stew)):
            patch = PP.read(VENDING / "tfc" / choice / PATCH)
            paths = {r.index - 1: r.full_path for r in patch.object_references if r.index > 0}
            self.assertEqual({paths[o.export_index] for o in patch.objects}, wanted, choice)
            self.assertEqual(A.unsupported(patch), "")

    def test_the_toggles_pick_the_part(self) -> None:
        from lid_db_manager.mod_loader import load_mod_folder
        mod = load_mod_folder(VENDING)
        for values, source in (({}, None), ({"refill_ammo": True}, "tfc/ammo"),
                               ({"decals": True}, "tfc/decals"),
                               ({"refill_ammo": True, "decals": True}, "tfc/both")):
            parts = [p for p in mod.with_settings(values).patches if p.type == "tfc_installer"]
            self.assertEqual([p.source_rel for p in parts], [source] if source else [], values)

    def test_every_switch_still_has_its_cases(self) -> None:
        # A switch reads its first case from the bytes after it. 0.4.0 moved
        # ShopBase.TickASync's switch to the end without it, so on shop state 0
        # (a shop opening or closing) the game ran past the script and crashed.
        for choice in ("ammo", "decals", "both"):
            for o in PP.read(VENDING / "tfc" / choice / PATCH).objects:
                lines = SM._read(SM._split(o.data)[2]).lines
                for at, line in enumerate(lines):
                    if line[4].startswith("switch<"):
                        self.assertTrue(lines[at + 1][4].startswith(("case ", "default")),
                                        f"{choice}: the switch at {line[1]:#x} lost its cases")

    @unittest.skipUnless(STOCK_UPK.is_file(), "needs the stock 1.92 BrgGame.upk (set LID_STOCK_UPK)")
    def test_they_combine_with_timers_and_stew_multi_pull(self) -> None:
        from lid_db_manager.upk.tfcmod import StockBodies
        stock = StockBodies(STOCK_UPK.read_bytes())
        ours = {o.export_index: o.data for o in PP.read(VENDING / "tfc" / "both" / PATCH).objects}
        others = [SIBLINGS / "LiD Timers" / "mods" / "Timers" / "tfc" / "cost1000" / PATCH,
                  SIBLINGS / "LiD Stew Multi Pull" / "mods" / "Stew Multi Pull" / "tfc" / "x10" / PATCH]
        shared = 0
        for path in others:
            if not path.is_file():
                continue
            for o in PP.read(path).objects:
                if o.export_index in ours:
                    shared += 1
                    for a, b in ((o.data, ours[o.export_index]), (ours[o.export_index], o.data)):
                        self.assertIsNotNone(SM.merge(stock(o.export_index), a, b), path)
        if not shared:
            self.skipTest("Timers and Stew Multi Pull are not beside this checkout")


if __name__ == "__main__":
    unittest.main()
