"""Carrying players across when shipped mods are merged or renamed.

Several shipped mods used to be one mod at different strengths - Weapon
Durability x2 and x5, TDM Rewards x2, x5 and x10 - and a handful more had
their number in the name. Once mods could carry settings, each family became
one mod with the number as a setting. Someone who had "x5" switched on should
open the new version and find the new mod switched on at 5, in the same place
in the load order, still able to be switched off - not a list that quietly
lost a mod.

Two things happen, both at startup and both logged:

``retire_shipped_folders`` moves the old folders into ``mods/_retired/``, which
the loader skips. Only folders that are recognisably the ones this project
shipped - right id, author KSFA - are touched, and they are moved, not deleted.
An unzipped new release over an old install is exactly how they come to still
be there.

``migrate_state`` rewrites state.json: enabled list, modpacks, chosen values,
and - where the new mod writes exactly the rows the old one did - the applied
record and its snapshot, so switching it off still puts the stock values back.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from . import snapshot as snapshot_module

SHIPPED_AUTHOR = "KSFA"
RETIRED_DIRNAME = "_retired"


@dataclass(frozen=True)
class Retired:
    new_id: str
    values: dict
    # True when the new mod changes exactly the rows the old one did, so the old
    # applied record and snapshot describe it truthfully and can simply move.
    # False when the old mod was wrong about which rows to change: its record
    # stays under the old id, and the usual "these mods were removed - put
    # their rows back?" offer undoes it properly.
    keeps_history: bool = True
    # Who the old folder has to say it is by before it is moved aside. A
    # bundled mod by someone else is retired the same way when a newer release
    # of it replaces it in the bundle.
    author: str = SHIPPED_AUTHOR


RETIRED: dict[str, Retired] = {
    # Version 1.0.0 of these changed 34 individual decal prices instead of the
    # draw, so what they applied is not what the new mod does.
    "decal-cost-25k": Retired("decal-draw-price", {"price": 25000}, keeps_history=False),
    "decal-cost-10k": Retired("decal-draw-price", {"price": 10000}, keeps_history=False),
    "decal-cost-5k": Retired("decal-draw-price", {"price": 5000}, keeps_history=False),
    "weapon-durability-2x": Retired("weapon-durability", {"multiplier": 2}),
    "weapon-durability-5x": Retired("weapon-durability", {"multiplier": 5}),
    "armor-durability-2x": Retired("armor-durability", {"multiplier": 2}),
    "armor-durability-5x": Retired("armor-durability", {"multiplier": 5}),
    "tdm-rewards-2x": Retired("tdm-rewards", {"multiplier": 2}),
    "tdm-rewards-5x": Retired("tdm-rewards", {"multiplier": 5}),
    "tdm-rewards-10x": Retired("tdm-rewards", {"multiplier": 10}),
    "bank-limit-10x": Retired("bank-limit", {"multiplier": 10}),
    "weapon-ammo-2x": Retired("weapon-ammo", {"multiplier": 2}),
    "weapon-magazine-2x": Retired("weapon-magazine", {"multiplier": 2}),
    "storage-10000": Retired("storage-limit", {"slots": 10000}),
    "reward-box-250": Retired("reward-box-limit", {"slots": 250}),
    "revive-cost-1kc": Retired("revive-cost", {"price": 1}),
    "body-prices-1kc": Retired("fighter-tier-prices", {"price": 1}),
    "nitro-boost-100000pct": Retired("nitro-boost-exp", {"percent": 100000}),
    # Newer releases of the bundled packs. The applied record moves across with
    # its old version number, so the next save sees an update: the old release
    # is taken off in full before the new one goes on.
    "LET IT DIE Crossover Content v3.75": Retired(
        "LET IT DIE Crossover Content v3.79", {}, author="S3er0i9ng"
    ),
    "Colored PlayStation Buttons v1.2": Retired(
        "Colored PlayStation Buttons v1.4", {}, author="S3er0i9ng"
    ),
}


def _is_ours(folder: Path, old_id: str, author: str = SHIPPED_AUTHOR) -> bool:
    """A folder this project shipped under that name, not someone's own mod."""
    try:
        data = json.loads((folder / "mod.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return False
    return (
        isinstance(data, dict)
        and str(data.get("id", "")).strip() == old_id
        and str(data.get("author", "")).strip() == author
    )


def retire_shipped_folders(mods_dir: Path, log=None) -> list[str]:
    """Move retired shipped mod folders out of the list. Returns what moved."""
    mods_dir = Path(mods_dir)
    moved: list[str] = []
    for old_id in RETIRED:
        folder = mods_dir / old_id
        if not folder.is_dir() or not _is_ours(folder, old_id, RETIRED[old_id].author):
            continue
        store = mods_dir / RETIRED_DIRNAME
        store.mkdir(parents=True, exist_ok=True)
        destination = store / old_id
        suffix = 2
        while destination.exists():
            destination = store / f"{old_id}-{suffix}"
            suffix += 1
        try:
            shutil.move(str(folder), str(destination))
        except OSError as exc:
            if log:
                log.warn(f"Could not move the retired mod {old_id} aside ({exc})")
            continue
        moved.append(old_id)
        if log:
            log.info(
                f"{old_id} was replaced by {RETIRED[old_id].new_id}; its folder is kept in "
                f"{RETIRED_DIRNAME}/"
            )
    return moved


def _move_snapshot(snapshots_dir: Path, old_id: str, new_id: str) -> None:
    old = snapshot_module.snapshot_path(snapshots_dir, old_id)
    new = snapshot_module.snapshot_path(snapshots_dir, new_id)
    if not old.is_file() or new.exists():
        return
    try:
        payload = json.loads(old.read_text(encoding="utf-8"))
        payload["mod_id"] = new_id
        new.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        old.unlink()
    except (OSError, ValueError):
        # Better a copy under the new name than no way back at all.
        shutil.copy2(old, new)
        old.unlink(missing_ok=True)


def migrate_state(state, installed_ids: set[str], snapshots_dir: Path, log=None) -> bool:
    """Point everything that named an old mod at its replacement.

    Does nothing for an old id that is still installed (someone kept it on
    purpose) or whose replacement is not. Returns True if anything changed.
    """
    changed = False
    for old_id, retired in RETIRED.items():
        new_id = retired.new_id
        if old_id in installed_ids or new_id not in installed_ids:
            continue
        touched = False

        if old_id in state.enabled_mods:
            if new_id in state.enabled_mods:
                # Two strengths of the same thing were both on; they always
                # conflicted, and the first one met keeps its place.
                state.enabled_mods.remove(old_id)
            else:
                state.enabled_mods[state.enabled_mods.index(old_id)] = new_id
            state.mod_settings.setdefault(new_id, dict(retired.values))
            touched = True

        for pack, mod_ids in state.modpacks.items():
            if old_id in mod_ids:
                renamed = [new_id if m == old_id else m for m in mod_ids]
                state.modpacks[pack] = list(dict.fromkeys(renamed))
                touched = True

        if retired.keeps_history:
            if old_id in state.applied and new_id not in state.applied:
                state.applied[new_id] = state.applied.pop(old_id)
                _move_snapshot(snapshots_dir, old_id, new_id)
                state.mod_settings.setdefault(new_id, dict(retired.values))
                touched = True
            if old_id in state.disabled_patches and new_id not in state.disabled_patches:
                state.disabled_patches[new_id] = state.disabled_patches.pop(old_id)
                touched = True

        if touched:
            changed = True
            if log:
                shown = ", ".join(f"{k} {v:,}" for k, v in retired.values.items())
                log.info(f"{old_id} is now {new_id}" + (f" ({shown})" if shown else ""))
    return changed


def migrate_record(record, installed_ids: set[str]):
    """The note inside a database, with retired mods named as their replacements.

    A database saved before a merge lists "weapon-durability-5x"; the scan has to
    look for "weapon-durability" at x5. Same rules as migrate_state. Returns a
    new record and leaves the one it was given alone.
    """
    if record is None or not record.mods:
        return record
    from dataclasses import replace

    mods = []
    for entry in record.mods:
        retired = RETIRED.get(entry.mod_id)
        if retired is not None and entry.mod_id not in installed_ids and retired.new_id in installed_ids:
            values = dict(retired.values)
            values.update(entry.values)
            entry = replace(entry, mod_id=retired.new_id, values=values)
        if any(existing.mod_id == entry.mod_id for existing in mods):
            continue
        mods.append(entry)
    return replace(record, mods=mods)
