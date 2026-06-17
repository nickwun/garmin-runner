from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from garmin_runner.config import AppSettings
from garmin_runner.fit import decode_fit_messages, extract_fit_bytes
from garmin_runner.garmin_client import download_original_activity, list_running_activities
from garmin_runner.normalize import normalize_activity
from garmin_runner.storage import ActivityStore


@dataclass(frozen=True)
class SyncResult:
    scanned: int
    downloaded: int
    skipped: int


def sync_running_activities(
    api: Any,
    settings: AppSettings,
    since: date,
    until: date | None = None,
) -> SyncResult:
    until = until or date.today()
    store = ActivityStore(settings.storage.database_path)
    store.initialize()

    settings.storage.summary_dir.mkdir(parents=True, exist_ok=True)
    settings.storage.fit_dir.mkdir(parents=True, exist_ok=True)

    activities = list_running_activities(api, since.isoformat(), until.isoformat())
    downloaded = 0
    skipped = 0

    for activity in activities:
        activity_id = str(activity.get("activityId", "")).strip()
        if not activity_id:
            skipped += 1
            continue

        if store.has_activity(activity_id):
            skipped += 1
            continue

        summary = api.get_activity(activity_id)
        summary_path = settings.storage.summary_dir / f"{activity_id}.json"
        fit_path = settings.storage.fit_dir / f"{activity_id}.fit"

        _write_json(summary_path, summary)
        fit_bytes = extract_fit_bytes(download_original_activity(api, activity_id))
        fit_path.write_bytes(fit_bytes)

        # Decode once with Garmin's official FIT SDK so corrupt files fail early.
        _messages, errors = decode_fit_messages(fit_path)
        if errors:
            raise ValueError(f"FIT 解析失败，activity_id={activity_id}")

        record = normalize_activity(
            summary,
            summary_path=_relative_path(summary_path),
            fit_path=_relative_path(fit_path),
        )
        if store.upsert_activity(record):
            downloaded += 1
        else:
            skipped += 1

    return SyncResult(scanned=len(activities), downloaded=downloaded, skipped=skipped)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _relative_path(path: Path) -> str:
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)
