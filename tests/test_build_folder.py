"""build.py names the program's folder after its version.

PyInstaller itself is swapped out: what is tested is what happens around it -
the folder it leaves is renamed "LID DB Mod Manager <version>", and the state a
developer's own copy keeps there survives the rebuild.
"""

from __future__ import annotations

import contextlib
import io
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import build  # noqa: E402
from lid_db_manager import __version__  # noqa: E402

EXE = f"{build.APP_NAME}.exe"


class VersionedFolder(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "mods" / "Shipped").mkdir(parents=True)
        (self.root / "mods" / "Shipped" / "mod.json").write_text("{}")
        self.dist = self.root / "dist"
        self.versioned = self.dist / f"{build.APP_NAME} {__version__}"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def fake_pyinstaller(self, one_file, icon, console) -> Path:
        out = self.dist / build.APP_NAME
        if out.exists():
            shutil.rmtree(out)      # what PyInstaller does to its output folder
        (out / "_internal").mkdir(parents=True)
        (out / EXE).write_text("new build")
        return out

    def run_build(self) -> int:
        fakes = {"PyInstaller": types.ModuleType("PyInstaller"),
                 "PySide6": types.ModuleType("PySide6")}
        with mock.patch.object(build, "ROOT", self.root), \
                mock.patch.object(build, "build", self.fake_pyinstaller), \
                mock.patch.object(sys, "argv", ["build.py"]), \
                mock.patch.dict(sys.modules, fakes), \
                contextlib.redirect_stdout(io.StringIO()):
            return build.main()

    def test_the_folder_carries_the_version(self) -> None:
        self.assertEqual(self.run_build(), 0)
        self.assertEqual(sorted(p.name for p in self.dist.iterdir()), [self.versioned.name])
        self.assertEqual((self.versioned / EXE).read_text(), "new build")
        self.assertTrue((self.versioned / "mods" / "Shipped" / "mod.json").is_file())

    def test_state_in_the_old_unversioned_folder_comes_along(self) -> None:
        old = self.dist / build.APP_NAME
        (old / "snapshots").mkdir(parents=True)
        (old / "state.json").write_text("mine")
        (old / "snapshots" / "x.json").write_text("rows")
        self.run_build()
        self.assertEqual((self.versioned / "state.json").read_text(), "mine")
        self.assertEqual((self.versioned / "snapshots" / "x.json").read_text(), "rows")

    def test_a_rebuild_of_the_same_version_keeps_its_state(self) -> None:
        self.run_build()
        (self.versioned / "state.json").write_text("mine")
        (self.versioned / EXE).write_text("old build")
        self.run_build()
        self.assertEqual((self.versioned / "state.json").read_text(), "mine")
        self.assertEqual((self.versioned / EXE).read_text(), "new build")


if __name__ == "__main__":
    unittest.main()
