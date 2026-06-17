from __future__ import annotations

from typing import Any

from garmin_runner.storage import ActivityRecord


def normalize_activity(
    activity: dict[str, Any],
    summary_path: str,
    fit_path: str,
) -> ActivityRecord:
    activity_id = str(activity["activityId"])
    activity_type = activity.get("activityType")
    if isinstance(activity_type, dict):
        activity_type_value = activity_type.get("typeKey") or activity_type.get("typeId")
    else:
        activity_type_value = activity_type

    return ActivityRecord(
        activity_id=activity_id,
        activity_type=str(activity_type_value) if activity_type_value is not None else None,
        start_time_local=activity.get("startTimeLocal"),
        start_time_gmt=activity.get("startTimeGMT"),
        activity_name=activity.get("activityName"),
        distance_m=_number(activity.get("distance")),
        duration_s=_number(activity.get("duration")),
        average_hr=_number(activity.get("averageHR") or activity.get("avgHR")),
        max_hr=_number(activity.get("maxHR")),
        calories=_number(activity.get("calories")),
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
