"""Small SQLite helpers shared by the patch types, validator and snapshotter."""

from __future__ import annotations

import hashlib
import re
import sqlite3
import time
from pathlib import Path

# Statements that write. Used to work out which tables a raw-SQL patch touches.
_WRITE_STMT_RE = re.compile(
    r"""\b(?:
            update\s+(?:or\s+\w+\s+)?          |
            insert\s+(?:or\s+\w+\s+)?into\s+   |
            replace\s+into\s+                  |
            delete\s+from\s+                   |
            drop\s+table\s+(?:if\s+exists\s+)? |
            alter\s+table\s+
        )
        ["'`\[]?(?P<table>[A-Za-z_][A-Za-z0-9_]*)["'`\]]?
    """,
    re.IGNORECASE | re.VERBOSE,
)

_COMMENT_RE = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)


def quote_ident(name: str) -> str:
    """Quote an identifier for interpolation into SQL."""
    return '"' + str(name).replace('"', '""') + '"'


def sha256_file(path: Path, chunk_size: int = 1 << 20) -> str:
    """Hex sha256 of a file, read in chunks so a large DB doesn't blow up RAM."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def connect(db_path: Path, *, read_only: bool = False) -> sqlite3.Connection:
    """Open the DB with manual transaction control.

    ``isolation_level=None`` turns off sqlite3's implicit transaction handling so
    the runner can issue its own BEGIN IMMEDIATE / COMMIT / ROLLBACK.
    """
    if read_only:
        escaped = (
            Path(db_path)
            .as_posix()
            .replace("%", "%25")
            .replace("?", "%3f")
            .replace("#", "%23")
        )
        con = sqlite3.connect(f"file:{escaped}?mode=ro", uri=True, isolation_level=None, timeout=5.0)
    else:
        con = sqlite3.connect(str(db_path), isolation_level=None, timeout=5.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def begin_immediate(con: sqlite3.Connection, timeout_seconds: float = 5.0) -> None:
    """Take the write lock, retrying with backoff while the DB is locked."""
    deadline = time.monotonic() + timeout_seconds
    delay = 0.05
    while True:
        try:
            con.execute("BEGIN IMMEDIATE")
            return
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                raise
            if time.monotonic() >= deadline:
                raise sqlite3.OperationalError(
                    "database is locked - close the game or any DB browser and try again"
                ) from exc
            time.sleep(delay)
            delay = min(delay * 2, 0.5)


def table_exists(con: sqlite3.Connection, table: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def column_names(con: sqlite3.Connection, table: str) -> list[str]:
    return [row["name"] for row in con.execute(f"PRAGMA table_info({quote_ident(table)})")]


def has_rowid(con: sqlite3.Connection, table: str) -> bool:
    """False for WITHOUT ROWID tables and views, which can't be snapshotted by rowid."""
    try:
        con.execute(f"SELECT rowid FROM {quote_ident(table)} LIMIT 1").fetchone()
    except sqlite3.OperationalError:
        return False
    return True


def count_where(con: sqlite3.Connection, table: str, where: str | None) -> int:
    sql = f"SELECT COUNT(*) FROM {quote_ident(table)}"
    if where:
        sql += f" WHERE {where}"
    return int(con.execute(sql).fetchone()[0])


def strip_comments(sql: str) -> str:
    return _COMMENT_RE.sub(" ", sql)


def split_statements(sql: str) -> list[str]:
    """Split a SQL script into complete statements.

    Uses ``sqlite3.complete_statement`` so semicolons inside string literals and
    inside BEGIN...END trigger bodies don't split a statement in half.
    """
    statements: list[str] = []
    buffer = ""
    for line in sql.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            stripped = buffer.strip()
            if strip_comments(stripped).strip().rstrip(";").strip():
                statements.append(stripped)
            buffer = ""
    tail = buffer.strip()
    if tail and strip_comments(tail).strip():
        statements.append(tail)
    return statements


# BEGIN / COMMIT / ROLLBACK as a whole statement. Deliberately does not match
# "ROLLBACK TO <savepoint>", which is savepoint control and nests fine.
_TRANSACTION_STMT_RE = re.compile(
    r"""^\s*(?:
            begin(?:\s+(?:deferred|immediate|exclusive))?(?:\s+transaction)?
          | commit(?:\s+transaction)?
          | end(?:\s+transaction)?
          | rollback(?:\s+transaction)?
        )\s*;?\s*$""",
    re.IGNORECASE | re.VERBOSE,
)


def _scan_tokens(statement: str):
    """Yield (index, char) for characters outside strings, comments and parens.

    Also yields the paren depth, so callers can find a keyword at the top level
    of a statement rather than inside a subquery.
    """
    i, depth, length = 0, 0, len(statement)
    while i < length:
        ch = statement[i]
        if ch == "'":  # string literal, '' escapes a quote
            i += 1
            while i < length:
                if statement[i] == "'":
                    if i + 1 < length and statement[i + 1] == "'":
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            continue
        if ch in '"`':  # quoted identifier
            closing = ch
            i += 1
            while i < length and statement[i] != closing:
                i += 1
            i += 1
            continue
        if ch == "[":  # bracketed identifier - not a paren
            while i < length and statement[i] != "]":
                i += 1
            i += 1
            continue
        if statement.startswith("--", i):
            i = statement.find("\n", i)
            if i == -1:
                return
            continue
        if statement.startswith("/*", i):
            end = statement.find("*/", i)
            if end == -1:
                return
            i = end + 2
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        yield i, ch, depth
        i += 1


def _keyword_at(statement: str, index: int, keyword: str) -> bool:
    end = index + len(keyword)
    if statement[index:end].upper() != keyword:
        return False
    before = statement[index - 1] if index else " "
    after = statement[end] if end < len(statement) else " "
    return not (before.isalnum() or before == "_") and not (after.isalnum() or after == "_")


def parse_row_scope(statement: str) -> tuple[str, str | None] | None:
    """Work out which rows a write statement targets.

    Returns ``(table, where_clause)`` for an UPDATE or DELETE whose target rows
    can be identified, with ``where_clause`` None when it hits every row. Returns
    None when the statement is something else, or is shaped in a way this cannot
    read - in which case callers must assume the whole table.

    This is what lets two raw-SQL mods that write the same table but different
    rows coexist without a bogus conflict warning.
    """
    text = strip_comments(statement).strip().rstrip(";")
    upper = text.upper().lstrip()
    if upper.startswith("UPDATE"):
        match = re.match(
            r"""\s*update\s+(?:or\s+\w+\s+)?["'`\[]?(?P<table>[A-Za-z_][A-Za-z0-9_]*)["'`\]]?\s""",
            text,
            re.IGNORECASE,
        )
    elif upper.startswith("DELETE"):
        match = re.match(
            r"""\s*delete\s+from\s+["'`\[]?(?P<table>[A-Za-z_][A-Za-z0-9_]*)["'`\]]?(?:\s|$)""",
            text,
            re.IGNORECASE,
        )
    else:
        return None
    if match is None:
        return None
    table = match.group("table")

    # The first WHERE at paren depth 0 - anything deeper belongs to a subquery.
    for index, char, depth in _scan_tokens(text):
        if depth == 0 and char in "wW" and _keyword_at(text, index, "WHERE"):
            where = text[index + 5 :].strip()
            return (table, where or None)
    return (table, None)


def rows_matching(con: sqlite3.Connection, table: str, where: str | None) -> set[int] | None:
    """rowids a WHERE clause selects, or None if it cannot be evaluated."""
    sql = f"SELECT rowid FROM {quote_ident(table)}"
    if where:
        sql += f" WHERE {where}"
    try:
        return {row[0] for row in con.execute(sql)}
    except sqlite3.Error:
        return None


def is_transaction_control(statement: str) -> bool:
    """True for a bare BEGIN / COMMIT / END / ROLLBACK statement.

    Mod SQL is usually written to be piped into the ``sqlite3`` shell, so it
    tends to wrap itself in BEGIN ... COMMIT. The runner already holds a
    transaction over the whole mod list, and running these would either error
    ("cannot start a transaction within a transaction") or - far worse - commit
    the runner's transaction early and break atomicity. They are dropped
    instead, with a warning.
    """
    return bool(_TRANSACTION_STMT_RE.match(strip_comments(statement).strip()))


def tables_written_by(sql: str) -> set[str]:
    """Best-effort set of tables a raw SQL script writes to.

    Regex-based, so it is deliberately over-inclusive: it is used to decide what
    to snapshot before a raw-SQL patch runs and what to warn about in conflict
    detection. Over-snapshotting is safe; missing a table is not.
    """
    return {match.group("table") for match in _WRITE_STMT_RE.finditer(strip_comments(sql))}


def compile_only(con: sqlite3.Connection, statement: str) -> None:
    """Parse and compile a statement without running it.

    EXPLAIN builds the bytecode program (so missing tables/columns and syntax
    errors surface) but never executes the statement itself.
    """
    con.execute("EXPLAIN " + statement.strip().rstrip(";"))
