"""Reading LET IT DIE packages and applying TFC Installer .PackagePatch files.

The format and the logic are FCH823's, from TFC Installer, ported with their
permission. The strongest test here is the last one: it applies a real patch
to a real stock package and compares the result, object by object, with what
TFC Installer itself produced. It needs files from a real game install and is
skipped without them; everything above it runs anywhere.
"""

from __future__ import annotations

import os
import struct
import unittest
from pathlib import Path

from lid_db_manager.upk import apply as A
from lid_db_manager.upk import lzo
from lid_db_manager.upk import package as P
from lid_db_manager.upk import packagepatch as PP
from lid_db_manager.upk import texture2d as T2
from lid_db_manager.upk import texturepack as TP


class LzoTests(unittest.TestCase):
    """Hand-built LZO1X streams, so every instruction here is one we meant."""

    END = bytes([0x11, 0x00, 0x00])

    def test_literals_alone(self) -> None:
        stream = bytes([17 + 5]) + b"hello" + self.END
        self.assertEqual(b"hello", lzo.decompress(stream, 5))

    def test_a_match_that_overlaps_itself(self) -> None:
        # "abc", then copy six bytes from three back: the copy reads bytes it
        # is itself writing, which is how runs are encoded.
        m2 = (5 << 5) | (2 << 2)            # length 6, distance 3
        stream = bytes([17 + 3]) + b"abc" + bytes([m2, 0x00]) + self.END
        self.assertEqual(b"abcabcabc", lzo.decompress(stream, 9))

    def test_the_wrong_size_is_refused(self) -> None:
        stream = bytes([17 + 5]) + b"hello" + self.END
        with self.assertRaises(lzo.LzoError):
            lzo.decompress(stream, 6)

    def test_a_truncated_stream_is_refused(self) -> None:
        with self.assertRaises(lzo.LzoError):
            lzo.decompress(bytes([17 + 5]) + b"hel", 5)

    def test_a_match_reaching_before_the_start_is_refused(self) -> None:
        m2 = (5 << 5) | (7 << 2)            # distance 8, with only 3 bytes out
        with self.assertRaises(lzo.LzoError):
            lzo.decompress(bytes([17 + 3]) + b"abc" + bytes([m2, 0x00]) + self.END, 9)


def a_patch(objects: list[tuple[int, list[tuple[int, bool]], bytes]],
            name_refs: list[tuple[int, str]] = ()) -> bytes:
    """A version 2 .PackagePatch with empty tables and the given objects."""

    def fstring(text: str) -> bytes:
        data = text.encode("latin-1") + b"\0"
        return struct.pack("<i", len(data)) + data

    out = struct.pack("<i", 2)
    for original in (10, 5, 20):                       # names, imports, exports
        out += struct.pack("<ii", original, 0)
    out += struct.pack("<i", len(objects))
    for index, records, data in objects:
        out += struct.pack("<ii", index, len(records))
        for position, wide in records:
            out += struct.pack("<qB", position, 1 if wide else 0)
        out += struct.pack("<i", len(data)) + data
    out += struct.pack("<i", 12)                       # reference version
    out += struct.pack("<i", len(name_refs))
    for index, text in name_refs:
        out += struct.pack("<i", index) + fstring(text)
    out += struct.pack("<i", 0)                        # object references
    return out


class PatchFormatTests(unittest.TestCase):
    def test_it_reads_back_what_was_written(self) -> None:
        raw = a_patch([(7, [(4, True)], b"\x01" * 16)], name_refs=[(3, "Texture2D")])
        patch = PP.read(raw)
        self.assertEqual(2, patch.version)
        self.assertEqual((10, 5, 20), (patch.names.original_count,
                                       patch.imports.original_count,
                                       patch.exports.original_count))
        self.assertEqual(1, len(patch.objects))
        self.assertEqual(7, patch.objects[0].export_index)
        self.assertEqual([PP.OffsetRecord(4, True)], patch.objects[0].offset_records)
        self.assertEqual([PP.NameReference(3, "Texture2D")], patch.name_references)

    def test_bytes_left_over_mean_it_is_not_this_format(self) -> None:
        with self.assertRaises(PP.PatchError):
            PP.read(a_patch([]) + b"\0")

    def test_a_truncated_patch_is_refused(self) -> None:
        with self.assertRaises(PP.PatchError):
            PP.read(a_patch([(1, [], b"x" * 32)])[:-40])

    def test_a_version_from_the_future_is_refused(self) -> None:
        raw = bytearray(a_patch([]))
        struct.pack_into("<i", raw, 0, 99)
        with self.assertRaises(PP.PatchError):
            PP.read(bytes(raw))

    def test_table_changes_are_turned_away_for_now(self) -> None:
        patch = PP.read(a_patch([]))
        patch.names.entries.append(PP.TableEntryUpdate(10, P.NameEntry("New", 0), True))
        self.assertIn("names", A.unsupported(patch))


# ---------------------------------------------------------------------------
# against a real install
# ---------------------------------------------------------------------------

# A fixed set of real files, kept outside the repository: the stock packages
# from TFC Installer's own backups, what TFC Installer produced from them, and
# the Tommygun mod itself. Game files are never committed, so these tests skip
# anywhere this folder does not exist.
#
# Point LID_TFC_REFERENCE at your own copy to run them. It wants three folders:
#   stock/       the packages as the game ships them
#   tfc-output/  the same packages after TFC Installer has been at them
#   tommygun/    the mod folder itself, as downloaded
REFERENCE = Path(os.environ.get("LID_TFC_REFERENCE", "tfc-reference")).expanduser()
STOCK = REFERENCE / "stock" / "WP_AssaultRifle3102_SF.upk"
THEIRS = REFERENCE / "tfc-output" / "WP_AssaultRifle3102_SF.upk"
MOD = REFERENCE / "tommygun"
PATCH = MOD / "Game" / "BrgGame" / "CookedPCConsole" / "WP_AssaultRifle3102_SF.upk.PackagePatch"
HAVE_REAL_FILES = STOCK.is_file() and THEIRS.is_file() and PATCH.is_file()


@unittest.skipUnless(HAVE_REAL_FILES, "needs a real LET IT DIE install with the Tommygun mod")
class AgainstTfcInstallerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.stock = P.read(STOCK)
        cls.theirs = P.read(THEIRS)
        cls.patch = PP.read(PATCH)

    def test_the_stock_package_reads(self) -> None:
        self.assertEqual((259, 35, 97), (len(self.stock.names), len(self.stock.imports),
                                         len(self.stock.exports)))
        self.assertEqual("Core.ObjectRedirector", self.stock.full_path(-1))

    def test_the_patch_fits_the_stock_package(self) -> None:
        self.assertEqual([], A.problems(self.stock, self.patch))

    def test_a_package_that_does_not_match_is_refused(self) -> None:
        wrong = P.read(STOCK)
        ref = self.patch.name_references[0]
        wrong.names[ref.index] = P.NameEntry("SomethingElse", 0)
        self.assertTrue(A.problems(wrong, self.patch))
        with self.assertRaises(A.ApplyError):
            A.apply(wrong, self.patch)

    def test_every_object_the_patch_touches_matches_tfc_installer(self) -> None:
        flat, placements = A.apply(P.read(STOCK), self.patch)
        mine = P.read(flat)
        self.assertEqual(len(self.theirs.data), len(mine.data))
        textures = {i for i, e in enumerate(self.theirs.exports)
                    if self.theirs.object_name(e.class_index) == "Texture2D"}
        for i in range(len(mine.exports)):
            if i in textures:
                continue       # the texture pack's job, not the patch's
            a, b = mine.exports[i], self.theirs.exports[i]
            self.assertEqual((b.serial_offset, b.serial_size), (a.serial_offset, a.serial_size),
                             f"export {i} placed differently")
            self.assertEqual(self.theirs.export_data(i), mine.export_data(i),
                             f"export {i} differs")
        moved = {p.export_index: p.moved for p in placements}
        self.assertEqual({47: False, 85: True, 90: True}, moved)

    def test_the_result_is_a_valid_uncompressed_package(self) -> None:
        flat, _ = A.apply(P.read(STOCK), self.patch)
        again = P.read(flat)
        self.assertEqual([], again.summary.chunks)
        self.assertFalse(again.summary.package_flags & P.PKG_STORE_COMPRESSED)
        self.assertTrue(all(e.serial_offset + e.serial_size <= len(again.data)
                            for e in again.exports))


@unittest.skipUnless(HAVE_REAL_FILES, "needs the reference copy of the Tommygun mod")
class TexturePackAgainstTfcInstallerTests(unittest.TestCase):
    """The whole mod - patch and texture pack - against TFC Installer's result.

    The one byte range left out is the package GUID: TFC Installer writes its
    own bookkeeping into it, and this deliberately does not.
    """

    TFC_INDEX = 0       # Tommygun's cache went in as Texture2D_0 there

    @classmethod
    def setUpClass(cls) -> None:
        pack = MOD / "TexturePack"
        cls.mapping = TP.read_mapping(pack / "Let it Die.TFCMapping")
        cls.local = (pack / "LocalMips_0.tfc").read_bytes()

    def install(self, name: str, patch: Path | None) -> tuple[P.Package, P.Package]:
        package = P.read(REFERENCE / "stock" / name)
        if patch is not None:
            flat, _ = A.apply(package, PP.read(patch))
            package = P.read(flat)
        by_path = T2.textures_by_path(package)
        updates = [(by_path[e.texture_id.lower()],
                    T2.update(package, by_path[e.texture_id.lower()], e,
                              self.TFC_INDEX, self.local))
                   for e in self.mapping.entries if e.texture_id.lower() in by_path]
        flat, _ = A.apply_textures(package, updates)
        return P.read(flat), P.read(REFERENCE / "tfc-output" / name)

    def assert_same_but_guid(self, mine: P.Package, theirs: P.Package) -> None:
        at = mine.summary.offsets_at["thumbnail_table_offset"] + 4
        a, b = bytearray(mine.data), bytearray(theirs.data)
        a[at:at + 16] = b[at:at + 16]
        self.assertEqual(len(b), len(a))
        self.assertTrue(a == b, "the package differs from TFC Installer's")

    def test_the_mapping_reads(self) -> None:
        self.assertEqual(3, self.mapping.version)
        self.assertEqual(6, len(self.mapping.entries))
        weapon = [e for e in self.mapping.entries if "_d" in e.texture_id.lower()[-2:]][0]
        self.assertEqual((2048, 2048), weapon.size)
        self.assertEqual((5, 7), (len(weapon.tfc_mips), len(weapon.local_mips)))

    def test_the_weapon_package_matches(self) -> None:
        self.assert_same_but_guid(*self.install("WP_AssaultRifle3102_SF.upk", PATCH))

    def test_the_single_mip_icon_matches(self) -> None:
        mine, theirs = self.install("UI_Icon_PT_ARM_WP031_0B4_SF.upk", None)
        self.assert_same_but_guid(mine, theirs)
        # the UI path forgets the texture cache entirely
        icon = T2.read(mine, T2.textures_by_path(mine)[
            "ui_icon_pt_arm_wp031_0b4\\tx_ui_image_pt_arm_wp031_0b4"])
        self.assertNotIn("TextureFileCacheName", [t.name for t in icon.tags])

    def test_textures_point_at_the_installed_cache(self) -> None:
        mine, _ = self.install("WP_AssaultRifle3102_SF.upk", PATCH)
        texture = T2.read(mine, T2.textures_by_path(mine)[
            "wp_assaultrifle3102\\textures\\tx_wp_assaultrifle3102_d"])
        tag = next(t for t in texture.tags if t.name == "TextureFileCacheName")
        index, number = struct.unpack("<ii", tag.value)
        self.assertEqual("Texture2D", mine.names[index].text)
        self.assertEqual(self.TFC_INDEX + 1, number)       # Texture2D_0


if __name__ == "__main__":
    unittest.main()
