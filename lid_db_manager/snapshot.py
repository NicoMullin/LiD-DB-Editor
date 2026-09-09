"""Pre-apply snapshots - how a mod gets reverted.

Before a mod's patches run, every row they are about to touch is read back and
written to ``snapshots/<mod-id>.json``. Reverting the mod replays that file.

Two entry kinds:

    "rows"  - specific columns of the rows a WHERE clause matched. Restoring
              writes those columns back by rowid.
    "table" - every column of every row, used for raw-SQL patches where we
              cannot reason about what changed. Restoring deletes rows the SQL
              inserted and rewrites the rest.
"""

from __future__ import annotations

import base64
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .errors import RevertError
from .patch import SnapshotSpec
from .sqlutil import column_names, quote_ident, table_exists

SNAPSHOT_FORMAT = 1


def _encode(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"__blob__": base64.b64encode(bytes(value)).decode("ascii")}
    return value


def _decode(value: Any) -> Any:
    if isinstance(value, dict) and "__blob__" in value:
        return base64.b64decode(value["__blob__"])
    return value


@dataclass
class SnapshotEntry:
    kind: str
    table: str
    columns: list[str]
    rows: list[list[Any]]  # each row is [rowid, *column values]

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "table": self.table,
            "columns": self.columns,
            "rows": [[_encode(v) for v in row] for row in self.rows],
        }

    @staticmethod
    def from_dict(data: dict) -> "SnapshotEntry":
        return SnapshotEntry(
            kind=data["kind"],
            table=data["table"],
            columns=list(data["columns"]),
            rows=[[_decode(v) for v in row] for row in data["rows"]],
        )


@dataclass
class Snapshot:
    mod_id: str
    captured_at: str
    db_path: str
    db_sha256_before: str = ""
    entries: list[SnapshotEntry] = field(default_factory=list)
    format: int = SNAPSHOT_FORMAT

    @property
    def row_count(self) -> int:
        return sum(len(entry.rows) for entry in self.entries)

    def tables(self) -> list[str]:
        return sorted({entry.table for entry in self.entries})

    def to_dict(self) -> dict:
        return {
            "format": self.format,
            "mod_id": self.mod_id,
            "captured_at": self.captured_at,
            "db_path": self.db_path,
            "db_sha256_before": self.db_sha256_before,
            "entries": [entry.to_dict() for entry in self.entries],
        }

    @staticmethod
    def from_dict(data: dict) -> "Snapshot":
        return Snapshot(
            mod_id=data["mod_id"],
            captured_at=data.get("captured_at", ""),
            db_path=data.get("db_path", ""),
            db_sha256_before=data.get("db_sha256_before", ""),
            entries=[SnapshotEntry.from_dict(e) for e in data.get("entries", [])],
            format=int(data.get("format", SNAPSHOT_FORMAT)),
        )


def capture(
    con: sqlite3.Connection,
    mod_id: str,
    specs: list[SnapshotSpec],
    *,
    db_path: str = "",
    db_sha256_before: str = "",
) -> Snapshot:
    """Read the pre-state described by ``specs`` off an open connection."""
    entries: list[SnapshotEntry] = []
    seen_full_tables: set[str] = set()

    for spec in specs:
        if not table_exists(con, spec.table):
            continue
        if spec.kind == "table":
            if spec.table in seen_full_tables:
                continue
            seen_full_tables.add(spec.table)
            columns = column_names(con, spec.table)
        else:
            columns = list(spec.columns)
        if not columns:
            continue

        selected = ", ".join(quote_ident(c) for c in columns)
        sql = f"SELECT rowid AS _rowid, {selected} FROM {quote_ident(spec.table)}"
        if spec.kind != "table" and spec.where:
            sql += f" WHERE {spec.where}"
        rows = [
            [row["_rowid"]] + [row[column] for column in columns]
            for row in con.execute(sql, spec.params if spec.kind != "table" else ())
        ]
        entries.append(SnapshotEntry(spec.kind, spec.table, columns, rows))

    return Snapshot(
        mod_id=mod_id,
        captured_at=datetime.now().isoformat(timespec="seconds"),
        db_path=db_path,
        db_sha256_before=db_sha256_before,
        entries=entries,
    )


def restore(con: sqlite3.Connection, snapshot: Snapshot) -> tuple[int, list[str]]:
    """Write a snapshot back. Runs inside the caller's transaction.

    Returns (rows restored, warnings).
    """
    restored = 0
    warnings: list[str] = []

    # Reverse order so a mod that touched the same table twice unwinds cleanly.
    for entry in reversed(snapshot.entries):
        if not table_exists(con, entry.table):
            warnings.append(f"table {entry.table!r} no longer exists - skipped")
            continue
        live_columns = set(column_names(con, entry.table))
        missing = [c for c in entry.columns if c not in live_columns]
        if missing:
            warnings.append(
                f"table {entry.table!r} no longer has column(s) {', '.join(missing)} - skipped"
            )
            continue

        if entry.kind == "table":
            keep = {row[0] for row in entry.rows}
            existing = {
                row[0] for row in con.execute(f"SELECT rowid FROM {quote_ident(entry.table)}")
            }
            extra = existing - keep
            if extra:
                con.executemany(
                    f"DELETE FROM {quote_ident(entry.table)} WHERE rowid = ?",
                    [(rowid,) for rowid in extra],
                )
            placeholders = ", ".join("?" for _ in range(len(entry.columns) + 1))
            column_list = ", ".join(["rowid"] + [quote_ident(c) for c in entry.columns])
            try:
                con.executemany(
                    f"INSERT OR REPLACE INTO {quote_ident(entry.table)} ({column_list}) "
                    f"VALUES ({placeholders})",
                    entry.rows,
                )
            except sqlite3.Error as exc:
                raise RevertError(f"could not restore table {entry.table!r}: {exc}") from exc
            restored += len(entry.rows)
            continue

        assignments = ", ".join(f"{quote_ident(c)} = ?" for c in entry.columns)
        sql = f"UPDATE {quote_ident(entry.table)} SET {assignments} WHERE rowid = ?"
        vanished = 0
        for row in entry.rows:
            rowid, values = row[0], row[1:]
            try:
                cursor = con.execute(sql, list(values) + [rowid])
            except sqlite3.Error as exc:
                raise RevertError(f"could not restore {entry.table}.rowid={rowid}: {exc}") from exc
            if cursor.rowcount:
                restored += cursor.rowcount
            else:
                vanished += 1
        if vanished:
            warnings.append(
                f"{entry.table}: {vanished} snapshotted row(s) no longer exist "
                "(the database was replaced since the snapshot was taken)"
            )

    return restored, warnings


# -- on-disk storage -----------------------------------------------------


def snapshot_path(snapshots_dir: Path, mod_id: str) -> Path:
    safe = "".join(ch if (ch.isalnum() or ch in "-_.") else "_" for ch in mod_id)
    return Path(snapshots_dir) / f"{safe}.json"


def save(snapshots_dir: Path, snapshot: Snapshot) -> Path:
    path = snapshot_path(snapshots_dir, snapshot.mod_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(snapshot.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8"
    )
    temporary.replace(path)
    return path


def load(snapshots_dir: Path, mod_id: str) -> Snapshot | None:
    path = snapshot_path(snapshots_dir, mod_id)
    if not path.is_file():
        return None
    try:
        return Snapshot.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError) as exc:
        raise RevertError(f"snapshot for {mod_id!r} is unreadable: {exc}") from exc


def discard(snapshots_dir: Path, mod_id: str) -> None:
    path = snapshot_path(snapshots_dir, mod_id)
    if path.is_file():
        path.unlink()


def known_mod_ids(snapshots_dir: Path) -> list[str]:
    """Mod ids that have a snapshot on disk - including ones whose folder is gone."""
    directory = Path(snapshots_dir)
    if not directory.is_dir():
        return []
    ids = []
    for path in sorted(directory.glob("*.json")):
        try:
            ids.append(json.loads(path.read_text(encoding="utf-8"))["mod_id"])
        except (OSError, ValueError, KeyError):
            continue
    return ids
