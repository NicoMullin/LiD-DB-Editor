"""Which mods the save in the game is played with, kept beside the save so it goes wherever the save goes.

The save library (savelibrary.py) remembers each save's mods in the
manager's own folder, which stays on one PC. The save in the game also moves
without the manager: Steam Cloud carries it to every PC the player uses, and
people copy it between installs by hand. So the mod list is also written next
to it, as ``Savedata/<steam id>/brggame.sav.lidmm.json``: named like the
game's own files, so Steam Cloud carries it along with the save.

The note says which save it is about, by what stays the same as the save is
played - the account it belongs to, its uid and the moment the game created
it (``user.psnacid``, ``user.uid``, ``user.created``) - so a note left by
another save, a save dropped in by hand, say, is never taken for this one's.
It holds the mod list as the manager's own state keeps it, with each mod's
version, and is rewritten every time the mod list is saved and every time a
save is switched in. A manager on any PC compares it with what its own
install has applied and says when they differ, with the save's list one click
away.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from . import savegame

LINK_NAME = "brggame.sav.lidmm.json"
FORMAT = 1
MOD_KEYS = ("enabled_mods", "mod_settings", "disabled_patches", "enabled_patches")


def identity(doc: dict) -> str:
    """What says which save this is, unchanged however long it is played."""
    user = doc.get("user") if isinstance(doc.get("user"), dict) else {}
    return f"{user.get('psnacid', '')}:{user.get('uid', '')}:{user.get('created', '')}"


def save_identity(save: Path) -> str:
    _, payload = savegame.read_container(Path(save).read_bytes())
    try:
        doc = json.loads(payload)
    except ValueError:
        raise savegame.SaveError("the save's contents are not readable - it may be damaged") from None
    return identity(doc if isinstance(doc, dict) else {})


def link_path(save: Path) -> Path:
    return Path(save).with_name(LINK_NAME)


@dataclass(frozen=True)
class Link:
    save: str               # identity() of the save it is about
    mods: dict              # MOD_KEYS -> as State keeps them
    versions: dict          # mod id -> version, of the mods it lists
    written: str
    by: str

    @property
    def enabled(self) -> list[str]:
        return [str(m) for m in self.mods.get("enabled_mods", []) or []]


def read(save: Path) -> Link | None:
    try:
        data = json.loads(link_path(save).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("format") != FORMAT or not isinstance(data.get("mods"), dict):
        return None
    mods = {k: data["mods"][k] for k in MOD_KEYS if k in data["mods"]}
    versions = data.get("versions") if isinstance(data.get("versions"), dict) else {}
    return Link(str(data.get("save", "")), mods, {str(k): str(v) for k, v in versions.items()},
                str(data.get("written", "")), str(data.get("by", "")))


def write(save: Path, mods: dict, versions: dict, by: str) -> Link:
    """Note ``mods`` beside ``save`` (written whole, then swapped in)."""
    link = Link(save_identity(save), {k: mods[k] for k in MOD_KEYS if k in mods}, dict(versions),
                time.strftime("%Y-%m-%d %H:%M:%S"), by)
    target = link_path(save)
    temporary = target.with_name(target.name + ".new")
    temporary.write_text(json.dumps({
        "format": FORMAT, "save": link.save, "mods": link.mods, "versions": link.versions,
        "written": link.written, "by": link.by,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, target)
    return link


def remove(save: Path) -> None:
    link_path(save).unlink(missing_ok=True)


@dataclass(frozen=True)
class Check:
    """How the save in the game's own note compares with what this install has applied."""

    save: Path
    link: Link | None
    belongs: bool            # the note is about this very save
    missing: tuple = ()      # mods the note lists that are not installed here
    differs: bool = False    # the note's list is not what this install has applied
    versions: tuple = ()     # (mod id, the note's version, this install's version) where they differ

    @property
    def state(self) -> str:
        if self.link is None:
            return "none"
        if not self.belongs:
            return "other"
        return "differs" if self.differs or self.missing else "matches"


CHOICE_KEYS = ("mod_settings", "disabled_patches", "enabled_patches")


def trim_choice(choice: dict) -> dict:
    """``choice`` with only what touches its ticked mods: the settings of a mod that is
    off change nothing in the game, so a comparison (and a mod pack) leaves them out."""
    enabled = [str(m) for m in choice.get("enabled_mods", []) or []]
    trimmed: dict = {"enabled_mods": enabled}
    for key in CHOICE_KEYS:
        found = choice.get(key) or {}
        trimmed[key] = {m: json.loads(json.dumps(found[m])) for m in enabled if found.get(m)}
    return trimmed


def same_choice(a: dict, b: dict) -> bool:
    """Two mod lists the same: the same mods in the same order, with the same settings and
    parts. Settings kept for mods that are off do not count."""
    def norm(mods: dict) -> tuple:
        mods = trim_choice(mods)
        return (mods["enabled_mods"], mods["mod_settings"],
                {k: sorted(v) for k, v in mods["disabled_patches"].items()},
                {k: sorted(v) for k, v in mods["enabled_patches"].items()})
    return norm(a) == norm(b)


def check(save: Path, applied: dict, installed: dict) -> Check:
    """``applied``: this install's mod list as last saved; ``installed``: mod id -> version here."""
    link = read(save)
    if link is None:
        return Check(save, None, False)
    try:
        belongs = link.save == save_identity(save)
    except (OSError, savegame.SaveError):
        belongs = False
    if not belongs:
        return Check(save, link, False)
    missing = tuple(m for m in link.enabled if m not in installed)
    versions = tuple((m, link.versions[m], installed[m]) for m in link.enabled
                     if m in installed and m in link.versions and link.versions[m] != installed[m])
    return Check(save, link, True, missing, not same_choice(link.mods, applied), versions)
