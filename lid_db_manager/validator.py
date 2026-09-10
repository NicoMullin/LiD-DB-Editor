"""Pre-apply validation.

Everything here is read-only: the database is opened in ``mode=ro`` and raw SQL
is compiled with EXPLAIN rather than executed. Nothing is applied unless every
enabled mod passes, so a broken mod can never leave the DB half-patched.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from .conflict import ConflictReport, analyze
from .errors import ValidationError
from .mod import Mod
from .sqlutil import connect


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


def validate_mod(con: sqlite3.Connection, mod: Mod) -> ModValidation:
    """Validate a single mod against an open, read-only connection."""
    result = ModValidation(mod_id=mod.id, warnings=list(mod.load_warnings))
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


def validate(db_path: Path, mods: list[Mod], installed_ids: set[str] | None = None) -> ValidationReport:
    """Validate the enabled mod list, in the order it will be applied."""
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
        for mod in ordered:
            report.results.append(validate_mod(con, mod))
    finally:
        con.close()
    return report
