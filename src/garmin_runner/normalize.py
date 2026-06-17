from __future__ import annotations

from typing import Any

from garmin_runner.storage import ActivityRecord


def normalize_activity(
    activity: dict[str, Any],
    summary_path: str,
    fit_path: str,
) -> ActivityRecord:
    activity_id = str(activity["activityId"])
    summary = activity.get("summaryDTO") if isinstance(activity.get("summaryDTO"), dict) else activity
    activity_type = activity.get("activityType") or activity.get("activityTypeDTO")
    if isinstance(activity_type, dict):
        activity_type_value = activity_type.get("typeKey") or activity_type.get("typeId")
    else:
        activity_type_value = activity_type

    return ActivityRecord(
        activity_id=activity_id,
        activity_type=str(activity_type_value) if activity_type_value is not None else None,
        start_time_local=summary.get("startTimeLocal"),
        start_time_gmt=summary.get("startTimeGMT"),
        activity_name=activity.get("activityName"),
        distance_m=_number(summary.get("distance")),
        duration_s=_number(summary.get("duration")),
        average_hr=_number(summary.get("averageHR") or summary.get("avgHR")),
        max_hr=_number(summary.get("maxHR")),
        calories=_number(summary.get("calories")),
        summary_path=summary_path,
        fit_path=fit_path,
    )


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
