"""Clean databases downloaded from the manager's GitHub repository.

Nothing here goes online: the opener that would fetch a URL is swapped for one
that serves the repository's listing and files from memory, the way GitHub
would.
"""

from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import tempfile
import unittest
import urllib.error
from pathlib import Path

from lid_db_manager import clean_db_download as C
from lid_db_manager import vanilla_library
from lid_db_manager.progress import Progress

BUILD = "5.0.4.1.0 - 1.89 (Steam 5.0.4.2.0)"


def a_database(path: Path, title: str = "5.0.4.1.0 - 1.89", steam: str = "5.0.4.2.0") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE master_const_str (id TEXT PRIMARY KEY, value TEXT)")
    con.executemany("INSERT INTO master_const_str VALUES (?, ?)",
                    [("TITLE_VERSION", title), ("TITLE_VERSION_STEAM", steam)])
    con.execute("CREATE TABLE master_skill (id TEXT PRIMARY KEY, rarity INT)")
    con.executemany("INSERT INTO master_skill VALUES (?, ?)", [(f"SKL_{i}", i % 5) for i in range(500)])
    con.commit()
    con.close()
    return path


def git_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


class Repository:
    """The repository on GitHub: its file listing and its raw files."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {"README.md": b"hello", "mods/x/mod.json": b"{}"}
        self.listed: dict[str, dict] = {}
        self.requested: list[str] = []

    def commit(self, database: Path, label: str | None = None, **override) -> str:
        label = label or vanilla_library.suggested_label(database)
        path = f"{vanilla_library.DIRNAME}/{label}/{vanilla_library.DB_NAME}"
        data = database.read_bytes()
        self.files[path] = data
        self.listed[path] = dict({"size": len(data), "sha": git_sha(data)}, **override)
        return path

    def opener(self, url: str):
        self.requested.append(url)
        if url == C.INDEX_URL:
            tree = [{"path": "LiD Vanilla DB", "type": "tree", "sha": "0" * 40}]
            for path, data in self.files.items():
                entry = {"path": path, "type": "blob", "size": len(data), "sha": git_sha(data)}
                entry.update(self.listed.get(path, {}))
                tree.append(entry)
            return io.BytesIO(json.dumps({"tree": tree, "truncated": False}).encode())
        for path, data in self.files.items():
            if url == C.file_url(path):
                return io.BytesIO(data)
        raise urllib.error.HTTPError(url, 404, "Not Found", None, None)


def offline(url: str):
    raise urllib.error.URLError("no route to host")


class Labels(unittest.TestCase):
    def test_a_build_is_found_in_the_folder_named_like_the_shipped_ones(self) -> None:
        self.assertEqual(C.label_for(BUILD), "5.0.4.2")
        self.assertEqual(C.label_for("5.0.4.1.0 - 1.89"), "5.0.4.1")
        self.assertEqual(C.label_for("5.0.2.0.0 - 1.86"), "5.0.2.0")

    def test_the_same_rule_that_names_the_folders(self) -> None:
        folder = Path(__file__).resolve().parent.parent / vanilla_library.DIRNAME
        checked = 0
        for database in sorted(folder.glob(f"*/{vanilla_library.DB_NAME}")):
            version = vanilla_library.database_version(database)
            self.assertEqual(C.label_for(version), database.parent.name, database)
            checked += 1
        if not checked:
            self.skipTest("no clean databases in this checkout")


class Downloading(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "manager"
        self.repo = Repository()
        self.clean = a_database(self.tmp / "steam" / "masters.db")

    def builds(self):
        return C.fetch_index(self.repo.opener)

    def test_the_listing_says_what_is_on_offer(self) -> None:
        self.repo.commit(self.clean)
        old = a_database(self.tmp / "old" / "masters.db", title="5.0.3.0.0 - 1.87", steam="5.0.3.0.0")
        self.repo.commit(old)
        self.assertEqual([b.label for b in self.builds()], ["5.0.3.0", "5.0.4.2"])
        self.assertEqual(self.repo.requested, [C.INDEX_URL])

    def test_it_lands_where_the_manager_already_looks(self) -> None:
        self.repo.commit(self.clean)
        reported = []
        kept = C.download(C.find(self.builds(), BUILD), self.root,
                          Progress(lambda text, value: reported.append(value)),
                          self.repo.opener, version=BUILD)
        self.assertEqual(kept.read_bytes(), self.clean.read_bytes())
        self.assertEqual(kept.parent.name, "5.0.4.2")
        found = vanilla_library.for_version(BUILD, self.root)
        self.assertIsNotNone(found)
        self.assertEqual(found.path, kept)
        self.assertEqual(reported[-1], 100)
        self.assertEqual(sorted(p.name for p in kept.parent.iterdir()), ["masters.db"])

    def test_picked_from_the_list_it_must_belong_in_its_folder(self) -> None:
        self.repo.commit(self.clean)
        kept = C.download(self.builds()[0], self.root, opener=self.repo.opener)
        self.assertEqual(vanilla_library.database_version(kept), BUILD)

    def test_a_download_that_does_not_match_is_not_kept(self) -> None:
        self.repo.commit(self.clean, sha="0" * 40)
        with self.assertRaises(C.DownloadError):
            C.download(self.builds()[0], self.root, opener=self.repo.opener)
        self.assertFalse(any((self.root / vanilla_library.DIRNAME).rglob("*.*"))
                         if (self.root / vanilla_library.DIRNAME).exists() else False)

    def test_nor_one_whose_database_is_another_build(self) -> None:
        other = a_database(self.tmp / "other" / "masters.db", title="5.0.3.0.0 - 1.87", steam="")
        self.repo.commit(other, label="5.0.4.2")
        with self.assertRaises(C.DownloadError) as caught:
            C.download(self.builds()[0], self.root, opener=self.repo.opener, version=BUILD)
        self.assertIn("5.0.3.0.0 - 1.87", str(caught.exception))
        self.assertIsNone(vanilla_library.for_version(BUILD, self.root))
        with self.assertRaises(C.DownloadError):
            C.download(self.builds()[0], self.root, opener=self.repo.opener)

    def test_only_files_in_the_clean_database_folders_are_offered(self) -> None:
        self.repo.files["LiD Vanilla DB/../../evil/masters.db"] = b"x"
        self.repo.files["LiD Vanilla DB/5.0.4.2/other.db"] = b"x"
        self.repo.files["elsewhere/5.0.4.2/masters.db"] = b"x"
        self.repo.commit(self.clean)
        self.assertEqual([b.path for b in self.builds()],
                         ["LiD Vanilla DB/5.0.4.2/masters.db"])

    def test_a_copy_already_there_is_never_overwritten(self) -> None:
        self.repo.commit(self.clean)
        mine = a_database(self.root / vanilla_library.DIRNAME / "5.0.4.2" / "masters.db")
        before = mine.read_bytes()
        with self.assertRaises(C.DownloadError):
            C.download(self.builds()[0], self.root, opener=self.repo.opener)
        self.assertEqual(mine.read_bytes(), before)

    def test_offline_or_none_up_says_so(self) -> None:
        with self.assertRaises(C.DownloadError) as caught:
            C.fetch_index(offline)
        self.assertIn("internet", str(caught.exception))
        with self.assertRaises(C.DownloadError) as caught:
            C.fetch_index(self.repo.opener)          # nothing committed
        self.assertIn("no clean databases", str(caught.exception))


try:
    import os as _os
    _os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtWidgets import QApplication

    HAVE_QT = True
except ImportError:  # pragma: no cover - depends on the environment
    HAVE_QT = False


@unittest.skipUnless(HAVE_QT, "PySide6 is not installed")
class FromTheWindow(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        import time
        from lid_db_manager.manager import Manager
        from lid_db_manager.paths import AppPaths
        from lid_db_manager.ui import main_window as mw

        self.mw, self.time = mw, time
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.repo = Repository()
        self.repo.commit(a_database(tmp / "steam" / "masters.db"))
        real_fetch, real_download = C.fetch_index, C.download
        self._real = (real_fetch, real_download)
        mw.clean_db_download.fetch_index = lambda: real_fetch(self.repo.opener)
        mw.clean_db_download.download = (
            lambda build, root, progress=None, version="": real_download(
                build, root, progress, self.repo.opener, version=version))
        self._first_run = mw.MainWindow._first_run_checks
        mw.MainWindow._first_run_checks = lambda self, **kwargs: None
        self.manager = Manager(AppPaths(tmp / "manager").ensure())
        self.window = mw.MainWindow(self.manager)
        self.window.show()

    def tearDown(self) -> None:
        from test_ui import destroy
        self.mw.clean_db_download.fetch_index, self.mw.clean_db_download.download = self._real
        self.mw.MainWindow._first_run_checks = self._first_run
        destroy(self.window)
        self._tmp.cleanup()

    def settle(self) -> None:
        deadline = self.time.monotonic() + 15
        while self.time.monotonic() < deadline:
            QCoreApplication.processEvents()
            if self.window.task is None:
                break
            self.time.sleep(0.01)
        for _ in range(6):
            QCoreApplication.processEvents()

    def test_it_is_on_the_tools_menu(self) -> None:
        texts = [a.text() for menu in self.window.menuBar().actions()
                 for a in menu.menu().actions()]
        self.assertIn("Download a clean database...", texts)

    def test_the_build_it_was_asked_for_arrives_then_what_follows_runs(self) -> None:
        followed = []
        self.window.download_clean_copy(BUILD, then=lambda: followed.append(True))
        self.settle()   # the list
        self.settle()   # the download
        self.assertIsNotNone(vanilla_library.for_version(BUILD, self.manager.paths.root))
        self.assertEqual(followed, [True])


if __name__ == "__main__":
    unittest.main()
