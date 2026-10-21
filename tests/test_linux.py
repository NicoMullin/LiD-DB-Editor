"""The parts that make the manager work on Linux: the game run through Proton,
Steam's Linux folders, file names in other capitals, and the Linux release.

Each runs on Windows too, against folders laid out like Linux's, except where
a test needs what only Linux has (links, run permissions, file names that
differ only in capitals); the Linux test run on GitHub covers those.
"""

from __future__ import annotations

import hashlib
import io
import os
import stat
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import fixtures  # noqa: F401 - puts the project on the path

from lid_db_manager import asset_runner, carry_over, steam_locate
from lid_db_manager import self_update as U

EXE = "LID DB Mod Manager"
TOP = "LID DB Mod Manager 9.1.0"
TAG = "Release_Beta_V9.1.0"
POSIX = os.name == "posix"


def case_sensitive(folder: Path) -> bool:
    probe = Path(folder) / "Case-Probe"
    probe.write_bytes(b"")
    try:
        return not (Path(folder) / "case-probe").exists()
    finally:
        probe.unlink()


class TheGameUnderProton(unittest.TestCase):
    """/proc, as Linux lays it out, with Wine's names for the game."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.proc = Path(self._tmp.name)
        (self.proc / "self").mkdir()        # not a process: skipped
        self.process(1, "systemd", b"/sbin/init\0")
        self.process(77, "steam", b"/home/me/.local/share/Steam/ubuntu12_32/steam\0-silent\0")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def process(self, pid: int, comm: str, cmdline: bytes) -> None:
        folder = self.proc / str(pid)
        folder.mkdir()
        (folder / "comm").write_text(comm + "\n", encoding="utf-8")
        (folder / "cmdline").write_bytes(cmdline)

    def running(self) -> bool:
        return asset_runner._linux_process_running(asset_runner.GAME_PROCESS, self.proc)

    def test_nothing_running(self) -> None:
        self.assertFalse(self.running())

    def test_by_its_short_name(self) -> None:
        # Linux keeps 15 characters of a process name: "BrgGame-Steam.e".
        self.process(4242, "BrgGame-Steam.e", b"")
        self.assertTrue(self.running())

    def test_by_the_windows_path_wine_started(self) -> None:
        self.process(4242, "wine64-preloader",
                     b"Z:\\home\\me\\.local\\share\\Steam\\steamapps\\common\\LET IT DIE\\"
                     b"Binaries\\Win64\\BrgGame-Steam.exe\0-seekfreeloading\0")
        self.assertTrue(self.running())

    def test_a_process_that_ends_while_read_is_skipped(self) -> None:
        (self.proc / "999").mkdir()         # no comm, no cmdline any more
        self.assertFalse(self.running())

    def test_no_proc_at_all_says_no(self) -> None:
        self.assertFalse(asset_runner._linux_process_running("BrgGame-Steam.exe",
                                                             self.proc / "missing"))

    def test_linux_reads_proc_and_other_systems_say_no(self) -> None:
        with mock.patch.object(asset_runner.sys, "platform", "linux"), \
                mock.patch.object(asset_runner, "_linux_process_running",
                                  return_value=True) as linux:
            self.assertTrue(asset_runner.game_running())
        linux.assert_called_once_with(asset_runner.GAME_PROCESS)
        with mock.patch.object(asset_runner.sys, "platform", "darwin"):
            self.assertFalse(asset_runner.game_running())


class SteamOnLinux(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def roots(self) -> list[Path]:
        with mock.patch.object(steam_locate.sys, "platform", "linux"), \
                mock.patch.object(steam_locate.Path, "home", return_value=self.home):
            return steam_locate.steam_roots()

    def test_the_usual_flatpak_and_snap_folders(self) -> None:
        for rel in (".local/share/Steam", ".var/app/com.valvesoftware.Steam/.local/share/Steam",
                    "snap/steam/common/.local/share/Steam"):
            (self.home / rel).mkdir(parents=True)
        self.assertEqual(self.roots(), [(self.home / rel).resolve() for rel in (
            ".local/share/Steam", ".var/app/com.valvesoftware.Steam/.local/share/Steam",
            "snap/steam/common/.local/share/Steam")])

    def test_no_windows_folder_on_linux(self) -> None:
        self.assertEqual(self.roots(), [])

    @unittest.skipUnless(POSIX, "links need Linux")
    def test_steams_own_links_count_once(self) -> None:
        real = self.home / ".local/share/Steam"
        real.mkdir(parents=True)
        (self.home / ".steam").mkdir()
        (self.home / ".steam/steam").symlink_to(real)
        (self.home / ".steam/root").symlink_to(real)
        self.assertEqual(self.roots(), [real.resolve()])

    def test_the_game_is_found_in_a_linux_library(self) -> None:
        steam = self.home / ".local/share/Steam"
        other = self.home / "Games/SteamLibrary"
        (steam / "steamapps").mkdir(parents=True)
        (steam / "steamapps/libraryfolders.vdf").write_text(
            f'"libraryfolders"\n{{\n\t"0"\n\t{{\n\t\t"path"\t\t"{steam.as_posix()}"\n\t}}\n'
            f'\t"1"\n\t{{\n\t\t"path"\t\t"{other.as_posix()}"\n\t}}\n}}\n', encoding="utf-8")
        db = other / "steamapps/common" / steam_locate.GAME_DIR / steam_locate.DB_REL
        db.parent.mkdir(parents=True)
        db.write_bytes(b"db")
        with mock.patch.object(steam_locate.sys, "platform", "linux"), \
                mock.patch.object(steam_locate.Path, "home", return_value=self.home):
            found = steam_locate.find_game_database(drives=[])
        self.assertEqual(found.resolve(), db.resolve())


class FileNamesInOtherCapitals(unittest.TestCase):
    """A mod made on Windows may spell a game file in other capitals."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.upk = self.root / "BrgGame" / "CookedPCConsole" / "BrgGame.upk"
        self.upk.parent.mkdir(parents=True)
        self.upk.write_bytes(b"stock")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_the_games_own_file_is_the_one_written(self) -> None:
        dest = asset_runner._resolve_target(self.root, "brggame/cookedpcconsole/brggame.upk")
        self.assertTrue(dest.is_file())
        self.assertTrue(dest.samefile(self.upk))

    def test_backslashes_too(self) -> None:
        dest = asset_runner._resolve_target(self.root, "BRGGAME\\CookedPCConsole\\BrgGame.upk")
        self.assertTrue(dest.samefile(self.upk))

    def test_a_new_file_keeps_the_mods_name_in_the_real_folder(self) -> None:
        dest = asset_runner._resolve_target(self.root, "brggame/cookedpcconsole/New_SF.upk")
        self.assertEqual(dest.name, "New_SF.upk")
        self.assertTrue(dest.parent.samefile(self.upk.parent))

    def test_written_as_is_when_it_already_matches(self) -> None:
        self.assertEqual(asset_runner._match_case(self.root, "BrgGame/CookedPCConsole/BrgGame.upk"),
                         "BrgGame/CookedPCConsole/BrgGame.upk")

    def test_escaping_the_game_folder_is_still_refused(self) -> None:
        for bad in ("../evil.upk", "BrgGame/../../evil.upk", "/abs/evil.upk"):
            with self.assertRaises(OSError, msg=bad):
                asset_runner._resolve_target(self.root, bad)

    def test_two_names_that_differ_only_in_capitals_are_left_alone(self) -> None:
        if not case_sensitive(self.root):
            self.skipTest("needs a file system that tells capitals apart")
        (self.upk.parent / "BRGGAME.UPK").write_bytes(b"other")
        self.assertEqual(asset_runner._match_case(self.root, "BrgGame/CookedPCConsole/brggame.upk"),
                         "BrgGame/CookedPCConsole/brggame.upk")


class TheLinuxProgram(unittest.TestCase):
    def test_carry_over_knows_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old = Path(tmp)
            (old / "state.json").write_text('{"db_path": "x"}', encoding="utf-8")
            (old / carry_over.LINUX_EXE_NAME).write_bytes(b"program")
            self.assertIsNotNone(carry_over.find_install(old))
            self.assertIsNotNone(carry_over.find_install(old / carry_over.LINUX_EXE_NAME))
        self.assertTrue(carry_over.is_program_file(Path("x") / carry_over.EXE_NAME))
        self.assertFalse(carry_over.is_program_file(Path("x") / "readme.txt"))


# -- the Linux release ---------------------------------------------------------


def a_linux_release(files: dict[str, bytes] | None = None, links: dict[str, str] | None = None,
                    extra: list[tarfile.TarInfo] | None = None) -> bytes:
    files = files if files is not None else {
        f"{TOP}/{EXE}": b"new program",
        f"{TOP}/_internal/libpython3.13.so.1.0": b"python",
        f"{TOP}/mods/Shipped/mod.json": b'{"version": "2"}',
    }
    links = links if links is not None else {f"{TOP}/_internal/libpython3.13.so": "libpython3.13.so.1.0"}
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o755 if name.endswith(EXE) else 0o644
            archive.addfile(info, io.BytesIO(data))
        for name, target in links.items():
            info = tarfile.TarInfo(name)
            info.type = tarfile.SYMTYPE
            info.linkname = target
            archive.addfile(info)
        for info in extra or []:
            archive.addfile(info, io.BytesIO(b"x" * info.size) if info.isfile() else None)
    return buffer.getvalue()


def asset(name: str, packed: bytes) -> dict:
    return {"name": name, "size": len(packed),
            "digest": "sha256:" + hashlib.sha256(packed).hexdigest(),
            "browser_download_url": f"{U.DOWNLOAD_PREFIX}{TAG}/{name}"}


class PickingTheDownload(unittest.TestCase):
    def setUp(self) -> None:
        self.windows = asset("LID-DB-Mod-Manager-9.1.0-win64.zip", b"zip")
        self.linux = asset("LID-DB-Mod-Manager-9.1.0-linux.tar.gz", b"tar")

    def test_each_system_gets_its_own(self) -> None:
        both = [self.windows, self.linux]
        self.assertEqual(U._pick_asset(both, linux=False).name, self.windows["name"])
        self.assertEqual(U._pick_asset(both, linux=True).name, self.linux["name"])

    def test_a_release_with_no_linux_build_offers_linux_nothing(self) -> None:
        self.assertIsNone(U._pick_asset([self.windows], linux=True))

    def test_the_linux_one_needs_its_hash_and_github_too(self) -> None:
        self.assertIsNone(U._pick_asset([dict(self.linux, digest=None)], linux=True))
        self.assertIsNone(U._pick_asset(
            [dict(self.linux, browser_download_url="https://example.com/x-linux.tar.gz")], linux=True))

    def test_copies_from_before_linux_still_see_one_zip(self) -> None:
        # Older copies took the release's one .zip; a tar.gz beside it must
        # not make that two.
        zips = [a for a in (self.windows, self.linux) if a["name"].lower().endswith(".zip")]
        self.assertEqual(len(zips), 1)


class UnpackingTheLinuxRelease(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name) / "LID DB Mod Manager 0.13.0"
        self.folder.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def fetch(self, packed: bytes) -> Path:
        info = asset("LID-DB-Mod-Manager-9.1.0-linux.tar.gz", packed)

        def opener(url):
            if url == U.LATEST_URL:
                import json
                return io.BytesIO(json.dumps({"tag_name": TAG, "assets": [info]}).encode())
            return io.BytesIO(packed)

        with mock.patch.object(U, "on_linux", return_value=True):
            release = U.check(opener)
        return U.fetch(release, self.folder, EXE, opener=opener)

    @unittest.skipUnless(POSIX, "links and run permissions need Linux")
    def test_it_unpacks_with_its_links_and_run_permission(self) -> None:
        new = self.fetch(a_linux_release())
        self.assertEqual(new, self.folder / U.UPDATE_DIR / "9.1.0" / TOP)
        self.assertEqual((new / EXE).read_bytes(), b"new program")
        self.assertTrue((new / EXE).stat().st_mode & stat.S_IXUSR, "the program can run")
        link = new / "_internal/libpython3.13.so"
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.read_bytes(), b"python")
        self.assertFalse(list((self.folder / U.UPDATE_DIR).glob("*.tar.gz")), "the download is removed")

    def test_without_links_it_unpacks_anywhere(self) -> None:
        new = self.fetch(a_linux_release(links={}))
        self.assertEqual((new / EXE).read_bytes(), b"new program")

    def test_a_file_reaching_outside_is_refused(self) -> None:
        with self.assertRaises(U.UpdateError):
            self.fetch(a_linux_release({f"{TOP}/{EXE}": b"x", "../../evil": b"x"}, links={}))
        self.assertFalse((self.folder.parent / "evil").exists())

    def test_a_link_reaching_outside_is_refused(self) -> None:
        with self.assertRaises(U.UpdateError):
            self.fetch(a_linux_release(links={f"{TOP}/_internal/x": "/etc/passwd"}))

    def test_something_that_is_not_a_tar_is_refused(self) -> None:
        with self.assertRaises(U.UpdateError):
            self.fetch(b"not a tar at all")

    @unittest.skipUnless(POSIX, "links and run permissions need Linux")
    def test_installing_keeps_links_and_run_permission(self) -> None:
        target = self.folder
        (target / "_internal").mkdir()
        (target / EXE).write_bytes(b"old program")
        (target / "state.json").write_bytes(b"mine")
        new = self.fetch(a_linux_release())
        U.install(new, target)
        self.assertEqual((target / EXE).read_bytes(), b"new program")
        self.assertTrue((target / EXE).stat().st_mode & stat.S_IXUSR)
        self.assertTrue((target / "_internal/libpython3.13.so").is_symlink())
        self.assertEqual((target / "state.json").read_bytes(), b"mine")

    def test_restarting_detaches_the_new_copy(self) -> None:
        with mock.patch.object(U.subprocess, "Popen") as popen, \
                mock.patch.object(U.sys, "platform", "linux"):
            U.restart(Path("/opt/new"), EXE, ["x"])
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        self.assertNotIn("creationflags", popen.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()
