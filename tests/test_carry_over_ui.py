"""Bringing an older copy over, through the window. Offscreen; needs PySide6."""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from fixtures import build_db
from test_carry_over import a_mod, write
from test_ui import destroy

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtWidgets import QApplication

    HAVE_QT = True
except ImportError:  # pragma: no cover - depends on the environment
    HAVE_QT = False


@unittest.skipUnless(HAVE_QT, "PySide6 is not installed")
class BringingAnOldCopyOver(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        from lid_db_manager import carry_over
        from lid_db_manager.manager import Manager
        from lid_db_manager.paths import AppPaths
        from lid_db_manager.ui import main_window as mw

        self.mw = mw
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        db = build_db(tmp / "game" / "masters.db")

        self.old = tmp / "Beta V0.10.1" / "LID DB Mod Manager"
        write(self.old / carry_over.EXE_NAME, "old program")
        a_mod(self.old / "mods" / "my-own-mod")
        write(self.old / "state.json", json.dumps({
            "version": 1, "db_path": str(db), "enabled_mods": ["my-own-mod"],
            "applied": {"my-own-mod": {"version": "1.0.0", "name": "my-own-mod"}},
        }))
        write(self.old / "snapshots" / "my-own-mod.json", '{"mod_id": "my-own-mod"}')

        self.new = tmp / "Beta V0.11" / "LID DB Mod Manager"
        write(self.new / carry_over.EXE_NAME, "new program")
        paths = AppPaths(self.new).ensure()

        # The new window's own first-run questions are not what is tested, and a
        # modal box offscreen would wait forever.
        self._real_first_run = mw.MainWindow._first_run_checks
        mw.MainWindow._first_run_checks = lambda self, **kwargs: None
        self.messages: list[tuple[str, str]] = []
        self._real_box = mw.QMessageBox
        messages = self.messages

        class RecordingBox:
            StandardButton = QMessageBox.StandardButton
            Icon = QMessageBox.Icon

            @staticmethod
            def warning(parent, title, text, *args, **kwargs):
                messages.append(("warning", title))

            @staticmethod
            def information(parent, title, text, *args, **kwargs):
                messages.append(("information", title))

        mw.QMessageBox = RecordingBox

        self.manager = Manager(paths)
        self.assertTrue(self.manager.started_fresh)
        self.window = mw.MainWindow(self.manager)
        self.window._confirm_carry_over = lambda plan: True
        self.window.show()
        self._settle()

    def tearDown(self) -> None:
        self.mw.QMessageBox = self._real_box
        self.mw.MainWindow._first_run_checks = self._real_first_run
        for window in [self.window, *self.mw._OPEN_WINDOWS]:
            destroy(window)
        self.mw._OPEN_WINDOWS.clear()
        self._settle()
        self._tmp.cleanup()

    def _settle(self, seconds: float = 10.0) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            QCoreApplication.processEvents()
            if self.window.task is None:
                break
            time.sleep(0.01)
        for _ in range(6):
            QCoreApplication.processEvents()

    def test_it_is_on_the_tools_menu(self) -> None:
        texts = [a.text() for menu in self.window.menuBar().actions()
                 for a in menu.menu().actions()]
        self.assertIn("Bring over from your old version...", texts)

    def test_its_exe_can_be_dropped(self) -> None:
        from lid_db_manager import carry_over
        self.assertTrue(self.window._droppable(self.old / carry_over.EXE_NAME))

    def test_it_comes_over_and_a_window_opens_on_it(self) -> None:
        self.assertTrue(self.window._carry_over_from(self.old))
        self._settle()
        self.assertEqual((self.new / "state.json").read_text(encoding="utf-8"),
                         (self.old / "state.json").read_text(encoding="utf-8"))
        self.assertTrue((self.new / "mods" / "my-own-mod" / "mod.json").is_file())
        self.assertIn(("information", "Brought over"), self.messages)

        # the old window is closed and can no longer write its empty state
        self.assertTrue(self.manager.state.read_only)
        self.assertFalse(self.window.isVisible())
        self.manager.state.save()
        self.assertIn("my-own-mod", (self.new / "state.json").read_text(encoding="utf-8"))

        # and the one that replaced it is working from the copied state
        self.assertEqual(len(self.mw._OPEN_WINDOWS), 1)
        reopened = self.mw._OPEN_WINDOWS[0].manager
        self.assertEqual(list(reopened.state.applied), ["my-own-mod"])
        self.assertFalse(reopened.state.read_only)

    def test_not_over_mods_this_copy_already_applied(self) -> None:
        from lid_db_manager.state import AppliedRecord
        self.manager.state.applied["something"] = AppliedRecord(version="1.0.0")
        self.assertFalse(self.window._carry_over_from(self.old))
        self.assertEqual(self.messages[-1], ("warning", "Cannot bring that copy over"))
        self.assertFalse(self.manager.state.read_only)
        self.assertFalse((self.new / "mods" / "my-own-mod").exists())


if __name__ == "__main__":
    unittest.main()
