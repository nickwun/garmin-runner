from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any


@dataclass(frozen=True)
class HeartRateZones:
    maf_low: int
    maf_high: int
    steady_high: int
    threshold_high: int


@dataclass(frozen=True)
class TrainingConfig:
    heart_rate_zones: HeartRateZones
    long_run_min_distance_km: float = 18.0
    long_run_min_duration_s: float = 5400.0


@dataclass(frozen=True)
class TimeSeriesPoint:
    timestamp: Any
    elapsed_s: float
    distance_m: float | None
    heart_rate_bpm: float | None
    speed_mps: float | None
    cadence_spm: float | None
    altitude_m: float | None


@dataclass(frozen=True)
class BasicMetrics:
    activity_id: str
    activity_name: str | None
    activity_date: date
    distance_km: float | None
    duration_s: float | None
    average_pace_s_per_km: float | None
    average_hr: float | None
    max_hr: float | None
    elevation_gain_m: float | None
    average_cadence_spm: float | None


@dataclass(frozen=True)
class HeartRateZoneSummary:
    seconds_by_zone: dict[str, float]


@dataclass(frozen=True)
class PaceStability:
    cv_pct: float | None
    label: str


@dataclass(frozen=True)
class HeartRateDrift:
    first_half_pace_hr_ratio: float | None
    second_half_pace_hr_ratio: float | None
    drift_pct: float | None
    label: str


@dataclass(frozen=True)
class SingleActivityAnalysis:
    basic: BasicMetrics
    hr_zones: HeartRateZoneSummary
    pace_stability: PaceStability
    heart_rate_drift: HeartRateDrift
    training_type: str
    execution_score: int
    coach_instruction: str


def training_config_from_settings(settings: Any) -> TrainingConfig:
    training = settings.training
    missing = [
        name
        for name in ("maf_low", "maf_high", "steady_high", "threshold_high")
        if getattr(training, name) is None
    ]
    if missing:
        raise ValueError(
            "缺少个人训练区间配置："
            + ", ".join(missing)
            + "。请在 config/athlete.yaml 的 training.heart_rate_zones 中填写。"
        )
    return TrainingConfig(
        heart_rate_zones=HeartRateZones(
            maf_low=training.maf_low,
            maf_high=training.maf_high,
            steady_high=training.steady_high,
            threshold_high=training.threshold_high,
        ),
        long_run_min_distance_km=training.long_run_min_distance_km,
        long_run_min_duration_s=training.long_run_min_duration_min * 60,
    )


def analyze_activity(
    summary: dict[str, Any],
    points: list[TimeSeriesPoint],
    config: TrainingConfig,
) -> SingleActivityAnalysis:
    basic = _basic_metrics(summary, points)
    hr_zones = _hr_zone_summary(points, config.heart_rate_zones)
    pace_stability = _pace_stability(points)
    drift = _heart_rate_drift(points)
    training_type = _classify_training(summary, basic, hr_zones, pace_stability, config)
    execution_score = _score_execution(training_type, hr_zones, pace_stability, drift)
    instruction = _coach_instruction(training_type, execution_score, pace_stability, drift)
    return SingleActivityAnalysis(
        basic=basic,
        hr_zones=hr_zones,
        pace_stability=pace_stability,
        heart_rate_drift=drift,
        training_type=training_type,
        execution_score=execution_score,
        coach_instruction=instruction,
    )


def _basic_metrics(summary: dict[str, Any], points: list[TimeSeriesPoint]) -> BasicMetrics:
    values = _summary_values(summary)
    distance_m = _number(values.get("distance")) or _last_number([p.distance_m for p in points])
    duration_s = _number(values.get("duration")) or _duration_from_points(points)
    distance_km = distance_m / 1000 if distance_m else None
    average_pace = duration_s / distance_km if duration_s and distance_km else None
    activity_date = _activity_date(values.get("startTimeLocal"))
    return BasicMetrics(
        activity_id=str(summary["activityId"]),
        activity_name=summary.get("activityName"),
        activity_date=activity_date,
        distance_km=distance_km,
        duration_s=duration_s,
        average_pace_s_per_km=average_pace,
        average_hr=_number(values.get("averageHR") or values.get("avgHR"))
        or _average([p.heart_rate_bpm for p in points]),
        max_hr=_number(values.get("maxHR")) or _max([p.heart_rate_bpm for p in points]),
        elevation_gain_m=_number(
            values.get("elevationGain")
            or values.get("elevationGainInMeters")
            or values.get("totalAscent")
        )
        or _elevation_gain(points),
        average_cadence_spm=_number(
            values.get("averageRunningCadenceInStepsPerMinute")
            or values.get("averageRunCadence")
            or values.get("avgRunCadence")
            or values.get("averageCadence")
        )
        or _average([p.cadence_spm for p in points]),
    )


def _hr_zone_summary(
    points: list[TimeSeriesPoint], zones: HeartRateZones
) -> HeartRateZoneSummary:
    seconds = {
        "below_maf": 0.0,
        "maf": 0.0,
        "steady": 0.0,
        "threshold": 0.0,
        "above_threshold": 0.0,
    }
    for previous, current in zip(points, points[1:]):
        if previous.heart_rate_bpm is None:
            continue
        duration = max(0.0, current.elapsed_s - previous.elapsed_s)
        seconds[_zone_name(previous.heart_rate_bpm, zones)] += duration
    return HeartRateZoneSummary(seconds_by_zone=seconds)


def _zone_name(heart_rate: float, zones: HeartRateZones) -> str:
    if heart_rate < zones.maf_low:
        return "below_maf"
    if heart_rate <= zones.maf_high:
        return "maf"
    if heart_rate <= zones.steady_high:
        return "steady"
    if heart_rate <= zones.threshold_high:
        return "threshold"
    return "above_threshold"


def _pace_stability(points: list[TimeSeriesPoint]) -> PaceStability:
    paces = _segment_paces(points)
    if len(paces) < 2:
        return PaceStability(cv_pct=None, label="数据不足")
    mean = statistics.fmean(paces)
    cv = statistics.pstdev(paces) / mean * 100 if mean else None
    if cv is None:
        label = "数据不足"
    elif cv <= 5:
        label = "稳定"
    elif cv <= 10:
        label = "轻微波动"
    else:
        label = "波动较大"
    return PaceStability(cv_pct=cv, label=label)


def _heart_rate_drift(points: list[TimeSeriesPoint]) -> HeartRateDrift:
    if len(points) < 3:
        return HeartRateDrift(None, None, None, "数据不足")
    midpoint = points[0].elapsed_s + (points[-1].elapsed_s - points[0].elapsed_s) / 2
    first = _pace_hr_ratio(points, upper_elapsed_s=midpoint)
    second = _pace_hr_ratio(points, lower_elapsed_s=midpoint)
    if first is None or second is None or first == 0:
        return HeartRateDrift(first, second, None, "数据不足")
    drift = (first - second) / first * 100
    if drift <= 3:
        label = "稳定"
    elif drift <= 7:
        label = "轻微漂移"
    else:
        label = "明显漂移"
    return HeartRateDrift(first, second, drift, label)


def _classify_training(
    summary: dict[str, Any],
    basic: BasicMetrics,
    hr_zones: HeartRateZoneSummary,
    pace_stability: PaceStability,
    config: TrainingConfig,
) -> str:
    name = (summary.get("activityName") or "").lower()
    distance = basic.distance_km or 0
    duration = basic.duration_s or 0
    total_zone_time = sum(hr_zones.seconds_by_zone.values()) or duration or 1
    pct = {key: value / total_zone_time for key, value in hr_zones.seconds_by_zone.items()}
    hard_pct = pct["threshold"] + pct["above_threshold"]

    if any(keyword in name for keyword in ("race", "比赛", "半马", "马拉松", "10k", "5k")):
        return "比赛"
    if distance >= config.long_run_min_distance_km or duration >= config.long_run_min_duration_s:
        return "长距离"
    if (
        (pct["above_threshold"] >= 0.20 and (pace_stability.cv_pct or 0) > 10)
        or "interval" in name
        or "间歇" in name
    ):
        return "间歇课"
    if hard_pct >= 0.30:
        return "阈值课"
    if pct["steady"] >= 0.35:
        return "稳态跑"
    if pct["maf"] >= 0.50:
        return "MAF 跑"
    if pct["below_maf"] + pct["maf"] >= 0.80:
        return "E 跑"
    if pct["below_maf"] >= 0.50 or (basic.average_hr and basic.average_hr < config.heart_rate_zones.maf_low):
        return "恢复跑"
    return "E 跑"


def _score_execution(
    training_type: str,
    hr_zones: HeartRateZoneSummary,
    pace_stability: PaceStability,
    drift: HeartRateDrift,
) -> int:
    score = 100.0
    if pace_stability.cv_pct is not None:
        score -= min(22.0, max(0.0, pace_stability.cv_pct - 5) * 2.0)
    if drift.drift_pct is not None:
        score -= min(25.0, max(0.0, drift.drift_pct - 3) * 2.5)

    total = sum(hr_zones.seconds_by_zone.values()) or 1
    pct = {key: value / total for key, value in hr_zones.seconds_by_zone.items()}
    if training_type in {"恢复跑", "MAF 跑", "E 跑"}:
        score -= (pct["threshold"] + pct["above_threshold"]) * 35
    if training_type in {"阈值课", "间歇课"} and pct["below_maf"] > 0.30:
        score -= (pct["below_maf"] - 0.30) * 20
    return max(0, min(100, round(score)))


def _coach_instruction(
    training_type: str,
    score: int,
    stability: PaceStability,
    drift: HeartRateDrift,
) -> str:
    notes: list[str] = []
    if training_type in {"恢复跑", "MAF 跑", "E 跑"}:
        notes.append("下一次同类训练继续把强度压在目标有氧区间，宁可慢一点，也不要后程追配速。")
    elif training_type in {"阈值课", "间歇课"}:
        notes.append("下一次质量课先保证热身充分，主训练段保持可控，不用把最后一组跑成冲刺。")
    elif training_type == "长距离":
        notes.append("下一次长距离优先稳定补给和后半程心率，目标是跑完后仍有余量。")
    elif training_type == "比赛":
        notes.append("赛后优先恢复，至少安排一到两天轻松跑或休息，再恢复结构化训练。")
    else:
        notes.append("下一次训练保持轻松可控，以完成质量和恢复状态为第一目标。")

    if stability.cv_pct is not None and stability.cv_pct > 10:
        notes.append("本次配速波动偏大，下一次用更明确的分段目标控制节奏。")
    if drift.drift_pct is not None and drift.drift_pct > 7:
        notes.append("心率漂移明显，近期有氧耐力或补给恢复可能不足，下一次降低强度。")
    if score < 70:
        notes.append("执行分偏低，下一次只保留一个训练目标，避免同时追心率、配速和距离。")
    return " ".join(notes)


def _segment_paces(points: list[TimeSeriesPoint]) -> list[float]:
    paces: list[float] = []
    for previous, current in zip(points, points[1:]):
        duration = current.elapsed_s - previous.elapsed_s
        distance = _segment_distance(previous, current)
        if duration <= 0 or distance <= 0:
            continue
        paces.append(duration / (distance / 1000))
    return paces


def _pace_hr_ratio(
    points: list[TimeSeriesPoint],
    lower_elapsed_s: float | None = None,
    upper_elapsed_s: float | None = None,
) -> float | None:
    paces: list[float] = []
    hrs: list[float] = []
    for previous, current in zip(points, points[1:]):
        midpoint = previous.elapsed_s + (current.elapsed_s - previous.elapsed_s) / 2
        if lower_elapsed_s is not None and midpoint < lower_elapsed_s:
            continue
        if upper_elapsed_s is not None and midpoint > upper_elapsed_s:
            continue
        duration = current.elapsed_s - previous.elapsed_s
        distance = _segment_distance(previous, current)
        if duration <= 0 or distance <= 0 or previous.heart_rate_bpm is None:
            continue
        paces.append(duration / (distance / 1000))
        hrs.append(previous.heart_rate_bpm)
    if not paces or not hrs:
        return None
    avg_hr = statistics.fmean(hrs)
    return statistics.fmean(paces) / avg_hr if avg_hr else None


def _segment_distance(previous: TimeSeriesPoint, current: TimeSeriesPoint) -> float:
    if previous.distance_m is not None and current.distance_m is not None:
        return max(0.0, current.distance_m - previous.distance_m)
    if previous.speed_mps is not None:
        return previous.speed_mps * max(0.0, current.elapsed_s - previous.elapsed_s)
    return 0.0


def _activity_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str) and value:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    return date.today()


def _duration_from_points(points: list[TimeSeriesPoint]) -> float | None:
    if not points:
        return None
    return max(0.0, points[-1].elapsed_s - points[0].elapsed_s)


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _average(values: list[float | None]) -> float | None:
    numbers = [value for value in values if value is not None]
    return statistics.fmean(numbers) if numbers else None


def _max(values: list[float | None]) -> float | None:
    numbers = [value for value in values if value is not None]
    return max(numbers) if numbers else None


def _last_number(values: list[float | None]) -> float | None:
    for value in reversed(values):
        if value is not None:
            return value
    return None


def _summary_values(summary: dict[str, Any]) -> dict[str, Any]:
    nested = summary.get("summaryDTO")
    if isinstance(nested, dict):
        merged = dict(nested)
        for key in ("activityId", "activityName"):
            if key in summary:
                merged[key] = summary[key]
        return merged
    return summary


def _elevation_gain(points: list[TimeSeriesPoint]) -> float | None:
    gain = 0.0
    previous = None
    for point in points:
        if point.altitude_m is None:
            continue
        if previous is not None and point.altitude_m > previous:
            gain += point.altitude_m - previous
        previous = point.altitude_m
    return gain if previous is not None else None
