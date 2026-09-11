"""Editing a mod's details from inside the program.

Renaming a mod, fixing its description, writing its readme - all things that
otherwise mean closing the manager and opening a text editor, which most people
reasonably do not want to do.

Renaming is the part with teeth. A mod's id is its folder name, and that name
keys three other things: its snapshot file (which is how Revert works), its slot
in the load order, and its record of having been applied. Move the folder and
leave those behind and the mod quietly loses its way back to vanilla. So a
rename moves all of them together, and puts the folder back if any of it fails.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from .errors import ModManagerError
from .mod import Mod
from .snapshot import snapshot_path


class ModEditError(ModManagerError):
    """The edit could not be made, and nothing was changed."""


# Keys mod.json holds that this module owns. Everything else in the file -
# patches, requires, apply mode - is left exactly as the author wrote it.
_OWNED_KEYS = ("id", "name", "description", "version", "author")


def safe_folder_name(name: str) -> str:
    """A folder name that cannot escape the mods directory."""
    cleaned = "".join(ch for ch in name.strip() if ch not in '<>:"/\\|?*').strip(" .")
    return cleaned or "mod"


def _sql_files(folder: Path) -> list[Path]:
    return sorted(p for p in folder.glob("*.sql") if p.name.lower() != "inverse.sql")


def _mod_json_payload(mod: Mod, folder: Path) -> dict:
    """The existing mod.json, or a fresh one for a mod that has never had one.

    A bare ``.sql`` folder gains a real mod.json the first time someone gives it
    a name - which is the point, since without one it shows in the list as
    "(unknown - .sql only)".
    """
    path = folder / "mod.json"
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            raise ModEditError(f"{path.name} could not be read: {exc}") from exc
        if not isinstance(data, dict):
            raise ModEditError(f"{path.name} is not a JSON object")
        return data

    sql = _sql_files(folder)
    if not sql:
        raise ModEditError("this mod has neither a mod.json nor a .sql file to describe")
    return {
        "requires": [],
        "conflicts_with": [],
        "patches": [
            {
                "type": "raw_sql_file",
                "path": sql[0].name,
                "description": f"Applies {sql[0].name}",
            }
        ],
    }


def _write_mod_json(folder: Path, data: dict) -> None:
    (folder / "mod.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def save_readme(mod: Mod, text: str) -> Path | None:
    """Write (or clear) the mod's readme. Returns the file, or None if removed."""
    existing = mod.readme
    target = existing or (mod.folder / "readme.md")
    if not text.strip():
        if existing and existing.is_file():
            existing.unlink()
        return None
    target.write_text(text.rstrip() + "\n", encoding="utf-8")
    return target


def edit_mod(
    mod: Mod,
    *,
    state,
    snapshots_dir: Path,
    name: str,
    description: str | None = None,
    author: str | None = None,
    version: str | None = None,
    readme: str | None = None,
) -> str:
    """Apply an edit, renaming the folder if the name changed. Returns the new id.

    ``description``, ``author``, ``version`` and ``readme`` are left alone when
    None, so a caller can change just one thing.
    """
    name = name.strip()
    if not name:
        raise ModEditError("a mod needs a name")

    folder = Path(mod.folder)
    new_id = safe_folder_name(name)
    target = folder.parent / new_id
    renaming = new_id != mod.id

    if renaming and target.exists():
        raise ModEditError(f"a mod folder called {new_id!r} already exists")

    data = _mod_json_payload(mod, folder)
    original_json = (folder / "mod.json").read_bytes() if (folder / "mod.json").is_file() else None

    if renaming:
        try:
            folder.rename(target)
        except OSError as exc:
            raise ModEditError(f"could not rename the folder: {exc}") from exc
    else:
        target = folder

    try:
        data["id"] = new_id
        data["name"] = name
        if description is not None:
            data["description"] = description.strip() or data.get("description") or name
        elif not data.get("description"):
            data["description"] = name
        if author is not None:
            data["author"] = author.strip() or "unknown"
        if version is not None:
            data["version"] = version.strip() or "1.0.0"
        data.setdefault("author", "unknown")
        data.setdefault("version", "1.0.0")
        _write_mod_json(target, data)

        if readme is not None:
            save_readme(
                Mod(
                    id=new_id, name=name, description="", version="", author="",
                    folder=target,
                ),
                readme,
            )

        if renaming:
            _move_state(state, snapshots_dir, mod.id, new_id)
    except Exception:
        # Put everything back: a half-renamed mod is worse than a refused edit.
        if renaming and target.exists():
            try:
                target.rename(folder)
            except OSError:  # pragma: no cover - only if the disk turns hostile
                pass
        if original_json is not None:
            (folder / "mod.json").write_bytes(original_json)
        raise

    return new_id


def _move_state(state, snapshots_dir: Path, old_id: str, new_id: str) -> None:
    """Carry the load-order slot, applied record and snapshot to the new id."""
    old_snapshot = snapshot_path(snapshots_dir, old_id)
    new_snapshot = snapshot_path(snapshots_dir, new_id)
    if old_snapshot.is_file():
        # The snapshot names the mod it belongs to; rewrite it as it moves so a
        # revert later can still recognise it.
        try:
            payload = json.loads(old_snapshot.read_text(encoding="utf-8"))
            payload["mod_id"] = new_id
            new_snapshot.write_text(
                json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
            )
            old_snapshot.unlink()
        except (OSError, ValueError):
            # Better a copied snapshot under the new name than none at all.
            shutil.copy2(old_snapshot, new_snapshot)
            old_snapshot.unlink(missing_ok=True)

    if old_id in state.enabled_mods:
        # Keep the load-order position: renaming must not change who wins.
        state.enabled_mods[state.enabled_mods.index(old_id)] = new_id
    if old_id in state.applied:
        state.applied[new_id] = state.applied.pop(old_id)
    if old_id in state.disabled_patches:
        # Which parts the player switched off is keyed by mod id too, and a
        # rename that dropped it would quietly switch them all back on.
        state.disabled_patches[new_id] = state.disabled_patches.pop(old_id)
    for pack, mod_ids in state.modpacks.items():
        state.modpacks[pack] = [new_id if m == old_id else m for m in mod_ids]
    state.save()
