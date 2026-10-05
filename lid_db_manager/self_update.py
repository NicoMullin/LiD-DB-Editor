"""Updating the program itself from its GitHub releases page.

One click, the way players expect an update to work:

1. Ask GitHub for the newest release (``releases/latest``, which never counts
   a pre-release).
2. Download its zip into ``<program>/_update/`` and keep it only if it matches
   the SHA-256 GitHub lists for it.
3. Unpack it there, next to - never over - the running program.
4. Start the NEW copy with ``--finish-update``. It waits for the old one to
   close, puts its own program files in place of the old ones, and starts the
   updated program. A Windows program cannot overwrite itself while it runs,
   which is why the new copy does the swapping.

The update happens inside the folder the program is already in, and the folder
keeps its name, so shortcuts and taskbar pins go on working. (A release zip
unpacks into a folder named after its version - "LID DB Mod Manager 0.11.0" -
but an updated folder is not renamed: a new name would break every shortcut to
it.)

Only program files are swapped: the .exe, ``_internal/`` and each mod that
ships with the program. What a player has done with it - ``state.json``,
snapshots, backups, logs, the cache, their own mods, any clean databases - is
never touched, so nothing has to be carried over. The old program files are
moved aside first and moved back if anything goes wrong.

Nothing here goes online on its own: a check is the player's click, or the
check at start that they switched on.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

from . import APP_NAME, __version__
from .clean_db_download import REPO
from .progress import Progress, ensure

LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
PAGE_URL = f"https://github.com/{REPO}/releases/latest"
DOWNLOAD_PREFIX = f"https://github.com/{REPO}/releases/download/"
TIMEOUT = 30
CHUNK = 1 << 16

# Started with these, the program does an update step instead of opening its
# window as usual (see ui/app.py). run.py hands them to the GUI, not the
# command line.
FINISH_FLAG = "--finish-update"
DONE_FLAG = "--updated-from"

# Where downloads and the unpacked new version wait, inside the program folder.
UPDATE_DIR = "_update"
PREVIOUS = "previous"
INTERNAL = "_internal"
MODS = "mods"
# Never replaced, even if a release were to carry one by mistake: everything
# here is the player's, not the program's.
PLAYER_DATA = frozenset(
    {"state.json", "snapshots", "backups", "logs", "cache", "LiD Vanilla DB", UPDATE_DIR}
)

# What build.py names the program and its .exe - and, with the version after
# it, the folder a release zip unpacks into.
RELEASE_NAME = "LID DB Mod Manager"

MAX_DOWNLOAD = 1 << 30          # 1 GB; a release is ~60 MB
MAX_UNPACKED = 3 << 30

Opener = Callable[[str], object]


class UpdateError(Exception):
    """The update did not happen, and this says why in words a player can act on."""


@dataclass(frozen=True)
class Asset:
    name: str
    url: str
    size: int
    sha256: str


@dataclass(frozen=True)
class Release:
    version: str        # "0.11.0", read out of the tag
    tag: str            # "Release_Beta_V0.11.0"
    title: str
    notes: str
    page: str           # the release's own page, for the browser
    asset: Asset | None  # None: no zip GitHub lists a hash for

    def is_newer_than(self, version: str = __version__) -> bool:
        return newer(self.version, version)


def version_numbers(text: str) -> tuple[int, ...]:
    """ "Release_Beta_V0.10.1" -> (0, 10, 1). Empty when there are none."""
    match = re.search(r"\d+(?:\.\d+)*", text or "")
    return tuple(int(part) for part in match.group().split(".")) if match else ()


def newer(candidate: str, current: str) -> bool:
    a, b = version_numbers(candidate), version_numbers(current)
    if not a or not b:
        return False
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) > b + (0,) * (width - len(b))


def folder_name(version: str) -> str:
    """What a release zip's folder is called: "LID DB Mod Manager 0.11.0"."""
    return f"{RELEASE_NAME} {version}"


# -- asking GitHub ---------------------------------------------------------


def _open(url: str):
    headers = {"User-Agent": f"{APP_NAME}/{__version__}"}
    if url.startswith("https://api.github.com/"):
        headers["Accept"] = "application/vnd.github+json"
    request = urllib.request.Request(url, headers=headers)
    return urllib.request.urlopen(request, timeout=TIMEOUT)  # noqa: S310 - fixed https URLs


def check(opener: Opener = _open) -> Release:
    """The newest release. Raises UpdateError when GitHub cannot be asked."""
    try:
        with opener(LATEST_URL) as response:
            raw = response.read(4 << 20)
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 429):
            raise UpdateError(
                "GitHub is limiting how often it can be asked. Try again in an hour."
            ) from exc
        raise UpdateError(
            f"GitHub did not say what the newest version is (it answered {exc.code})."
        ) from exc
    except (urllib.error.URLError, OSError) as exc:
        raise UpdateError(_offline(exc)) from exc
    try:
        data = json.loads(raw.decode("utf-8"))
        tag = str(data["tag_name"])
        return Release(
            version=".".join(map(str, version_numbers(tag))),
            tag=tag,
            title=str(data.get("name") or tag),
            notes=str(data.get("body") or "").strip(),
            page=str(data.get("html_url") or PAGE_URL),
            asset=_pick_asset(data.get("assets") or []),
        )
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise UpdateError("GitHub's answer about the newest version could not be read.") from exc


def _pick_asset(assets: list) -> Asset | None:
    """The program's zip: the one .zip on the release, or of several the one
    named after the program. Only one GitHub lists a SHA-256 for, and only one
    served from this repository's own releases."""
    zips = []
    for entry in assets:
        name = str(entry.get("name") or "")
        url = str(entry.get("browser_download_url") or "")
        digest = str(entry.get("digest") or "")
        if not name.lower().endswith(".zip") or not url.startswith(DOWNLOAD_PREFIX):
            continue
        if not digest.startswith("sha256:"):
            continue
        zips.append(Asset(name=name, url=url, size=int(entry["size"]),
                          sha256=digest.split(":", 1)[1].lower()))
    if len(zips) > 1:
        wanted = re.sub(r"[^a-z]", "", RELEASE_NAME.lower())
        named = [a for a in zips if wanted in re.sub(r"[^a-z]", "", a.name.lower())]
        zips = named
    return zips[0] if len(zips) == 1 else None


# -- can this copy update itself? -------------------------------------------


def program_dir() -> Path:
    return Path(sys.executable).resolve().parent


def exe_name() -> str:
    return Path(sys.executable).name


def why_not_here(folder: Path | None = None) -> str:
    """Why this copy cannot update itself, or "" when it can."""
    if not getattr(sys, "frozen", False):
        return "This copy runs from source - update it with git, or download the new release."
    folder = Path(folder) if folder is not None else program_dir()
    if not (folder / INTERNAL).is_dir():
        return "Only the folder version of the program can update itself."
    probe = folder / f".{UPDATE_DIR}-write-test-{os.getpid()}"
    try:
        probe.write_bytes(b"")
        probe.unlink()
    except OSError:
        return (f"The program cannot write to its own folder ({folder}). Move it somewhere "
                "writable, such as your Documents, and it can update itself.")
    return ""


# -- download and unpack -----------------------------------------------------


def safe_version(version: str) -> str:
    return re.sub(r"[^0-9A-Za-z.]+", "-", version).strip("-.") or "new"


def fetch(
    release: Release,
    folder: Path,
    exe: str,
    progress: Progress | None = None,
    opener: Opener = _open,
) -> Path:
    """Download and unpack ``release`` under ``<folder>/_update``.

    Returns the unpacked program folder, the one holding ``exe``.
    """
    progress = ensure(progress)
    asset = release.asset
    if asset is None:
        raise UpdateError(
            "That release has no download the program can check, so it was not used. "
            "Get it from the release page instead."
        )
    if not asset.url.startswith(DOWNLOAD_PREFIX) or asset.size > MAX_DOWNLOAD:
        raise UpdateError("That release's download is not one the program will use.")
    work = Path(folder) / UPDATE_DIR
    work.mkdir(parents=True, exist_ok=True)
    stem = safe_version(release.version)
    packed = work / f"{stem}.zip"
    partial = work / f"{stem}.zip.part"
    progress.stage(f"Downloading {APP_NAME} {release.version}...", 0, 80)
    try:
        _download(asset, partial, progress, opener)
        os.replace(partial, packed)
    finally:
        partial.unlink(missing_ok=True)
    progress.stage("Unpacking it...", 80, 100)
    try:
        return unpack(packed, work / stem, exe, progress)
    finally:
        packed.unlink(missing_ok=True)


def _download(asset: Asset, path: Path, progress: Progress, opener: Opener) -> None:
    digest = hashlib.sha256()
    done = 0
    try:
        with opener(asset.url) as response, open(path, "wb") as out:
            while True:
                chunk = response.read(CHUNK)
                if not chunk:
                    break
                done += len(chunk)
                if done > asset.size:
                    raise UpdateError("The download is bigger than GitHub says, so it was stopped.")
                out.write(chunk)
                digest.update(chunk)
                progress.step(done, asset.size)
    except urllib.error.HTTPError as exc:
        raise UpdateError(f"GitHub did not hand over the download (it answered {exc.code}).") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise UpdateError(_offline(exc)) from exc
    if done != asset.size or digest.hexdigest() != asset.sha256:
        raise UpdateError(
            "The download did not match the SHA-256 GitHub lists for it - it may have been "
            "cut short. Nothing was changed; try again."
        )


def unpack(packed: Path, into: Path, exe: str, progress: Progress | None = None) -> Path:
    """Unpack a release zip into ``into`` (emptied first). Returns the folder
    in it that holds ``exe`` and ``_internal``."""
    progress = ensure(progress)
    if into.exists():
        shutil.rmtree(into)
    into.mkdir(parents=True)
    try:
        with zipfile.ZipFile(packed) as archive:
            members = archive.infolist()
            total = sum(m.file_size for m in members)
            if total > MAX_UNPACKED:
                raise UpdateError("The download unpacks to far more than a release should.")
            for member in members:
                parts = PurePosixPath(member.filename.replace("\\", "/")).parts
                if (not parts or member.filename.startswith(("/", "\\"))
                        or any(p in ("..", "") or ":" in p for p in parts)):
                    raise UpdateError(f"The download holds a file it should not: {member.filename}")
            done = 0
            for member in members:
                target = into.joinpath(*PurePosixPath(member.filename.replace("\\", "/")).parts)
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, open(target, "wb") as out:
                    shutil.copyfileobj(source, out, CHUNK)
                done += member.file_size
                progress.step(done, total)
    except zipfile.BadZipFile as exc:
        raise UpdateError("The download is not a zip the program can open.") from exc
    found = [
        folder for folder in [into, *into.iterdir(), *(p for d in into.iterdir() if d.is_dir()
                                                       for p in d.iterdir())]
        if folder.is_dir() and (folder / exe).is_file() and (folder / INTERNAL).is_dir()
    ]
    if len(found) != 1:
        raise UpdateError(f"The download does not hold {exe} where a release keeps it.")
    return found[0]


# -- handing over to the new copy -----------------------------------------


def hand_over(new_folder: Path, exe: str, target: Path, from_version: str = __version__):
    """Start the new copy, which finishes the update once this process exits."""
    return restart(new_folder, exe, [FINISH_FLAG, str(target), str(os.getpid()), from_version])


def restart(folder: Path, exe: str, args: list[str]):
    return subprocess.Popen(  # noqa: S603 - the program's own exe, hash-checked if new
        [str(folder / exe), *args],
        cwd=str(folder),
        close_fds=True,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )


def wait_for_exit(pid: int, timeout: float = 60.0) -> bool:
    """True once process ``pid`` has ended, False if it is still running."""
    if pid <= 0 or pid == os.getpid():
        return True
    if sys.platform.startswith("win"):
        import ctypes

        synchronize = 0x00100000
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(synchronize, False, pid)
        if not handle:
            return True     # gone already
        try:
            return kernel32.WaitForSingleObject(handle, int(timeout * 1000)) == 0
        finally:
            kernel32.CloseHandle(handle)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return True
        time.sleep(0.1)
    return False


def what_is_replaced(new_folder: Path) -> list[PurePosixPath]:
    """The program's own files in a release: each top-level item, except that
    mods/ is taken one mod at a time so the player's own mods stay."""
    items: list[PurePosixPath] = []
    for item in sorted(new_folder.iterdir()):
        if item.name in PLAYER_DATA:
            continue
        if item.name == MODS and item.is_dir():
            items += [PurePosixPath(MODS, mod.name) for mod in sorted(item.iterdir())]
        else:
            items.append(PurePosixPath(item.name))
    return items


def install(new_folder: Path, target: Path, progress: Progress | None = None) -> list[str]:
    """Put the program files in ``new_folder`` in place of those in ``target``.

    What is replaced is first moved into ``<target>/_update/previous``; if any
    step fails everything is put back as it was and UpdateError is raised.
    Returns what was replaced, for the log.
    """
    progress = ensure(progress)
    new_folder, target = Path(new_folder), Path(target)
    aside_root = target / UPDATE_DIR / PREVIOUS
    if aside_root.exists():
        shutil.rmtree(aside_root)
    items = what_is_replaced(new_folder)
    files = sum(1 for rel in items for _ in _files(new_folder.joinpath(*rel.parts)))
    copied = 0
    moved: list[tuple[Path, Path]] = []
    added: list[Path] = []

    def count(source, destination, *, follow_symlinks=True):
        nonlocal copied
        shutil.copy2(source, destination)
        copied += 1
        progress.step(copied, files)

    progress.stage(f"Putting {APP_NAME} {__version__} in place...", 0, 100)
    try:
        for rel in items:
            source = new_folder.joinpath(*rel.parts)
            destination = target.joinpath(*rel.parts)
            if destination.exists() or destination.is_symlink():
                aside = aside_root.joinpath(*rel.parts)
                aside.parent.mkdir(parents=True, exist_ok=True)
                _retry(lambda: os.replace(destination, aside))
                moved.append((destination, aside))
            destination.parent.mkdir(parents=True, exist_ok=True)
            added.append(destination)
            if source.is_dir():
                shutil.copytree(source, destination, copy_function=count)
            else:
                count(source, destination)
    except BaseException as exc:
        problems = _put_back(added, moved)
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        detail = f"{type(exc).__name__}: {exc}"
        if problems:
            raise UpdateError(
                f"The update failed ({detail}), and not everything could be put back. "
                f"The old program files are in {aside_root}; copy them back into {target}."
            ) from exc
        raise UpdateError(f"The update failed, and the old version was put back. ({detail})") from exc
    return [str(rel) for rel in items]


def _files(path: Path):
    if path.is_dir():
        yield from (p for p in path.rglob("*") if p.is_file())
    else:
        yield path


def _put_back(added: list[Path], moved: list[tuple[Path, Path]]) -> list[str]:
    problems = []
    for path in reversed(added):
        try:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            elif path.exists():
                path.unlink()
        except OSError as exc:
            problems.append(f"{path}: {exc}")
    for destination, aside in reversed(moved):
        try:
            _retry(lambda: os.replace(aside, destination))
        except OSError as exc:
            problems.append(f"{destination}: {exc}")
    return problems


def _retry(action, tries: int = 20, delay: float = 0.25) -> None:
    """Windows keeps a file busy for a moment after the program using it has
    closed, and a virus scanner may be reading a new one. Try again briefly."""
    for attempt in range(tries):
        try:
            action()
            return
        except PermissionError:
            if attempt == tries - 1:
                raise
            time.sleep(delay)


def clean_up(folder: Path) -> None:
    """Remove what an update left in ``<folder>/_update``. What is still in
    use is left for the next start."""
    shutil.rmtree(Path(folder) / UPDATE_DIR, ignore_errors=True)


def _offline(exc: Exception) -> str:
    return (
        "Could not reach GitHub. Check the internet connection and try again. "
        f"({getattr(exc, 'reason', exc)})"
    )
