"""The program updating itself from its GitHub releases page.

Nothing here goes online: the opener that would fetch a URL serves a release
built in memory, laid out the way the real zip is.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import zipfile
from pathlib import Path
from unittest import mock

from lid_db_manager import self_update as U

EXE = "LID DB Mod Manager.exe"
TOP = "LID DB Mod Manager"
TAG = "Release_Beta_V9.1.0"


def a_release_zip(files: dict[str, bytes] | None = None) -> bytes:
    files = files if files is not None else {
        f"{TOP}/{EXE}": b"new exe",
        f"{TOP}/_internal/base_library.zip": b"new library",
        f"{TOP}/_internal/new_only.dll": b"new dll",
        f"{TOP}/mods/Shipped/mod.json": b'{"version": "2"}',
        f"{TOP}/mods/_templates/readme.txt": b"templates",
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


class GitHub:
    """The releases API and the release download, served from memory."""

    def __init__(self, packed: bytes | None = None, **asset) -> None:
        self.packed = a_release_zip() if packed is None else packed
        self.requested: list[str] = []
        name = asset.pop("name", "LID.DB.Mod.Manager.zip")
        self.asset = {
            "name": name,
            "size": len(self.packed),
            "digest": "sha256:" + hashlib.sha256(self.packed).hexdigest(),
            "browser_download_url": f"{U.DOWNLOAD_PREFIX}{TAG}/{name}",
        }
        self.asset.update(asset)
        self.assets = [self.asset]

    def answer(self) -> dict:
        return {"tag_name": TAG, "name": "Beta V9.1.0", "body": "What changed.",
                "html_url": f"https://github.com/{U.REPO}/releases/tag/{TAG}",
                "assets": self.assets}

    def opener(self, url: str):
        self.requested.append(url)
        if url == U.LATEST_URL:
            return io.BytesIO(json.dumps(self.answer()).encode())
        if url == self.asset["browser_download_url"]:
            return io.BytesIO(self.packed)
        raise urllib.error.HTTPError(url, 404, "Not Found", None, None)


def offline(url: str):
    raise urllib.error.URLError("no route to host")


def tree(folder: Path) -> dict[str, bytes]:
    return {p.relative_to(folder).as_posix(): p.read_bytes()
            for p in sorted(folder.rglob("*")) if p.is_file()}


class Versions(unittest.TestCase):
    def test_the_number_is_read_out_of_the_tag(self) -> None:
        self.assertEqual(U.version_numbers("Release_Beta_V0.10.1"), (0, 10, 1))
        self.assertEqual(U.version_numbers("Release_Beta_V0.10"), (0, 10))
        self.assertEqual(U.version_numbers("no number"), ())

    def test_newer(self) -> None:
        self.assertTrue(U.newer("0.11.0", "0.10.1"))
        self.assertTrue(U.newer("0.10.10", "0.10.9"))   # not compared as text
        self.assertFalse(U.newer("0.10", "0.10.0"))
        self.assertFalse(U.newer("0.10.1", "0.10.1"))
        self.assertFalse(U.newer("0.9.9", "0.10.0"))
        self.assertFalse(U.newer("", "0.10.0"))


class Checking(unittest.TestCase):
    def test_it_reads_the_newest_release(self) -> None:
        github = GitHub()
        release = U.check(github.opener)
        self.assertEqual(github.requested, [U.LATEST_URL])
        self.assertEqual(release.version, "9.1.0")
        self.assertEqual(release.notes, "What changed.")
        self.assertTrue(release.is_newer_than("0.10.1"))
        self.assertEqual(release.asset.sha256, hashlib.sha256(github.packed).hexdigest())

    def test_a_zip_without_a_listed_hash_is_not_offered(self) -> None:
        github = GitHub(digest=None)
        self.assertIsNone(U.check(github.opener).asset)

    def test_nor_one_from_anywhere_else(self) -> None:
        github = GitHub(browser_download_url="https://example.com/LID.DB.Mod.Manager.zip")
        self.assertIsNone(U.check(github.opener).asset)

    def test_of_several_zips_the_one_named_after_the_program(self) -> None:
        github = GitHub()
        other = dict(github.asset, name="Extra.Mods.zip",
                     browser_download_url=f"{U.DOWNLOAD_PREFIX}{TAG}/Extra.Mods.zip")
        github.assets = [other, github.asset]
        self.assertEqual(U.check(github.opener).asset.name, "LID.DB.Mod.Manager.zip")

    def test_offline_and_rate_limited_say_so(self) -> None:
        with self.assertRaises(U.UpdateError) as caught:
            U.check(offline)
        self.assertIn("internet", str(caught.exception))

        def limited(url):
            raise urllib.error.HTTPError(url, 403, "Forbidden", None, None)
        with self.assertRaises(U.UpdateError) as caught:
            U.check(limited)
        self.assertIn("hour", str(caught.exception))


class Fetching(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.parent = Path(self._tmp.name)
        self.folder = self.parent / "LID DB Mod Manager 0.10.2"
        self.folder.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def fetch(self, github: GitHub) -> Path:
        return U.fetch(U.check(github.opener), self.folder, EXE, opener=github.opener)

    def test_it_unpacks_inside_the_program_folder_not_over_it(self) -> None:
        new = self.fetch(GitHub())
        self.assertEqual(new, self.folder / U.UPDATE_DIR / "9.1.0" / TOP)
        self.assertEqual((new / EXE).read_bytes(), b"new exe")
        self.assertEqual(sorted(p.name for p in (self.folder / U.UPDATE_DIR).iterdir()),
                         ["9.1.0"], "the zip is not left behind")
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), [U.UPDATE_DIR])
        self.assertEqual([p.name for p in self.parent.iterdir()], [self.folder.name],
                         "nothing is made beside the program folder")

    def test_a_download_that_does_not_match_its_hash_is_not_used(self) -> None:
        with self.assertRaises(U.UpdateError):
            self.fetch(GitHub(digest="sha256:" + "0" * 64))
        self.assertEqual(list((self.folder / U.UPDATE_DIR).iterdir()), [])

    def test_a_zip_reaching_outside_its_folder_is_refused(self) -> None:
        github = GitHub(a_release_zip({f"{TOP}/{EXE}": b"x", "../../evil.exe": b"x"}))
        with self.assertRaises(U.UpdateError):
            self.fetch(github)
        self.assertFalse((self.folder.parent / "evil.exe").exists())

    def test_a_zip_without_the_program_in_it_is_refused(self) -> None:
        with self.assertRaises(U.UpdateError):
            self.fetch(GitHub(a_release_zip({"readme.txt": b"hello"})))


class Installing(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.target = root / "LID DB Mod Manager 0.10.2"
        old = {
            EXE: b"old exe",
            "_internal/base_library.zip": b"old library",
            "_internal/old_only.dll": b"old dll",
            "mods/Shipped/mod.json": b'{"version": "1"}',
            "mods/My Own Mod/mod.json": b"mine",
            "state.json": b'{"enabled_mods": ["Shipped"]}',
            "snapshots/Shipped/rows.json": b"rows",
            "backups/game_files/BrgGame.upk": b"stock",
            "cache/textures.json": b"cache",
            "logs/session.log": b"log",
            "LiD Vanilla DB/5.0.4.2/masters.db": b"clean",
        }
        for name, data in old.items():
            path = self.target / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        self.before = tree(self.target)
        packed = root / "new.zip"
        packed.write_bytes(a_release_zip({
            f"{TOP} 9.1.0/{EXE}": b"new exe",
            f"{TOP} 9.1.0/_internal/base_library.zip": b"new library",
            f"{TOP} 9.1.0/_internal/new_only.dll": b"new dll",
            f"{TOP} 9.1.0/mods/Shipped/mod.json": b'{"version": "2"}',
            f"{TOP} 9.1.0/mods/New Mod/mod.json": b"new mod",
            # A release zipped with someone's own state in it must not cost
            # the player theirs.
            f"{TOP} 9.1.0/state.json": b"{}",
            f"{TOP} 9.1.0/backups/x": b"x",
        }))
        self.new = U.unpack(packed, self.target / U.UPDATE_DIR / "9.1.0", EXE)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_program_files_are_swapped_and_the_players_are_not(self) -> None:
        U.install(self.new, self.target)
        after = tree(self.target)
        self.assertEqual(after[EXE], b"new exe")
        self.assertEqual(after["_internal/base_library.zip"], b"new library")
        self.assertIn("_internal/new_only.dll", after)
        self.assertNotIn("_internal/old_only.dll", after, "_internal is replaced whole")
        self.assertEqual(after["mods/Shipped/mod.json"], b'{"version": "2"}')
        self.assertEqual(after["mods/New Mod/mod.json"], b"new mod")
        for name in ("mods/My Own Mod/mod.json", "state.json", "snapshots/Shipped/rows.json",
                     "backups/game_files/BrgGame.upk", "cache/textures.json",
                     "logs/session.log", "LiD Vanilla DB/5.0.4.2/masters.db"):
            self.assertEqual(after[name], self.before[name], name)
        self.assertNotIn("backups/x", after)
        previous = f"{U.UPDATE_DIR}/{U.PREVIOUS}"
        self.assertEqual(after[f"{previous}/{EXE}"], b"old exe")
        self.assertEqual(after[f"{previous}/mods/Shipped/mod.json"], b'{"version": "1"}')

    def test_the_folder_keeps_its_name_so_shortcuts_still_work(self) -> None:
        U.install(self.new, self.target)
        self.assertEqual(self.target.name, "LID DB Mod Manager 0.10.2")
        self.assertEqual((self.target / EXE).read_bytes(), b"new exe")

    def test_a_failure_part_way_puts_everything_back(self) -> None:
        real = U.shutil.copy2
        calls = []

        def flaky(source, destination, *args, **kwargs):
            calls.append(source)
            if len(calls) == 3:
                raise OSError("disk full")
            return real(source, destination, *args, **kwargs)

        with mock.patch.object(U.shutil, "copy2", flaky):
            with self.assertRaises(U.UpdateError) as caught:
                U.install(self.new, self.target)
        self.assertIn("put back", str(caught.exception))
        after = {k: v for k, v in tree(self.target).items()
                 if not k.startswith(U.UPDATE_DIR + "/")}
        self.assertEqual(after, self.before)

    def test_clean_up_takes_the_leftovers(self) -> None:
        U.install(self.new, self.target)
        U.clean_up(self.target)
        self.assertFalse((self.target / U.UPDATE_DIR).exists())
        self.assertEqual(tree(self.target)[EXE], b"new exe")


class ThisCopy(unittest.TestCase):
    def test_from_source_it_says_so(self) -> None:
        with mock.patch.object(U.sys, "frozen", False, create=True):
            self.assertIn("source", U.why_not_here())

    def test_a_built_folder_can_update_itself(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(U.sys, "frozen", True, create=True):
            folder = Path(tmp)
            self.assertIn("folder version", U.why_not_here(folder))
            (folder / U.INTERNAL).mkdir()
            self.assertEqual(U.why_not_here(folder), "")
            self.assertEqual(list(folder.iterdir()), [folder / U.INTERNAL])
            self.assertNotIn("write-test", " ".join(p.name for p in folder.parent.iterdir()))

    def test_the_new_copy_is_started_with_what_it_needs(self) -> None:
        with mock.patch.object(U.subprocess, "Popen") as popen:
            U.hand_over(Path("C:/new"), EXE, Path("C:/old"), "0.10.1")
        args = popen.call_args.args[0]
        self.assertEqual(args, [str(Path("C:/new") / EXE), U.FINISH_FLAG, str(Path("C:/old")),
                                str(os.getpid()), "0.10.1"])

    def test_waiting_for_the_old_one_to_close(self) -> None:
        self.assertTrue(U.wait_for_exit(os.getpid()))
        running = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            self.assertFalse(U.wait_for_exit(running.pid, timeout=0.2))
        finally:
            running.kill()
            running.wait()
        self.assertTrue(U.wait_for_exit(running.pid, timeout=5))


class StartingUp(unittest.TestCase):
    def test_the_update_steps_go_to_the_window_not_the_command_line(self) -> None:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        import run

        for flag in (U.FINISH_FLAG, U.DONE_FLAG):
            with mock.patch.object(sys, "argv", ["run.py", flag, "x"]), \
                    mock.patch.object(run, "_run_gui", return_value=0) as gui, \
                    mock.patch("lid_db_manager.cli.main") as cli:
                run.main()
            gui.assert_called_once()
            cli.assert_not_called()


try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QMessageBox

    HAVE_QT = True
except ImportError:  # pragma: no cover - depends on the environment
    HAVE_QT = False


@unittest.skipUnless(HAVE_QT, "PySide6 is not installed")
class FinishingInTheNewCopy(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_it_swaps_the_files_then_starts_the_updated_program(self) -> None:
        from lid_db_manager.ui import app as app_module

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "program"
            (target / "_internal").mkdir(parents=True)
            (target / EXE).write_bytes(b"old exe")
            (target / "state.json").write_bytes(b"mine")
            packed = Path(tmp) / "new.zip"
            packed.write_bytes(a_release_zip())
            new = U.unpack(packed, target / U.UPDATE_DIR / "9.1.0", EXE)
            with mock.patch.object(U, "program_dir", return_value=new), \
                    mock.patch.object(U, "exe_name", return_value=EXE), \
                    mock.patch.object(U, "wait_for_exit", return_value=True), \
                    mock.patch.object(U, "restart") as restart:
                code = app_module.finish_update([str(target), "1234", "0.10.1"])
            self.assertEqual(code, 0)
            self.assertEqual((target / EXE).read_bytes(), b"new exe")
            self.assertEqual((target / "state.json").read_bytes(), b"mine")
            folder, exe, args = restart.call_args.args
            self.assertEqual((folder, exe), (target, EXE))
            self.assertEqual(args[:2], [U.DONE_FLAG, "0.10.1"])

    def test_if_it_cannot_the_old_version_opens_again(self) -> None:
        from lid_db_manager.ui import app as app_module

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "program"
            target.mkdir()
            (target / EXE).write_bytes(b"old exe")
            with mock.patch.object(U, "program_dir", return_value=Path(tmp) / "new"), \
                    mock.patch.object(U, "exe_name", return_value=EXE), \
                    mock.patch.object(U, "wait_for_exit", return_value=True), \
                    mock.patch.object(U, "install", side_effect=U.UpdateError("no")), \
                    mock.patch.object(app_module.QMessageBox, "critical"), \
                    mock.patch.object(U, "restart") as restart:
                self.assertEqual(app_module.finish_update([str(target), "1234", "0.10.1"]), 1)
            restart.assert_called_once_with(target, EXE, [])


@unittest.skipUnless(HAVE_QT, "PySide6 is not installed")
class FromTheWindow(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        from lid_db_manager.manager import Manager
        from lid_db_manager.paths import AppPaths
        from lid_db_manager.ui import main_window as mw

        self.mw = mw
        self._tmp = tempfile.TemporaryDirectory()
        self._first_run = mw.MainWindow._first_run_checks
        mw.MainWindow._first_run_checks = lambda self, **kwargs: None
        self.manager = Manager(AppPaths(Path(self._tmp.name) / "manager").ensure())
        self.window = mw.MainWindow(self.manager)
        self.github = GitHub()
        self.release = U.check(self.github.opener)

    def tearDown(self) -> None:
        from test_ui import destroy
        self.mw.MainWindow._first_run_checks = self._first_run
        destroy(self.window)
        self._tmp.cleanup()

    def offer(self, result, *, quiet: bool, press: str = "Not now", can_update: str = ""):
        """Show the answer to a check; returns the box's buttons, or None if
        nothing was shown."""
        shown = []

        def exec_(box):
            shown.append([b.text() for b in box.buttons()])
            for button in box.buttons():
                if button.text() == press:
                    button.click()
                    return
            raise AssertionError(f"no {press!r} button in {shown[-1]}")

        with mock.patch.object(self.mw.QMessageBox, "exec", exec_), \
                mock.patch.object(self.mw.QMessageBox, "information") as info, \
                mock.patch.object(self.mw.QMessageBox, "warning") as warning, \
                mock.patch.object(self.mw.self_update, "why_not_here", return_value=can_update), \
                mock.patch.object(self.window, "update_now") as update_now, \
                mock.patch.object(self.mw.QDesktopServices, "openUrl") as open_url:
            self.window._after_update_check(result, quiet=quiet)
        self.update_now, self.open_url, self.info, self.warning = (
            update_now, open_url, info, warning)
        return shown[0] if shown else None

    def test_both_menus_have_it(self) -> None:
        tools = [a.text() for menu in self.window.menuBar().actions()
                 if menu.text() == "&Tools" for a in menu.menu().actions()]
        helps = [a.text() for menu in self.window.menuBar().actions()
                 if menu.text() == "&Help" for a in menu.menu().actions()]
        self.assertIn("Check for updates...", tools)
        self.assertIn("Check for updates...", helps)
        self.assertIn("Check for updates when it starts", helps)

    def test_the_check_at_start_is_off_until_switched_on(self) -> None:
        self.assertFalse(self.manager.state.settings.check_for_updates)
        self.window.update_at_start_action.setChecked(True)
        self.assertTrue(json.loads(self.manager.paths.state_file.read_text())
                        ["settings"]["check_for_updates"])

    def test_a_newer_version_is_offered(self) -> None:
        buttons = self.offer(self.release, quiet=False, press="Update now")
        self.assertCountEqual([b for b in buttons if b != "Show Details..."],
                              ["Update now", "Open the release page", "Not now"])
        self.update_now.assert_called_once_with(self.release)

    def test_where_it_cannot_update_itself_it_offers_the_page(self) -> None:
        buttons = self.offer(self.release, quiet=False, press="Open the release page",
                             can_update="This copy runs from source.")
        self.assertNotIn("Update now", buttons)
        self.open_url.assert_called_once()
        self.update_now.assert_not_called()

    def test_up_to_date_says_so_only_when_asked(self) -> None:
        older = U.Release("0.0.1", "v0.0.1", "", "", "", None)
        self.assertIsNone(self.offer(older, quiet=False))
        self.info.assert_called_once()
        self.assertIsNone(self.offer(older, quiet=True))
        self.info.assert_not_called()

    def test_a_skipped_version_is_not_raised_again_at_start(self) -> None:
        self.assertIn("Skip this version",
                      self.offer(self.release, quiet=True, press="Skip this version"))
        self.assertEqual(self.manager.state.settings.skipped_update, "9.1.0")
        self.assertIsNone(self.offer(self.release, quiet=True))
        self.assertIsNotNone(self.offer(self.release, quiet=False), "asking still offers it")

    def test_a_failed_check_at_start_stays_quiet(self) -> None:
        self.assertIsNone(self.offer(U.UpdateError("offline"), quiet=True))
        self.warning.assert_not_called()

    def test_once_downloaded_it_hands_over_and_closes(self) -> None:
        with mock.patch.object(self.mw.self_update, "hand_over") as hand_over, \
                mock.patch.object(self.window, "_later") as later:
            self.window._after_update_fetch(Path("C:/new"), self.release, Path("C:/old"), EXE)
        hand_over.assert_called_once_with(Path("C:/new"), EXE, Path("C:/old"))
        self.assertTrue(self.manager.state.read_only)
        later.assert_called_once_with(self.mw._quit_for_update)


if __name__ == "__main__":
    unittest.main()
