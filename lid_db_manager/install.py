"""Turning a file someone hands the manager into a mod folder.

Covers everything a user might drop on the window or pick from the Tools menu:

    a .sql file        -> a new mod folder built around it
    a mod folder       -> copied in as-is
    an asset folder    -> a mod folder with one asset_file patch, mod.json written
    a .zip             -> extracted (a single top-level folder is unwrapped)
    a modded masters.db -> diffed against vanilla, the difference written as SQL

An "asset folder" is one with no mod.json but game files to copy - a bare pile
of .upk, or an ``assets/`` subfolder of them (with or without the pack's own
``catalog.json`` alongside). Only the files are taken; a catalog's database
changes, if any, are its installer's own logic and do not come across.

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

from . import crossover, dbdiff, vetted
from .errors import ModManagerError
from .sqlutil import sha256_file

KIND_SQL = "sql"
KIND_FOLDER = "folder"
KIND_ZIP = "zip"
KIND_DATABASE = "database"
KIND_ASSET_FOLDER = "asset_folder"
# A recognised content pack: artwork plus the database changes that make it
# reachable in game, installed together as one mod.
KIND_CONTENT_PACK = "content_pack"
# A mod release matching one of the vetted recordings in recipes/ - artwork
# plus the one executable hash the game needs updating to accept it.
KIND_VETTED_MOD = "vetted_mod"

# Where LET IT DIE keeps the loose packages an asset mod replaces.
ASSET_TARGET_DIR = "BrgGame/CookedPCConsole"
ASSET_EXTENSIONS = (".upk",)


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
    # Set when the source was recognised as a known content pack, whether or
    # not its database half could be matched to a recorded recipe.
    pack: "crossover.PackInfo | None" = None
    # Set when the source matched a vetted recording in recipes/.
    recipe: "vetted.ExeRecipe | None" = None


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


def _asset_root(folder: Path) -> Path | None:
    """The directory to mirror when a folder is an asset drop, or None.

    Prefers an ``assets/`` subfolder of game files; falls back to loose game
    files sitting directly in the dropped folder.
    """
    assets = folder / "assets"
    if assets.is_dir() and any(
        p.suffix.lower() in ASSET_EXTENSIONS for p in assets.rglob("*") if p.is_file()
    ):
        return assets
    if any(p.suffix.lower() in ASSET_EXTENSIONS for p in folder.glob("*") if p.is_file()):
        return folder
    return None


def _asset_files(root: Path) -> list[Path]:
    return sorted(
        p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in ASSET_EXTENSIONS
    )


def _folder_candidate(folder: Path, found_in: str = "") -> InstallCandidate | None:
    """What this one folder is, if it holds game files at all. None if it does not.

    ``found_in`` names the subfolder it was found in, when the drop was on a
    parent - so the note can say where the content actually came from rather
    than looking like it read the whole download.
    """
    where = f"  (from {found_in}/)" if found_in else ""

    # A release matching a vetted recording is recognised before anything else:
    # its artwork alone would be refused by the game, so installing it as a
    # plain pile of .upk would look like it worked and quietly not.
    recording = vetted.identify_mod_folder(folder)
    if recording is not None:
        return InstallCandidate(
            folder, KIND_VETTED_MOD, recording.summary,
            f"{recording.package} -> {ASSET_TARGET_DIR}, and the one hash the game "
            f"keeps for it{where}",
            recipe=recording,
        )

    asset_root = _asset_root(folder)
    if asset_root is None:
        return None
    count = len(_asset_files(asset_root))
    pack = crossover.identify(folder)
    if pack.complete:
        recipe = pack.recipe
        return InstallCandidate(
            folder, KIND_CONTENT_PACK, pack.suggested_name,
            f"{recipe.summary}, plus {count} game file(s) -> {ASSET_TARGET_DIR}{where}",
            pack=pack,
        )
    note = f"{count} game file(s) -> {ASSET_TARGET_DIR}{where}"
    if pack.is_pack:
        # Recognised, but its database half cannot be matched. Installing the
        # artwork alone is harmless - it simply stays unreachable.
        return InstallCandidate(
            folder, KIND_ASSET_FOLDER, pack.suggested_name,
            note + "  (artwork only)", warnings=[pack.reason], pack=pack,
        )
    if (folder / "catalog.json").is_file():
        note += "  (catalog.json database changes are not imported - files only)"
    return InstallCandidate(folder, KIND_ASSET_FOLDER, folder.name, note)


def _nested_pack(folder: Path) -> InstallCandidate | None:
    """A recognised content pack one level inside the folder that was dropped.

    Releases have started bundling the pack as a subfolder beside other things -
    a second mod, an installer, artwork of their own - so the folder a player
    actually has is the one above the pack. Looking one level down finds it.

    Only a recognised content pack is taken this way, never a plain pile of
    .upk: those subfolders can be anything (a second mod with its own rules, a
    backup, a source tree), and copying whichever one happened to sort first
    into the game would be a guess. One level only, and no recursion.
    """
    try:
        children = sorted(p for p in folder.iterdir() if p.is_dir())
    except OSError:
        return None
    found = []
    for child in children:
        if child.name.startswith((".", "__")):
            continue
        if crossover.identify(child).is_pack:
            found.append(child)
    if not found:
        return None
    if len(found) > 1:
        names = ", ".join(p.name for p in found)
        raise InstallError(
            f"{folder.name} holds more than one content pack ({names}). "
            "Drop the one you want rather than the folder above it."
        )
    candidate = _folder_candidate(found[0], found_in=found[0].name)

    # A download can hold more than one mod. Only the pack is taken, but say
    # what else was in there rather than leaving it looking like nothing was.
    others = [
        child for child in children
        if child != found[0] and vetted.identify_mod_folder(child) is not None
    ]
    if candidate is not None and others:
        names = ", ".join(f"{vetted.identify_mod_folder(c).summary} ({c.name}/)"
                          for c in others)
        candidate.warnings.append(
            f"{folder.name} also holds {names}. Only the content pack was taken - "
            "drop that subfolder on its own to add it as a separate mod."
        )
    return candidate


def inspect(source: Path, vanilla: Path | None = None) -> InstallCandidate:
    """Work out what a path is, without writing anything.

    For a database this also computes the diff, so the caller can show what the
    mod would contain - and refuse early if the file is the wrong game version.
    """
    source = Path(source)
    if not source.exists():
        raise InstallError(f"{source} does not exist")

    if source.is_dir():
        if (source / "mod.json").is_file() or any(source.glob("*.sql")):
            return InstallCandidate(source, KIND_FOLDER, source.name, "folder copied as-is")
        here = _folder_candidate(source)
        if here is not None:
            return here
        nested = _nested_pack(source)
        if nested is not None:
            return nested
        raise InstallError(
            f"{source.name} has no mod.json, no .sql file and no game files to copy, "
            "so it is not a mod folder"
        )

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


def _write_vetted_mod(candidate: InstallCandidate, folder: Path) -> Path:
    """A mod release matching a vetted recording.

    The mod.json is not written here - it is copied, byte for byte, from the
    one that ships beside the recording. What gets installed is therefore a
    file that was reviewed and committed, not something this code made up at
    the time, which is the whole reason the recording is vetted at all.

    Only the replacement package is taken from the player's download. Anything
    else in that folder - an installer, a manifest, a script - is left behind.
    """
    recipe = candidate.recipe
    if recipe is None:
        raise InstallError("this mod does not match a vetted recording")
    source = candidate.source / recipe.package
    if not source.is_file():
        raise InstallError(f"{candidate.source.name} no longer contains {recipe.package}")
    try:
        mod_json = recipe.mod_json()
    except vetted.VettedError as exc:
        raise InstallError(str(exc)) from None

    folder.mkdir(parents=True)
    try:
        (folder / "assets").mkdir()
        shutil.copy2(source, folder / "assets" / recipe.package)
        if sha256_file(folder / "assets" / recipe.package) != recipe.asset_sha256:
            raise InstallError(f"{recipe.package} changed while it was being copied")
        mod_json = dict(mod_json)
        mod_json["id"] = folder.name
        (folder / "mod.json").write_text(
            json.dumps(mod_json, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        for doc in candidate.source.glob("*"):
            if doc.is_file() and doc.suffix.lower() in (".txt", ".md"):
                shutil.copy2(doc, folder / doc.name)
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return folder


def _write_content_pack(candidate: InstallCandidate, folder: Path, name: str,
                        description: str, author: str, version: str) -> Path:
    """One mod holding both halves of a recognised content pack.

    The database half is a recipe that ships with the manager; the artwork is
    copied out of the pack the player downloaded. They go in together because
    either half alone is wrong: the rows without the artwork give invisible or
    wrong-textured gear, and the artwork without the rows is unreachable.
    """
    pack = candidate.pack
    recipe = pack.recipe if pack else None
    if pack is None or recipe is None:
        raise InstallError("this content pack has no recorded database changes")
    asset_root = _asset_root(candidate.source)
    if asset_root is None:
        raise InstallError(f"{candidate.source.name} has no game files to copy")

    folder.mkdir(parents=True)
    try:
        files = _asset_files(asset_root)
        if not files:
            raise InstallError("no game files found to copy")
        for src in files:
            dest = folder / "assets" / src.relative_to(asset_root)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)

        sql_name = f"{safe_folder_name(name)}.sql"
        (folder / sql_name).write_text(recipe.sql(), encoding="utf-8")

        # The pack's own readme and changelog, so the Readme tab shows what the
        # content is and who made it.
        for doc in candidate.source.glob("*"):
            if doc.is_file() and doc.suffix.lower() in (".txt", ".md"):
                shutil.copy2(doc, folder / doc.name)

        _write_mod_json_patches(
            folder, name,
            description or (
                f"{recipe.summary}, with the {len(files)} artwork packages they need. "
                f"Database changes recorded from the pack's own v{recipe.version} "
                "installer; artwork from the pack itself."
            ),
            author, version or recipe.version,
            [
                {
                    "type": "raw_sql_file",
                    "path": sql_name,
                    "description": f"{recipe.summary} (v{recipe.version})",
                },
                {
                    "type": "asset_file",
                    "source": "assets",
                    "target": ASSET_TARGET_DIR,
                    "description": f"{len(files)} artwork package(s)",
                },
            ],
            game_version=recipe.game_version,
        )
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return folder


def _write_mod_json_patches(folder: Path, name: str, description: str, author: str,
                            version: str, patches: list[dict],
                            requires: list[str] | None = None,
                            game_version: str = "") -> None:
    payload = {
        "id": folder.name,
        "name": name,
        "description": description.strip() or f"Imported from {folder.name}",
        "version": version.strip() or "1.0.0",
        "author": author.strip() or "unknown",
        # Only written when it is actually known. An empty value would read as
        # "built for no particular build", which is not the same as silence.
        **({"game_version": game_version} if game_version else {}),
        "requires": [r for r in (requires or []) if r],
        "conflicts_with": [],
        "patches": patches,
    }
    (folder / "mod.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
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


def _write_delta_mod(
    folder: Path,
    delta,
    *,
    name: str,
    description: str,
    author: str,
    version: str,
    source_name: str,
    vanilla: Path | None,
    part_of: str = "",
    requires: list[str] | None = None,
) -> Path:
    """Write one delta out as a self-contained mod folder."""
    folder.mkdir(parents=True)
    try:
        stamp = datetime.now().isoformat(timespec="seconds")
        fingerprint = sha256_file(Path(vanilla)) if vanilla else "unknown"

        def header_for(what, summary: str) -> str:
            text = (
                f"{what}\n"
                f"Generated from {source_name} on {stamp}.\n"
                f"Every value below differs from the vanilla database it was compared\n"
                f"against - vanilla sha256 {fingerprint}.\n"
                f"{summary}"
            )
            if part_of:
                text += (
                    f"\n\nOne piece of {part_of}, split so it can be switched on and off\n"
                    f"on its own. Turning pieces off can produce a combination the\n"
                    f"original author never tried."
                )
            return text

        # One file - and so one switch - per table. That is what lets a whole
        # rework stay a single mod the player can still take pieces of. Done
        # even for a lone table, so the switch is always keyed by the table name
        # and never by a position that shifts when the mod is edited.
        patches = []
        for table_delta in delta.tables:
            piece = dbdiff.DbDelta(tables=[table_delta])
            sql_name = f"{safe_folder_name(table_delta.table)}.sql"
            (folder / sql_name).write_text(
                dbdiff.to_sql(piece, header_for(table_delta.table, piece.summary())),
                encoding="utf-8",
            )
            patches.append(
                {
                    "type": "raw_sql_file",
                    "path": sql_name,
                    # The table name is what the player's choice is remembered
                    # against, so it must not drift.
                    "id": table_delta.table,
                    "description": f"{table_delta.table} - {piece.summary()}",
                }
            )
        _write_mod_json_patches(folder, name, description, author, version, patches, requires)
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return folder


def install_database(
    candidate: InstallCandidate,
    mods_dir: Path,
    name: str,
    description: str = "",
    author: str = "",
    version: str = "1.0.0",
    vanilla: Path | None = None,
    overwrite: bool = False,
    selection: dict | None = None,
    split_by_table: bool = False,
    requires: list[str] | None = None,
) -> list[Path]:
    """Turn a modded database into one mod per table, or one mod for the lot.

    Splitting is the point: each table's changes become an ordinary mod, so the
    player can switch off the part of a rework they do not want and keep the
    rest - later, not only at import time.
    """
    if candidate.delta is None:
        raise InstallError("this file was not compared against vanilla, so it has no changes")

    delta = candidate.delta.filtered(selection) if selection is not None else candidate.delta
    if delta.empty:
        raise InstallError("nothing was selected, so there is no mod to write")

    pieces = delta.split_by_table() if split_by_table else [delta]
    part_of = name if len(pieces) > 1 else ""

    planned: list[tuple[Path, object, str]] = []
    for piece in pieces:
        # The table always goes in the name when splitting, even for a lone
        # piece: importing the same rework twice for two different tables must
        # not collide, and the name should say what the mod covers.
        table = piece.tables[0].table if split_by_table else ""
        piece_name = f"{name} - {table}" if table else name
        folder = Path(mods_dir) / safe_folder_name(piece_name)
        if folder.exists():
            if not overwrite:
                raise InstallError(f"A mod folder called {folder.name!r} already exists.")
            shutil.rmtree(folder)
        planned.append((folder, piece, piece_name))

    written: list[Path] = []
    try:
        for folder, piece, piece_name in planned:
            summary = piece.summary()
            written.append(
                _write_delta_mod(
                    folder,
                    piece,
                    name=piece_name,
                    description=description.strip() or summary,
                    author=author,
                    version=version,
                    source_name=candidate.source.name,
                    vanilla=vanilla,
                    part_of=part_of,
                    requires=requires,
                )
            )
    except Exception:
        # All or none: a half-written pack is worse than a clear failure.
        for folder in written:
            shutil.rmtree(folder, ignore_errors=True)
        raise
    return written


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

    if candidate.kind == KIND_CONTENT_PACK:
        return _write_content_pack(candidate, folder, name, description, author, version)

    if candidate.kind == KIND_VETTED_MOD:
        return _write_vetted_mod(candidate, folder)

    if candidate.kind == KIND_ASSET_FOLDER:
        asset_root = _asset_root(candidate.source)
        if asset_root is None:
            raise InstallError(f"{candidate.source.name} has no game files to copy")
        folder.mkdir(parents=True)
        try:
            files = _asset_files(asset_root)
            if not files:
                raise InstallError("no game files found to copy")
            for src in files:
                dest = folder / "assets" / src.relative_to(asset_root)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
            # Bring any provenance docs along so the Readme tab has something.
            for doc in candidate.source.glob("*"):
                if doc.is_file() and doc.suffix.lower() in (".txt", ".md"):
                    shutil.copy2(doc, folder / doc.name)
            _write_mod_json_patches(
                folder, name, description, author, version,
                [{
                    "type": "asset_file",
                    "source": "assets",
                    "target": ASSET_TARGET_DIR,
                    "description": f"{len(files)} replacement game file(s)",
                }],
            )
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            raise
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
