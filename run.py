#!/usr/bin/env python3
"""Entry point.

    python run.py            -> GUI
    python run.py <command>  -> CLI (see `python run.py --help`)

The GUI needs PySide6; the CLI needs nothing outside the standard library, so a
missing PySide6 degrades to a clear message rather than a traceback.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lid_db_manager import APP_NAME  # noqa: E402


def _run_gui() -> int:
    try:
        from lid_db_manager.ui.app import run
    except ImportError as exc:
        if "PySide6" not in str(exc):
            raise
        print(f"{APP_NAME}: the GUI needs PySide6.", file=sys.stderr)
        print("    pip install -r requirements.txt", file=sys.stderr)
        print("Or use the command line: python run.py --help", file=sys.stderr)
        return 2
    return run()


def main() -> int:
    if len(sys.argv) > 1:
        from lid_db_manager.cli import main as cli_main

        return cli_main()
    return _run_gui()


if __name__ == "__main__":
    raise SystemExit(main())
