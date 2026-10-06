from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


def private_json(path: Path, value: Any) -> None:
    private_text(path, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def private_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(value)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class HealthStore:
    """Append-only successful snapshots and independent latest request status."""

    def __init__(self, database: Path, data_dir: Path) -> None:
        self.database = database
        self.data_dir = data_dir

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> Path | None:
        self.database.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        backup = None
        with closing(self.connect()) as conn:
            exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name='health_snapshots'").fetchone()
            if not exists:
                # SQLite backup also includes committed WAL pages; never copy a live DB file.
                backup = self.data_dir / "backups" / f"before-health-{uuid4().hex}.sqlite"
                backup.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with closing(sqlite3.connect(backup)) as destination:
                    conn.backup(destination)
                backup.chmod(0o600)
            with conn:
                conn.executescript("""
                    CREATE TABLE IF NOT EXISTS health_snapshots (
                        day TEXT NOT NULL, kind TEXT NOT NULL, digest TEXT NOT NULL,
                        fetched_at TEXT NOT NULL, metrics_json TEXT NOT NULL,
                        raw_path TEXT NOT NULL, schema_version INTEGER NOT NULL,
                        PRIMARY KEY (day, kind, digest)
                    );
                    CREATE TABLE IF NOT EXISTS health_sync_status (
                        day TEXT NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL,
                        detail TEXT NOT NULL, checked_at TEXT NOT NULL,
                        PRIMARY KEY (day, kind)
                    );
                    CREATE TABLE IF NOT EXISTS recovery_feedback (
                        id INTEGER PRIMARY KEY, day TEXT NOT NULL, recorded_at TEXT NOT NULL,
                        values_json TEXT NOT NULL
                    );
                """)
        self.database.chmod(0o600)
        return backup

    def save(self, day: str, kind: str, status: str, detail: str,
             fetched_at: str, raw: Any = None, metrics: dict | None = None) -> None:
        fetched_at = datetime.fromisoformat(fetched_at).astimezone(timezone.utc).isoformat()
        with closing(self.connect()) as conn, conn:
            if raw is not None:
                digest = hashlib.sha256(json.dumps(raw, sort_keys=True, allow_nan=False).encode()).hexdigest()
                raw_path = self.data_dir / "raw" / "health" / day / f"{kind}_{digest}.json"
                if not raw_path.exists():
                    private_json(raw_path, raw)
            if status == "available":
                conn.execute("""
                    INSERT OR IGNORE INTO health_snapshots VALUES (?, ?, ?, ?, ?, ?, 1)
                """, (day, kind, digest, fetched_at, json.dumps(metrics, ensure_ascii=False), str(raw_path)))
            conn.execute("""
                INSERT INTO health_sync_status VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(day, kind) DO UPDATE SET status=excluded.status,
                  detail=excluded.detail, checked_at=excluded.checked_at
            """, (day, kind, status, detail, fetched_at))

    def snapshots(self, since: str, until: str) -> list[dict]:
        if not self.database.exists():
            return []
        with closing(self.connect()) as conn:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='health_snapshots'").fetchone():
                return []
            rows = conn.execute("""
                SELECT * FROM health_snapshots WHERE day BETWEEN ? AND ? ORDER BY fetched_at
            """, (since, until)).fetchall()
        return [{**dict(row), "metrics": json.loads(row["metrics_json"])} for row in rows]

    def statuses(self, since: str, until: str) -> list[dict]:
        if not self.database.exists():
            return []
        with closing(self.connect()) as conn:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='health_sync_status'").fetchone():
                return []
            return [dict(row) for row in conn.execute(
                "SELECT * FROM health_sync_status WHERE day BETWEEN ? AND ? ORDER BY day, kind", (since, until))]

    def add_feedback(self, day: str, values: dict, recorded_at: str | None = None) -> None:
        stamp = recorded_at or datetime.now(timezone.utc).isoformat()
        stamp = datetime.fromisoformat(stamp).astimezone(timezone.utc).isoformat()
        with closing(self.connect()) as conn, conn:
            conn.execute("INSERT INTO recovery_feedback(day, recorded_at, values_json) VALUES (?, ?, ?)",
                         (day, stamp, json.dumps(values, ensure_ascii=False)))

    def feedback(self, day: str, cutoff: datetime) -> dict | None:
        if not self.database.exists():
            return None
        with closing(self.connect()) as conn:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='recovery_feedback'").fetchone():
                return None
            rows = conn.execute("SELECT * FROM recovery_feedback WHERE day=? ORDER BY recorded_at DESC, id DESC",
                                (day,)).fetchall()
        for row in rows:
            if datetime.fromisoformat(row["recorded_at"]) <= cutoff:
                return {**json.loads(row["values_json"]), "recorded_at": row["recorded_at"]}
        return None
