"""The five patch types a mod can be built from.

    update_set     - set columns on rows matching a WHERE clause
    text_replace   - find/replace inside a text row (master_text and friends)
    raw_sql        - inline SQL escape hatch
    raw_sql_file   - SQL read from a file next to mod.json
    asset_file     - copy game files (whole .upk packages) into the game folder

Every patch knows how to validate itself, describe what it will change, say
what needs snapshotting before it runs, and apply itself to an open connection.
The first four write the database; ``asset_file`` writes files on disk and is a
deliberate no-op against the connection - the real copy happens in a separate
step (see asset_runner) once the database transaction has committed.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .asset_runner import forbidden_target_reason
from .errors import ApplyError, ModLoadError, ValidationError
from .sqlutil import (
    column_names,
    compile_only,
    count_where,
    has_rowid,
    if_not_exists,
    is_transaction_control,
    parse_row_scope,
    quote_ident,
    rows_matching,
    split_statements,
    table_exists,
    tables_created_by,
    tables_written_by,
)

# Past this many resolvable statements in one raw-SQL patch, stop working out
# individual rows and fall back to "the whole table" - the precision is not
# worth making the mod list sluggish.
ROW_SCOPE_STATEMENT_LIMIT = 2000

# Row-count ceiling for a whole-table snapshot taken on behalf of a raw-SQL
# patch. Past this we warn and fall back to the .db backup for revert.
FULL_TABLE_SNAPSHOT_LIMIT = 200_000

# How many rows a diff preview shows before it says "... and N more".
PREVIEW_ROW_LIMIT = 50


@dataclass
class SnapshotSpec:
    """What the snapshotter should capture before a patch runs.

    ``kind`` is one of:

        "rows"  - specific columns of the rows a WHERE clause matches
        "keys"  - specific columns of named rows, addressed by primary key.
                  Used when a diff against vanilla has told us exactly which
                  rows a mod touches, so raw SQL no longer costs a whole-table
                  copy.
        "table" - every column of every row, for anything we cannot pin down
        "absent"- the table does not exist yet. Nothing to copy; reverting
                  means dropping whatever the mod created.
    """

    kind: str
    table: str
    columns: list[str] = field(default_factory=list)
    where: str | None = None
    params: tuple[Any, ...] = ()
    key_columns: list[str] = field(default_factory=list)
    keys: list[tuple] = field(default_factory=list)


@dataclass
class DiffRow:
    """One row's before/after, for the preview panel."""

    key: str
    before: str
    after: str


@dataclass
class DiffPreview:
    patch_summary: str
    table: str
    total_rows: int
    rows: list[DiffRow] = field(default_factory=list)
    note: str = ""


@dataclass
class PatchResult:
    rows_changed: int
    warnings: list[str] = field(default_factory=list)


class Patch:
    """Base class. Subclasses implement the hooks below."""

    type = "abstract"

    def __init__(self, data: dict, mod_dir: Path, index: int):
        self.raw = data
        self.mod_dir = mod_dir
        self.index = index
        self.description: str = str(data.get("description", "") or "")
        # What a player's decision to switch this part off is remembered
        # against. An explicit "id" survives the author reordering or inserting
        # patches; the positional fallback does not, so anything meant to be
        # toggled - an imported rework's tables, say - is given one.
        self.key: str = str(data.get("id") or "").strip() or f"#{index + 1}"

    @property
    def part_label(self) -> str:
        """How this part is named in the list when it can be switched off."""
        return self.description.strip() or self.key

    # -- construction ----------------------------------------------------

    @staticmethod
    def from_dict(data: dict, mod_dir: Path, index: int) -> "Patch":
        if not isinstance(data, dict):
            raise ModLoadError(str(mod_dir.name), f"patch #{index + 1} is not a JSON object")
        patch_type = data.get("type")
        cls = _PATCH_TYPES.get(patch_type)
        if cls is None:
            known = ", ".join(sorted(_PATCH_TYPES))
            raise ModLoadError(
                str(mod_dir.name),
                f"patch #{index + 1} has unknown type {patch_type!r} (expected one of: {known})",
            )
        return cls(data, mod_dir, index)

    # -- hooks -----------------------------------------------------------

    def summary(self) -> str:
        raise NotImplementedError

    def tables(self) -> set[str]:
        """Tables this patch writes to."""
        raise NotImplementedError

    def targets(self) -> set[tuple[str, str]]:
        """(table, column) pairs written. "*" means "the whole table"."""
        raise NotImplementedError

    def validate(self, con: sqlite3.Connection, mod_id: str) -> list[str]:
        """Raise ValidationError on a hard failure; return a list of warnings."""
        raise NotImplementedError

    def estimate_rows(self, con: sqlite3.Connection) -> int | None:
        return None

    def snapshot_specs(self, con: sqlite3.Connection) -> list[SnapshotSpec]:
        raise NotImplementedError

    def touched_rows(self, con: sqlite3.Connection) -> dict[str, set[int] | None]:
        """rowids this patch writes, per table.

        A value of None means "could not tell - assume every row". Conflict
        detection uses this to avoid warning about two mods that write the same
        table in places that never overlap.
        """
        return {table: None for table in self.tables()}

    def asset_targets(self) -> set[str]:
        """Game files this patch writes, as forward-slash paths under the game root.

        Empty for every patch that only touches the database. Conflict detection
        and ``Mod.asset_targets()`` read this uniformly, so nothing needs an
        isinstance check to notice an asset patch.
        """
        return set()

    def preview(self, con: sqlite3.Connection) -> DiffPreview:
        raise NotImplementedError

    def apply(self, con: sqlite3.Connection, mod_id: str) -> PatchResult:
        raise NotImplementedError

    # -- shared helpers --------------------------------------------------

    def _require_table(self, con: sqlite3.Connection, mod_id: str, table: str) -> None:
        if not table_exists(con, table):
            raise ValidationError(mod_id, f"table {table!r} does not exist in this database")

    def _require_columns(
        self, con: sqlite3.Connection, mod_id: str, table: str, columns: list[str]
    ) -> None:
        existing = column_names(con, table)
        missing = [c for c in columns if c not in existing]
        if missing:
            raise ValidationError(
                mod_id,
                f"table {table!r} has no column(s) {', '.join(sorted(missing))} "
                f"(it has: {', '.join(existing)})",
            )


class UpdateSetPatch(Patch):
    """UPDATE <table> SET <col>=<value>, ... WHERE <where>."""

    type = "update_set"

    def __init__(self, data: dict, mod_dir: Path, index: int):
        super().__init__(data, mod_dir, index)
        self.table = data.get("table")
        self.set_values: dict[str, Any] = data.get("set") or {}
        self.where: str | None = data.get("where") or None
        self.expected_rows: int | None = data.get("expected_rows")

        label = f"patch #{index + 1} (update_set)"
        if not self.table or not isinstance(self.table, str):
            raise ModLoadError(mod_dir.name, f"{label} needs a 'table' string")
        if not isinstance(self.set_values, dict) or not self.set_values:
            raise ModLoadError(mod_dir.name, f"{label} needs a non-empty 'set' object")
        for column, value in self.set_values.items():
            if not isinstance(value, (int, float, str, bool, type(None))):
                raise ModLoadError(
                    mod_dir.name,
                    f"{label} sets {column!r} to an unsupported value type "
                    f"({type(value).__name__}); use a number, string, boolean or null",
                )
        if self.where is not None and not isinstance(self.where, str):
            raise ModLoadError(mod_dir.name, f"{label} has a non-string 'where'")
        if self.expected_rows is not None and not isinstance(self.expected_rows, int):
            raise ModLoadError(mod_dir.name, f"{label} has a non-integer 'expected_rows'")

    @property
    def columns(self) -> list[str]:
        return list(self.set_values)

    def summary(self) -> str:
        assignments = ", ".join(f"{k}={v!r}" for k, v in self.set_values.items())
        clause = f" WHERE {self.where}" if self.where else " (all rows)"
        return f"{self.table}: set {assignments}{clause}"

    def tables(self) -> set[str]:
        return {self.table}

    def targets(self) -> set[tuple[str, str]]:
        return {(self.table, column) for column in self.columns}

    def validate(self, con: sqlite3.Connection, mod_id: str) -> list[str]:
        warnings: list[str] = []
        self._require_table(con, mod_id, self.table)
        self._require_columns(con, mod_id, self.table, self.columns)
        if not has_rowid(con, self.table):
            raise ValidationError(
                mod_id, f"table {self.table!r} has no rowid, so its rows cannot be snapshotted"
            )
        try:
            matched = count_where(con, self.table, self.where)
        except sqlite3.Error as exc:
            raise ValidationError(mod_id, f"WHERE clause is not valid SQL: {exc}") from exc
        if matched == 0:
            warnings.append(f"{self.summary()} matches 0 rows - this patch will do nothing")
        elif self.expected_rows is not None and matched != self.expected_rows:
            warnings.append(
                f"{self.table}: expected {self.expected_rows} rows, "
                f"this database matches {matched} (game version drift is normal)"
            )
        return warnings

    def estimate_rows(self, con: sqlite3.Connection) -> int | None:
        try:
            return count_where(con, self.table, self.where)
        except sqlite3.Error:
            return None

    def snapshot_specs(self, con: sqlite3.Connection) -> list[SnapshotSpec]:
        return [SnapshotSpec("rows", self.table, self.columns, self.where)]

    def touched_rows(self, con: sqlite3.Connection) -> dict[str, set[int] | None]:
        return {self.table: rows_matching(con, self.table, self.where)}

    def preview(self, con: sqlite3.Connection) -> DiffPreview:
        cols = ", ".join(quote_ident(c) for c in self.columns)
        sql = f"SELECT rowid AS _rowid, {cols} FROM {quote_ident(self.table)}"
        if self.where:
            sql += f" WHERE {self.where}"
        sql += f" LIMIT {PREVIEW_ROW_LIMIT}"
        total = self.estimate_rows(con) or 0
        rows: list[DiffRow] = []
        after = ", ".join(f"{c}={self.set_values[c]!r}" for c in self.columns)
        for row in con.execute(sql):
            before = ", ".join(f"{c}={row[c]!r}" for c in self.columns)
            rows.append(DiffRow(key=f"rowid {row['_rowid']}", before=before, after=after))
        note = ""
        if total > len(rows):
            note = f"... and {total - len(rows)} more row(s)"
        return DiffPreview(self.summary(), self.table, total, rows, note)

    def apply(self, con: sqlite3.Connection, mod_id: str) -> PatchResult:
        assignments = ", ".join(f"{quote_ident(c)} = ?" for c in self.columns)
        sql = f"UPDATE {quote_ident(self.table)} SET {assignments}"
        if self.where:
            sql += f" WHERE {self.where}"
        params = [self.set_values[c] for c in self.columns]
        try:
            cursor = con.execute(sql, params)
        except sqlite3.Error as exc:
            raise ApplyError(mod_id, f"{self.summary()} failed: {exc}") from exc
        warnings: list[str] = []
        if self.expected_rows is not None and cursor.rowcount != self.expected_rows:
            warnings.append(
                f"{self.table}: changed {cursor.rowcount} rows, mod expected {self.expected_rows}"
            )
        return PatchResult(rows_changed=max(cursor.rowcount, 0), warnings=warnings)


class TextReplacePatch(Patch):
    """Find/replace inside a text column of the rows a match dict selects."""

    type = "text_replace"

    def __init__(self, data: dict, mod_dir: Path, index: int):
        super().__init__(data, mod_dir, index)
        self.table = data.get("table") or "master_text"
        self.match: dict[str, Any] = data.get("match") or {}
        self.column: str = data.get("column") or "txt"
        self.replacements: list[dict] = data.get("replace") or []
        self.require_find: bool = bool(data.get("require_find", False))

        label = f"patch #{index + 1} (text_replace)"
        if not isinstance(self.match, dict) or not self.match:
            raise ModLoadError(mod_dir.name, f"{label} needs a non-empty 'match' object")
        if not isinstance(self.replacements, list) or not self.replacements:
            raise ModLoadError(mod_dir.name, f"{label} needs a non-empty 'replace' array")
        for entry in self.replacements:
            if not isinstance(entry, dict) or "find" not in entry or "with" not in entry:
                raise ModLoadError(
                    mod_dir.name, f"{label} has a 'replace' entry without both 'find' and 'with'"
                )

    def _where(self) -> tuple[str, tuple[Any, ...]]:
        clause = " AND ".join(f"{quote_ident(k)} = ?" for k in self.match)
        return clause, tuple(self.match.values())

    def summary(self) -> str:
        selector = ", ".join(f"{k}={v!r}" for k, v in self.match.items())
        return f"{self.table}[{selector}].{self.column}: {len(self.replacements)} replacement(s)"

    def tables(self) -> set[str]:
        return {self.table}

    def targets(self) -> set[tuple[str, str]]:
        return {(self.table, self.column)}

    def text_keys(self) -> set[tuple]:
        """Identity of the rows this patch edits, for finer conflict detection."""
        return {(self.table, self.column, tuple(sorted(self.match.items())))}

    def validate(self, con: sqlite3.Connection, mod_id: str) -> list[str]:
        warnings: list[str] = []
        self._require_table(con, mod_id, self.table)
        self._require_columns(con, mod_id, self.table, list(self.match) + [self.column])
        if not has_rowid(con, self.table):
            raise ValidationError(
                mod_id, f"table {self.table!r} has no rowid, so its rows cannot be snapshotted"
            )
        clause, params = self._where()
        rows = con.execute(
            f"SELECT {quote_ident(self.column)} AS txt FROM {quote_ident(self.table)} WHERE {clause}",
            params,
        ).fetchall()
        if not rows:
            message = f"{self.summary()} matches no rows"
            if self.require_find:
                raise ValidationError(mod_id, message)
            warnings.append(message)
            return warnings
        joined = "\n".join(str(row["txt"] or "") for row in rows)
        for entry in self.replacements:
            if str(entry["find"]) not in joined:
                message = (
                    f"{self.table}: text {entry['find']!r} is not present in the matched row(s) - "
                    "the mod may target a different game version"
                )
                if self.require_find:
                    raise ValidationError(mod_id, message)
                warnings.append(message)
        return warnings

    def estimate_rows(self, con: sqlite3.Connection) -> int | None:
        clause, params = self._where()
        return int(
            con.execute(
                f"SELECT COUNT(*) FROM {quote_ident(self.table)} WHERE {clause}", params
            ).fetchone()[0]
        )

    def snapshot_specs(self, con: sqlite3.Connection) -> list[SnapshotSpec]:
        clause, params = self._where()
        return [SnapshotSpec("rows", self.table, [self.column], clause, params)]

    def touched_rows(self, con: sqlite3.Connection) -> dict[str, set[int] | None]:
        clause, params = self._where()
        try:
            rows = con.execute(
                f"SELECT rowid FROM {quote_ident(self.table)} WHERE {clause}", params
            )
            return {self.table: {row[0] for row in rows}}
        except sqlite3.Error:
            return {self.table: None}

    def _rewrite(self, text: str) -> str:
        for entry in self.replacements:
            text = text.replace(str(entry["find"]), str(entry["with"]))
        return text

    def preview(self, con: sqlite3.Connection) -> DiffPreview:
        clause, params = self._where()
        sql = (
            f"SELECT rowid AS _rowid, {quote_ident(self.column)} AS txt "
            f"FROM {quote_ident(self.table)} WHERE {clause} LIMIT {PREVIEW_ROW_LIMIT}"
        )
        rows = []
        for row in con.execute(sql, params):
            before = str(row["txt"] or "")
            rows.append(
                DiffRow(key=f"rowid {row['_rowid']}", before=before, after=self._rewrite(before))
            )
        total = self.estimate_rows(con) or 0
        note = f"... and {total - len(rows)} more row(s)" if total > len(rows) else ""
        return DiffPreview(self.summary(), self.table, total, rows, note)

    def apply(self, con: sqlite3.Connection, mod_id: str) -> PatchResult:
        clause, params = self._where()
        try:
            rows = con.execute(
                f"SELECT rowid AS _rowid, {quote_ident(self.column)} AS txt "
                f"FROM {quote_ident(self.table)} WHERE {clause}",
                params,
            ).fetchall()
        except sqlite3.Error as exc:
            raise ApplyError(mod_id, f"{self.summary()} failed to read rows: {exc}") from exc

        changed = 0
        for row in rows:
            before = str(row["txt"] or "")
            after = self._rewrite(before)
            if after == before:
                continue
            try:
                con.execute(
                    f"UPDATE {quote_ident(self.table)} SET {quote_ident(self.column)} = ? "
                    "WHERE rowid = ?",
                    (after, row["_rowid"]),
                )
            except sqlite3.Error as exc:
                raise ApplyError(mod_id, f"{self.summary()} failed: {exc}") from exc
            changed += 1

        warnings: list[str] = []
        if rows and changed == 0:
            warnings.append(
                f"{self.summary()}: matched {len(rows)} row(s) but nothing changed "
                "(already applied, or the 'find' text is absent)"
            )
        return PatchResult(rows_changed=changed, warnings=warnings)


class RawSqlPatch(Patch):
    """Inline SQL. Full escape hatch - anything SQLite accepts."""

    type = "raw_sql"

    def __init__(self, data: dict, mod_dir: Path, index: int):
        super().__init__(data, mod_dir, index)
        self.sql: str = data.get("sql") or ""
        if not isinstance(self.sql, str) or not self.sql.strip():
            raise ModLoadError(mod_dir.name, f"patch #{index + 1} (raw_sql) needs a non-empty 'sql'")

    def source_label(self) -> str:
        return "inline SQL"

    def sql_text(self) -> str:
        return self.sql

    def summary(self) -> str:
        if self.description:
            return f"{self.description} [{self.source_label()}]"
        first = " ".join(self.sql_text().split())
        return (first[:110] + "...") if len(first) > 110 else first

    def statements(self) -> list[str]:
        """The statements that will actually run - transaction control removed."""
        return [s for s in split_statements(self.sql_text()) if not is_transaction_control(s)]

    def transaction_statements(self) -> list[str]:
        """The BEGIN/COMMIT/ROLLBACK lines that were dropped, for the warning."""
        return [s for s in split_statements(self.sql_text()) if is_transaction_control(s)]

    def tables(self) -> set[str]:
        return tables_written_by(self.sql_text())

    def creates_tables(self) -> set[str]:
        """Tables this patch brings into being, rather than writing to."""
        return tables_created_by(self.sql_text())

    def targets(self) -> set[tuple[str, str]]:
        return {(table, "*") for table in self.tables()}

    def _check_on_a_schema_copy(
        self, con: sqlite3.Connection, mod_id: str, statements: list[str]
    ) -> None:
        """Run the script for real against an empty copy of the schema.

        Statements are normally compiled one at a time, which cannot work when a
        script builds on itself - an index over a table two lines above it has
        nothing to resolve against yet. So for a script that creates tables, the
        whole thing runs in memory over the database's schema with none of its
        data: cheap, thrown away, and a stricter check than compiling, because
        the statements really do execute.
        """
        scratch = sqlite3.connect(":memory:")
        try:
            for (definition,) in con.execute(
                "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL "
                "ORDER BY CASE type WHEN 'table' THEN 0 ELSE 1 END"
            ):
                try:
                    scratch.execute(definition)
                except sqlite3.Error:
                    continue  # a view or trigger over something we could not build
            for statement in statements:
                try:
                    scratch.execute(if_not_exists(statement))
                except sqlite3.Error as exc:
                    short = " ".join(statement.split())[:90]
                    raise ValidationError(
                        mod_id, f"SQL is not valid ({exc}) in: {short}"
                    ) from exc
        finally:
            scratch.close()

    def validate(self, con: sqlite3.Connection, mod_id: str) -> list[str]:
        statements = self.statements()
        if not statements:
            raise ValidationError(mod_id, f"{self.source_label()} contains no SQL statements")
        if any(not table_exists(con, t) for t in self.creates_tables()):
            self._check_on_a_schema_copy(con, mod_id, statements)
        else:
            for statement in statements:
                try:
                    compile_only(con, if_not_exists(statement))
                except sqlite3.Error as exc:
                    short = " ".join(statement.split())[:90]
                    raise ValidationError(mod_id, f"SQL is not valid ({exc}) in: {short}") from exc

        warnings: list[str] = []
        relaxed = sum(1 for s in statements if if_not_exists(s) != s)
        if relaxed:
            warnings.append(
                f"{self.source_label()}: {relaxed} CREATE statement(s) read as "
                "CREATE ... IF NOT EXISTS, so the mod can be applied more than once"
            )
        dropped = self.transaction_statements()
        if dropped:
            words = ", ".join(sorted({" ".join(d.split()).rstrip(";").upper() for d in dropped}))
            warnings.append(
                f"{self.source_label()}: ignoring {words} - the manager already runs every "
                "enabled mod inside one transaction, and honouring these would break that"
            )

        tables = self.tables()
        if not tables:
            warnings.append(
                f"{self.source_label()}: could not work out which tables it writes to; "
                "revert will fall back to the .db backup"
            )
        # Only the ones that are genuinely new. A dump that opens with a
        # CREATE TABLE IF NOT EXISTS for a table already in the database is
        # creating nothing, and must not be described as if it were.
        created = {t for t in self.creates_tables() if not table_exists(con, t)}
        if created:
            warnings.append(
                f"{self.source_label()}: creates {len(created)} table(s) the database does "
                f"not have ({', '.join(sorted(created))}) - reverting will drop them again"
            )
        for table in sorted(tables):
            if table in created:
                continue  # it does not exist yet because this patch makes it
            if not table_exists(con, table):
                raise ValidationError(mod_id, f"table {table!r} does not exist in this database")
            if not has_rowid(con, table):
                warnings.append(f"table {table!r} has no rowid; it cannot be snapshotted for revert")
                continue
            rows = count_where(con, table, None)
            if rows > FULL_TABLE_SNAPSHOT_LIMIT:
                warnings.append(
                    f"table {table!r} has {rows} rows - too large to snapshot, "
                    "revert will fall back to the .db backup"
                )
        return warnings

    def snapshot_specs(self, con: sqlite3.Connection) -> list[SnapshotSpec]:
        specs: list[SnapshotSpec] = []
        created = self.creates_tables()
        for table in sorted(self.tables()):
            if table in created and not table_exists(con, table):
                specs.append(SnapshotSpec("absent", table))
                continue
            if not table_exists(con, table) or not has_rowid(con, table):
                continue
            if count_where(con, table, None) > FULL_TABLE_SNAPSHOT_LIMIT:
                continue
            specs.append(SnapshotSpec("table", table))
        return specs

    def touched_rows(self, con: sqlite3.Connection) -> dict[str, set[int] | None]:
        found: dict[str, set[int] | None] = {table: set() for table in self.tables()}
        statements = self.statements()
        if len(statements) > ROW_SCOPE_STATEMENT_LIMIT:
            return {table: None for table in found}

        for statement in statements:
            if tables_created_by(statement):
                # A CREATE changes no existing row, so it tells us nothing about
                # overlap. Shared dumps routinely open with a CREATE TABLE IF NOT
                # EXISTS for a table that is already there; treating that as
                # "unknown rows" would throw away the row-level precision that
                # stops two mods being reported as clashing when they never meet.
                continue
            scope = parse_row_scope(statement)
            if scope is None:
                # Something we cannot reason about (an INSERT, a trigger, ...).
                # Only the tables it writes become unknown, not every table.
                for table in tables_written_by(statement):
                    found[table] = None
                continue
            table, where = scope
            if found.get(table, set()) is None:
                continue
            matched = rows_matching(con, table, where)
            if matched is None:
                found[table] = None
            else:
                found.setdefault(table, set())
                found[table] |= matched
        return found

    def preview(self, con: sqlite3.Connection) -> DiffPreview:
        tables = sorted(self.tables())
        rows = [
            DiffRow(
                key=table,
                before=(
                    f"{count_where(con, table, None)} row(s) now"
                    if table_exists(con, table)
                    else "missing table"
                ),
                after="rewritten by raw SQL",
            )
            for table in tables
        ]
        note = "Raw SQL - row-level preview is not available. Statements:\n" + "\n".join(
            self.statements()
        )
        return DiffPreview(self.summary(), ", ".join(tables) or "(unknown)", len(rows), rows, note)

    def apply(self, con: sqlite3.Connection, mod_id: str) -> PatchResult:
        changed = 0
        for statement in self.statements():
            # Every save re-runs the whole enabled list, so a bare CREATE would
            # fail the second time round on a table the mod itself made.
            statement = if_not_exists(statement)
            try:
                cursor = con.execute(statement)
            except sqlite3.Error as exc:
                short = " ".join(statement.split())[:90]
                raise ApplyError(
                    mod_id, f"{self.source_label()} failed ({exc}) in: {short}"
                ) from exc
            if cursor.rowcount and cursor.rowcount > 0:
                changed += cursor.rowcount
        return PatchResult(rows_changed=changed)


class RawSqlFilePatch(RawSqlPatch):
    """SQL read from a file inside the mod folder."""

    type = "raw_sql_file"

    def __init__(self, data: dict, mod_dir: Path, index: int):
        # Skip RawSqlPatch.__init__ - the SQL lives on disk, not in the JSON.
        Patch.__init__(self, data, mod_dir, index)
        raw_path = data.get("path")
        label = f"patch #{index + 1} (raw_sql_file)"
        if not raw_path or not isinstance(raw_path, str):
            raise ModLoadError(mod_dir.name, f"{label} needs a 'path' string")
        candidate = (mod_dir / raw_path).resolve()
        try:
            candidate.relative_to(mod_dir.resolve())
        except ValueError:
            raise ModLoadError(
                mod_dir.name, f"{label} points outside the mod folder: {raw_path}"
            ) from None
        self.path = candidate
        self.rel_path = raw_path
        self.sql = ""

    def source_label(self) -> str:
        return self.rel_path

    def sql_text(self) -> str:
        # Read at apply-time so editing the .sql file doesn't need a rescan.
        # A missing file reads as empty here; validate() and apply() are what
        # turn that into an error, so conflict analysis and the mod list keep
        # working on a half-broken mod folder.
        if not self.path.is_file():
            return ""
        return self.path.read_text(encoding="utf-8-sig")

    def validate(self, con: sqlite3.Connection, mod_id: str) -> list[str]:
        if not self.path.is_file():
            raise ValidationError(
                mod_id, f"SQL file {self.rel_path!r} is missing from the mod folder"
            )
        return super().validate(con, mod_id)

    def apply(self, con: sqlite3.Connection, mod_id: str) -> PatchResult:
        if not self.path.is_file():
            raise ApplyError(mod_id, f"SQL file {self.rel_path!r} is missing from the mod folder")
        return super().apply(con, mod_id)


def _clean_game_relative(raw: str, label: str, mod_ref: str) -> str:
    """Validate a game-root-relative path from mod.json without a game folder.

    The real resolve()/relative_to() containment check happens in asset_runner
    once the game root is known; this catches the obvious escapes at load time -
    an absolute path, a drive letter, a ``..`` segment - and normalises
    separators to forward slashes.
    """
    text = str(raw or "").strip().replace("\\", "/")
    if not text:
        raise ModLoadError(mod_ref, f"{label} needs a non-empty 'target'")
    if text.startswith("/") or (len(text) > 1 and text[1] == ":"):
        raise ModLoadError(
            mod_ref, f"{label} 'target' must be relative to the game folder: {raw!r}"
        )
    parts = [p for p in text.split("/") if p and p != "."]
    if any(p == ".." for p in parts):
        raise ModLoadError(mod_ref, f"{label} 'target' may not contain '..': {raw!r}")
    if not parts:
        raise ModLoadError(mod_ref, f"{label} needs a non-empty 'target'")
    return "/".join(parts)


class AssetFilePatch(Patch):
    """Copy whole game files - .upk packages - into the game folder.

    ``source`` is a file or a directory inside the mod folder (default
    ``assets``). ``target`` is where it goes, relative to the game root (the
    folder that holds ``BrgGame``). A directory source mirrors every file under
    it into ``target/<relative path>``.

    This patch never touches the database: ``apply`` is a no-op. The manager
    copies the files in a separate step after the DB transaction commits.
    """

    type = "asset_file"

    def __init__(self, data: dict, mod_dir: Path, index: int):
        super().__init__(data, mod_dir, index)
        label = f"patch #{index + 1} (asset_file)"
        self.source_rel = str(data.get("source") or "assets").strip().replace("\\", "/")
        self.target = _clean_game_relative(data.get("target"), label, mod_dir.name)

        candidate = (mod_dir / self.source_rel).resolve()
        try:
            candidate.relative_to(mod_dir.resolve())
        except ValueError:
            raise ModLoadError(
                mod_dir.name, f"{label} 'source' points outside the mod folder: {self.source_rel!r}"
            ) from None
        self.source_path = candidate

        # Refused at load, so the mod shows in the list as broken with the
        # reason beside it rather than looking installable until Save.
        reason = self.forbidden_reason()
        if reason:
            raise ModLoadError(mod_dir.name, f"{label} {reason}")

    def forbidden_reason(self) -> str:
        """The first file this patch would place that no mod may write, if any."""
        for target in [self.target, *(t for _, t in self.pairs())]:
            reason = forbidden_target_reason(target)
            if reason:
                return reason
        return ""

    # -- file list -----------------------------------------------------------

    def pairs(self) -> list[tuple[Path, str]]:
        """(file on disk, game-root-relative destination), read at call time.

        A missing source reads as an empty list here - validate() and the asset
        runner turn that into an error, so conflict analysis and the mod list
        keep working on a half-built mod folder.
        """
        if self.source_path.is_dir():
            out: list[tuple[Path, str]] = []
            for found in sorted(self.source_path.rglob("*")):
                if found.is_file():
                    rel = found.relative_to(self.source_path).as_posix()
                    out.append((found, f"{self.target}/{rel}"))
            return out
        if self.source_path.is_file():
            return [(self.source_path, self.target)]
        return []

    def asset_targets(self) -> set[str]:
        return {target for _, target in self.pairs()}

    # -- hooks -------------------------------------------------------------

    def summary(self) -> str:
        files = self.pairs()
        if self.description:
            return f"{self.description} [{len(files)} game file(s)]"
        if len(files) == 1:
            return f"game file {files[0][1]}"
        return f"{len(files)} game file(s) -> {self.target}"

    def tables(self) -> set[str]:
        return set()

    def targets(self) -> set[tuple[str, str]]:
        return set()

    def validate(self, con: sqlite3.Connection, mod_id: str) -> list[str]:
        if not self.source_path.exists():
            raise ValidationError(
                mod_id, f"asset source {self.source_rel!r} is missing from the mod folder"
            )
        files = self.pairs()
        if not files:
            raise ValidationError(
                mod_id, f"asset source {self.source_rel!r} contains no files to copy"
            )
        # Again here: a file dropped into the mod folder after it was loaded
        # would otherwise slip past the load-time check.
        reason = self.forbidden_reason()
        if reason:
            raise ValidationError(mod_id, f"refused - {reason}")
        warnings: list[str] = []
        non_upk = sorted({t for _, t in files if not t.lower().endswith(".upk")})
        if non_upk:
            shown = ", ".join(non_upk[:3]) + (" ..." if len(non_upk) > 3 else "")
            warnings.append(
                f"{len(non_upk)} target(s) are not .upk files ({shown}) - copied as-is anyway"
            )
        return warnings

    def snapshot_specs(self, con: sqlite3.Connection) -> list[SnapshotSpec]:
        return []

    def preview(self, con: sqlite3.Connection) -> DiffPreview:
        files = self.pairs()
        rows = [
            DiffRow(key=target, before="(game file)", after="replaced by this mod")
            for _, target in files[:PREVIEW_ROW_LIMIT]
        ]
        note = ""
        if len(files) > len(rows):
            note = f"... and {len(files) - len(rows)} more file(s)"
        return DiffPreview(self.summary(), self.target, len(files), rows, note)

    def apply(self, con: sqlite3.Connection, mod_id: str) -> PatchResult:
        # Files are copied by asset_runner after the DB transaction commits;
        # nothing happens against the connection.
        return PatchResult(rows_changed=0)


_PATCH_TYPES: dict[str, type[Patch]] = {
    UpdateSetPatch.type: UpdateSetPatch,
    TextReplacePatch.type: TextReplacePatch,
    RawSqlPatch.type: RawSqlPatch,
    RawSqlFilePatch.type: RawSqlFilePatch,
    AssetFilePatch.type: AssetFilePatch,
}

PATCH_TYPE_NAMES = tuple(sorted(_PATCH_TYPES))
