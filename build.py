#!/usr/bin/env python3
"""Build a standalone Windows .exe with PyInstaller.

    python build.py              -> dist/LID DB Mod Manager/   (recommended)
    python build.py --onefile    -> dist/LID DB Mod Manager.exe

Run it with the same interpreter PySide6 is installed in.

What this does that a bare `pyinstaller run.py` does not: it copies `mods/` next
to the finished executable. Mods are read from disk at runtime so users can add
their own - bundling them inside the exe would put them somewhere the app cannot
see and the user cannot edit.

There is deliberately no --exclude-module list. PySide6 is ~630 MB installed and
an exclusion list looks like it should help, but PyInstaller's PySide6 hook
already collects only the Qt modules that are actually imported: measured on
PyInstaller 6.22 / PySide6 6.11, excluding 44 unused Qt modules changed the
bundle size by 0 MB. If you add one, measure before you keep it.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP_NAME = "LID DB Mod Manager"


def folder_size_mb(path: Path) -> float:
    if path.is_file():
        return path.stat().st_size / (1024 * 1024)
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / (1024 * 1024)


def msvc_runtime_dlls() -> list[Path]:
    """The C++ runtime DLLs Qt links against, taken from the PySide6 wheel.

    Qt6Core/Gui/Widgets import MSVCP140.dll (and _1, _2), but PyInstaller only
    collects VCRUNTIME140*. On a machine without the Visual C++ redistributable
    the app would then fail to start with a missing-DLL box. The PySide6 wheel
    ships its own copies precisely so it can be deployed app-locally, so take
    them from there - they are guaranteed to match the Qt build being bundled.
    """
    import PySide6

    package = Path(PySide6.__file__).parent
    names = ("msvcp140", "vcruntime140", "concrt140")
    return sorted(
        dll for dll in package.glob("*.dll") if dll.name.lower().startswith(names)
    )


def build(one_file: bool, icon: Path | None, keep_console: bool) -> Path:
    command = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--name", APP_NAME,
        "--onefile" if one_file else "--onedir",
        "--console" if keep_console else "--windowed",
        # Qt needs no writable state of its own; keep the bundle deterministic.
        "--noupx",
    ]
    runtime = msvc_runtime_dlls()
    for dll in runtime:
        # Next to the Qt DLLs that need them, mirroring a normal PySide6 install.
        command += ["--add-binary", f"{dll}{os.pathsep}PySide6"]
    if runtime:
        print(f"Bundling {len(runtime)} MSVC runtime DLL(s) so the build needs no redistributable")
    if icon is not None:
        command += ["--icon", str(icon)]
    command.append(str(ROOT / "run.py"))

    print("$ " + " ".join(f'"{c}"' if " " in c else c for c in command))
    started = time.monotonic()
    subprocess.run(command, cwd=ROOT, check=True)
    print(f"\nPyInstaller finished in {time.monotonic() - started:.0f}s")

    return ROOT / "dist" / (f"{APP_NAME}.exe" if one_file else APP_NAME)


def stage_mods(target_dir: Path) -> None:
    """Put mods/ next to the executable, where the app looks for it at runtime."""
    destination = target_dir / "mods"
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(
        ROOT / "mods",
        destination,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".*"),
    )
    count = sum(1 for p in destination.iterdir() if p.is_dir() and not p.name.startswith("_"))
    print(f"Copied mods/ -> {destination}  ({count} mod(s) plus the templates)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--onefile",
        action="store_true",
        help="single .exe instead of a folder (smaller to send, slower to start)",
    )
    parser.add_argument("--icon", type=Path, help="path to an .ico file")
    parser.add_argument(
        "--console",
        action="store_true",
        help="keep the console window, so the command line works from the .exe too",
    )
    args = parser.parse_args()

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller is not installed. Run:  pip install pyinstaller", file=sys.stderr)
        return 2
    try:
        import PySide6  # noqa: F401
    except ImportError:
        print(
            "PySide6 is not installed in this interpreter, so the build would have no GUI.\n"
            "Run:  pip install -r requirements.txt",
            file=sys.stderr,
        )
        return 2

    result = build(args.onefile, args.icon, args.console)
    if not result.exists():
        print(f"Expected {result} but it is not there.", file=sys.stderr)
        return 1

    # For --onefile the exe stands alone, so mods/ goes beside it in dist/.
    stage_mods(result.parent if args.onefile else result)

    payload = result.parent if args.onefile else result
    print(f"\nBuilt: {result}")
    print(f"Ship the whole '{payload.name}' folder - {folder_size_mb(payload):.0f} MB")
    print(
        "\nThe app keeps mods/, logs/, snapshots/, backups/ and state.json next to\n"
        "the executable, so put it somewhere writable - not Program Files."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
