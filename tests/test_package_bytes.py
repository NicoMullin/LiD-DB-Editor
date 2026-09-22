"""Changing bytes inside a package: the chunk rebuild, and the patch type."""

from __future__ import annotations

import os
import random
import struct
import tempfile
import unittest
from pathlib import Path

from fixtures import write_mod

from lid_db_manager.errors import ModLoadError
from lid_db_manager.mod_loader import load_mod_folder
from lid_db_manager.upk import bytepatch, lzo
from lid_db_manager.upk import package as P

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# The real package, for the test that proves this against the game itself.
GAME_PACKAGE = Path(os.environ.get(
    "LID_GAME_UPK",
    r"C:\Program Files (x86)\Steam\steamapps\common\LET IT DIE"
    r"\BrgGame\CookedPCConsole\BrgGame.upk"))
HAVE_GAME = GAME_PACKAGE.is_file()

DROP_DELAY = b"\x2c\x06\x1f" + b"Item Drop Delay Time" + b"\x00\x28\x2c"
DROP_DELAY_TAIL = b"\x25\x1e\xcd\xcc\xcc\x3d\x16"


class StoreTests(unittest.TestCase):
    """A literal run is valid LZO1X, which is what lets a chunk be rebuilt."""

    def test_it_decodes_back_to_the_same_bytes(self) -> None:
        random.seed(1)
        for size in (0, 1, 3, 4, 18, 19, 274, 70_000, 131_072):
            data = bytes(random.getrandbits(8) for _ in range(size))
            self.assertEqual(data, lzo.decompress(lzo.store(data), size), f"size {size}")

    def test_a_rebuilt_chunk_reads_back_through_the_package_reader(self) -> None:
        raw = bytes(random.getrandbits(8) for _ in range(300_000))
        blob = bytepatch.encode_chunk(raw, 131_072)
        chunk = P.CompressedChunk(uncompressed_offset=0, uncompressed_size=len(raw),
                                  compressed_offset=0, compressed_size=len(blob))
        self.assertEqual(raw, P.decompress_chunk(blob, chunk, P.COMPRESS_LZO))

    def test_a_chunk_has_to_keep_its_size(self) -> None:
        raw = b"x" * 1000
        blob = bytepatch.encode_chunk(raw, 512)
        chunk = P.CompressedChunk(0, len(raw), 0, len(blob))
        site = bytepatch.Site(chunk_index=0, chunk=chunk, raw=raw, at=0,
                              block_size=512, entry_at=0)
        with self.assertRaises(bytepatch.SiteError):
            bytepatch.rewrite(blob, site, raw + b"more")


def a_mod(folder: Path, **overrides) -> Path:
    edit = {
        "name": "Item Drop Delay Time",
        "find": [{"hex": "2c061f"}, {"text": "Item Drop Delay Time"}, {"hex": "00282c"}],
        "follows": [{"hex": "251ecdcccc3d16"}],
        "write": {"type": "u8", "value": 0},
    }
    edit.update(overrides.pop("edit", {}))
    data = {
        "patches": [{
            "type": "package_bytes",
            "target": "BrgGame/CookedPCConsole/BrgGame.upk",
            "edits": [edit],
        }],
    }
    data["patches"][0].update(overrides)
    return write_mod(folder, "bytes-mod", data)


class PatchTypeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.mods = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _load(self, **overrides):
        return load_mod_folder(a_mod(self.mods, **overrides)).patches[0]

    def test_it_reads_a_signature_out_of_hex_and_text(self) -> None:
        patch = self._load()
        self.assertEqual(DROP_DELAY, patch.edits[0]["find"])
        self.assertEqual(DROP_DELAY_TAIL, patch.edits[0]["follows"])
        self.assertEqual({"BrgGame/CookedPCConsole/BrgGame.upk"}, patch.asset_targets())
        self.assertEqual(set(), patch.tables(), "it writes no database rows")

    def test_the_target_has_to_be_a_package(self) -> None:
        with self.assertRaises(ModLoadError) as caught:
            self._load(target="BrgGame/CookedPCConsole/masters.db")
        self.assertIn(".upk", str(caught.exception))

    def test_a_target_outside_the_game_folder_is_refused(self) -> None:
        with self.assertRaises(ModLoadError):
            self._load(target="../../Windows/System32/thing.upk")

    def test_a_value_too_big_for_its_width_is_refused(self) -> None:
        with self.assertRaises(ModLoadError) as caught:
            self._load(edit={"write": {"type": "u8", "value": 300}})
        self.assertIn("does not fit", str(caught.exception))

    def test_an_unknown_width_is_refused(self) -> None:
        with self.assertRaises(ModLoadError):
            self._load(edit={"write": {"type": "u24", "value": 1}})

    def test_hex_that_is_not_hex_is_refused(self) -> None:
        with self.assertRaises(ModLoadError) as caught:
            self._load(edit={"find": [{"hex": "zz"}]})
        self.assertIn("hex", str(caught.exception))

    def test_edits_cannot_be_empty(self) -> None:
        folder = write_mod(self.mods, "empty-mod", {
            "patches": [{"type": "package_bytes",
                         "target": "BrgGame/CookedPCConsole/BrgGame.upk",
                         "edits": []}]})
        with self.assertRaises(ModLoadError):
            load_mod_folder(folder)


def a_package(chunks: list[bytes]) -> tuple[bytes, bytes]:
    """A small but genuine LET IT DIE package: summary, chunk table, chunks.

    Returns the file and the flat form of it - the header as the reader hands
    it back, then every chunk decoded, which is what a patch works on.
    """
    head = bytearray()
    head += struct.pack("<I", P.TAG)
    head += struct.pack("<HH", P.FILE_VERSION, P.LICENSEE_VERSION)
    head += struct.pack("<i", 0)                    # total header size, filled in below
    head += struct.pack("<i", 1) + b"\x00"           # folder name, one empty string
    head += struct.pack("<I", 0)                    # package flags
    for _ in range(11):                             # counts and offsets
        head += struct.pack("<i", 0)
    head += b"\x00" * 16                            # guid
    head += struct.pack("<i", 0)                    # no generations
    head += struct.pack("<ii", 0, 0)                # engine, cooker
    head += struct.pack("<I", P.COMPRESS_LZO)
    table_at = len(head)
    head += struct.pack("<i", len(chunks))
    head += b"\x00" * (16 * len(chunks))            # entries, filled in below

    body_at = len(head)
    out = bytearray(head)
    # The flat form's header stops where the chunk table starts: the table
    # belongs to the file, not to the package the game reads out of it. Real
    # packages are laid out the same way.
    flat_at = table_at
    entries = []
    for raw in chunks:
        blob = bytepatch.encode_chunk(raw, 512)
        entries.append((flat_at, len(raw), len(out), len(blob)))
        out += blob
        flat_at += len(raw)
    for number, entry in enumerate(entries):
        struct.pack_into("<4I", out, table_at + 4 + number * 16, *entry)
    struct.pack_into("<i", out, 8, body_at)         # total header size
    flat = bytes(out[:table_at]) + b"".join(chunks)
    return bytes(out), flat


def decoded(data: bytes) -> bytes:
    """The package as the game would see it: header, then every chunk."""
    summary = P.read_summary(data)
    out = bytearray(data[:summary.chunks[0].uncompressed_offset])
    for chunk in summary.chunks:
        out += P.decompress_chunk(data, chunk, P.COMPRESS_LZO)
    return bytes(out)


class WriteBackTests(unittest.TestCase):
    """Writing a package back as chunks, reusing the ones that did not change."""

    def test_a_built_package_reads_back(self) -> None:
        data, flat = a_package([b"a" * 1000, b"b" * 1000])
        self.assertEqual(flat, decoded(data), "the test's own package is wrong")

    def test_unchanged_chunks_keep_their_original_bytes(self) -> None:
        data, flat = a_package([b"a" * 1000, b"b" * 1000, b"c" * 1000])
        after = bytearray(flat)
        after[-1500] = ord("X")                     # somewhere inside chunk 1
        out = bytepatch.write_chunks(data, flat, bytes(after))
        self.assertIsNotNone(out)
        self.assertEqual(bytes(after), decoded(out), "it must decode to what was asked")

        was, now = P.read_summary(data), P.read_summary(out)

        def blob(data: bytes, chunk) -> bytes:
            return data[chunk.compressed_offset:
                        chunk.compressed_offset + chunk.compressed_size]

        # Chunk 0 was not touched, so its compressed bytes are the originals.
        self.assertEqual(blob(data, was.chunks[0]), blob(out, now.chunks[0]))
        # Chunk 1 was, so it was encoded again and reads differently.
        self.assertNotEqual(blob(data, was.chunks[1]), blob(out, now.chunks[1]))
        # The last chunk is always re-encoded, because whatever a patch appends
        # goes on the end of it - but here it encodes back to the same bytes,
        # so there is nothing to tell apart. The real proof is the decode above.

    def test_what_a_patch_appended_goes_on_the_last_chunk(self) -> None:
        data, flat = a_package([b"a" * 1000, b"b" * 500])
        after = flat + b"APPENDED" * 100
        out = bytepatch.write_chunks(data, flat, after)
        summary = P.read_summary(out)
        self.assertEqual(2, len(summary.chunks), "the chunk table must not grow")
        self.assertEqual(500 + 800, summary.chunks[-1].uncompressed_size)
        self.assertEqual(after, decoded(out))

    def test_it_declines_when_the_header_changed(self) -> None:
        data, flat = a_package([b"a" * 100])
        after = bytearray(flat)
        after[40] ^= 0xFF                           # inside the summary
        self.assertIsNone(bytepatch.write_chunks(data, flat, bytes(after)))

    def test_it_declines_when_the_package_shrank_past_the_last_chunk(self) -> None:
        data, flat = a_package([b"a" * 1000, b"b" * 500])
        self.assertIsNone(bytepatch.write_chunks(data, flat, flat[:800]))


class ShippedModTests(unittest.TestCase):
    """The Instant Drops mod as it ships."""

    def setUp(self) -> None:
        self.mod = load_mod_folder(PROJECT_ROOT / "mods" / "instant-drops")

    def test_it_asks_for_the_file_check_to_be_off(self) -> None:
        self.assertEqual(["BrgGame.upk"], self.mod.requires_check_off)

    def test_the_setting_drives_the_byte_that_is_written(self) -> None:
        self.assertEqual(0, self.mod.patches[0].edits[0]["value"])
        at_one_second = self.mod.with_settings({"tenths": 10})
        self.assertEqual(10, at_one_second.patches[0].edits[0]["value"])

    def test_the_stock_value_is_within_reach(self) -> None:
        setting = self.mod.settings[0]
        self.assertLessEqual(setting.minimum, 0)
        self.assertGreaterEqual(setting.maximum, 20, "20 tenths is what the game ships")


@unittest.skipUnless(HAVE_GAME, "needs LET IT DIE installed (set LID_GAME_UPK)")
class AgainstTheRealPackageTests(unittest.TestCase):
    """The one that matters: the game's own 179 MB package."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.data = GAME_PACKAGE.read_bytes()

    def test_the_drop_delay_is_where_the_mod_says_it_is(self) -> None:
        site = bytepatch.find(self.data, DROP_DELAY, follows=DROP_DELAY_TAIL)
        self.assertEqual(20, site.raw[site.at], "the stock wait is 20 x 0.1s")

    def test_changing_it_changes_exactly_one_byte_of_the_package(self) -> None:
        mod = load_mod_folder(PROJECT_ROOT / "mods" / "instant-drops")
        out = mod.patches[0].transform_target(
            "BrgGame/CookedPCConsole/BrgGame.upk", self.data)
        before, after = P.read(self.data).data, P.read(out).data
        self.assertEqual(len(before), len(after), "the decoded package must not move")
        differing = [i for i in range(len(before)) if before[i] != after[i]]
        self.assertEqual(1, len(differing))
        self.assertEqual(0, after[differing[0]])

    def test_bytes_that_are_not_in_the_package_are_refused(self) -> None:
        with self.assertRaises(bytepatch.SiteError):
            bytepatch.find(self.data, b"NoSuchLabelInAnyPackage_qqq")

    def test_a_wrong_tail_is_refused(self) -> None:
        with self.assertRaises(bytepatch.SiteError):
            bytepatch.find(self.data, DROP_DELAY, follows=b"\x00\x01\x02\x03")


if __name__ == "__main__":
    unittest.main()
