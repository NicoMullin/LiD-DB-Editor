"""Mod packs: a mod list packed up whole, to keep or to hand to someone else.

A mod pack is one file, ``<name>.lidpack`` - a zip holding:

    lid-modpack.json        what is in it (see Pack below)
    mods/<mod folder>/      every mod the list ticks, as the files on disk
    lid-plugins/<x>.dll     the plugins those mods go with
    lid-plugins/<x>.ini     a plugin's own settings beside it (the HUD's layout)
    dinput8.dll             the LiD Plugin Loader, when a plugin is in it
    save/brggame.sav        a save played with this list (only when shared with one)

The manifest keeps the list itself: which mods in their load order, each one's
chosen settings, and the parts switched on or off - the same "choice" a save's
own note keeps (savelink.py). So a pack loads to exactly what it was made
from, on any PC, with nothing to download: someone giving out a save can give
the pack with it, and the save is played as it was meant to be.

The layout is that of a Get mods package (get_mods.py), so the same install
puts the mods, plugins and loader in place. Nothing is written until the
player has seen what a load would change (``plan``), and a mod copy it
replaces is kept in the backups first.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath

from . import get_mods
from .install import safe_folder_name
from .savelink import trim_choice

FORMAT = 1
SUFFIX = ".lidpack"
MANIFEST = "lid-modpack.json"
SAVE_FILE = "save/brggame.sav"
MAX_UNPACKED = 4 << 30           # the Crossover pack alone is a quarter of a gigabyte


class PackError(Exception):
    """Nothing was written, and this says why in words a player can act on."""


@dataclass(frozen=True)
class PackMod:
    id: str
    name: str
    version: str
    digest: str                  # of the folder's files, to tell a changed copy from the same one
    plugins: tuple[str, ...] = ()


@dataclass
class Pack:
    name: str
    created: str
    made_by: str
    choice: dict
    mods: list[PackMod] = field(default_factory=list)
    plugins: list[str] = field(default_factory=list)       # the DLLs it carries
    configs: list[str] = field(default_factory=list)       # their settings files, in lid-plugins
    missing_plugins: list[str] = field(default_factory=list)   # wanted, but not there to pack
    loader: bool = False
    save: dict | None = None                                # {"nickname": ...} when it holds one
    path: Path | None = None

    @property
    def mod_ids(self) -> list[str]:
        return [m.id for m in self.mods]

    def mod(self, mod_id: str) -> PackMod | None:
        return next((m for m in self.mods if m.id == mod_id), None)

    def to_dict(self) -> dict:
        return {
            "format": FORMAT, "name": self.name, "created": self.created, "made_by": self.made_by,
            "choice": self.choice,
            "mods": [{"id": m.id, "name": m.name, "version": m.version, "digest": m.digest,
                      "plugins": list(m.plugins)} for m in self.mods],
            "plugins": self.plugins, "configs": self.configs, "missing_plugins": self.missing_plugins,
            "loader": self.loader, "save": self.save,
        }


# -- the files ------------------------------------------------------------------

def folder_digest(folder: Path) -> str:
    """One hash of every file in a mod folder, by name and content."""
    digest = hashlib.sha256()
    folder = Path(folder)
    for path in sorted(p for p in folder.rglob("*") if p.is_file() and "__pycache__" not in p.parts):
        digest.update(path.relative_to(folder).as_posix().encode("utf-8") + b"\0")
        with open(path, "rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                digest.update(block)
        digest.update(b"\0")
    return digest.hexdigest()


def file_name(name: str) -> str:
    return safe_folder_name(name) + SUFFIX


# -- writing --------------------------------------------------------------------

def write(target: Path, name: str, choice: dict, mods: list, game_root: Path | None,
          made_by: str, save: tuple[bytes, str] | None = None) -> Pack:
    """Pack ``mods`` (the scanned Mod objects ``choice`` ticks, in its order) into
    ``target``. ``save`` is (the .sav's bytes, its nickname) to put in with them."""
    target = Path(target)
    choice = trim_choice(choice)
    by_id = {m.id: m for m in mods}
    absent = [m for m in choice["enabled_mods"] if m not in by_id]
    if absent:
        raise PackError(f"These mods are not installed here, so they cannot be packed: {', '.join(absent)}")
    pack = Pack(name=name.strip() or target.stem, created=datetime.now().isoformat(timespec="seconds"),
                made_by=made_by, choice=choice)
    plugin_dir = get_mods.win64(game_root) / get_mods.PLUGINS if game_root else None
    partial = target.with_name(target.name + ".partial")
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for mod_id in choice["enabled_mods"]:
                mod = by_id[mod_id]
                folder = Path(mod.folder)
                for path in sorted(p for p in folder.rglob("*") if p.is_file() and "__pycache__" not in p.parts):
                    archive.write(path, f"mods/{mod_id}/{path.relative_to(folder).as_posix()}")
                plugins = tuple(getattr(mod, "plugins", ()) or ())
                pack.mods.append(PackMod(mod_id, mod.name, str(mod.version or ""), folder_digest(folder), plugins))
                for dll in plugins:
                    if dll in pack.plugins or dll in pack.missing_plugins:
                        continue
                    found = _plugin_file(plugin_dir, dll)
                    if found is None:
                        pack.missing_plugins.append(dll)
                        continue
                    archive.write(found, f"{get_mods.PLUGINS}/{dll}")
                    pack.plugins.append(dll)
                    config = _plugin_config(plugin_dir, dll)
                    if config is not None:
                        archive.write(config, f"{get_mods.PLUGINS}/{config.name}")
                        pack.configs.append(config.name)
            if pack.plugins and game_root and get_mods.loader_state(game_root) == "ours":
                archive.write(get_mods.win64(game_root) / get_mods.LOADER, get_mods.LOADER)
                pack.loader = True
            if save is not None:
                data, nickname = save
                archive.writestr(SAVE_FILE, data)
                pack.save = {"nickname": nickname}
            archive.writestr(MANIFEST, json.dumps(pack.to_dict(), ensure_ascii=False, indent=2))
        os.replace(partial, target)
    except OSError as exc:
        raise PackError(f"The mod pack could not be written: {exc}") from None
    finally:
        partial.unlink(missing_ok=True)
    pack.path = target
    return pack


def _plugin_file(plugin_dir: Path | None, dll: str) -> Path | None:
    if plugin_dir is None:
        return None
    for name in (dll, dll + get_mods.OFF):          # a plugin switched off is packed all the same
        if (plugin_dir / name).is_file():
            return plugin_dir / name
    return None


def _plugin_config(plugin_dir: Path, dll: str) -> Path | None:
    config = plugin_dir / (Path(dll).stem + ".ini")
    return config if config.is_file() else None


# -- reading --------------------------------------------------------------------

def read(path: Path) -> Pack:
    """A pack's manifest, checked against what the file holds."""
    path = Path(path)
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            try:
                data = json.loads(archive.read(MANIFEST).decode("utf-8"))
            except KeyError:
                raise PackError(f"{path.name} is not a mod pack: it has no {MANIFEST}.") from None
    except zipfile.BadZipFile:
        raise PackError(f"{path.name} is not a mod pack the manager can read.") from None
    except OSError as exc:
        raise PackError(f"{path.name} could not be read: {exc}") from None
    except ValueError:
        raise PackError(f"The list inside {path.name} could not be read.") from None
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise PackError(f"{path.name} is a kind of mod pack this manager does not know - update the manager.")
    try:
        choice = data["choice"]
        if not isinstance(choice, dict):
            raise TypeError
        pack = Pack(
            name=str(data["name"]), created=str(data.get("created") or ""),
            made_by=str(data.get("made_by") or ""), choice=trim_choice(choice),
            mods=[PackMod(str(m["id"]), str(m.get("name") or m["id"]), str(m.get("version") or ""),
                          str(m.get("digest") or ""), tuple(str(p) for p in m.get("plugins") or ()))
                  for m in data.get("mods") or []],
            plugins=[str(p) for p in data.get("plugins") or []],
            configs=[str(c) for c in data.get("configs") or []],
            missing_plugins=[str(p) for p in data.get("missing_plugins") or []],
            loader=bool(data.get("loader")),
            save=data["save"] if isinstance(data.get("save"), dict) else None,
            path=path,
        )
    except (KeyError, TypeError, AttributeError):
        raise PackError(f"The list inside {path.name} is missing something.") from None
    for mod in pack.mods:
        if not _plain_name(mod.id) or f"mods/{mod.id}/mod.json" not in names:
            raise PackError(f"{path.name} says it holds the mod {mod.id!r}, but it does not.")
    if [m for m in pack.choice["enabled_mods"] if m not in pack.mod_ids]:
        raise PackError(f"{path.name} ticks mods it does not hold.")
    for dll in pack.plugins:
        if not get_mods.DLL_NAME.match(dll) or dll.lower() == get_mods.LOADER \
                or f"{get_mods.PLUGINS}/{dll}" not in names:
            raise PackError(f"{path.name} names a plugin it does not hold: {dll!r}.")
    for config in pack.configs:
        if not _plain_name(config) or not config.lower().endswith(".ini") \
                or f"{get_mods.PLUGINS}/{config}" not in names:
            raise PackError(f"{path.name} names a settings file it does not hold: {config!r}.")
    if pack.loader and get_mods.LOADER not in names:
        raise PackError(f"{path.name} says it holds the loader, but it does not.")
    if pack.save is not None and SAVE_FILE not in names:
        raise PackError(f"{path.name} says it holds a save, but it does not.")
    return pack


def _plain_name(name: str) -> bool:
    return bool(name) and name not in (".", "..") and not any(c in name for c in '/\\:')


def save_bytes(pack: Pack) -> bytes:
    if pack.save is None or pack.path is None:
        raise PackError("This mod pack holds no save.")
    with zipfile.ZipFile(pack.path) as archive:
        return archive.read(SAVE_FILE)


def unpack(pack: Pack, into: Path) -> Path:
    """The pack's files in ``into`` (emptied first), refusing anything that would
    land outside it. Returns the folder, laid out as a Get mods package."""
    into = Path(into)
    if into.exists():
        shutil.rmtree(into)
    into.mkdir(parents=True)
    root = into.resolve()
    with zipfile.ZipFile(pack.path) as archive:
        members = archive.infolist()
        if sum(m.file_size for m in members) > MAX_UNPACKED:
            raise PackError("That mod pack unpacks to far more than a mod list should.")
        for member in members:
            rel = PurePosixPath(member.filename)
            if rel.is_absolute() or ".." in rel.parts or ":" in member.filename or "\\" in member.filename:
                raise PackError(f"That mod pack holds a file it should not: {member.filename}")
            target = root.joinpath(*rel.parts)
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, open(target, "wb") as out:
                shutil.copyfileobj(source, out)
    return root


# -- loading --------------------------------------------------------------------

@dataclass
class Plan:
    """What loading a pack would change, for the player to see first."""
    add: list[PackMod] = field(default_factory=list)                 # not installed here
    replace: list[tuple[PackMod, str]] = field(default_factory=list)  # a different copy here (its version)
    same: list[PackMod] = field(default_factory=list)
    new_plugins: list[str] = field(default_factory=list)             # not here
    other_plugins: list[str] = field(default_factory=list)           # a different build here
    configs: list[str] = field(default_factory=list)                 # different here (or missing)
    loader: bool = False                                             # the loader is not here yet

    @property
    def writes_anything(self) -> bool:
        return bool(self.add or self.replace or self.new_plugins or self.other_plugins
                    or self.configs or self.loader)

    @property
    def replaces_anything(self) -> bool:
        """Something of the player's own would be swapped for the pack's."""
        return bool(self.replace or self.other_plugins or self.configs)

    def lines(self, keep_mine: bool = False) -> list[str]:
        out = [f"add the mod {m.name} {m.version}".rstrip() for m in self.add]
        if not keep_mine:
            out += [f"replace your {m.name} {have} with the pack's {m.version}".replace("  ", " ")
                    if have != m.version else f"replace your copy of {m.name} {m.version} with the pack's "
                    "(the files differ)" for m, have in self.replace]
        if self.loader:
            out.append(f"put the LiD Plugin Loader ({get_mods.LOADER}) next to the game")
        out += [f"put the plugin {dll} in {get_mods.PLUGINS}" for dll in self.new_plugins]
        if not keep_mine:
            out += [f"replace your {dll} with the pack's build" for dll in self.other_plugins]
            out += [f"use the pack's {c} (the plugin's own settings)" for c in self.configs]
        return out


def plan(pack: Pack, folder: Path, mods_dir: Path, game_root: Path | None) -> Plan:
    """``folder``: the pack unpacked (``unpack``)."""
    result = Plan()
    for mod in pack.mods:
        here = Path(mods_dir) / mod.id
        if not (here / "mod.json").is_file():
            result.add.append(mod)
        elif folder_digest(here) == (mod.digest or folder_digest(Path(folder) / "mods" / mod.id)):
            result.same.append(mod)
        else:
            result.replace.append((mod, _version_of(here)))
    plugin_dir = get_mods.win64(game_root) / get_mods.PLUGINS if game_root else None
    for dll in pack.plugins:
        have = _plugin_file(plugin_dir, dll)
        if have is None:
            result.new_plugins.append(dll)
        elif _file_digest(have) != _file_digest(Path(folder) / get_mods.PLUGINS / dll):
            result.other_plugins.append(dll)
    for config in pack.configs:
        have = plugin_dir / config if plugin_dir else None
        if have is None or not have.is_file() or \
                _file_digest(have) != _file_digest(Path(folder) / get_mods.PLUGINS / config):
            result.configs.append(config)
    result.loader = bool(pack.plugins and game_root and get_mods.loader_state(game_root) != "ours")
    return result


def install(pack: Pack, folder: Path, the_plan: Plan, mods_dir: Path, game_root: Path | None,
            backups_dir: Path, keep_mine: bool = False) -> list[str]:
    """Put what ``the_plan`` lists in place. With ``keep_mine`` a mod that is
    already here (and a plugin's settings) is left as it is; only what is
    missing is added. A copy that is replaced goes to the backups first.
    Returns what was done, for the log."""
    folder = Path(folder)
    mods = [m.id for m in the_plan.add] + ([] if keep_mine else [m.id for m, _ in the_plan.replace])
    plugins = the_plan.new_plugins + ([] if keep_mine else the_plan.other_plugins)
    loader = get_mods.LOADER if the_plan.loader and pack.loader else ""
    done: list[str] = []
    stamp = time.strftime("%Y-%m-%d-%H%M%S")
    aside = Path(backups_dir) / "modpacks" / f"{stamp} before {safe_folder_name(pack.name)}"
    for mod_id in mods:
        here = Path(mods_dir) / mod_id
        if here.exists():
            shutil.copytree(here, aside / "mods" / mod_id, dirs_exist_ok=True)
    package = get_mods.Package(folder=folder, name=f"the mod pack {pack.name}", version=pack.created,
                               mods=mods, plugins=plugins, loader=loader)
    if package.mods or package.plugins or package.loader:
        try:
            done += get_mods.install(package, mods_dir, game_root)
        except get_mods.GetModsError as exc:
            raise PackError(str(exc)) from None
    if not keep_mine and game_root is not None:
        plugin_dir = get_mods.win64(game_root) / get_mods.PLUGINS
        for config in the_plan.configs:
            target = plugin_dir / config
            if target.is_file():
                (aside / get_mods.PLUGINS).mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, aside / get_mods.PLUGINS / config)
            plugin_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(folder / get_mods.PLUGINS / config, target)
            done.append(f"put the pack's {config} in {plugin_dir}")
    if aside.exists():
        done.append(f"your own copies of what was replaced are in {aside}")
    return done


def _version_of(folder: Path) -> str:
    try:
        return str(json.loads((folder / "mod.json").read_text(encoding="utf-8")).get("version") or "")
    except (OSError, ValueError, AttributeError):
        return ""


def _file_digest(path: Path) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return ""
