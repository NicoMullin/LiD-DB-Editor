"""A brand-new game save, as the game makes one, for "Start a new save".

The game makes a new save by itself when there is none - it asks for a
profile name and starts the tutorial - but taking the save away does not work
on Steam: Steam Cloud keeps the folder and puts the old save straight back.
So the manager writes a new one in its place instead, which Steam treats like
any save the game rewrote.

There are two starts, each a save the game itself wrote (game build 1.91):

- ``templates/new-save.sav``: made on the game's first start, so the new
  game begins with the tutorial;
- ``templates/new-save-after-tutorial.sav``: the same save once the tutorial
  was played through - the tutorial's fighter fell at its end, as it always
  does - so the game carries on from there: the two films after the tutorial,
  then choosing a fighter. For players who have seen the tutorial plenty.

Each has the two things that belonged to whoever made it blanked out, and
``user.created`` - when the game made it - is set to the moment the new save
is made, so each new game is a save of its own (see savelink.identity):

- ``user.psnacid``: the Steam account the save belongs to (on PC this is the
  64-bit Steam id, the number the save's folder is named after);
- ``user.nm``: the profile name typed in at the start.

Both are written back for the player here, each where it stands in the JSON
text, so every other byte of the game's own new save stays as it made it.
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

from . import savegame


def _templates_dir() -> Path:
    """Source tree, or the frozen build's data folder (build.py adds it there)."""
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        return Path(bundled) / "lid_db_manager" / "templates"
    return Path(__file__).resolve().parent / "templates"


TUTORIAL = "tutorial"
AFTER_TUTORIAL = "after-tutorial"
TEMPLATES = {
    TUTORIAL: _templates_dir() / "new-save.sav",
    AFTER_TUTORIAL: _templates_dir() / "new-save-after-tutorial.sav",
}
START_LABELS = {
    AFTER_TUTORIAL: "After the tutorial (the films, then choosing a fighter)",
    TUTORIAL: "From the very start, with the tutorial",
}
STEAM_ID = re.compile(r"^7656119\d{10}$")
MAX_NAME = 16
ACCOUNT_FIELD = b'"psnacid":""'
NAME_FIELD = b'"nm":""'
# The account's creation time, which comes right after its (blank) ids
CREATED = re.compile(rb'("psnacid":"","sid":"","created":)\d+')


def check_name(name: str) -> str:
    name = (name or "").strip()
    if not name:
        raise savegame.SaveError("the profile name is empty")
    if len(name) > MAX_NAME:
        raise savegame.SaveError(f"the profile name is longer than {MAX_NAME} letters")
    if any(ord(c) < 32 for c in name):
        raise savegame.SaveError("the profile name has a character the game cannot show")
    return name


def _json_text(value: str) -> bytes:
    """``value`` as the game writes a string: JSON, with "/" written "\\/"."""
    return json.dumps(value, ensure_ascii=False).encode("utf-8").replace(b"/", b"\\/")


def new_save(steam_id: str, profile_name: str, start: str = TUTORIAL, now: float | None = None) -> bytes:
    """A new game's save for ``steam_id``, whose profile is called ``profile_name``,
    beginning at ``start`` (TUTORIAL or AFTER_TUTORIAL)."""
    if start not in TEMPLATES:
        raise savegame.SaveError(f"there is no new game that starts at {start!r}")
    steam_id = str(steam_id).strip()
    if not STEAM_ID.match(steam_id):
        raise savegame.SaveError(f"{steam_id!r} is not a Steam account's id")
    name = check_name(profile_name)
    try:
        version, payload = savegame.read_container(TEMPLATES[start].read_bytes())
    except OSError as exc:
        raise savegame.SaveError(f"the new-save template is missing ({exc})") from None
    if payload.count(ACCOUNT_FIELD) != 1 or payload.count(NAME_FIELD) != 1:
        raise savegame.SaveError("the new-save template is not the one this program expects")
    # Made now, as far as anything is concerned: user.created is half of what
    # tells one save from another (savelink.identity), and two new games from
    # the same template must not look like the same save.
    created, count = CREATED.subn(rb"\g<1>%d" % int(now if now is not None else time.time()), payload)
    if count != 1:
        raise savegame.SaveError("the new-save template is not the one this program expects")
    payload = created
    payload = payload.replace(ACCOUNT_FIELD, b'"psnacid":' + _json_text(steam_id), 1)
    payload = payload.replace(NAME_FIELD, b'"nm":' + _json_text(name), 1)
    data = savegame.write_container(version, payload)
    document = json.loads(savegame.read_container(data)[1])
    if document["user"]["psnacid"] != steam_id or document["user"]["nm"] != name:
        raise savegame.SaveError("the new save did not read back right")
    return data
