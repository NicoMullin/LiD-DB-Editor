"""Reading, and narrowly editing, the hash table inside the game executable.

These build a small but genuine PE file rather than leaning on the installed
game, so they run anywhere. The shape is the part that matters: a code section,
a resource section, and a resource tree with the hash table at type 10 / id 1010
where the real executable keeps it.

What is really being tested is the refusals. The one function here that produces
changed bytes is allowed to touch an executable only because it can prove it
changed twenty bytes of data and nothing else, so every way that proof could
fail has a test.
"""

from __future__ import annotations

import hashlib
import struct
import unittest

from lid_db_manager import exe_checksums as X

CODE = b"\x90" * 512          # stand-in for real code
SECTION_ALIGN = 0x1000


def a_resource_blob(entries: list[tuple[str, str]], marker_after: int | None = None) -> bytes:
    """The hash table itself: name, NUL, 20 raw bytes, repeated."""
    out = b""
    for index, (name, sha1) in enumerate(entries):
        if marker_after is not None and index == marker_after:
            out += X.SECTION_MARKER + b"\0" + b"\0" * X.HASH_BYTES
        out += name.encode("ascii") + b"\0" + bytes.fromhex(sha1)
    return out


def an_executable(entries: list[tuple[str, str]], *, marker_after: int | None = None,
                  code: bytes = CODE, resource_type: int = X.RESOURCE_TYPE,
                  resource_id: int = X.RESOURCE_ID) -> bytes:
    """A minimal 64-bit PE carrying one RCDATA resource."""
    blob = a_resource_blob(entries, marker_after)

    # .rsrc holds a three-level directory tree, then the data entry, then blob.
    # Each level is a 16-byte header plus one 8-byte entry, so 24 bytes, and
    # each entry points at the next level by its offset within the section.
    level = 24
    def directory(key: int, points_at: int, is_dir: bool = True) -> bytes:
        where = (0x80000000 | points_at) if is_dir else points_at
        return struct.pack("<IIHHHH", 0, 0, 0, 0, 0, 1) + struct.pack("<II", key, where)

    tree = (
        directory(resource_type, level)          # -> the id level at 24
        + directory(resource_id, level * 2)      # -> the language level at 48
        + directory(0x409, level * 3, False)     # -> the data entry at 72
    )
    data_entry_at = len(tree)                    # 72
    blob_rva_offset = data_entry_at + 16         # 88

    text_rva, rsrc_rva = SECTION_ALIGN, SECTION_ALIGN * 2
    data_entry = struct.pack("<IIII", rsrc_rva + blob_rva_offset, len(blob), 0, 0)
    rsrc = tree + data_entry + blob

    pe_at = 0x80
    optional_size = 240                              # PE32+ with 16 directories
    headers_size = pe_at + 24 + optional_size + 40 * 2
    text_at = (headers_size + 0x1FF) // 0x200 * 0x200
    rsrc_at = text_at + (len(code) + 0x1FF) // 0x200 * 0x200

    optional = bytearray(optional_size)
    struct.pack_into("<H", optional, 0, 0x20B)       # PE32+
    struct.pack_into("<II", optional, 112 + 16, rsrc_rva, len(rsrc))   # resource directory

    coff = struct.pack("<HHIIIHH", 0x8664, 2, 0, 0, 0, optional_size, 0x22)
    sections = (
        struct.pack("<8sIIII", b".text", len(code), text_rva, len(code), text_at)
        + b"\0" * 16
        + struct.pack("<8sIIII", b".rsrc", len(rsrc), rsrc_rva, len(rsrc), rsrc_at)
        + b"\0" * 16
    )

    raw = bytearray(rsrc_at + len(rsrc))
    raw[0:2] = b"MZ"
    struct.pack_into("<I", raw, 0x3C, pe_at)
    raw[pe_at:pe_at + 4] = b"PE\0\0"
    raw[pe_at + 4:pe_at + 24] = coff
    raw[pe_at + 24:pe_at + 24 + optional_size] = bytes(optional)
    raw[pe_at + 24 + optional_size:pe_at + 24 + optional_size + 80] = sections
    raw[text_at:text_at + len(code)] = code
    raw[rsrc_at:rsrc_at + len(rsrc)] = rsrc
    return bytes(raw)


A = "1b26d222e55b5da895839a0d412497670d94be10"
B = "1ba2f780f15180912dedf706119f96670d6e6e18"
C = "aaaaaaaabbbbbbbbccccccccddddddddeeeeeeee"


class ReadingTheTable(unittest.TestCase):
    def test_names_and_hashes_come_back(self) -> None:
        raw = an_executable([("UI_ButtonGuide_STM_SF.upk", A), ("Other_SF.upk", C)])
        found = X.read_entries(raw)
        self.assertEqual(set(found), {"ui_buttonguide_stm_sf.upk", "other_sf.upk"})
        self.assertEqual(found["ui_buttonguide_stm_sf.upk"].sha1, A)

    def test_the_offset_really_points_at_the_hash(self) -> None:
        raw = an_executable([("A.upk", A), ("B.upk", C)])
        for entry in X.read_entries(raw).values():
            self.assertEqual(raw[entry.at:entry.at + 20].hex(), entry.sha1)

    def test_names_are_matched_without_case(self) -> None:
        raw = an_executable([("UI_ButtonGuide_STM_SF.upk", A)])
        self.assertIn("ui_buttonguide_stm_sf.upk", X.read_entries(raw))

    def test_entries_after_the_section_marker_are_left_out(self) -> None:
        """Past the marker the entries mean something else."""
        raw = an_executable([("A.upk", A), ("B.upk", C)], marker_after=1)
        self.assertEqual(set(X.read_entries(raw)), {"a.upk"})

    def test_a_non_executable_is_refused(self) -> None:
        with self.assertRaises(X.ExeFormatError):
            X.read_entries(b"this is not an executable")

    def test_an_executable_with_no_such_resource_is_refused(self) -> None:
        raw = an_executable([("A.upk", A)], resource_id=999)
        with self.assertRaises(X.ExeFormatError):
            X.read_entries(raw)


class TheCodeFingerprint(unittest.TestCase):
    def test_it_is_the_hash_of_the_code_section(self) -> None:
        raw = an_executable([("A.upk", A)])
        self.assertEqual(X.code_fingerprint(raw), hashlib.sha256(CODE).hexdigest())

    def test_different_code_gives_a_different_fingerprint(self) -> None:
        one = an_executable([("A.upk", A)])
        two = an_executable([("A.upk", A)], code=b"\xcc" * 512)
        self.assertNotEqual(X.code_fingerprint(one), X.code_fingerprint(two))

    def test_changing_a_hash_does_not_change_it(self) -> None:
        """The whole point: a table edit is data, and this proves it."""
        raw = an_executable([("A.upk", A)])
        self.assertEqual(
            X.code_fingerprint(X.apply_entry(raw, "A.upk", A, B)),
            X.code_fingerprint(raw),
        )


class ChangingOneHash(unittest.TestCase):
    def setUp(self) -> None:
        self.raw = an_executable([("A.upk", A), ("UI_ButtonGuide_STM_SF.upk", A), ("C.upk", C)])

    def test_the_named_entry_changes(self) -> None:
        changed = X.apply_entry(self.raw, "UI_ButtonGuide_STM_SF.upk", A, B)
        self.assertEqual(X.read_entries(changed)["ui_buttonguide_stm_sf.upk"].sha1, B)

    def test_nothing_outside_the_one_hash_moves(self) -> None:
        """Not "exactly 20 bytes differ" - two hashes can share byte values -
        but every byte that differs is inside that one hash's 20."""
        at = X.read_entries(self.raw)["ui_buttonguide_stm_sf.upk"].at
        changed = X.apply_entry(self.raw, "UI_ButtonGuide_STM_SF.upk", A, B)
        self.assertEqual(len(changed), len(self.raw))
        differing = [i for i in range(len(self.raw)) if self.raw[i] != changed[i]]
        self.assertTrue(differing, "nothing changed at all")
        self.assertTrue(all(at <= i < at + X.HASH_BYTES for i in differing))
        self.assertEqual(changed[:at], self.raw[:at])
        self.assertEqual(changed[at + X.HASH_BYTES:], self.raw[at + X.HASH_BYTES:])

    def test_the_other_entries_are_untouched(self) -> None:
        changed = X.apply_entry(self.raw, "UI_ButtonGuide_STM_SF.upk", A, B)
        after = X.read_entries(changed)
        self.assertEqual(after["a.upk"].sha1, A)
        self.assertEqual(after["c.upk"].sha1, C)

    def test_a_wrong_before_hash_refuses(self) -> None:
        """Wrong build, or something already changed it. Either way, stop."""
        with self.assertRaises(X.ExeFormatError) as caught:
            X.apply_entry(self.raw, "UI_ButtonGuide_STM_SF.upk", C, B)
        self.assertIn("not the expected", str(caught.exception))

    def test_an_unlisted_package_refuses(self) -> None:
        with self.assertRaises(X.ExeFormatError) as caught:
            X.apply_entry(self.raw, "NotThere.upk", A, B)
        self.assertIn("carries no hash", str(caught.exception))

    def test_a_duplicated_name_refuses(self) -> None:
        """228 names really do appear twice in the shipped table."""
        raw = an_executable([("Dup.upk", A), ("Dup.upk", C)])
        with self.assertRaises(X.ExeFormatError) as caught:
            X.apply_entry(raw, "Dup.upk", A, B)
        self.assertIn("more than once", str(caught.exception))

    def test_a_malformed_hash_refuses(self) -> None:
        for bad in ("", "xyz", "ab" * 19, "g" * 40):
            with self.subTest(bad=bad), self.assertRaises(X.ExeFormatError):
                X.apply_entry(self.raw, "A.upk", A, bad)

    def test_a_pointless_edit_refuses(self) -> None:
        with self.assertRaises(X.ExeFormatError):
            X.apply_entry(self.raw, "A.upk", A, A)

    def test_it_never_mutates_what_it_was_given(self) -> None:
        before = bytes(self.raw)
        X.apply_entry(self.raw, "A.upk", A, B)
        self.assertEqual(self.raw, before)


if __name__ == "__main__":
    unittest.main()


class AlreadyInPlace(unittest.TestCase):
    """The executable already carries the change and there is no way back.

    This is what a lost or moved manager folder looks like: the game files are
    still modified, but the saved originals are gone. Adopting that silently
    would record the modified file as the original, so switching the mod off
    later would put the modification back.
    """

    def setUp(self) -> None:
        self.raw = an_executable([("UI_ButtonGuide_STM_SF.upk", B)])   # already patched

    def test_it_refuses(self) -> None:
        with self.assertRaises(X.ExeFormatError):
            X.apply_entry(self.raw, "UI_ButtonGuide_STM_SF.upk", A, B)

    def test_it_says_the_change_is_already_in_place(self) -> None:
        with self.assertRaises(X.ExeFormatError) as caught:
            X.apply_entry(self.raw, "UI_ButtonGuide_STM_SF.upk", A, B)
        self.assertIn("already expects", str(caught.exception))

    def test_it_says_what_to_do_about_it(self) -> None:
        with self.assertRaises(X.ExeFormatError) as caught:
            X.apply_entry(self.raw, "UI_ButtonGuide_STM_SF.upk", A, B)
        self.assertIn("Verify integrity", str(caught.exception))

    def test_a_third_unrelated_value_reads_as_a_build_mismatch(self) -> None:
        """Different wording, because it means something different."""
        other = an_executable([("UI_ButtonGuide_STM_SF.upk", C)])
        with self.assertRaises(X.ExeFormatError) as caught:
            X.apply_entry(other, "UI_ButtonGuide_STM_SF.upk", A, B)
        message = str(caught.exception)
        self.assertIn("not the one this change was recorded against", message)
        self.assertNotIn("already expects", message)
