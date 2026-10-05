"""Bringing an older copy of the manager over to a new release.

Players kept being stuck after an update: the new copy knew nothing of the
mods the old one had applied, so everything had to be switched off in the old
copy and on again in the new one. Carrying the old copy's data over removes
that - and must never take the old program or its old shipped mods along.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lid_db_manager import carry_over
from lid_db_manager.manager import Manager
from lid_db_manager.paths import AppPaths
from lid_db_manager.state import State


def write(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def a_mod(folder: Path, version: str = "1.0.0") -> Path:
    write(folder / "mod.json", json.dumps({
        "id": folder.name, "name": folder.name, "description": "a test mod", "version": version,
        "author": "someone",
        "patches": [{"type": "raw_sql", "sql": "UPDATE master_skill SET rarity = 1 WHERE 0;"}],
    }))
    return folder


def an_old_copy(root: Path, *, applied=("shipped-mod", "my-own-mod"), exe=True) -> Path:
    if exe:
        write(root / carry_over.EXE_NAME, "old program")
    write(root / "_internal" / "old.dll", "old program")
    write(root / "state.json", json.dumps({
        "version": 1,
        "db_path": str(root.parent / "game" / "masters.db"),
        "enabled_mods": list(applied),
        "applied": {m: {"version": "1.0.0", "name": m} for m in applied},
        "mod_settings": {"shipped-mod": {"count": "25"}},
    }))
    write(root / "snapshots" / "shipped-mod.json", '{"mod_id": "shipped-mod"}')
    write(root / "backups" / "game_files" / "manifest.json", '{"x": {"backup": "x.original"}}')
    write(root / "backups" / "game_files" / "x.original", "stock bytes")
    write(root / "backups" / "2026-10-01_12-00-00.db", "dated backup")
    write(root / "cache" / "texture-index.json", "{}")
    write(root / "logs" / "session.log", "old log")
    a_mod(root / "mods" / "shipped-mod", "1.0.0")
    a_mod(root / "mods" / "my-own-mod")
    a_mod(root / "mods" / "_retired" / "old-thing")
    write(root / "LiD Vanilla DB" / "5.0.4.1" / "masters.db", "clean")
    # Up to 0.10.1 the program carried clean copies inside itself.
    write(root / "_internal" / "LiD Vanilla DB" / "5.0.4.1" / "masters.db", "bundled 5.0.4.1")
    write(root / "_internal" / "LiD Vanilla DB" / "5.0.4.2" / "masters.db", "bundled 5.0.4.2")
    return root


def a_new_copy(root: Path) -> AppPaths:
    write(root / carry_over.EXE_NAME, "new program")
    a_mod(root / "mods" / "shipped-mod", "2.0.0")
    return AppPaths(root).ensure()


class Finding(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())

    def test_the_folder_its_exe_or_its_state_file(self) -> None:
        old = an_old_copy(self.tmp / "LID DB Mod Manager")
        for path in (old, old / carry_over.EXE_NAME, old / "state.json"):
            found = carry_over.find_install(path)
            self.assertIsNotNone(found, path)
            self.assertEqual(found.root, old)
        self.assertEqual(found.applied, ["my-own-mod", "shipped-mod"])

    def test_or_the_folder_a_zip_was_unpacked_into(self) -> None:
        old = an_old_copy(self.tmp / "Beta V0.10.1" / "LID DB Mod Manager")
        self.assertEqual(carry_over.find_install(self.tmp / "Beta V0.10.1").root, old)

    def test_a_mod_folder_is_not_a_copy_of_the_manager(self) -> None:
        mod = a_mod(self.tmp / "some-mod")
        write(mod / "state.json", "{}")       # even one with a stray state.json
        self.assertIsNone(carry_over.find_install(mod))
        self.assertIsNone(carry_over.find_install(self.tmp / "nowhere"))


class Planning(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.old = carry_over.read_install(an_old_copy(self.tmp / "old"))
        self.paths = a_new_copy(self.tmp / "new")

    def test_what_comes_over(self) -> None:
        plan = carry_over.plan(self.old, self.paths, has_applied_here=False)
        self.assertEqual(plan.data_dirs, ["snapshots", "backups", "cache"])
        self.assertEqual(plan.own_mods, ["my-own-mod"])
        self.assertEqual(plan.shipped_mods, ["shipped-mod"])
        self.assertEqual(plan.vanilla_files, ["LiD Vanilla DB/5.0.4.1/masters.db",
                                              "_internal/LiD Vanilla DB/5.0.4.2/masters.db"])

    def test_not_this_copy_itself(self) -> None:
        same = carry_over.read_install(an_old_copy(self.tmp / "new"))
        with self.assertRaises(carry_over.CarryOverError):
            carry_over.plan(same, self.paths, has_applied_here=False)

    def test_not_a_copy_that_was_never_used(self) -> None:
        root = self.tmp / "unused"
        write(root / carry_over.EXE_NAME)
        write(root / "state.json", json.dumps({"version": 1}))
        with self.assertRaises(carry_over.CarryOverError):
            carry_over.plan(carry_over.read_install(root), self.paths, has_applied_here=False)

    def test_not_over_a_copy_that_has_applied_mods_of_its_own(self) -> None:
        with self.assertRaises(carry_over.CarryOverError):
            carry_over.plan(self.old, self.paths, has_applied_here=True)


class Copying(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.old_root = an_old_copy(self.tmp / "old")
        self.before = {p: p.read_bytes() for p in self.old_root.rglob("*") if p.is_file()}
        self.paths = a_new_copy(self.tmp / "new")
        old = carry_over.read_install(self.old_root)
        self.report = carry_over.carry_over(
            carry_over.plan(old, self.paths, has_applied_here=False))
        self.new = self.paths.root

    def test_the_data_comes_over(self) -> None:
        for relative in ("state.json", "snapshots/shipped-mod.json",
                         "backups/game_files/manifest.json", "backups/game_files/x.original",
                         "backups/2026-10-01_12-00-00.db", "cache/texture-index.json",
                         "mods/my-own-mod/mod.json", "LiD Vanilla DB/5.0.4.1/masters.db"):
            self.assertEqual((self.new / relative).read_bytes(),
                             (self.old_root / relative).read_bytes(), relative)

    def test_clean_databases_the_old_program_carried_come_too(self) -> None:
        self.assertEqual((self.new / "LiD Vanilla DB" / "5.0.4.2" / "masters.db").read_text(),
                         "bundled 5.0.4.2")
        self.assertEqual((self.new / "LiD Vanilla DB" / "5.0.4.1" / "masters.db").read_text(),
                         "clean", "the one the player kept wins over the bundled one")

    def test_the_program_logs_and_old_shipped_mods_do_not(self) -> None:
        self.assertEqual((self.new / carry_over.EXE_NAME).read_text(), "new program")
        self.assertFalse((self.new / "_internal" / "old.dll").exists())
        self.assertFalse((self.new / "logs" / "session.log").exists())
        self.assertFalse((self.new / "mods" / "_retired").exists())
        shipped = json.loads((self.new / "mods" / "shipped-mod" / "mod.json").read_text())
        self.assertEqual(shipped["version"], "2.0.0")

    def test_the_old_folder_is_untouched(self) -> None:
        after = {p: p.read_bytes() for p in self.old_root.rglob("*") if p.is_file()}
        self.assertEqual(after, self.before)

    def test_the_new_copy_starts_where_the_old_one_left_off(self) -> None:
        manager = Manager(self.paths)
        self.assertFalse(manager.started_fresh)
        self.assertEqual(sorted(manager.state.applied), ["my-own-mod", "shipped-mod"])
        self.assertEqual(manager.state.applied["shipped-mod"].version, "1.0.0")
        self.assertEqual(manager.state.mod_settings, {"shipped-mod": {"count": "25"}})
        # the shipped mod is newer here, so the next save swaps it over
        self.assertEqual(manager.scan.get("shipped-mod").version, "2.0.0")


class TheOldWindowCannotUndoIt(unittest.TestCase):
    def test_a_read_only_state_never_writes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text('{"version": 1, "enabled_mods": ["kept"]}', encoding="utf-8")
            stale = State(path=path)
            stale.read_only = True
            stale.save()
            self.assertEqual(State.load(path).enabled_mods, ["kept"])

    def test_a_first_start_is_told_apart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = AppPaths(Path(tmp))
            self.assertTrue(Manager(paths).started_fresh)
            Manager(paths).state.save()
            self.assertFalse(Manager(paths).started_fresh)


if __name__ == "__main__":
    unittest.main()
