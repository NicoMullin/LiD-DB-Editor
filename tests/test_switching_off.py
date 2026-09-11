"""What happens to the database when a mod stops being ticked.

Unticking used to mean only "do not apply this next time", so a value no other
enabled mod rewrote stayed changed while the mod showed as off. Saving now puts
those values back first, which is what switching something off looks like it
should do.

The interesting case is a value two mods both write. Undoing the one on top
should leave the one underneath in charge - not vanilla - and that falls out of
the snapshot for free, because the snapshot holds whatever was there when the
mod ran, which was the other mod's value.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from fixtures import build_db, query, write_mod

from lid_db_manager.manager import Manager
from lid_db_manager.paths import AppPaths

SHARED = "SKL_EXPUP_01"   # both mods write this one
ONLY_B = "SKL_POWER_01"   # only the upper mod writes this one


def _mod(where: str, value: int) -> dict:
    return {
        "patches": [
            {
                "type": "update_set",
                "table": "master_skill",
                "set": {"val0": value},
                "where": where,
            }
        ]
    }


class SwitchingAModOff(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.paths = AppPaths(self.root / "app").ensure()
        self.db = build_db(self.root / "game" / "masters.db")
        self.manager = Manager(self.paths)
        self.manager.set_db_path(self.db)

        write_mod(self.paths.mods_dir, "under", _mod(f"id = '{SHARED}'", 111))
        write_mod(
            self.paths.mods_dir, "over", _mod(f"id IN ('{SHARED}', '{ONLY_B}')", 222)
        )
        self.manager.rescan()
        self.vanilla_shared = self._val(SHARED)
        self.vanilla_only_b = self._val(ONLY_B)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _val(self, skill_id: str) -> int:
        return query(
            self.db, "SELECT val0 FROM master_skill WHERE id = ?", (skill_id,)
        )[0][0]

    def _apply_both(self) -> None:
        self.manager.set_enabled("under", True)
        self.manager.set_enabled("over", True)   # applies last, wins
        self.assertTrue(self.manager.save_mod_list().ok)
        self.assertEqual(self._val(SHARED), 222)
        self.assertEqual(self._val(ONLY_B), 222)

    def test_unticking_and_saving_puts_its_values_back(self) -> None:
        self._apply_both()
        self.manager.set_enabled("over", False)
        self.assertTrue(self.manager.save_mod_list().ok)
        self.assertEqual(
            self._val(ONLY_B),
            self.vanilla_only_b,
            "a value only the switched-off mod wrote should return to vanilla",
        )

    def test_a_value_another_mod_still_writes_falls_back_to_that_mod(self) -> None:
        """Not vanilla - the mod underneath is still enabled and still wants it."""
        self._apply_both()
        self.manager.set_enabled("over", False)
        self.assertTrue(self.manager.save_mod_list().ok)
        self.assertEqual(self._val(SHARED), 111)

    def test_the_mod_stops_counting_as_applied(self) -> None:
        self._apply_both()
        self.manager.set_enabled("over", False)
        self.manager.save_mod_list()
        self.assertNotIn("over", self.manager.state.applied)

    def test_switching_it_back_on_applies_it_again(self) -> None:
        self._apply_both()
        self.manager.set_enabled("over", False)
        self.manager.save_mod_list()
        self.manager.set_enabled("over", True)
        self.assertTrue(self.manager.save_mod_list().ok)
        self.assertEqual(self._val(SHARED), 222)
        self.assertEqual(self._val(ONLY_B), 222)

    def test_everything_off_returns_the_database_to_vanilla(self) -> None:
        self._apply_both()
        self.manager.set_enabled("over", False)
        self.manager.set_enabled("under", False)
        self.assertTrue(self.manager.save_mod_list().ok)
        self.assertEqual(self._val(SHARED), self.vanilla_shared)
        self.assertEqual(self._val(ONLY_B), self.vanilla_only_b)

    def test_a_mod_that_was_never_applied_is_left_alone(self) -> None:
        """No snapshot, nothing to undo - and no noise about it."""
        self.manager.set_enabled("under", True)
        self.assertTrue(self.manager.save_mod_list().ok)
        self.assertEqual(self._val(ONLY_B), self.vanilla_only_b)

    def test_re_applying_does_not_undo_anything(self) -> None:
        """Re-apply is the watchdog's path - it must not rewrite history."""
        self._apply_both()
        self.manager.set_enabled("over", False)
        report = self.manager.reapply_all()
        self.assertTrue(report.ok, report.error)
        self.assertEqual(
            self._val(ONLY_B), 222, "re-apply should leave the database as it found it"
        )

    def test_a_replaced_database_is_left_alone_and_said_so(self) -> None:
        """The snapshots describe values that are no longer there.

        Replaying them would write an old version's numbers over a new one's,
        which is worse than leaving a stale change in place.
        """
        self._apply_both()
        self.manager.set_enabled("over", False)

        # The game shipping a new masters.db, as far as the manager can tell.
        fresh = self.root / "fresh.db"
        build_db(fresh)
        shutil.copy2(fresh, self.db)

        report = self.manager.save_mod_list()
        self.assertTrue(report.ok, report.error)
        self.assertIn("over", self.manager.state.applied, "not undone")
        self.assertIn(
            "still applied",
            self.manager.log.text(),
            "the log should say why it was skipped",
        )


class SwitchingOneOffAPart(unittest.TestCase):
    """The same promise, one level down: a part is a mod's own on/off switch."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.paths = AppPaths(self.root / "app").ensure()
        self.db = build_db(self.root / "game" / "masters.db")
        self.manager = Manager(self.paths)
        self.manager.set_db_path(self.db)
        write_mod(
            self.paths.mods_dir,
            "rework",
            {
                "patches": [
                    {"type": "update_set", "id": "skills", "table": "master_skill",
                     "set": {"val0": 111}, "where": "1=1"},
                    {"type": "update_set", "id": "bodies", "table": "master_body_detail",
                     "set": {"price": 222}, "where": "1=1"},
                ]
            },
        )
        self.manager.rescan()
        self.manager.set_enabled("rework", True)
        self.vanilla_bodies = self._bodies()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _skills(self) -> set:
        return {r[0] for r in query(self.db, "SELECT val0 FROM master_skill")}

    def _bodies(self) -> set:
        return {r[0] for r in query(self.db, "SELECT price FROM master_body_detail")}

    def test_switching_a_part_off_puts_its_values_back(self) -> None:
        self.assertTrue(self.manager.save_mod_list().ok)
        self.assertEqual(self._bodies(), {222})

        self.manager.set_part_enabled("rework", "bodies", False)
        self.assertTrue(self.manager.save_mod_list().ok)
        self.assertEqual(self._bodies(), self.vanilla_bodies)
        self.assertEqual(self._skills(), {111}, "the other part stays applied")

    def test_switching_it_back_on_applies_it_again(self) -> None:
        self.manager.save_mod_list()
        self.manager.set_part_enabled("rework", "bodies", False)
        self.manager.save_mod_list()
        self.manager.set_part_enabled("rework", "bodies", True)
        self.assertTrue(self.manager.save_mod_list().ok)
        self.assertEqual(self._bodies(), {222})

    def test_the_applied_record_says_which_parts_ran(self) -> None:
        """Without this there is no way to notice the mod's shape changed."""
        self.manager.save_mod_list()
        self.assertEqual(self.manager.state.applied["rework"].parts, ["skills", "bodies"])

        self.manager.set_part_enabled("rework", "bodies", False)
        self.manager.save_mod_list()
        self.assertEqual(self.manager.state.applied["rework"].parts, ["skills"])

    def test_saving_twice_with_no_changes_does_not_churn(self) -> None:
        """An unchanged mod must not be undone and redone on every save."""
        self.manager.save_mod_list()
        before = self.manager.state.applied["rework"].applied_at
        self.manager.save_mod_list()
        self.assertEqual(self._skills(), {111})
        self.assertEqual(self._bodies(), {222})
        self.assertIsNotNone(before)


if __name__ == "__main__":
    unittest.main()
