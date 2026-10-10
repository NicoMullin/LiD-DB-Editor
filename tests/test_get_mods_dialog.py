"""The Get mods window, driven against a fake GitHub (see test_get_mods.py)."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from fixtures import build_db
from test_get_mods import LOADER_BYTES, FakeGitHub, package_zip

from lid_db_manager import get_mods as G
from lid_db_manager.manager import Manager
from lid_db_manager.paths import AppPaths

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication, QMessageBox

    HAVE_QT = True
except ImportError:  # pragma: no cover - depends on the environment
    HAVE_QT = False


@unittest.skipUnless(HAVE_QT, "needs PySide6")
class TheWindow(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        from lid_db_manager.ui.get_mods_dialog import GetModsDialog

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.paths = AppPaths(root / "app").ensure()
        self.game = root / "game"
        G.win64(self.game).mkdir(parents=True)
        self.manager = Manager(self.paths)
        self.manager.set_db_path(build_db(self.game / "BrgGame" / "masters.db"))
        self.manager.set_game_root_override(self.game)
        self.github = FakeGitHub()
        self.github.publish(G.LOADER_OFFER, "v1.0.0", package_zip("Plugin Loader", "1.0.0", loader=True))
        self.github.publish(G.offer("timers"), "v0.2.0", package_zip(
            "Timers", "0.2.0", mods=["Timers"], plugins=["lid-timers.dll"]))
        self.dialog = GetModsDialog(self.manager, opener=self.github)
        self.addCleanup(self.dialog.deleteLater)

    def wait(self) -> None:
        deadline = time.monotonic() + 20
        while self.dialog.task is not None and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.app.processEvents()
        self.assertIsNone(self.dialog.task, "the background work did not finish")

    def row(self, key: str) -> int:
        return [entry.key for entry in G.CATALOG].index(key)

    def test_it_lists_every_mod_with_what_you_have(self) -> None:
        self.assertEqual(self.dialog.table.rowCount(), len(G.CATALOG))
        self.assertEqual(self.dialog.table.item(self.row("timers"), 1).text(), "-")
        self.assertIn("no Plugin Loader yet", self.dialog.where.text())

    def test_checking_github_fills_in_the_newest(self) -> None:
        self.dialog.check()
        self.wait()
        self.assertEqual(self.dialog.table.item(self.row("timers"), 2).text(), "0.2.0")
        self.assertEqual(self.dialog.table.item(self.row("loader"), 2).text(), "1.0.0")
        # no release yet: a ? with the reason on hover
        self.assertEqual(self.dialog.table.item(self.row("deathmetals"), 2).text(), "?")
        self.assertIn("no release", self.dialog.table.item(self.row("deathmetals"), 2).toolTip())

    def test_get_brings_the_loader_too_after_asking(self) -> None:
        asked = []

        def answer(box):
            asked.append(box.informativeText())
            return QMessageBox.StandardButton.Yes

        with mock.patch.object(QMessageBox, "exec", answer), \
                mock.patch.object(QMessageBox, "information"):
            self.dialog.get("timers")
            self.wait()
            self.wait()
        self.assertEqual(len(asked), 1)
        self.assertIn("dinput8.dll", asked[0])
        self.assertIn("lid-timers.dll", asked[0])
        self.assertEqual((G.win64(self.game) / "dinput8.dll").read_bytes(), LOADER_BYTES)
        self.assertTrue((G.win64(self.game) / "lid-plugins" / "lid-timers.dll").is_file())
        self.assertTrue((self.paths.mods_dir / "Timers" / "mod.json").is_file())
        self.assertTrue(self.dialog.changed)
        self.assertEqual(self.dialog.table.item(self.row("timers"), 1).text(), "mod 0.2.0, plugin")
        self.assertEqual(self.dialog.buttons["timers"].text(), "Update")
        self.assertFalse(self.dialog._work_dir().exists())     # the download is cleared away

    def test_saying_no_writes_nothing(self) -> None:
        with mock.patch.object(QMessageBox, "exec", lambda box: QMessageBox.StandardButton.Cancel):
            self.dialog.get("timers")
            self.wait()
        self.assertFalse((G.win64(self.game) / "dinput8.dll").exists())
        self.assertFalse((self.paths.mods_dir / "Timers").exists())
        self.assertIn("Nothing was installed", self.dialog.status.text())

    def test_someone_elses_dinput8_stops_it_before_any_download(self) -> None:
        (G.win64(self.game) / "dinput8.dll").write_bytes(b"MZ foreign")
        with mock.patch.object(QMessageBox, "warning") as warned:
            self.dialog.get("timers")
        warned.assert_called_once()
        self.assertIsNone(self.dialog.task)


if __name__ == "__main__":
    unittest.main()
