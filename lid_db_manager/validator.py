"""Pre-apply validation.

Everything here is read-only: the database is opened in ``mode=ro`` and raw SQL
is compiled with EXPLAIN rather than executed. Nothing is applied unless every
enabled mod passes, so a broken mod can never leave the DB half-patched.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from . import asset_runner, exe_checksums, vetted
from .conflict import ConflictReport, analyze
from .errors import ValidationError
from .mod import Mod
from .sqlutil import connect, database_game_version


@dataclass
class ModValidation:
    mod_id: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    estimated_rows: int = 0
    patch_summaries: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass
class ValidationReport:
    results: list[ModValidation] = field(default_factory=list)
    conflicts: ConflictReport = field(default_factory=ConflictReport)
    fatal: str = ""

    @property
    def ok(self) -> bool:
        return not self.fatal and all(result.ok for result in self.results)

    @property
    def failed(self) -> list[ModValidation]:
        return [result for result in self.results if not result.ok]

    def for_mod(self, mod_id: str) -> ModValidation | None:
        for result in self.results:
            if result.mod_id == mod_id:
                return result
        return None

    def summary_line(self) -> str:
        if self.fatal:
            return f"Validation could not run: {self.fatal}"
        total = len(self.results)
        bad = len(self.failed)
        warnings = sum(len(result.warnings) for result in self.results) + len(
            self.conflicts.conflicts
        )
        if bad:
            return f"{bad} of {total} mod(s) failed validation"
        return f"Validated {total} mod(s) OK ({warnings} warning(s))"


def game_version_warning(con: sqlite3.Connection, mod: Mod) -> str:
    """Why this mod's game build and the database's differ, or "".

    A mod is a set of changes measured against one build of the game. When the
    game is patched, most of those changes still land exactly as intended - the
    rows are addressed by name, and a patch rarely touches the same ones. But
    when it does not fit, nothing raises: the mod applies, and something is
    quietly wrong in game. So a mismatch is reported rather than assumed
    harmless, and it is a warning rather than a refusal because the usual
    outcome really is that it is fine.

    Only mods that say which build they were made for are checked. A mod that
    says nothing is not guessed about.
    """
    builds = list(mod.game_versions) or ([mod.game_version] if mod.game_version else [])
    if not builds:
        return ""
    actual = database_game_version(con)
    if not actual or actual in builds:
        return ""
    return (
        f"built for game {' and '.join(builds)}, but your database is {actual}. "
        "It will still apply, and usually that is fine - but if the update "
        "changed anything this mod touches, the result will be wrong in game "
        "rather than reported here. Check what it does in the In plain English "
        "tab, and look for a release built for your version."
    )


def listed_names(game_root: Path | None) -> set[str] | None:
    """Every file name the game's executable keeps a hash for, lower case.

    None when there is nothing to read - a loose test folder, or a layout this
    does not know - which is different from "it lists nothing".

    Reading it means parsing a 45 MB executable, so a caller asking about several
    mods reads it once and passes the answer to ``still_checked`` rather than
    letting each mod read it again.
    """
    if game_root is None:
        return None
    exe = Path(game_root) / vetted.GAME_EXE
    if not exe.is_file():
        return None
    try:
        return {name.lower() for name in exe_checksums.read_entries(exe.read_bytes())}
    except Exception:
        return None


def still_checked(mod: Mod, game_root: Path | None, listed: set[str] | None = None) -> list[str]:
    """Which of this mod's declared files the game still keeps a hash for.

    The executable carries a list of file names with the hash it expects each to
    have. A replacement for a listed file is refused at startup, with an error
    box naming the package and nothing else to go on. A mod that knows it
    replaces such a file says so in ``requires_check_off``, and this is what
    turns that into an explanation before anything is written.

    Nothing is claimed when the executable cannot be read - a loose test folder,
    or a layout this does not know. Guessing would refuse mods that are fine.
    """
    names = list(mod.requires_check_off)
    # A TFC Installer mod rebuilds packages rather than naming them, so every
    # package it will rebuild counts - it does not have to list them itself.
    for patch in mod.patches:
        rebuilt = getattr(patch, "transform_targets", None)
        if callable(rebuilt) and callable(getattr(patch, "bind", None)):
            names += [t for t in rebuilt() if t.lower().endswith(".upk")]
    if not names:
        return []
    # Read here only when the caller has not already done it for us.
    if listed is None:
        listed = listed_names(game_root)
    if listed is None:
        return []
    seen, out = set(), []
    for name in names:
        short = Path(name).name
        if short.lower() in listed and short.lower() not in seen:
            seen.add(short.lower())
            out.append(short)
    return out


def check_off_error(mod: Mod, game_root: Path | None,
                    listed: set[str] | None = None) -> str:
    """Why this mod cannot be applied as the game stands, or ""."""
    blocked = still_checked(mod, game_root, listed)
    if not blocked:
        return ""
    files = ", ".join(blocked)
    one = len(blocked) == 1
    it = "it" if one else "them"
    return (
        f"this mod replaces {files}, which your game still checks. Applied as it "
        f"is, the game would refuse {'that file' if one else 'those files'} at "
        f"startup with an error naming {it}, before the intro.\n"
        f"    To fix it: Tools > Hash Patcher, tick {files}, then "
        f"'Switch off for the ticked files'. Saving again will then work.\n"
        "    That takes the file off the list of ones the game checks. It "
        "changes one byte per file and no program code, and the Hash Patcher "
        "puts it back whenever you want."
    )


def validate_mod(con: sqlite3.Connection, mod: Mod,
                 game_root: Path | None = None,
                 listed: set[str] | None = None) -> ModValidation:
    """Validate a single mod against an open, read-only connection."""
    result = ModValidation(mod_id=mod.id, warnings=list(mod.load_warnings))
    mismatch = game_version_warning(con, mod)
    if mismatch:
        result.warnings.append(mismatch)
    blocked = check_off_error(mod, game_root, listed)
    if blocked:
        result.errors.append(blocked)
    for patch in mod.patches:
        try:
            result.patch_summaries.append(patch.summary())
        except ValidationError as exc:  # a raw_sql_file whose file went missing
            result.errors.append(exc.reason)
            continue
        try:
            result.warnings.extend(patch.validate(con, mod.id))
            estimate = patch.estimate_rows(con)
            if estimate:
                result.estimated_rows += estimate
        except ValidationError as exc:
            result.errors.append(exc.reason)
        except sqlite3.Error as exc:
            result.errors.append(f"database error while validating: {exc}")
    return result


def validate(
    db_path: Path,
    mods: list[Mod],
    installed_ids: set[str] | None = None,
    game_root: Path | None = None,
) -> ValidationReport:
    """Validate the enabled mod list, in the order it will be applied.

    ``game_root`` is the folder holding BrgGame. Passing it matters when the
    database is not in the game's own layout - a loose copy, or a folder the
    player pointed at by hand - because it cannot be derived from db_path then,
    and without it a mod blocked by the game's file check reads as fine.
    """
    report = ValidationReport()
    db_path = Path(db_path)
    if not db_path.is_file():
        report.fatal = f"database file not found: {db_path}"
        return report

    ordered = list(mods)  # already in load order

    try:
        con = connect(db_path, read_only=True)
    except sqlite3.Error as exc:
        report.fatal = f"could not open the database: {exc}"
        report.conflicts = analyze(ordered, installed_ids)
        return report
    try:
        # With the connection in hand, conflicts are worked out per row rather
        # than per table, so two mods writing different parts of one table stay
        # quiet instead of warning about each other.
        report.conflicts = analyze(ordered, installed_ids, con)
        # Where the game is, worked out from the database's own place, so a mod
        # that needs the game's file check off can be told apart from one that
        # does not. None for a loose copy, and then nothing is claimed.
        game_root = game_root or asset_runner.game_root_for(db_path)
        if game_root is not None:
            # So TFC Installer mods know every package they will rebuild, for
            # the file-check test below. Cheap once the texture index exists.
            try:
                asset_runner.bind_tfc_patches_for_validation(ordered, game_root)
            except (OSError, ValueError):
                pass
        # One read of the executable for the whole run rather than one per mod.
        listed = listed_names(game_root)
        for mod in ordered:
            report.results.append(validate_mod(con, mod, game_root, listed))
    finally:
        con.close()
    return report
