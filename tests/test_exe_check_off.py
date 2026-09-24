"""Switching the game's own file check off, one package at a time.

The executable lists the files it verifies, and a file that is not on the list
is never verified - so changing one byte of a name takes that package out of
the check for good. These build a genuine little PE the same way
``test_exe_checksums`` does, so they run without the game installed.

What is really under test is the proof: the write is allowed near an executable
only because it can show it changed the bytes it meant to and nothing else, and
that the change can be taken back out again exactly.
"""

from __future__ import annotations

import unittest

from lid_db_manager import exe_check_off as OFF
from lid_db_manager import exe_checksums as X

from test_exe_checksums import an_executable

A = "a" * 40
B = "b" * 40
C = "c" * 40


def a_game(entries=None) -> bytes:
    return an_executable(
        entries
        or [
            ("BrgGame.upk", A),
            ("UI_ButtonGuide_STM_SF.upk", B),
            ("BrgGame.ini", C),
        ]
    )


class ReadingTheList(unittest.TestCase):
    def test_every_appearance_is_found(self) -> None:
        """A name can be listed twice, and the game may look up either."""
        raw = a_game([("BrgGame.upk", A), ("Other.upk", B), ("BrgGame.upk", C)])
        names = [listing.name for listing in OFF.listings(raw)]
        self.assertEqual(names, ["brggame.upk", "other.upk", "brggame.upk"])

    def test_a_listed_file_is_checked(self) -> None:
        raw = a_game()
        self.assertTrue(OFF.is_checked(raw, "BrgGame.upk"))
        self.assertTrue(OFF.is_checked(raw, "brggame.upk"))
        self.assertFalse(OFF.is_checked(raw, "never_listed.upk"))

    def test_nothing_is_switched_off_to_begin_with(self) -> None:
        self.assertEqual(OFF.switched_off_names(a_game()), [])


class SwitchingOff(unittest.TestCase):
    def test_one_package_leaves_the_rest_alone(self) -> None:
        raw = a_game()
        changed, done = OFF.switch_off(raw, ["BrgGame.upk"])
        self.assertEqual(done, ["brggame.upk"])
        self.assertFalse(OFF.is_checked(changed, "BrgGame.upk"))
        self.assertTrue(OFF.is_checked(changed, "UI_ButtonGuide_STM_SF.upk"))
        self.assertTrue(OFF.is_checked(changed, "BrgGame.ini"))

    def test_it_is_one_byte_and_the_length_never_changes(self) -> None:
        raw = a_game()
        changed, _ = OFF.switch_off(raw, ["BrgGame.upk"])
        self.assertEqual(len(changed), len(raw))
        self.assertEqual(sum(1 for a, b in zip(raw, changed) if a != b), 1)

    def test_the_code_is_not_touched(self) -> None:
        """The whole point: no program code changes, so nothing else can break."""
        raw = a_game()
        changed, _ = OFF.switch_off(raw, ["BrgGame.upk", "BrgGame.ini"])
        self.assertEqual(X.code_fingerprint(changed), X.code_fingerprint(raw))

    def test_the_list_keeps_its_length(self) -> None:
        raw = a_game()
        changed, _ = OFF.switch_off(raw, ["BrgGame.upk"])
        self.assertEqual(len(OFF.listings(changed)), len(OFF.listings(raw)))

    def test_every_appearance_of_a_duplicated_name_goes(self) -> None:
        raw = a_game([("BrgGame.upk", A), ("Other.upk", B), ("BrgGame.upk", C)])
        changed, done = OFF.switch_off(raw, ["BrgGame.upk"])
        self.assertEqual(done, ["brggame.upk"])
        self.assertFalse(OFF.is_checked(changed, "BrgGame.upk"))
        self.assertEqual(sum(1 for a, b in zip(raw, changed) if a != b), 2)

    def test_a_name_the_list_never_had_is_not_an_error(self) -> None:
        raw = a_game()
        changed, done = OFF.switch_off(raw, ["nothing_like_this.upk"])
        self.assertEqual(done, [])
        self.assertEqual(changed, raw)

    def test_switching_off_twice_changes_nothing_the_second_time(self) -> None:
        raw = a_game()
        once, _ = OFF.switch_off(raw, ["BrgGame.upk"])
        twice, done = OFF.switch_off(once, ["BrgGame.upk"])
        self.assertEqual(done, [])
        self.assertEqual(twice, once)

    def test_a_switched_off_name_is_recognised_on_sight(self) -> None:
        """No record is kept anywhere, so the file has to say so itself."""
        raw = a_game()
        changed, _ = OFF.switch_off(raw, ["BrgGame.upk", "BrgGame.ini"])
        self.assertEqual(OFF.switched_off_names(changed), ["brggame.inx", "brggame.upx"])
        self.assertEqual(OFF.checked_names(changed), ["ui_buttonguide_stm_sf.upk"])


class SwitchingBackOn(unittest.TestCase):
    def test_it_comes_back_byte_for_byte(self) -> None:
        raw = a_game()
        changed, _ = OFF.switch_off(raw, ["BrgGame.upk"])
        back, done = OFF.switch_on(changed, ["BrgGame.upk"])
        self.assertEqual(done, ["brggame.upk"])
        self.assertEqual(back, raw)

    def test_all_of_them_at_once(self) -> None:
        raw = a_game()
        changed, _ = OFF.switch_off(raw, OFF.checked_names(raw))
        self.assertEqual(OFF.checked_names(changed), [])
        back, done = OFF.switch_on(changed)
        self.assertEqual(len(done), 3)
        self.assertEqual(back, raw)

    def test_what_a_name_was_is_read_off_the_name(self) -> None:
        """Every extension the game lists has a different first two letters, so
        the one character that was changed is always recoverable."""
        self.assertEqual(OFF.original_name("brggame.upx"), "brggame.upk")
        self.assertEqual(OFF.original_name("brggame.inx"), "brggame.ini")
        self.assertEqual(OFF.original_name("shader.usx"), "shader.usf")
        self.assertEqual(OFF.original_name("brggame.upk"), "brggame.upk")

    def test_one_file_can_be_put_back_without_the_others(self) -> None:
        raw = a_game()
        changed, _ = OFF.switch_off(raw, ["BrgGame.upk", "BrgGame.ini"])
        back, done = OFF.switch_on(changed, ["BrgGame.ini"])
        self.assertEqual(done, ["brggame.ini"])
        self.assertTrue(OFF.is_checked(back, "BrgGame.ini"))
        self.assertFalse(OFF.is_checked(back, "BrgGame.upk"))

    def test_nothing_switched_off_is_not_an_error(self) -> None:
        raw = a_game()
        back, done = OFF.switch_on(raw)
        self.assertEqual(done, [])
        self.assertEqual(back, raw)


class NamesThatAlreadyEndInTheMark(unittest.TestCase):
    """A name ending in the mark would make switching it off a no-op."""

    def test_such_a_name_still_goes_off(self) -> None:
        raw = a_game([("weird.upX", A), ("BrgGame.upk", B)])
        changed, done = OFF.switch_off(raw, ["weird.upX"])
        self.assertEqual(done, ["weird.upx"])
        self.assertFalse(OFF.is_checked(changed, "weird.upX"))
        self.assertEqual(sum(1 for a, b in zip(raw, changed) if a != b), 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
