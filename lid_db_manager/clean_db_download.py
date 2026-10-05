"""Clean copies of masters.db, downloaded from the manager's GitHub repository.

Everything the manager says about a database - which mods are already in it,
what a mod changes, the values the mod builder starts from - is measured
against an untouched copy of the same game build. Those copies live in the
repository's own ``LiD Vanilla DB/<build>/masters.db`` folders, the same ones
the manager reads from disk, and a player fetches only the build their game is
on:

    https://api.github.com/repos/<REPO>/git/trees/main?recursive=1
    https://raw.githubusercontent.com/<REPO>/main/LiD Vanilla DB/5.0.4.2/masters.db

The first lists every file in the repository with its size and git's own
checksum of it (a SHA-1 of the file's contents). A download is kept only when
both match and the database itself says it is the build that was asked for;
anything else is thrown away. Putting a new build up is committing its
masters.db to a folder named the way vanilla_library.suggested_label names it.

Nothing here goes online on its own: each download is the player's own click.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from . import APP_NAME, __version__
from . import vanilla_library
from .progress import Progress, ensure

REPO = "NicoMullin/LiD-DB-Editor"
BRANCH = "main"
INDEX_URL = f"https://api.github.com/repos/{REPO}/git/trees/{BRANCH}?recursive=1"
TIMEOUT = 30
CHUNK = 1 << 16
MAX_SIZE = 512 << 20    # a masters.db is ~57 MB
_PATH = re.compile(
    rf"^{re.escape(vanilla_library.DIRNAME)}/([0-9A-Za-z][0-9A-Za-z. ()_-]*)/"
    rf"{re.escape(vanilla_library.DB_NAME)}$"
)


def file_url(path: str) -> str:
    return (f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/"
            + urllib.parse.quote(path))


def label_for(version: str) -> str:
    """The folder a build's clean copy sits in, from the build a database says
    it is: "5.0.4.1.0 - 1.89 (Steam 5.0.4.2.0)" -> "5.0.4.2",
    "5.0.3.0.0 - 1.87" -> "5.0.3.0". The same rule as
    vanilla_library.suggested_label, which names the folders."""
    steam = re.search(r"\(Steam ([0-9.]+)\)", version or "")
    number = steam.group(1) if steam else (version or "").split(" - ")[0].strip()
    parts = number.split(".")
    return ".".join(parts[:4]) if len(parts) > 4 else number


# url -> an open response: something with read(n), usable in a with-block.
# Swapped out by the tests.
Opener = Callable[[str], object]


class DownloadError(Exception):
    """Nothing was kept, and this says why in words a player can act on."""


@dataclass(frozen=True)
class CleanBuild:
    label: str          # the folder: "5.0.4.2"
    path: str           # in the repository: "LiD Vanilla DB/5.0.4.2/masters.db"
    size: int
    sha: str            # git's SHA-1 of the file

    @property
    def build(self) -> str:
        return self.label


def _open(url: str):
    headers = {"User-Agent": f"{APP_NAME}/{__version__}"}
    if url.startswith("https://api.github.com/"):
        headers["Accept"] = "application/vnd.github+json"
    request = urllib.request.Request(url, headers=headers)
    return urllib.request.urlopen(request, timeout=TIMEOUT)  # noqa: S310 - fixed https URLs


def fetch_index(opener: Opener = _open) -> list[CleanBuild]:
    """The builds on offer. Raises DownloadError when they cannot be read."""
    try:
        with opener(INDEX_URL) as response:
            raw = response.read(64 << 20)
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 429):
            raise DownloadError(
                "GitHub is limiting how often it can be asked. Try again in an hour."
            ) from exc
        raise DownloadError(
            f"Could not read the list of clean databases on GitHub (it answered {exc.code})."
        ) from exc
    except (urllib.error.URLError, OSError) as exc:
        raise DownloadError(_offline(exc)) from exc
    try:
        data = json.loads(raw.decode("utf-8"))
        found = []
        for entry in data["tree"]:
            match = _PATH.match(str(entry.get("path") or ""))
            if not match or entry.get("type") != "blob":
                continue
            found.append(CleanBuild(label=match.group(1), path=entry["path"],
                                    size=int(entry["size"]), sha=str(entry["sha"]).lower()))
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise DownloadError("The list of clean databases on GitHub could not be read.") from exc
    if not found:
        raise DownloadError("The manager's GitHub page has no clean databases up.")
    return sorted(found, key=lambda b: tuple(int(n) for n in re.findall(r"\d+", b.label)))


def find(builds: Iterable[CleanBuild], version: str) -> CleanBuild | None:
    """The clean copy for a build, as a database names it."""
    label = label_for(version)
    for build in builds:
        if build.label == label:
            return build
    return None


def download(
    build: CleanBuild,
    root: Path,
    progress: Progress | None = None,
    opener: Opener = _open,
    version: str = "",
) -> Path:
    """Fetch, check and keep one clean database. Returns where it was kept.

    It goes in <root>/LiD Vanilla DB/<label>/masters.db, one of the places
    vanilla_library already looks. A copy already there is never overwritten.
    With ``version`` the database must say it is exactly that build;
    without, that it belongs in the folder it came from.
    """
    progress = ensure(progress)
    if not _PATH.match(build.path) or build.path.split("/")[1] != build.label:
        raise DownloadError("The list of clean databases names a file it should not.")
    if not 0 < build.size <= MAX_SIZE:
        raise DownloadError("That clean database is not a size the manager will download.")
    folder = Path(root) / vanilla_library.DIRNAME / build.label
    target = folder / vanilla_library.DB_NAME
    if target.exists():
        raise DownloadError(f"There is already a clean copy of build {build.label} in {folder}.")
    folder.mkdir(parents=True, exist_ok=True)

    handle, name = tempfile.mkstemp(prefix="clean-db-", suffix=".incoming", dir=folder)
    os.close(handle)    # reopened by name below; an open handle locks it on Windows
    incoming = Path(name)
    try:
        progress.stage(f"Downloading the clean database for build {build.label}...", 0, 100)
        _fetch(build, incoming, progress, opener)
        found = vanilla_library.database_version(incoming)
        if version:
            ok = found == version
        else:
            ok = bool(found) and label_for(found) == build.label
        if not ok:
            raise DownloadError(
                f"The download says it is build {found or 'unknown'}, not "
                f"{version or build.label}, so it was not kept."
            )
        os.replace(incoming, target)
    finally:
        incoming.unlink(missing_ok=True)
        _remove_if_empty(folder)
    return target


def _fetch(build: CleanBuild, path: Path, progress: Progress, opener: Opener) -> None:
    # Git's checksum of a file: SHA-1 over "blob <size>\0" and then the bytes.
    digest = hashlib.sha1(f"blob {build.size}\0".encode())  # noqa: S324 - git's own
    done = 0
    try:
        with opener(file_url(build.path)) as response, open(path, "wb") as out:
            while True:
                chunk = response.read(CHUNK)
                if not chunk:
                    break
                done += len(chunk)
                if done > build.size:
                    raise DownloadError("The download is bigger than it should be, so it was stopped.")
                out.write(chunk)
                digest.update(chunk)
                progress.step(done, build.size)
    except urllib.error.HTTPError as exc:
        raise DownloadError(
            f"GitHub does not have {build.path} (it answered {exc.code})."
        ) from exc
    except (urllib.error.URLError, OSError) as exc:
        raise DownloadError(_offline(exc)) from exc
    if done != build.size or digest.hexdigest() != build.sha:
        raise DownloadError(
            "The download did not match what GitHub lists for it - it may have been cut "
            "short. Nothing was kept; try again."
        )


def _remove_if_empty(folder: Path) -> None:
    try:
        if folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()
    except OSError:
        pass


def _offline(exc: Exception) -> str:
    return (
        "Could not reach GitHub to fetch the clean database. Check the internet "
        f"connection and try again. ({getattr(exc, 'reason', exc)})"
    )
