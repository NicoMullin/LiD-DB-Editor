"""Turning a file someone hands the manager into a mod folder.

Covers everything a user might drop on the window or pick from the Tools menu:

    a .sql file        -> a new mod folder built around it
    a mod folder       -> copied in as-is
    a .zip             -> extracted (a single top-level folder is unwrapped)
    a modded masters.db -> diffed against vanilla, the difference written as SQL

Installing never enables or applies anything. The mod turns up in the list
unticked so its diff can be looked at first.
"""

from __future__ import annotations

import json
import re
import shutil
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import dbdiff
from .errors import ModManagerError
from .sqlutil import sha256_file

KIND_SQL = "sql"
KIND_FOLDER = "folder"
KIND_ZIP = "zip"
KIND_DATABASE = "database"


class InstallError(ModManagerError):
    """The dropped thing could not be turned into a mod."""


@dataclass
class InstallCandidate:
    """What a dropped path turns out to be, before anything is written."""

    source: Path
    kind: str
    suggested_name: str
    note: str = ""
    delta: "dbdiff.DbDelta | None" = None
    warnings: list[str] = field(default_factory=list)


def _title_from(text: str) -> str:
    """`SkillCosts.sql` -> `Skill Costs`; `shop-prices-1kc` -> `Shop Prices 1Kc`."""
    stem = re.sub(r"[-_]+", " ", text).strip()
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", stem)
    return " ".join(word[:1].upper() + word[1:] for word in spaced.split()) or text


def safe_folder_name(name: str) -> str:
    """A mod folder name. Spaces are fine - the scanner handles them."""
    cleaned = "".join(ch for ch in name if ch.isalnum() or ch in " -_.").strip(" .")
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned or cleaned.startswith(("_", ".")):
        cleaned = "mod " + cleaned.lstrip("_. ")
    return cleaned[:80]


def inspect(source: Path, vanilla: Path | None = None) -> InstallCandidate:
    """Work out what a path is, without writing anything.

    For a database this also computes the diff, so the caller can show what the
    mod would contain - and refuse early if the file is the wrong game version.
    """
    source = Path(source)
    if not source.exists():
        raise InstallError(f"{source} does not exist")

    if source.is_dir():
        has_mod = (source / "mod.json").is_file() or any(source.glob("*.sql"))
        if not has_mod:
            raise InstallError(
                f"{source.name} has no mod.json and no .sql file, so it is not a mod folder"
            )
        return InstallCandidate(source, KIND_FOLDER, source.name, "folder copied as-is")

    suffix = source.suffix.lower()
    if suffix == ".zip":
        return InstallCandidate(source, KIND_ZIP, _title_from(source.stem), "zip archive")
    if suffix == ".sql":
        return InstallCandidate(source, KIND_SQL, _title_from(source.stem), "SQL patch")
    if suffix in (".db", ".sqlite", ".sqlite3"):
        if vanilla is None or not Path(vanilla).is_file():
            raise InstallError(
                "Comparing a modded database needs a vanilla copy to compare against, "
                "and none exists yet. Save your mod list once first - that writes "
                "masters.db.original - or point the manager at a clean database."
            )
        delta = dbdiff.compare(Path(vanilla), source)
        if delta.empty:
            raise InstallError(
                f"{source.name} is identical to your vanilla database - there is "
                "nothing to turn into a mod."
            )
        return InstallCandidate(
            source,
            KIND_DATABASE,
            _title_from(source.stem if source.stem != "masters" else source.parent.name),
            delta.summary(),
            delta=delta,
            warnings=list(delta.warnings),
        )
    raise InstallError(
        f"Do not know what to do with {source.name}. Drop a .sql patch, a mod "
        "folder, a .zip, or a modded masters.db."
    )


def _write_mod_json(folder: Path, name: str, description: str, author: str,
                    version: str, sql_file: str | None) -> None:
    payload = {
        "id": folder.name,
        "name": name,
        # mod.json requires a description, so a blank one gets a useful default
        # rather than a mod that fails to load.
        "description": description.strip() or f"Imported from {folder.name}",
        "version": version.strip() or "1.0.0",
        "author": author.strip() or "unknown",
        "requires": [],
        "conflicts_with": [],
        "patches": [
            {
                "type": "raw_sql_file",
                "path": sql_file,
                "description": f"Applies {sql_file}",
            }
        ],
    }
    (folder / "mod.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def install(
    candidate: InstallCandidate,
    mods_dir: Path,
    name: str,
    description: str = "",
    author: str = "",
    version: str = "1.0.0",
    vanilla: Path | None = None,
    overwrite: bool = False,
) -> Path:
    """Write the mod folder. Returns its path."""
    mods_dir = Path(mods_dir)
    folder = mods_dir / safe_folder_name(name)
    if folder.exists():
        if not overwrite:
            raise InstallError(f"A mod folder called {folder.name!r} already exists.")
        shutil.rmtree(folder)

    if candidate.kind == KIND_FOLDER:
        shutil.copytree(candidate.source, folder,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        return folder

    if candidate.kind == KIND_ZIP:
        staging = folder.with_name(folder.name + ".unpacking")
        shutil.rmtree(staging, ignore_errors=True)
        try:
            with zipfile.ZipFile(candidate.source) as archive:
                for member in archive.namelist():
                    # Refuse paths that would escape the folder.
                    if member.startswith("/") or ".." in Path(member).parts:
                        raise InstallError(f"{candidate.source.name} contains an unsafe path")
                archive.extractall(staging)
            entries = [p for p in staging.iterdir() if p.name != "__MACOSX"]
            root = entries[0] if len(entries) == 1 and entries[0].is_dir() else staging
            if not ((root / "mod.json").is_file() or any(root.glob("*.sql"))):
                raise InstallError(
                    f"{candidate.source.name} has no mod.json and no .sql file inside"
                )
            shutil.move(str(root), str(folder))
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        return folder

    folder.mkdir(parents=True)
    try:
        if candidate.kind == KIND_SQL:
            sql_name = candidate.source.name
            shutil.copy2(candidate.source, folder / sql_name)
        else:  # KIND_DATABASE
            sql_name = "changes.sql"
            stamp = datetime.now().isoformat(timespec="seconds")
            fingerprint = sha256_file(Path(vanilla)) if vanilla else "unknown"
            header = (
                f"{name}\n"
                f"Generated from {candidate.source.name} on {stamp}.\n"
                f"Every value below differs from the vanilla database it was compared\n"
                f"against - vanilla sha256 {fingerprint}.\n"
                f"{candidate.delta.summary()}"
            )
            (folder / sql_name).write_text(
                dbdiff.to_sql(candidate.delta, header), encoding="utf-8"
            )
        _write_mod_json(folder, name, description, author, version, sql_name)
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return folder
