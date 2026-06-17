from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ActivityRecord:
    activity_id: str
    activity_type: str | None
    start_time_local: str | None
    activity_name: str | None
    distance_m: float | None
    duration_s: float | None
    summary_path: str
    fit_path: str
    start_time_gmt: str | None = None
    average_hr: float | None = None
    max_hr: float | None = None
    calories: float | None = None


class ActivityStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS activities (
                    activity_id TEXT PRIMARY KEY,
                    activity_type TEXT,
                    start_time_local TEXT,
                    start_time_gmt TEXT,
                    activity_name TEXT,
                    distance_m REAL,
                    duration_s REAL,
                    average_hr REAL,
                    max_hr REAL,
                    calories REAL,
                    summary_path TEXT NOT NULL,
                    fit_path TEXT NOT NULL,
                    synced_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_activities_start_time_local
                ON activities(start_time_local)
                """
            )

    def upsert_activity(self, record: ActivityRecord) -> bool:
        synced_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO activities (
                    activity_id,
                    activity_type,
                    start_time_local,
                    start_time_gmt,
                    activity_name,
                    distance_m,
                    duration_s,
                    average_hr,
                    max_hr,
                    calories,
                    summary_path,
                    fit_path,
                    synced_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.activity_id,
                    record.activity_type,
                    record.start_time_local,
                    record.start_time_gmt,
                    record.activity_name,
                    record.distance_m,
                    record.duration_s,
                    record.average_hr,
                    record.max_hr,
                    record.calories,
                    record.summary_path,
                    record.fit_path,
                    synced_at,
                ),
            )
        return cursor.rowcount == 1

    def refresh_activity(self, record: ActivityRecord) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE activities
                SET activity_type = ?,
                    start_time_local = ?,
                    start_time_gmt = ?,
                    activity_name = ?,
                    distance_m = ?,
                    duration_s = ?,
                    average_hr = ?,
                    max_hr = ?,
                    calories = ?,
                    summary_path = ?,
                    fit_path = ?
                WHERE activity_id = ?
                """,
                (
                    record.activity_type,
                    record.start_time_local,
                    record.start_time_gmt,
                    record.activity_name,
                    record.distance_m,
                    record.duration_s,
                    record.average_hr,
                    record.max_hr,
                    record.calories,
                    record.summary_path,
                    record.fit_path,
                    record.activity_id,
                ),
            )

    def has_activity(self, activity_id: str) -> bool:
        return self.get_activity(activity_id) is not None

    def get_activity(self, activity_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM activities WHERE activity_id = ?",
                (activity_id,),
            ).fetchone()
        return dict(row) if row else None

    def list_activities(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM activities ORDER BY start_time_local, activity_id"
            ).fetchall()
        return [dict(row) for row in rows]

    def list_recent_activities(
        self,
        limit: int = 20,
        since: str | None = None,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM activities"
        params: list[Any] = []
        if since is not None:
            query += " WHERE start_time_local >= ?"
            params.append(since)
        query += " ORDER BY start_time_local DESC, activity_id DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
