"""Switching the game's plugin DLLs on and off with the mods that name them.

The LiD Plugin Loader (a dinput8.dll next to the game) loads every file in
``Binaries\\Win64\\lid-plugins`` whose name ends in ``.dll``. A plugin renamed
to ``<name>.dll.off`` stays where it is but is not loaded - that is how one is
switched off, and all this module does: rename, never delete.

A mod names the plugin(s) it goes with (``"plugin": "lid-timers.dll"`` in its
mod.json); a plugin with nothing else to change gets a mod of its own that is
only that line. On every save each named plugin is switched to match: on while
any mod that names it is on, off when none is (see ``follow_mods``). A plugin
no mod names is left as it is.

The game holds a plugin it has loaded, so nothing is renamed while it runs.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from . import asset_runner
from .get_mods import OFF, PLUGINS, win64

DLL_NAME = re.compile(r"^[A-Za-z0-9_.-]+\.dll$", re.IGNORECASE)


class PluginError(Exception):
    """Nothing was renamed, and this says why in words a player can act on."""


def folder(game_root: Path) -> Path:
    return win64(game_root) / PLUGINS


def present(game_root: Path) -> dict[str, tuple[str, bool]]:
    """Plugin name (lower case) -> (its own name, whether it is on), for every
    plugin in lid-plugins. With both copies there, the loaded one counts."""
    where = folder(game_root)
    found: dict[str, tuple[str, bool]] = {}
    if not where.is_dir():
        return found
    for path in where.iterdir():
        lower = path.name.lower()
        if not path.is_file():
            continue
        if lower.endswith(".dll"):
            found[lower] = (path.name, True)
        elif lower.endswith(".dll" + OFF):
            name = path.name[: -len(OFF)]
            found.setdefault(name.lower(), (name, False))
    return found


def switch(game_root: Path, name: str, on: bool) -> bool:
    """Switch one plugin on or off by renaming it. True when something changed."""
    if not DLL_NAME.match(name):
        raise PluginError(f"{name!r} is not a plugin's name")
    reason = asset_runner.game_lock_reason()
    if reason:
        raise PluginError(reason)
    where = folder(game_root)
    loaded, parked = where / name, where / (name + OFF)
    source, target = (parked, loaded) if on else (loaded, parked)
    if not source.is_file():
        return False                    # already that way (or not there at all)
    try:
        # the copy being switched to wins: an old leftover of the other kind
        # (an update put a new .dll beside a parked .off, say) is replaced
        os.replace(source, target)
    except OSError as exc:
        raise PluginError(f"{name} could not be renamed: {exc}") from exc
    return True


def follow_mods(game_root: Path | None, mods, enabled_ids, log=None) -> list[str]:
    """Each plugin a mod names, switched to match: on while any mod naming it
    is on, off when none is. Returns what changed, for the log. A plugin that
    is not there is only mentioned when a mod wanting it is on."""
    changed: list[str] = []
    if game_root is None or not folder(game_root).is_dir():
        return changed
    enabled = set(enabled_ids)
    wanted: dict[str, list] = {}            # plugin (lower case) -> mods naming it
    names: dict[str, str] = {}
    for mod in mods:
        for name in getattr(mod, "plugins", ()):
            wanted.setdefault(name.lower(), []).append(mod)
            names.setdefault(name.lower(), name)
    there = present(game_root)
    for key, naming in wanted.items():
        on_by = [mod.id for mod in naming if mod.id in enabled]
        if key not in there:
            if on_by and log is not None:
                log.warn(f"{', '.join(on_by)} goes with the plugin {names[key]}, which is not in "
                         f"{folder(game_root)} - get it with Get mods")
            continue
        name, is_on = there[key]
        want = bool(on_by)
        if is_on == want:
            continue
        try:
            if switch(game_root, name, want):
                why = f"with {', '.join(on_by)}" if want else f"- {', '.join(m.id for m in naming)} off"
                changed.append(f"{name} switched {'on' if want else 'off'} {why}")
        except PluginError as exc:
            if log is not None:
                log.warn(f"{name} could not be switched {'on' if want else 'off'}: {exc}")
    if log is not None:
        for line in changed:
            log.info(line)
    return changed
