"""Scanning the ``mods/`` folder.

Detection rule for each subfolder:

    1. contains ``mod.json``                  -> full mod (any of the 4 patch types)
    2. contains ``mod.sql`` + ``inverse.sql`` -> paired SQL mod (explicit revert)
    3. contains exactly one ``*.sql``         -> single raw_sql_file mod
    4. otherwise                              -> skipped, with a warning

Folders whose name starts with ``_`` or ``.`` are treated as templates or
scratch space and are skipped silently (that is how ``mods/_example`` stays out
of the mod list).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .errors import ModLoadError
from .mod import Mod, load_mod_json, load_mod_sql

# Directories inside mods/ that are never mods.
RESERVED_NAMES = {"logs", "snapshots", "backups"}


@dataclass
class LoadFailure:
    folder: str
    reason: str


@dataclass
class ScanResult:
    mods: list[Mod] = field(default_factory=list)
    failures: list[LoadFailure] = field(default_factory=list)
    skipped: list[LoadFailure] = field(default_factory=list)

    @property
    def by_id(self) -> dict[str, Mod]:
        return {mod.id: mod for mod in self.mods}

    def get(self, mod_id: str) -> Mod | None:
        return self.by_id.get(mod_id)


def _is_candidate(folder: Path) -> bool:
    if not folder.is_dir():
        return False
    name = folder.name
    return not (name.startswith("_") or name.startswith(".") or name.lower() in RESERVED_NAMES)


def load_mod_folder(folder: Path) -> Mod:
    """Parse one mod folder. Raises ModLoadError if it is not a usable mod."""
    if (folder / "mod.json").is_file():
        return load_mod_json(folder)

    sql_files = sorted(p for p in folder.glob("*.sql") if p.is_file())
    lower = {p.name.lower(): p for p in sql_files}
    if "mod.sql" in lower and "inverse.sql" in lower:
        return load_mod_sql(folder, lower["mod.sql"])

    payload = [p for p in sql_files if p.name.lower() != "inverse.sql"]
    if len(payload) == 1:
        return load_mod_sql(folder, payload[0])
    if len(payload) > 1:
        raise ModLoadError(
            folder.name,
            f"folder has {len(payload)} .sql files and no mod.json - "
            "add a mod.json listing them, or leave a single mod.sql",
        )
    raise ModLoadError(folder.name, "folder has neither mod.json nor a .sql file")


def scan_mods(mods_dir: Path) -> ScanResult:
    """Load every mod folder under ``mods_dir``, sorted by id."""
    result = ScanResult()
    mods_dir = Path(mods_dir)
    if not mods_dir.is_dir():
        result.failures.append(LoadFailure(str(mods_dir), "mods folder does not exist"))
        return result

    for folder in sorted(mods_dir.iterdir()):
        if not folder.is_dir():
            continue
        if not _is_candidate(folder):
            result.skipped.append(LoadFailure(folder.name, "template or reserved folder"))
            continue
        try:
            result.mods.append(load_mod_folder(folder))
        except ModLoadError as exc:
            result.failures.append(LoadFailure(exc.mod_ref, exc.reason))
        except Exception as exc:  # a malformed mod must never take the scan down
            result.failures.append(LoadFailure(folder.name, f"unexpected error: {exc}"))

    result.mods.sort(key=lambda mod: mod.id)
    return result


def resolve_order(mods: list[Mod]) -> list[Mod]:
    """Order mods so every mod comes after the ones it 'requires'.

    Dependencies that are absent from the list are ignored here - the validator
    is what reports them. A dependency cycle falls back to the input order for
    the mods caught in it, so a bad 'requires' can't hang the apply.
    """
    by_id = {mod.id: mod for mod in mods}
    ordered: list[Mod] = []
    placed: set[str] = set()
    visiting: set[str] = set()

    def visit(mod: Mod) -> None:
        if mod.id in placed or mod.id in visiting:
            return
        visiting.add(mod.id)
        for dependency_id in mod.requires:
            dependency = by_id.get(dependency_id)
            if dependency is not None:
                visit(dependency)
        visiting.discard(mod.id)
        placed.add(mod.id)
        ordered.append(mod)

    for mod in mods:
        visit(mod)
    return ordered
