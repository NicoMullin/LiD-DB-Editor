"""Small SQLite helpers shared by the patch types, validator and snapshotter."""

from __future__ import annotations

import hashlib
import re
import sqlite3
import time
from pathlib import Path

# A table reference, optionally schema-qualified ("main"."master_part",
# main.master_part, ...) - tools like DB Browser for SQLite's own "Export to
# SQL" write the "main". prefix on some statements and not others, so it has
# to be recognised or the schema name gets mistaken for the table name. Only
# the real table name is captured.
_TABLE_REF = r'''(?:["'`\[]?[A-Za-z_][A-Za-z0-9_]*["'`\]]?\s*\.\s*)?
                  ["'`\[]?(?P<table>[A-Za-z_][A-Za-z0-9_]*)["'`\]]?'''

# Statements that write. Used to work out which tables a raw-SQL patch touches.
_WRITE_STMT_RE = re.compile(
    rf"""\b(?:
            update\s+(?:or\s+\w+\s+)?          |
            insert\s+(?:or\s+\w+\s+)?into\s+   |
            replace\s+into\s+                  |
            delete\s+from\s+                   |
            drop\s+table\s+(?:if\s+exists\s+)? |
            alter\s+table\s+                   |
            create\s+(?:temp(?:orary)?\s+)?table\s+(?:if\s+not\s+exists\s+)?
        )
        {_TABLE_REF}
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Tables a script brings into being. These are the one kind of table that may
# legitimately not exist yet when a mod is validated.
_CREATE_TABLE_RE = re.compile(
    rf"""\bcreate\s+(?:temp(?:orary)?\s+)?table\s+(?:if\s+not\s+exists\s+)?
        {_TABLE_REF}
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Everything in a SQL script that is not bare code: string literals, quoted
# identifiers and comments. Matching them in one pass, left to right, is what
# keeps them from being read inside one another - a "--" in a line of dialogue
# opens no comment, and a quote inside a comment opens no literal.
_NOISE_RE = re.compile(
    r"""'(?:[^']|'')*'     # string literal
      | "(?:[^"]|"")*"     # quoted identifier
      | `[^`]*`            # quoted identifier, MySQL style
      | \[[^\]]*\]         # bracketed identifier
      | --[^\n]*           # line comment
      | /\*.*?\*/          # block comment
    """,
    re.DOTALL | re.VERBOSE,
)


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


def connect(db_path: Path, *, read_only: bool = False,
            foreign_keys: bool = True) -> sqlite3.Connection:
    """Open the DB with manual transaction control.

    ``isolation_level=None`` turns off sqlite3's implicit transaction handling so
    the runner can issue its own BEGIN IMMEDIATE / COMMIT / ROLLBACK.

    ``foreign_keys=False`` is for putting saved rows back. masters.db declares
    foreign keys that its own shipped data breaks - on game 5.0.3 there are 256
    such rows, 255 of them in ``master_asset`` with an empty ``type``, plus two
    tables whose foreign key definitions do not even resolve. SQLite only
    notices a broken row when something touches it, so the game never trips over
    them and neither does an ordinary mod. Restoring a whole-table snapshot does
    touch them - it writes every row back - and the constraint then fails on the
    game's own data rather than on anything a mod did. Since a restore only ever
    puts back what was already there, the check has nothing to protect and is
    switched off for it. It stays on everywhere else.
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
    # Must be set outside a transaction; it is a no-op inside one.
    con.execute(f"PRAGMA foreign_keys = {'ON' if foreign_keys else 'OFF'}")
    return con


def begin_immediate(con: sqlite3.Connection, timeout_seconds: float = 5.0) -> None:
    """Take the write lock, retrying with backoff while the DB is locked.

    Foreign keys are checked at COMMIT rather than after each statement. They
    are still enforced - a transaction that ends with a broken reference will
    not commit - but a mod is allowed to pass through states that are only
    momentarily inconsistent on the way there.

    That matters because ``INSERT OR REPLACE`` is a delete followed by an
    insert, and several of this game's tables point at each other with
    ON DELETE RESTRICT. Re-applying a mod over rows it had already written
    would otherwise fail on the delete half of a row being put back exactly as
    it was - a change of nothing at all, refused.
    """
    deadline = time.monotonic() + timeout_seconds
    delay = 0.05
    while True:
        try:
            con.execute("BEGIN IMMEDIATE")
            con.execute("PRAGMA defer_foreign_keys = ON")  # resets at commit
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


# The two shapes dbdiff writes an added row as. Anything else is not examined:
# the answer is only ever used to explain something, so "cannot tell" is fine.
_INSERT_VALUES = re.compile(
    r"^INSERT(?:\s+OR\s+\w+)?\s+INTO\s+(?P<table>\"[^\"]+\"|[A-Za-z_]\w*)\s*"
    r"\((?P<cols>.+?)\)\s*VALUES\s*\((?P<vals>.+)\)\s*;?$",
    re.IGNORECASE | re.DOTALL,
)
_INSERT_GUARDED = re.compile(
    r"^INSERT\s+INTO\s+.+?\bWHERE\s+NOT\s+EXISTS\s*\((?P<probe>SELECT\s+1\s+FROM\s+.+)\)\s*;?$",
    re.IGNORECASE | re.DOTALL,
)


def row_already_there(con: sqlite3.Connection, statement: str) -> bool | None:
    """Is the row this INSERT would add already in the table?

    None means "could not tell" - the statement is not one of the shapes we
    write, or the table is not there. Used only to explain what happened, so a
    wrong guess must never be possible: anything unrecognised returns None.
    """
    # Comments come through attached to the statement after them, so the SQL
    # rarely starts at the first character. Only the comments go: the text the
    # statement inserts is left exactly as written, newlines and all, because
    # the probe below compares it against what is in the table.
    text = strip_comments(statement).strip()
    guarded = _INSERT_GUARDED.match(text)
    if guarded:
        # The statement already carries the test; just run it.
        probe = f"SELECT EXISTS({guarded.group('probe')})"
    else:
        plain = _INSERT_VALUES.match(text)
        if not plain:
            return None
        # Row-value IS compares the whole tuple at once, so the column and value
        # lists never have to be split - which would mean parsing SQL literals.
        probe = (
            f"SELECT EXISTS(SELECT 1 FROM {plain.group('table')} "
            f"WHERE ({plain.group('cols')}) IS ({plain.group('vals')}))"
        )
    try:
        return bool(con.execute(probe).fetchone()[0])
    except sqlite3.Error:
        return None


def inserts_already_present(
    con: sqlite3.Connection, statements: list[str], sample: int = 40
) -> tuple[int, int]:
    """(how many added rows were checked, how many were already there).

    Stops after ``sample`` so validating a large mod stays quick, and spreads
    that sample over the whole script rather than taking it from the front. A
    big mod is usually a dump of one table after another, and the tables it
    opens with are often ones it did not change at all - a sample taken only
    from there would report a rebalance of the later tables as already
    installed. A short script is walked in order, as before.
    """
    checked = present = 0
    stride = max(1, len(statements) // (sample * 2))
    for index in range(0, len(statements), stride):
        if checked >= sample:
            break
        answer = row_already_there(con, statements[index])
        if answer is None:
            continue
        checked += 1
        present += int(answer)
    return checked, present


def database_game_version(con: sqlite3.Connection) -> str:
    """The game build this database came from, or "" if it does not say.

    ``master_const_str.TITLE_VERSION`` reads like "5.0.3.0.0 - 1.87". A mod
    built by diffing one database only strictly describes that build, so this
    is what a mod's own recorded version is compared against.
    """
    try:
        row = con.execute(
            "SELECT value FROM master_const_str WHERE id = 'TITLE_VERSION'"
        ).fetchone()
    except sqlite3.Error:
        return ""
    if not row:
        return ""
    return str(row[0] or "").strip()


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


def _blanked(text: str) -> str:
    """The same text as spaces, with its line breaks kept."""
    return "".join("\n" if char == "\n" else " " for char in text)


def _blank_noise(sql: str, *, literals: bool) -> str:
    """Blank out comments - and optionally string literals - in place.

    The result is the same length as the input and keeps its line breaks, so
    offsets into it still line up with the original text.
    """

    def replace(match: re.Match[str]) -> str:
        text = match.group()
        if text.startswith(("--", "/*")):
            return _blanked(text)
        if literals and text.startswith("'"):
            return "'" + _blanked(text[1:-1]) + "'"
        return text  # a quoted identifier - that is where table names live

    return _NOISE_RE.sub(replace, sql)


def strip_comments(sql: str) -> str:
    """Remove comments, and only comments.

    A "--" inside a string literal is text, not a comment. A mod that rewrites
    the game's text tables carries thousands of lines of dialogue, so a blunt
    regex would eat to the end of the line and could swallow real SQL with it.
    """
    return _blank_noise(sql, literals=False)


def mask_literals(sql: str) -> str:
    """Remove comments and the contents of string literals.

    What a mod's data says is not SQL. A line of in-game mail reading "this
    update adds new routes" must not be read as a write to a table "adds".
    """
    return _blank_noise(sql, literals=True)


def _each_statement(chunk: str) -> list[str]:
    """Split one complete buffer where it holds more than one statement.

    A script almost always puts one statement per line, and then there is
    nothing to do. But nothing stops a mod writing several on one line, and
    handing them over as one string fails - SQLite's driver takes a single
    statement at a time, so the mod was turned away with "you can only execute
    one statement at a time", which says nothing about what to change.

    The quick count first is what keeps this off the hot path: only a buffer
    that could hold a second statement is scanned character by character.
    """
    if chunk.count(";") < 2:
        return [chunk.strip()]
    pieces: list[str] = []
    start = 0
    for index, char, _depth in _scan_tokens(chunk):
        if char != ";":
            continue
        piece = chunk[start : index + 1]
        if not sqlite3.complete_statement(piece):
            continue  # a semicolon inside a BEGIN...END trigger body
        pieces.append(piece.strip())
        start = index + 1
    rest = chunk[start:].strip()
    if rest:
        pieces.append(rest)
    return pieces


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
            for piece in _each_statement(buffer):
                if strip_comments(piece).strip().rstrip(";").strip():
                    statements.append(piece)
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
            rf"""\s*update\s+(?:or\s+\w+\s+)?{_TABLE_REF}\s""",
            text,
            re.IGNORECASE | re.VERBOSE,
        )
    elif upper.startswith("DELETE"):
        match = re.match(
            rf"""\s*delete\s+from\s+{_TABLE_REF}(?:\s|$)""",
            text,
            re.IGNORECASE | re.VERBOSE,
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
    text = mask_literals(sql)
    return {
        match.group("table")
        for match in _WRITE_STMT_RE.finditer(text)
        if not _is_foreign_key_rule(text, match.start())
    }


# The UPDATE in "REFERENCES other(id) ON UPDATE CASCADE" is a foreign-key rule
# inside a CREATE TABLE, not a statement: the word after it is an action -
# CASCADE, RESTRICT, SET NULL - rather than a table.
_ON_BEFORE_RE = re.compile(r"\bon\s+$", re.IGNORECASE)


def _is_foreign_key_rule(text: str, start: int) -> bool:
    return bool(_ON_BEFORE_RE.search(text, max(0, start - 40), start))


_CREATE_HEAD_RE = re.compile(
    r"^\s*create\s+(?:temp(?:orary)?\s+)?(?:table|(?:unique\s+)?index)\s+", re.IGNORECASE
)
_IF_NOT_EXISTS_RE = re.compile(r"\bif\s+not\s+exists\b", re.IGNORECASE)


def if_not_exists(statement: str) -> str:
    """Make a CREATE safe to run a second time.

    Mods get applied more than once - every save re-runs the whole enabled list
    against a database that already has last time's changes. A bare CREATE TABLE
    fails on the second pass, which is a needless way for a working mod to break,
    so it is given an IF NOT EXISTS. Statements that already have one, and
    anything that is not a CREATE, come back untouched.
    """
    if _IF_NOT_EXISTS_RE.search(statement):
        return statement
    match = _CREATE_HEAD_RE.match(statement)
    if not match:
        return statement
    return statement[: match.end()] + "IF NOT EXISTS " + statement[match.end() :]


def tables_created_by(sql: str) -> set[str]:
    """Tables a raw SQL script creates for itself.

    Everything else a mod writes to has to exist already - that check is what
    catches a typo'd table name. A CREATE is the exception, so validation has to
    know which names to let through.
    """
    return {match.group("table") for match in _CREATE_TABLE_RE.finditer(mask_literals(sql))}


def compile_only(con: sqlite3.Connection, statement: str) -> None:
    """Parse and compile a statement without running it.

    EXPLAIN builds the bytecode program (so missing tables/columns and syntax
    errors surface) but never executes the statement itself.
    """
    con.execute("EXPLAIN " + statement.strip().rstrip(";"))
