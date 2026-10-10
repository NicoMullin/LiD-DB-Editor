"""Run every test the way the GitHub build does.

    python tools/run_tests.py

The same as `python -m unittest discover -s tests -t tests -v`, except for how
it ends. The tests make real Qt widgets, and on GitHub's Windows machines the
process sometimes ends with exit code 1 after every test has passed - "OK"
printed, no error, nothing else - while Python and Qt shut down. The same
commit passes one run and fails the next.

The result is known before that shutdown starts, so this sets the exit code
from the result and leaves at once: 0 when every test passed, 1 otherwise. A
real failure still fails the build. On Windows it ends with TerminateProcess,
which skips even the unloading of Qt's DLLs (os._exit still runs that, and
the windows the tests opened leave Qt threads behind).
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent.parent / "tests"

if __name__ == "__main__":
    suite = unittest.defaultTestLoader.discover(str(TESTS), top_level_dir=str(TESTS))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    passed = result.wasSuccessful() and result.testsRun > 0
    code = 0 if passed else 1
    print(f"exit code {code}", file=sys.stderr)
    sys.stdout.flush()
    sys.stderr.flush()
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        kernel32.TerminateProcess.argtypes = (ctypes.c_void_p, ctypes.c_uint)
        kernel32.TerminateProcess(kernel32.GetCurrentProcess(), code)
    os._exit(code)
