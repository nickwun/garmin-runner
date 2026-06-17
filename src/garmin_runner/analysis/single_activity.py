from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

PAUSE_GAP_SECONDS = 30.0
PAUSE_GAP_MAX_DISTANCE_M = 10.0


@dataclass(frozen=True)
class HeartRateZones:
    maf_low: int = 120
    maf_high: int = 145
    steady_high: int = 165
    threshold_high: int = 178
    recovery_high: int = 135
    easy_low: int = 133
    easy_high: int = 145
    aerobic_high: int = 155
    mp_bridge_high: int = 170
    vo2_high: int = 188
    sprint_high: int = 194


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
    moving_duration_s: float | None
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
    late_slowdown_pct: float | None = None


@dataclass(frozen=True)
class HeartRateDrift:
    first_half_pace_hr_ratio: float | None
    second_half_pace_hr_ratio: float | None
    drift_pct: float | None
    label: str
    applicable: bool = True
    reason: str | None = None


@dataclass(frozen=True)
class AnalysisConfidence:
    level: str
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class TrainingGuidance:
    tomorrow: str
    next_48_72_hours: str
    prohibited: str


@dataclass(frozen=True)
class WorkoutPhase:
    name: str
    distance_km: float
    duration_s: float
    average_pace_s_per_km: float | None


@dataclass(frozen=True)
class WorkoutBreakdown:
    warmup: WorkoutPhase
    main: WorkoutPhase
    cooldown: WorkoutPhase
    quality: WorkoutPhase


@dataclass(frozen=True)
class SingleActivityAnalysis:
    basic: BasicMetrics
    hr_zones: HeartRateZoneSummary
    pace_stability: PaceStability
    heart_rate_drift: HeartRateDrift
    training_type: str
    execution_score: int
    coach_instruction: str
    confidence: AnalysisConfidence
    not_applicable_notes: tuple[str, ...]
    guidance: TrainingGuidance
    workout_breakdown: WorkoutBreakdown | None = None


def training_config_from_settings(settings: Any) -> TrainingConfig:
    training = settings.training
    return TrainingConfig(
        heart_rate_zones=HeartRateZones(
            maf_low=training.recovery_low,
            maf_high=training.easy_high,
            steady_high=training.steady_high,
            threshold_high=training.threshold_high,
            recovery_high=training.recovery_high,
            easy_low=training.easy_low,
            easy_high=training.easy_high,
            aerobic_high=training.aerobic_high,
            mp_bridge_high=training.mp_bridge_high,
            vo2_high=training.vo2_high,
            sprint_high=training.sprint_high,
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
    training_type = _classify_training(summary, basic, hr_zones, pace_stability, config)
    workout_breakdown = _workout_breakdown(points, training_type)
    drift = _heart_rate_drift(summary, points, training_type, pace_stability)
    confidence = _analysis_confidence(summary, basic, points, pace_stability, training_type)
    not_applicable_notes = _not_applicable_notes(drift, confidence)
    execution_score = _score_execution(
        training_type, hr_zones, pace_stability, drift, confidence
    )
    guidance = _training_guidance(training_type, execution_score, pace_stability, drift)
    instruction = _coach_instruction(guidance, pace_stability, drift, confidence)
    return SingleActivityAnalysis(
        basic=basic,
        hr_zones=hr_zones,
        pace_stability=pace_stability,
        heart_rate_drift=drift,
        training_type=training_type,
        execution_score=execution_score,
        coach_instruction=instruction,
        confidence=confidence,
        not_applicable_notes=not_applicable_notes,
        guidance=guidance,
        workout_breakdown=workout_breakdown,
    )


def _basic_metrics(summary: dict[str, Any], points: list[TimeSeriesPoint]) -> BasicMetrics:
    values = _summary_values(summary)
    distance_m = _number(values.get("distance")) or _last_number([p.distance_m for p in points])
    duration_s = _number(values.get("duration")) or _duration_from_points(points)
    moving_duration_s = _number(
        values.get("movingDuration")
        or values.get("moving_duration")
        or values.get("movingDurationInSeconds")
    )
    distance_km = distance_m / 1000 if distance_m else None
    average_pace = duration_s / distance_km if duration_s and distance_km else None
    activity_date = _activity_date(values.get("startTimeLocal"))
    return BasicMetrics(
        activity_id=str(summary["activityId"]),
        activity_name=summary.get("activityName"),
        activity_date=activity_date,
        distance_km=distance_km,
        duration_s=duration_s,
        moving_duration_s=moving_duration_s,
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
        "below_range": 0.0,
        "very_easy": 0.0,
        "easy": 0.0,
        "aerobic": 0.0,
        "steady": 0.0,
        "mp_bridge": 0.0,
        "threshold": 0.0,
        "vo2": 0.0,
        "sprint": 0.0,
    }
    for previous, current, duration, _distance in _valid_segments(points):
        if previous.heart_rate_bpm is None:
            continue
        seconds[_zone_name(previous.heart_rate_bpm, zones)] += duration
    return HeartRateZoneSummary(seconds_by_zone=seconds)


def _zone_name(heart_rate: float, zones: HeartRateZones) -> str:
    if heart_rate < zones.maf_low:
        return "below_range"
    if heart_rate < zones.easy_low:
        return "very_easy"
    if heart_rate <= zones.easy_high:
        return "easy"
    if heart_rate <= zones.aerobic_high:
        return "aerobic"
    if heart_rate <= zones.steady_high:
        return "steady"
    if heart_rate <= zones.mp_bridge_high:
        return "mp_bridge"
    if heart_rate <= zones.threshold_high:
        return "threshold"
    if heart_rate <= zones.vo2_high:
        return "vo2"
    return "sprint"


def _pace_stability(points: list[TimeSeriesPoint]) -> PaceStability:
    paces = _split_paces(points)
    if len(paces) < 2:
        return PaceStability(cv_pct=None, label="数据不足", late_slowdown_pct=None)
    mean = statistics.fmean(paces)
    cv = statistics.pstdev(paces) / mean * 100 if mean else None
    slowdown = _late_slowdown(points)
    if cv is None:
        label = "数据不足"
    elif cv <= 5:
        label = "稳定"
    elif cv <= 10:
        label = "轻微波动"
    elif cv <= 18:
        label = "有波动"
    else:
        label = "波动较大"
    return PaceStability(cv_pct=cv, label=label, late_slowdown_pct=slowdown)


def _heart_rate_drift(
    summary: dict[str, Any],
    points: list[TimeSeriesPoint],
    training_type: str,
    pace_stability: PaceStability,
) -> HeartRateDrift:
    if _drift_not_applicable(summary, training_type, pace_stability):
        reason = "间歇课、比赛或明显变速课不使用全程前后半心率漂移判断"
        return HeartRateDrift(None, None, None, "不适用", applicable=False, reason=reason)
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
    hard_pct = pct["threshold"] + pct["vo2"] + pct["sprint"]
    avg_hr = basic.average_hr
    planned_easy = _name_has_any(name, ("easy", "轻松", "e跑", "e 跑", "recovery", "恢复"))

    if any(keyword in name for keyword in ("race", "比赛", "半马", "马拉松", "10k", "5k")):
        return "比赛"
    if _looks_like_threshold_interval(name):
        return "阈值间歇"
    if (
        (pct["vo2"] + pct["sprint"] >= 0.20 and (pace_stability.cv_pct or 0) > 10)
        or "interval" in name
        or "间歇" in name
    ):
        return "间歇课"
    if any(keyword in name for keyword in ("4×2", "5×2", "4x2", "5x2", "tempo", "threshold", "阈值")):
        return "阈值课"
    reaches_long_run_threshold = (
        distance >= config.long_run_min_distance_km
        and duration >= config.long_run_min_duration_s
    )
    if distance >= 20 or reaches_long_run_threshold:
        return "长距离"
    if hard_pct >= 0.30:
        return "阈值课"
    if avg_hr is not None:
        if _looks_like_warmup_or_cooldown(name, distance, duration):
            return "热身/冷身"
        if planned_easy and avg_hr > config.heart_rate_zones.aerobic_high:
            if avg_hr <= config.heart_rate_zones.steady_high:
                return "轻松跑跑成稳态"
            return "轻松跑跑成质量课"
        if "maf" in name and avg_hr <= config.heart_rate_zones.easy_high:
            return "MAF 跑"
        if avg_hr < config.heart_rate_zones.easy_low:
            return "恢复跑" if distance <= 8 and duration <= 45 * 60 else "E 跑"
        if avg_hr <= config.heart_rate_zones.easy_high:
            return "E 跑"
        if avg_hr <= config.heart_rate_zones.aerobic_high:
            return "中长有氧 / 稍稳有氧"
        if avg_hr <= config.heart_rate_zones.steady_high:
            return "稳态跑"
        if avg_hr <= config.heart_rate_zones.mp_bridge_high:
            return "马配桥梁"
        if avg_hr <= config.heart_rate_zones.threshold_high:
            return "阈值课"
        return "间歇课"
    if pct["steady"] >= 0.35:
        return "稳态跑"
    if pct["easy"] >= 0.50:
        return "E 跑"
    return "E 跑"


def _score_execution(
    training_type: str,
    hr_zones: HeartRateZoneSummary,
    pace_stability: PaceStability,
    drift: HeartRateDrift,
    confidence: AnalysisConfidence,
) -> int:
    score = 100.0
    total = sum(hr_zones.seconds_by_zone.values()) or 1
    pct = {key: value / total for key, value in hr_zones.seconds_by_zone.items()}
    easy_types = {"恢复跑", "MAF 跑", "E 跑"}
    hard_pct = pct["threshold"] + pct["vo2"] + pct["sprint"]
    above_easy_pct = (
        pct["aerobic"]
        + pct["steady"]
        + pct["mp_bridge"]
        + pct["threshold"]
        + pct["vo2"]
        + pct["sprint"]
    )

    if training_type in easy_types:
        score -= max(0.0, above_easy_pct - 0.08) * 45
        score -= hard_pct * 45
        if drift.applicable and drift.drift_pct is not None:
            score -= min(12.0, max(0.0, drift.drift_pct - 7) * 1.5)
        if pace_stability.cv_pct is not None:
            score -= min(6.0, max(0.0, pace_stability.cv_pct - 18) * 0.5)
    elif training_type == "轻松跑跑成稳态":
        score -= 28
        score -= (pct["mp_bridge"] + hard_pct) * 35
    elif training_type == "长距离":
        if drift.applicable and drift.drift_pct is not None:
            score -= min(25.0, max(0.0, drift.drift_pct - 5) * 3.0)
        if pace_stability.late_slowdown_pct is not None:
            score -= min(20.0, max(0.0, pace_stability.late_slowdown_pct - 5) * 2.0)
        score -= hard_pct * 30
    elif training_type in {"中长有氧 / 稍稳有氧", "稳态跑", "马配桥梁"}:
        score -= max(0.0, hard_pct - 0.08) * 45
        if drift.applicable and drift.drift_pct is not None:
            score -= min(15.0, max(0.0, drift.drift_pct - 7) * 2.0)
        if pace_stability.cv_pct is not None:
            score -= min(10.0, max(0.0, pace_stability.cv_pct - 15) * 0.8)
    elif training_type in {"阈值课", "间歇课", "阈值间歇"}:
        target_pct = pct["steady"] + pct["mp_bridge"] + pct["threshold"]
        easyish_pct = pct["below_range"] + pct["very_easy"] + pct["easy"]
        score -= max(0.0, 0.65 - target_pct) * 45
        score -= max(0.0, easyish_pct - 0.25) * 50
        if pace_stability.cv_pct is not None:
            score -= min(10.0, max(0.0, pace_stability.cv_pct - 12) * 0.4)
        if pct.get("below_range", 0) > 0.35:
            score -= (pct.get("below_range", 0) - 0.35) * 20
        if hard_pct > 0.45:
            score -= (hard_pct - 0.45) * 20
    else:
        if pace_stability.cv_pct is not None:
            score -= min(12.0, max(0.0, pace_stability.cv_pct - 15) * 0.8)
        if drift.applicable and drift.drift_pct is not None:
            score -= min(15.0, max(0.0, drift.drift_pct - 7) * 2.0)

    if training_type in {"阈值课", "间歇课", "阈值间歇"} and pct.get("below_range", 0) > 0.30:
        score -= (pct.get("below_range", 0) - 0.30) * 20
    if confidence.level == "low":
        score -= 5
    return max(0, min(100, round(score)))


def _coach_instruction(
    guidance: TrainingGuidance,
    stability: PaceStability,
    drift: HeartRateDrift,
    confidence: AnalysisConfidence,
) -> str:
    notes: list[str] = []
    if confidence.level != "high":
        notes.append("本次数据可信度下降，所有结论优先当作训练复盘线索，不作为单次定论。")
    if stability.cv_pct is not None and stability.cv_pct > 18:
        notes.append("配速波动较大，请先确认是否包含暂停、红绿灯或结构化变速。")
    if drift.applicable and drift.drift_pct is not None and drift.drift_pct > 7:
        notes.append("心率漂移明显，近期有氧耐力、补给或恢复可能不足。")
    if not notes:
        notes.append("本次建议主要依据训练类型、心率分布、配速稳定性和数据可信度生成。")
    return " ".join(notes)


def _training_guidance(
    training_type: str,
    score: int,
    stability: PaceStability,
    drift: HeartRateDrift,
) -> TrainingGuidance:
    if training_type in {"恢复跑", "E 跑"}:
        tomorrow = "安排休息或 30-50 分钟轻松有氧，心率继续压在恢复/E 跑区间。"
        future = "保持有氧频率；如果腿感轻松，可在 48-72 小时后安排一次小剂量节奏训练。"
        prohibited = "不要因为配速慢而补强度，不要在后程追配速。"
    elif training_type == "MAF 跑":
        tomorrow = "优先轻松恢复，继续用低心率确认身体状态。"
        future = "48-72 小时内可继续低心率跑，暂不急着加入阈值刺激。"
        prohibited = "不要用速度评价本次训练好坏。"
    elif training_type == "轻松跑跑成稳态":
        tomorrow = "改为恢复日或完全休息，把心率重新压回恢复区间。"
        future = "未来 48-72 小时避免连续质量刺激，等主观疲劳下降后再恢复结构化训练。"
        prohibited = "不要把原计划轻松跑继续跑成稳态或阈值。"
    elif training_type == "热身/冷身":
        tomorrow = "按主课或整体训练负荷安排恢复；单独这段只作为热身/冷身记录解读。"
        future = "未来 48-72 小时参考当天完整训练组合，不用单独放大这条短记录。"
        prohibited = "不要把热身或冷身当作独立有氧课评分，也不要用它推断当天主训练质量。"
    elif training_type in {"中长有氧 / 稍稳有氧", "稳态跑", "马配桥梁"}:
        tomorrow = "安排轻松跑或休息，观察腿部和晨起心率。"
        future = "48-72 小时内可做一次短有氧或技术跑，质量课视恢复情况再定。"
        prohibited = "不要连续两天把有氧跑进阈值以上。"
    elif training_type in {"阈值课", "间歇课", "阈值间歇"}:
        tomorrow = "安排恢复跑或休息，不再叠加强度。"
        future = "未来 48-72 小时以恢复和低强度有氧为主，下一次质量课至少隔一天。"
        prohibited = "不要用全程平均配速评价间歇质量，不要补跑额外强度。"
    elif training_type == "长距离":
        tomorrow = "优先休息或 30-40 分钟恢复跑。"
        future = "未来 48-72 小时关注睡眠、补给和腿部恢复，再决定是否恢复节奏训练。"
        prohibited = "不要在恢复不足时安排阈值课；不要忽视后半程心率漂移和配速掉速。"
    elif training_type == "比赛":
        tomorrow = "赛后优先恢复，至少安排休息或极轻松活动。"
        future = "未来 48-72 小时只看恢复，不急于恢复训练计划。"
        prohibited = "不要用比赛后疲劳状态继续堆量或补课。"
    else:
        tomorrow = "安排轻松可控训练，以恢复状态为第一目标。"
        future = "未来 48-72 小时根据疲劳和睡眠调整训练强度。"
        prohibited = "不要同时追心率、配速和距离。"
    if score < 70:
        prohibited += " 本次执行分偏低，下一次只保留一个训练目标。"
    return TrainingGuidance(
        tomorrow=tomorrow,
        next_48_72_hours=future,
        prohibited=prohibited,
    )


def _segment_paces(points: list[TimeSeriesPoint]) -> list[float]:
    paces: list[float] = []
    for _previous, _current, duration, distance in _valid_segments(points):
        paces.append(duration / (distance / 1000))
    return paces


def _split_paces(points: list[TimeSeriesPoint], split_m: float = 1000.0) -> list[float]:
    paces: list[float] = []
    acc_duration = 0.0
    acc_distance = 0.0
    for _previous, _current, duration, distance in _valid_segments(points):
        if distance <= 0:
            continue
        acc_duration += duration
        acc_distance += distance
        if acc_distance >= split_m:
            paces.append(acc_duration / (acc_distance / 1000))
            acc_duration = 0.0
            acc_distance = 0.0
    if len(paces) < 2:
        return _segment_paces(points)
    return paces


@dataclass(frozen=True)
class _Chunk:
    start_m: float
    end_m: float
    distance_m: float
    duration_s: float
    pace_s_per_km: float


def _workout_breakdown(
    points: list[TimeSeriesPoint],
    training_type: str,
) -> WorkoutBreakdown | None:
    if training_type != "阈值间歇":
        return None
    chunks = _distance_chunks(points)
    if len(chunks) < 3:
        return None
    paces = [chunk.pace_s_per_km for chunk in chunks]
    if max(paces) - min(paces) < 20:
        return None
    fast_cutoff = min(paces) + (max(paces) - min(paces)) * 0.45
    fast_indexes = [
        index for index, chunk in enumerate(chunks) if chunk.pace_s_per_km <= fast_cutoff
    ]
    if not fast_indexes:
        return None

    first_fast = fast_indexes[0]
    last_fast = fast_indexes[-1]
    warmup = chunks[:first_fast]
    main = chunks[first_fast : last_fast + 1]
    cooldown = chunks[last_fast + 1 :]
    quality = [chunks[index] for index in fast_indexes]
    if not main:
        return None
    return WorkoutBreakdown(
        warmup=_phase("热身", warmup),
        main=_phase("阈值间歇训练段", main),
        cooldown=_phase("冷身", cooldown),
        quality=_phase("阈值快段", quality),
    )


def _distance_chunks(
    points: list[TimeSeriesPoint],
    chunk_m: float = 500.0,
) -> list[_Chunk]:
    chunks: list[_Chunk] = []
    acc_distance = 0.0
    acc_duration = 0.0
    start_m: float | None = None
    end_m = 0.0
    for previous, current, duration, distance in _valid_segments(points):
        if start_m is None:
            start_m = previous.distance_m or end_m
        acc_distance += distance
        acc_duration += duration
        end_m = current.distance_m if current.distance_m is not None else end_m + distance
        if acc_distance >= chunk_m:
            chunks.append(
                _Chunk(
                    start_m=start_m,
                    end_m=end_m,
                    distance_m=acc_distance,
                    duration_s=acc_duration,
                    pace_s_per_km=acc_duration / (acc_distance / 1000),
                )
            )
            start_m = end_m
            acc_distance = 0.0
            acc_duration = 0.0
    if acc_distance > 0 and acc_duration > 0 and start_m is not None:
        chunks.append(
            _Chunk(
                start_m=start_m,
                end_m=end_m,
                distance_m=acc_distance,
                duration_s=acc_duration,
                pace_s_per_km=acc_duration / (acc_distance / 1000),
            )
        )
    return chunks


def _phase(name: str, chunks: list[_Chunk]) -> WorkoutPhase:
    distance = sum(chunk.distance_m for chunk in chunks) / 1000
    duration = sum(chunk.duration_s for chunk in chunks)
    pace = duration / distance if duration and distance else None
    return WorkoutPhase(
        name=name,
        distance_km=round(distance, 2),
        duration_s=duration,
        average_pace_s_per_km=pace,
    )


def _pace_hr_ratio(
    points: list[TimeSeriesPoint],
    lower_elapsed_s: float | None = None,
    upper_elapsed_s: float | None = None,
) -> float | None:
    paces: list[float] = []
    hrs: list[float] = []
    for previous, current, duration, distance in _valid_segments(points):
        midpoint = previous.elapsed_s + (current.elapsed_s - previous.elapsed_s) / 2
        if lower_elapsed_s is not None and midpoint < lower_elapsed_s:
            continue
        if upper_elapsed_s is not None and midpoint > upper_elapsed_s:
            continue
        if duration <= 0 or distance <= 0 or previous.heart_rate_bpm is None:
            continue
        paces.append(duration / (distance / 1000))
        hrs.append(previous.heart_rate_bpm)
    if not paces or not hrs:
        return None
    avg_hr = statistics.fmean(hrs)
    return statistics.fmean(paces) / avg_hr if avg_hr else None


def _late_slowdown(points: list[TimeSeriesPoint]) -> float | None:
    if len(points) < 3:
        return None
    midpoint = points[0].elapsed_s + (points[-1].elapsed_s - points[0].elapsed_s) / 2
    first = _average_pace(points, upper_elapsed_s=midpoint)
    second = _average_pace(points, lower_elapsed_s=midpoint)
    if first is None or second is None or first == 0:
        return None
    return (second - first) / first * 100


def _average_pace(
    points: list[TimeSeriesPoint],
    lower_elapsed_s: float | None = None,
    upper_elapsed_s: float | None = None,
) -> float | None:
    duration_sum = 0.0
    distance_sum = 0.0
    for previous, current, duration, distance in _valid_segments(points):
        midpoint = previous.elapsed_s + (current.elapsed_s - previous.elapsed_s) / 2
        if lower_elapsed_s is not None and midpoint < lower_elapsed_s:
            continue
        if upper_elapsed_s is not None and midpoint > upper_elapsed_s:
            continue
        duration_sum += duration
        distance_sum += distance
    if duration_sum <= 0 or distance_sum <= 0:
        return None
    return duration_sum / (distance_sum / 1000)


def _valid_segments(
    points: list[TimeSeriesPoint],
) -> list[tuple[TimeSeriesPoint, TimeSeriesPoint, float, float]]:
    segments: list[tuple[TimeSeriesPoint, TimeSeriesPoint, float, float]] = []
    for previous, current in zip(points, points[1:]):
        duration = current.elapsed_s - previous.elapsed_s
        distance = _segment_distance(previous, current)
        if duration <= 0 or distance <= 0:
            continue
        if _is_pause_gap(duration, distance):
            continue
        segments.append((previous, current, duration, distance))
    return segments


def _is_pause_gap(duration: float, distance: float) -> bool:
    return duration > PAUSE_GAP_SECONDS and distance <= PAUSE_GAP_MAX_DISTANCE_M


def _has_obvious_pause(points: list[TimeSeriesPoint], duration_s: float | None) -> bool:
    raw_elapsed = _duration_from_points(points)
    if duration_s is not None and raw_elapsed is not None and raw_elapsed - duration_s > 60:
        return True
    for previous, current in zip(points, points[1:]):
        duration = current.elapsed_s - previous.elapsed_s
        distance = _segment_distance(previous, current)
        if _is_pause_gap(duration, distance):
            return True
    return False


def _analysis_confidence(
    summary: dict[str, Any],
    basic: BasicMetrics,
    points: list[TimeSeriesPoint],
    pace_stability: PaceStability,
    training_type: str,
) -> AnalysisConfidence:
    reasons: list[str] = []
    penalty = 0
    if basic.average_hr is None and not any(p.heart_rate_bpm is not None for p in points):
        reasons.append("FIT/summary 缺少心率，生理判断可信度下降")
        penalty += 2
    if not any(p.distance_m is not None for p in points):
        reasons.append("FIT 缺少 distance/距离 records，配速和漂移判断可信度下降")
        penalty += 2
    if not any(p.speed_mps is not None for p in points):
        reasons.append("FIT 缺少 speed/enhanced_speed 速度字段，配速稳定性可信度下降")
        penalty += 1
    if len(points) < 60 and (basic.duration_s or 0) >= 600:
        reasons.append("FIT records 太少，无法稳定判断训练过程")
        penalty += 2
    if _has_obvious_pause(points, basic.duration_s):
        reasons.append("FIT 中存在明显暂停或长时间 gap，已过滤暂停段")
        penalty += 1
    if _drift_not_applicable(summary, training_type, pace_stability):
        reasons.append("间歇、比赛或明显变速课会降低全程趋势指标可信度")
        penalty += 1

    if penalty >= 3:
        level = "low"
    elif penalty >= 1:
        level = "medium"
    else:
        level = "high"
    return AnalysisConfidence(level=level, reasons=tuple(reasons))


def _not_applicable_notes(
    drift: HeartRateDrift,
    confidence: AnalysisConfidence,
) -> tuple[str, ...]:
    notes: list[str] = []
    if not drift.applicable and drift.reason:
        notes.append(f"心率漂移：{drift.reason}")
    for reason in confidence.reasons:
        if "缺少" in reason or "太少" in reason:
            notes.append(reason)
    return tuple(notes)


def _drift_not_applicable(
    summary: dict[str, Any],
    training_type: str,
    pace_stability: PaceStability,
) -> bool:
    name = (summary.get("activityName") or "").lower()
    if _name_has_any(name, ("interval", "间歇", "4×", "5×", "4x", "5x")):
        return True
    if training_type in {"比赛", "间歇课", "阈值间歇"}:
        return True
    return bool(pace_stability.cv_pct is not None and pace_stability.cv_pct > 25)


def _name_has_any(name: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in name for keyword in keywords)


def _looks_like_threshold_interval(name: str) -> bool:
    compact = name.replace(" ", "")
    if any(pattern in compact for pattern in ("×2公里", "x2公里", "x2km")):
        return True
    return "2公里" in compact and _name_has_any(
        name, ("interval", "repeats", "间歇")
    )


def _looks_like_warmup_or_cooldown(name: str, distance_km: float, duration_s: float) -> bool:
    if _name_has_any(name, ("warm", "cool", "热身", "冷身", "放松")):
        return True
    if _name_has_any(name, ("tempo", "threshold", "阈值", "间歇", "interval")):
        return False
    return distance_km <= 5.0 and duration_s <= 35 * 60


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
