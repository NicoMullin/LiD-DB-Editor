"""Getting KSFA's plugin mods from their GitHub releases.

Some mods need a plugin: a DLL the game loads through the LiD Plugin Loader
(a dinput8.dll next to the game). Those do not come with the manager. Each
lives in its own GitHub repository, whose releases are built and tested by
GitHub itself, and each release carries one package zip with everything:

    lid-package.json        what is in it (see Package below)
    mods/<mod folder>/      the manager mod, if it has one
    lid-plugins/<x>.dll     the plugin
    dinput8.dll             the loader itself (only the loader's package)

Getting one (the "Get mods" window):

1. Ask GitHub for the repository's newest release (``releases/latest``,
   which never counts a pre-release).
2. Download its package zip and keep it only if it matches the SHA-256
   GitHub lists for it.
3. Unpack it, refusing anything that would land outside the folder, and
   check it holds what its lid-package.json says.
4. Put the mod in the manager's mods folder, and the plugin in the game's
   ``Binaries\\Win64\\lid-plugins``. A plugin needs the loader: when it is
   missing the window gets it first. A dinput8.dll that is not the loader
   is never replaced.

Nothing here goes online on its own: every download is the player's click,
and the window says what will be written before anything is.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
import urllib.error
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from . import self_update
from .progress import Progress, ensure

OWNER = "NicoMullin"
API = "https://api.github.com/repos/{owner}/{repo}/releases/latest"
PAGE = "https://github.com/{owner}/{repo}/releases/latest"
DOWNLOADS = "https://github.com/{owner}/{repo}/releases/download/"
PACKAGE_FILE = "lid-package.json"
LOADER = "dinput8.dll"
PLUGINS = "lid-plugins"
WIN64 = ("Binaries", "Win64")
MAX_PACKAGE = 256 << 20          # a package is a few MB at most
MAX_UNPACKED = 512 << 20
DLL_NAME = re.compile(r"^[A-Za-z0-9_.-]+\.dll$")

# How a dinput8.dll says what it is, in its version resource (UTF-16).
LOADER_MARK = "LiD Plugin Loader".encode("utf-16-le")
OLD_FIGHTER_MARK = "LiD Fighter Mod".encode("utf-16-le")   # 1.x was a dinput8.dll of its own


class GetModsError(Exception):
    """Nothing was installed, and this says why in words a player can act on."""


@dataclass(frozen=True)
class Offer:
    """One of KSFA's repositories, as the Get mods window lists it."""
    key: str
    title: str
    repo: str
    blurb: str
    mods: tuple[str, ...] = ()          # mod folders its package brings
    plugin: str = ""                    # the DLL it puts in lid-plugins
    is_loader: bool = False

    @property
    def package(self) -> str:
        return f"{self.repo}.zip"

    @property
    def page(self) -> str:
        return PAGE.format(owner=OWNER, repo=self.repo)


LOADER_OFFER = Offer(
    "loader", "Plugin Loader", "LiD-Plugin-Loader",
    "Loads the plugins below: a dinput8.dll next to the game and a lid-plugins folder. "
    "Got for you along with the first plugin.",
    is_loader=True,
)

CATALOG: tuple[Offer, ...] = (
    LOADER_OFFER,
    Offer("stewpull", "Stew Multi Pull", "LiD-Stew-Multi-Pull",
          "Purchase x5 to x25 at the stew: every decal shown together at the end, the whole run "
          "paid for, drawn and saved in one go. Its plugin also runs the Death Metal and festival "
          "stews.",
          mods=("Stew Multi Pull",), plugin="lid-stewpull.dll"),
    Offer("deathmetals", "Death Metals", "LiD-Death-Metals",
          "Death Metals back: the Death Metal rewards, the count on screen, Uncle Death's exchange, "
          "and Express Passes, the Coin Locker and a stew paid in Death Metals (the stew also needs "
          "Stew Multi Pull's plugin).",
          mods=("Death Metals",), plugin="lid-deathmetals.dll"),
    Offer("timers", "Timers", "LiD-Timers",
          "Crafting, research and facility upgrades take time again, with Uncle Death's time skip "
          "and the facility countdown.",
          mods=("Timers",), plugin="lid-timers.dll"),
    Offer("loginbonus", "Login Bonus", "LiD-Login-Bonus",
          "A random daily login bonus, from the online game's rewards for your floor or your own "
          "lists.",
          mods=("Login Bonus",), plugin="lid-loginbonus.dll"),
    Offer("classpassives", "Fighter Class Passives", "LiD-Fighter-Class-Passives",
          "A skill decal for each fighter class, always on without taking a slot, and the "
          "pause-map reveals.",
          mods=("fighter-passives",), plugin="lid-classpassives.dll"),
    Offer("offlinefixes", "Offline Fixes", "LiD-Offline-Fixes",
          "Fixes the game's own offline mode: no more closing on the spot when a Hater dies as "
          "you take the stairs.",
          plugin="lid-offlinefixes.dll"),
)


def offer(key: str) -> Offer:
    for entry in CATALOG:
        if entry.key == key:
            return entry
    raise KeyError(key)


# -- asking GitHub ---------------------------------------------------------


@dataclass(frozen=True)
class Release:
    offer: Offer
    version: str
    tag: str
    page: str
    asset: self_update.Asset | None     # the package zip; None when it has none we can check


def latest(entry: Offer, opener=self_update._open) -> Release:
    """The newest release of ``entry``. Raises GetModsError when GitHub cannot be asked."""
    url = API.format(owner=OWNER, repo=entry.repo)
    try:
        with opener(url) as response:
            raw = response.read(4 << 20)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise GetModsError(f"{entry.title} has no release on GitHub yet.") from exc
        if exc.code in (403, 429):
            raise GetModsError(
                "GitHub is limiting how often it can be asked. Try again in an hour."
            ) from exc
        raise GetModsError(f"GitHub did not answer about {entry.title} (it said {exc.code}).") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise GetModsError(self_update._offline(exc)) from exc
    try:
        data = json.loads(raw.decode("utf-8"))
        tag = str(data["tag_name"])
        return Release(
            offer=entry,
            version=".".join(map(str, self_update.version_numbers(tag))),
            tag=tag,
            page=str(data.get("html_url") or entry.page),
            asset=_pick_package(entry, data.get("assets") or []),
        )
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise GetModsError(f"GitHub's answer about {entry.title} could not be read.") from exc


def _pick_package(entry: Offer, assets: list) -> self_update.Asset | None:
    """The package zip: exactly its name, from that repository's own releases,
    with a SHA-256 GitHub lists for it."""
    prefix = DOWNLOADS.format(owner=OWNER, repo=entry.repo)
    for item in assets:
        name = str(item.get("name") or "")
        url = str(item.get("browser_download_url") or "")
        digest = str(item.get("digest") or "")
        if name == entry.package and url.startswith(prefix) and digest.startswith("sha256:"):
            return self_update.Asset(name=name, url=url, size=int(item["size"]),
                                     sha256=digest.split(":", 1)[1].lower())
    return None


def download(release: Release, work: Path, progress: Progress | None = None,
             opener=self_update._open) -> Path:
    """Download the release's package zip into ``work``; checked against its SHA-256."""
    progress = ensure(progress)
    asset = release.asset
    title = release.offer.title
    if asset is None:
        raise GetModsError(
            f"The newest {title} release has no {release.offer.package} the manager can check. "
            f"Get it from its release page instead."
        )
    prefix = DOWNLOADS.format(owner=OWNER, repo=release.offer.repo)
    if not asset.url.startswith(prefix) or asset.size > MAX_PACKAGE:
        raise GetModsError(f"That {title} download is not one the manager will use.")
    work.mkdir(parents=True, exist_ok=True)
    packed = work / asset.name
    partial = work / f"{asset.name}.part"
    progress.stage(f"Downloading {title} {release.version}...", 0, 100)
    try:
        self_update._download(asset, partial, progress, opener)
        os.replace(partial, packed)
    except self_update.UpdateError as exc:
        raise GetModsError(str(exc)) from exc
    finally:
        partial.unlink(missing_ok=True)
    return packed


# -- the package -------------------------------------------------------------


@dataclass
class Package:
    folder: Path
    name: str
    version: str
    mods: list[str] = field(default_factory=list)
    plugins: list[str] = field(default_factory=list)
    replaces: list[str] = field(default_factory=list)
    loader: str = ""


def unpack(packed: Path, into: Path) -> Package:
    """Unpack a package zip into ``into`` (emptied first) and check it."""
    if into.exists():
        shutil.rmtree(into)
    into.mkdir(parents=True)
    root = into.resolve()
    try:
        with zipfile.ZipFile(packed) as archive:
            members = archive.infolist()
            if sum(m.file_size for m in members) > MAX_UNPACKED:
                raise GetModsError("That package unpacks to far more than a mod should.")
            for member in members:
                rel = PurePosixPath(member.filename)
                if rel.is_absolute() or ".." in rel.parts or ":" in member.filename \
                        or "\\" in member.filename:
                    raise GetModsError(f"That package holds a file it should not: {member.filename}")
                target = root.joinpath(*rel.parts)
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, open(target, "wb") as out:
                    shutil.copyfileobj(source, out)
    except zipfile.BadZipFile as exc:
        raise GetModsError("The download is not a zip the manager can read.") from exc
    return read_package(root)


def read_package(folder: Path) -> Package:
    """What an unpacked package says it holds - and that it really holds it."""
    try:
        data = json.loads((folder / PACKAGE_FILE).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise GetModsError(f"That zip is not a mod package: it has no {PACKAGE_FILE}.") from exc
    except (OSError, ValueError) as exc:
        raise GetModsError(f"Its {PACKAGE_FILE} could not be read.") from exc
    if not isinstance(data, dict) or data.get("format") != 1:
        raise GetModsError(f"Its {PACKAGE_FILE} is a kind this manager does not know - update the manager.")
    try:
        package = Package(
            folder=folder,
            name=str(data["name"]),
            version=str(data["version"]),
            mods=[str(m) for m in data.get("mods") or []],
            plugins=[str(p) for p in data.get("plugins") or []],
            replaces=[str(p) for p in data.get("replaces") or []],
            loader=str(data.get("loader") or ""),
        )
    except (KeyError, TypeError) as exc:
        raise GetModsError(f"Its {PACKAGE_FILE} is missing something.") from exc
    for mod in package.mods:
        if "/" in mod or "\\" in mod or mod in ("", ".", "..") \
                or not (folder / "mods" / mod / "mod.json").is_file():
            raise GetModsError(f"The package says it holds the mod {mod!r}, but it does not.")
    for dll in package.plugins + package.replaces:
        if not DLL_NAME.match(dll) or dll.lower() == LOADER:
            raise GetModsError(f"The package names a plugin the manager will not use: {dll!r}.")
    for dll in package.plugins:
        if not (folder / PLUGINS / dll).is_file():
            raise GetModsError(f"The package says it holds {dll}, but it does not.")
    if package.loader and (package.loader != LOADER or not (folder / LOADER).is_file()):
        raise GetModsError("The package says it holds the loader, but it does not.")
    return package


# -- the game folder ---------------------------------------------------------


def win64(game_root: Path) -> Path:
    return Path(game_root).joinpath(*WIN64)


def loader_state(game_root: Path) -> str:
    """'missing', 'ours' (the LiD Plugin Loader), 'old-fighter' (the Fighter
    Mod 1.x dinput8.dll, which the loader replaces) or 'foreign'."""
    path = win64(game_root) / LOADER
    if not path.is_file():
        return "missing"
    try:
        data = path.read_bytes()
    except OSError:
        return "foreign"
    if LOADER_MARK in data:
        return "ours"
    if OLD_FIGHTER_MARK in data:
        return "old-fighter"
    return "foreign"


@dataclass(frozen=True)
class Installed:
    mods: dict          # mod folder -> its version, or "" when it is not there
    plugin: bool        # the plugin is in lid-plugins
    loader: str         # loader_state, or "" without a game folder

    @property
    def anything(self) -> bool:
        return self.plugin or any(self.mods.values())


def installed(entry: Offer, mods_dir: Path, game_root: Path | None) -> Installed:
    versions = {}
    for mod in entry.mods:
        version = ""
        try:
            data = json.loads((Path(mods_dir) / mod / "mod.json").read_text(encoding="utf-8"))
            version = str(data.get("version") or "?")
        except (OSError, ValueError):
            pass
        versions[mod] = version
    plugin = bool(game_root and entry.plugin and (win64(game_root) / PLUGINS / entry.plugin).is_file())
    state = loader_state(game_root) if game_root else ""
    if entry.is_loader:
        plugin = state == "ours"
    return Installed(versions, plugin, state)


def needs_loader(package: Package, game_root: Path | None) -> bool:
    return bool(package.plugins) and game_root is not None and loader_state(game_root) != "ours"


def what_it_writes(package: Package, mods_dir: Path, game_root: Path | None) -> list[str]:
    """In plain words, every place the install writes to - for the window to
    show before anything happens."""
    lines = [f"the mod {m!r} into {Path(mods_dir) / m}" for m in package.mods]
    if package.loader and game_root:
        lines.append(f"{LOADER} into {win64(game_root)}")
    if package.plugins and game_root:
        folder = win64(game_root) / PLUGINS
        lines += [f"{dll} into {folder}" for dll in package.plugins]
        lines += [f"remove {dll} from {folder} (its old name)" for dll in package.replaces
                  if (folder / dll).is_file()]
    return lines


def install(package: Package, mods_dir: Path, game_root: Path | None,
            progress: Progress | None = None) -> list[str]:
    """Put the package's mod(s), plugin and loader in place. Returns what was
    done, for the log. Checks everything it can before writing anything."""
    progress = ensure(progress)
    mods_dir = Path(mods_dir)
    needs_game = bool(package.plugins or package.loader)
    if needs_game and game_root is None:
        raise GetModsError(
            f"{package.name} puts a plugin in the game folder, and the manager does not know where "
            "that is yet. Set it with Tools > Set game folder, then try again."
        )
    if needs_game and not win64(game_root).is_dir():
        raise GetModsError(f"There is no {Path(*WIN64)} folder in {game_root} - is that the game folder?")
    state = loader_state(game_root) if needs_game else ""
    if package.loader and state == "foreign":
        raise GetModsError(
            f"There is already a {LOADER} in {win64(game_root)} that is not the LiD Plugin Loader - "
            "another mod's, perhaps. It was left alone. Move it out of the way first if you want the "
            "loader there."
        )
    if package.plugins and not package.loader and state != "ours":
        raise GetModsError(f"{package.name} needs the LiD Plugin Loader, which is not installed yet.")

    done: list[str] = []
    progress.stage(f"Installing {package.name} {package.version}...", 0, 100)
    for mod in package.mods:
        destination = mods_dir / mod
        source = package.folder / "mods" / mod
        if destination.exists():
            # the old copy waits next to the download, not in the mods folder,
            # where a rescan would take it for a second copy of the mod
            aside = package.folder.parent / "replaced" / mod
            aside.parent.mkdir(parents=True, exist_ok=True)
            if aside.exists():
                shutil.rmtree(aside)
            _retry(lambda: os.replace(destination, aside))
            try:
                shutil.copytree(source, destination)
            except BaseException:
                shutil.rmtree(destination, ignore_errors=True)
                os.replace(aside, destination)
                raise
            shutil.rmtree(aside, ignore_errors=True)
            done.append(f"updated the mod {mod}")
        else:
            mods_dir.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, destination)
            done.append(f"added the mod {mod}")
    if package.loader:
        target = win64(game_root) / LOADER
        _put_file(package.folder / LOADER, target)
        (win64(game_root) / PLUGINS).mkdir(exist_ok=True)
        if state == "ours":
            done.append(f"updated the loader, {target}")
        elif state == "old-fighter":
            done.append(f"replaced the old Fighter Mod {LOADER} with the loader in {win64(game_root)}")
        else:
            done.append(f"put the loader, {LOADER}, in {win64(game_root)}")
    for dll in package.plugins:
        folder = win64(game_root) / PLUGINS
        folder.mkdir(exist_ok=True)
        _put_file(package.folder / PLUGINS / dll, folder / dll)
        done.append(f"put {dll} in {folder}")
        for old in package.replaces:
            if (folder / old).is_file():
                _retry(lambda: (folder / old).unlink())
                done.append(f"removed {old} (its old name) from {folder}")
    return done


def _put_file(source: Path, target: Path) -> None:
    """Copy next to the target, then swap it in. The game holds a plugin it
    has loaded, so a running game makes this fail - and says so."""
    partial = target.with_name(target.name + ".new")
    shutil.copy2(source, partial)
    try:
        _retry(lambda: os.replace(partial, target))
    except PermissionError as exc:
        partial.unlink(missing_ok=True)
        raise GetModsError(
            f"{target.name} could not be replaced - is the game running? Close it and try again."
        ) from exc


def _retry(action, tries: int = 12, delay: float = 0.25) -> None:
    for attempt in range(tries):
        try:
            action()
            return
        except PermissionError:
            if attempt == tries - 1:
                raise
            time.sleep(delay)
