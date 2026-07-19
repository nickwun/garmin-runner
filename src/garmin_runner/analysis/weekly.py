from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from enum import Enum
from pathlib import Path


EASY_TYPES = {"恢复跑", "MAF 跑", "E 跑"}
STEADY_TYPES = {"中长有氧 / 稍稳有氧", "稳态跑", "马配桥梁", "轻松跑跑成稳态"}
HIGH_INTENSITY_TYPES = {"阈值课", "间歇课", "阈值间歇"}
LONG_RUN_TYPES = {"长距离"}
MEDIUM_HIGH_TYPES = STEADY_TYPES | HIGH_INTENSITY_TYPES | LONG_RUN_TYPES


@dataclass(frozen=True)
class WeeklyTrainingStructure:
    rest_day: str = "monday"
    normal_volume_min_km: float = 100.0
    normal_volume_max_km: float = 120.0
    tuesday_quality: bool = True
    friday_steady: bool = True
    weekend_long_run: bool = True
    marathon_goal: str = "年底 2:45 全马目标"
    b_race_note: str = "东营作为 B 赛测试，不全力"


class WeeklyWorkoutPhaseRole(str, Enum):
    WARMUP = "warmup"
    MAIN = "main"
    COOLDOWN = "cooldown"


@dataclass(frozen=True)
class WeeklyWorkoutPhase:
    role: WeeklyWorkoutPhaseRole
    name: str
    distance_km: float
    duration_s: float

    def __post_init__(self) -> None:
        if not isinstance(self.role, WeeklyWorkoutPhaseRole):
            raise ValueError("周训练组成只接受热身、主训练和冷身三个互斥阶段")


@dataclass(frozen=True)
class WeeklyActivity:
    activity_id: str
    activity_date: date
    activity_name: str | None
    distance_km: float
    duration_s: float
    average_hr: float | None
    training_type: str
    execution_score: int
    report_path: Path
    intensity_distance_km: float | None = None
    intensity_duration_s: float | None = None
    start_time_local: datetime | None = None
    workout_phases: tuple[WeeklyWorkoutPhase, ...] = ()


@dataclass(frozen=True)
class WeeklyContext:
    week_start: date
    week_end: date
    activities: list[WeeklyActivity]
    previous_week_distance_km: float | None
    recent_4w_avg_distance_km: float | None
    structure: WeeklyTrainingStructure


@dataclass(frozen=True)
class IntensityBucket:
    distance_km: float
    duration_s: float


@dataclass(frozen=True)
class DailyCompositionItem:
    label: str
    distance_km: float


@dataclass(frozen=True)
class DailyTrainingSummary:
    activity_date: date
    training_type: str
    composition: tuple[DailyCompositionItem, ...]
    activities: tuple[WeeklyActivity, ...]
    total_distance_km: float
    total_duration_s: float
    combined_pace_s_per_km: float | None
    average_hr: float | None
    is_rest_day: bool


@dataclass(frozen=True)
class KeyWorkout:
    activity: WeeklyActivity
    comment: str


@dataclass(frozen=True)
class NextWeekAdvice:
    direction: str
    tuesday_quality: str
    friday_steady: str
    weekend_long_run: str
    must_rest_monday: bool


@dataclass(frozen=True)
class WeeklyAnalysis:
    week_start: date
    week_end: date
    iso_year: int
    iso_week: int
    conclusion: str
    total_distance_km: float
    total_duration_s: float
    running_days: int
    rest_days: int
    longest_distance_km: float
    long_run_distance_ratio: float
    previous_week_delta_km: float | None
    previous_week_delta_pct: float | None
    recent_4w_delta_km: float | None
    recent_4w_delta_pct: float | None
    easy: IntensityBucket
    steady: IntensityBucket
    high_intensity: IntensityBucket
    long_run: IntensityBucket
    auxiliary: IntensityBucket
    high_intensity_count: int
    high_intensity_time_ratio: float
    daily_summaries: list[DailyTrainingSummary]
    key_workouts: list[KeyWorkout]
    risk_signals: list[str]
    next_week: NextWeekAdvice
    prohibited: list[str]
    structure: WeeklyTrainingStructure


def analyze_week(context: WeeklyContext) -> WeeklyAnalysis:
    activities = sorted(
        (
            item
            for item in context.activities
            if context.week_start <= item.activity_date <= context.week_end
        ),
        key=lambda item: item.activity_date,
    )
    context = replace(context, activities=activities)
    total_distance = round(sum(item.distance_km for item in activities), 2)
    total_duration = sum(item.duration_s for item in activities)
    running_days = len({item.activity_date for item in activities})
    period_days = max(0, (context.week_end - context.week_start).days + 1)
    rest_days = max(0, period_days - running_days)
    longest = max((item.distance_km for item in activities), default=0.0)

    easy = _bucket(activities, EASY_TYPES)
    steady = _bucket(activities, STEADY_TYPES)
    high = _bucket(activities, HIGH_INTENSITY_TYPES)
    long_run = _bucket(activities, LONG_RUN_TYPES)
    grouped_types = EASY_TYPES | STEADY_TYPES | HIGH_INTENSITY_TYPES | LONG_RUN_TYPES
    auxiliary = _auxiliary_bucket(activities, grouped_types)

    high_count = sum(1 for item in activities if item.training_type in HIGH_INTENSITY_TYPES)
    high_ratio = high.duration_s / total_duration if total_duration else 0.0
    long_ratio = long_run.distance_km / total_distance if total_distance else 0.0

    previous_delta_km, previous_delta_pct = _delta(
        total_distance, context.previous_week_distance_km
    )
    recent_delta_km, recent_delta_pct = _delta(
        total_distance, context.recent_4w_avg_distance_km
    )

    risks = _risk_signals(
        activities=activities,
        high_ratio=high_ratio,
        previous_delta_pct=previous_delta_pct,
        recent_delta_pct=recent_delta_pct,
        structure=context.structure,
    )
    conclusion = _conclusion(total_distance, high_count, long_run.distance_km, risks, context)
    next_week = _next_week_advice(conclusion, risks, context.structure)
    prohibited = _prohibited(conclusion, risks)

    iso = context.week_start.isocalendar()
    return WeeklyAnalysis(
        week_start=context.week_start,
        week_end=context.week_end,
        iso_year=iso.year,
        iso_week=iso.week,
        conclusion=conclusion,
        total_distance_km=total_distance,
        total_duration_s=total_duration,
        running_days=running_days,
        rest_days=rest_days,
        longest_distance_km=round(longest, 2),
        long_run_distance_ratio=round(long_ratio, 3),
        previous_week_delta_km=previous_delta_km,
        previous_week_delta_pct=previous_delta_pct,
        recent_4w_delta_km=recent_delta_km,
        recent_4w_delta_pct=recent_delta_pct,
        easy=easy,
        steady=steady,
        high_intensity=high,
        long_run=long_run,
        auxiliary=auxiliary,
        high_intensity_count=high_count,
        high_intensity_time_ratio=round(high_ratio, 3),
        daily_summaries=_daily_summaries(context),
        key_workouts=_key_workouts(activities),
        risk_signals=risks,
        next_week=next_week,
        prohibited=prohibited,
        structure=context.structure,
    )


def _daily_summaries(context: WeeklyContext) -> list[DailyTrainingSummary]:
    activities_by_date: dict[date, list[WeeklyActivity]] = {}
    for activity in context.activities:
        activities_by_date.setdefault(activity.activity_date, []).append(activity)

    summaries: list[DailyTrainingSummary] = []
    current_date = context.week_start
    while current_date <= context.week_end:
        activities = tuple(
            sorted(activities_by_date.get(current_date, []), key=_activity_order_key)
        )
        if not activities:
            summaries.append(
                DailyTrainingSummary(
                    activity_date=current_date,
                    training_type="休息",
                    composition=(),
                    activities=(),
                    total_distance_km=0.0,
                    total_duration_s=0.0,
                    combined_pace_s_per_km=None,
                    average_hr=None,
                    is_rest_day=True,
                )
            )
            current_date += timedelta(days=1)
            continue

        raw_total_distance = sum(item.distance_km for item in activities)
        total_distance = round(raw_total_distance, 2)
        total_duration = sum(item.duration_s for item in activities)
        hr_activities = [item for item in activities if item.average_hr is not None]
        hr_duration = sum(item.duration_s for item in hr_activities)
        average_hr = (
            sum(item.average_hr * item.duration_s for item in hr_activities) / hr_duration
            if hr_duration
            else None
        )
        dominant = _dominant_activity(activities)
        training_type = dominant.training_type
        if training_type != "热身/冷身" and _has_warmup_or_cooldown(activities):
            training_type = f"{training_type}（含热身冷身）"
        summaries.append(
            DailyTrainingSummary(
                activity_date=current_date,
                training_type=training_type,
                composition=_composition_items(activities),
                activities=activities,
                total_distance_km=total_distance,
                total_duration_s=total_duration,
                combined_pace_s_per_km=(
                    total_duration / raw_total_distance
                    if raw_total_distance > 0 and total_duration > 0
                    else None
                ),
                average_hr=average_hr,
                is_rest_day=False,
            )
        )
        current_date += timedelta(days=1)
    return summaries


def _activity_order_key(activity: WeeklyActivity) -> tuple[bool, datetime, str]:
    local_time = (
        activity.start_time_local.replace(tzinfo=None)
        if activity.start_time_local is not None
        else datetime.max
    )
    return (
        activity.start_time_local is None,
        local_time,
        activity.activity_id,
    )


def _dominant_activity(activities: tuple[WeeklyActivity, ...]) -> WeeklyActivity:
    return min(
        activities,
        key=lambda item: (
            -_training_priority(item.training_type),
            -item.duration_s,
            *_activity_order_key(item),
        ),
    )


def _composition_items(
    activities: tuple[WeeklyActivity, ...],
) -> tuple[DailyCompositionItem, ...]:
    main_span = _main_activity_span(activities)
    composition: list[DailyCompositionItem] = []
    for index, activity in enumerate(activities):
        if activity.workout_phases:
            composition.extend(
                DailyCompositionItem(phase.name, phase.distance_km)
                for phase in activity.workout_phases
            )
            continue
        label = activity.training_type
        if label == "热身/冷身" and main_span is not None:
            label = _auxiliary_label(index, main_span)
        elif label in EASY_TYPES and main_span is not None:
            label = _adjacent_easy_label(activities, index, main_span)
        composition.append(DailyCompositionItem(label, activity.distance_km))
    return tuple(composition)


def _main_activity_span(
    activities: tuple[WeeklyActivity, ...],
) -> tuple[int, int] | None:
    main_indices = _main_activity_indices(activities)
    if not main_indices:
        return None
    return min(main_indices), max(main_indices)


def _main_activity_indices(
    activities: tuple[WeeklyActivity, ...],
) -> tuple[int, ...]:
    non_auxiliary = [
        (index, activity)
        for index, activity in enumerate(activities)
        if activity.training_type != "热身/冷身"
    ]
    if not non_auxiliary:
        return ()
    highest_priority = max(
        _training_priority(activity.training_type) for _, activity in non_auxiliary
    )
    return tuple(
        index
        for index, activity in non_auxiliary
        if _training_priority(activity.training_type) == highest_priority
    )


def _auxiliary_label(index: int, main_span: tuple[int, int]) -> str:
    first_main, last_main = main_span
    if index < first_main:
        return "热身"
    if index > last_main:
        return "冷身"
    return "辅助跑"


def _adjacent_easy_label(
    activities: tuple[WeeklyActivity, ...],
    index: int,
    main_span: tuple[int, int],
) -> str:
    activity = activities[index]
    first_main, last_main = main_span
    main_priority = _training_priority(activities[first_main].training_type)
    if _training_priority(activity.training_type) >= main_priority:
        return activity.training_type

    if index == first_main - 1 and _activities_are_adjacent(
        activity, activities[first_main]
    ):
        return "热身"
    if index == last_main + 1 and _activities_are_adjacent(
        activities[last_main], activity
    ):
        return "冷身"

    main_indices = _main_activity_indices(activities)
    previous_main = max((item for item in main_indices if item < index), default=None)
    next_main = min((item for item in main_indices if item > index), default=None)
    if (
        previous_main is not None
        and next_main is not None
        and index == previous_main + 1
        and index == next_main - 1
        and _activities_are_adjacent(activities[previous_main], activity)
        and _activities_are_adjacent(activity, activities[next_main])
    ):
        return "辅助跑"
    return activity.training_type


def _activities_are_adjacent(
    preceding: WeeklyActivity,
    following: WeeklyActivity,
) -> bool:
    if preceding.start_time_local is None or following.start_time_local is None:
        return False
    if not math.isfinite(preceding.duration_s) or preceding.duration_s < 0:
        return False
    preceding_end = _local_wall_time(preceding.start_time_local) + timedelta(
        seconds=preceding.duration_s
    )
    gap_seconds = (
        _local_wall_time(following.start_time_local) - preceding_end
    ).total_seconds()
    return 0 <= gap_seconds <= 600


def _local_wall_time(value: datetime) -> datetime:
    return value.replace(tzinfo=None)


def _has_warmup_or_cooldown(activities: tuple[WeeklyActivity, ...]) -> bool:
    return any(
        activity.training_type == "热身/冷身"
        or any(
            phase.role
            in {WeeklyWorkoutPhaseRole.WARMUP, WeeklyWorkoutPhaseRole.COOLDOWN}
            for phase in activity.workout_phases
        )
        for activity in activities
    )


def _training_priority(training_type: str) -> int:
    if training_type == "比赛":
        return 7
    if training_type in LONG_RUN_TYPES:
        return 6
    if training_type in HIGH_INTENSITY_TYPES:
        return 5
    if training_type in STEADY_TYPES:
        return 4
    if training_type in {"MAF 跑", "E 跑"}:
        return 3
    if training_type == "恢复跑":
        return 2
    if training_type == "热身/冷身":
        return 1
    return 0


def _bucket(
    activities: list[WeeklyActivity],
    training_types: set[str] | None,
) -> IntensityBucket:
    selected = (
        activities
        if training_types is None
        else [item for item in activities if item.training_type in training_types]
    )
    return IntensityBucket(
        distance_km=round(sum(_bucket_distance(item) for item in selected), 2),
        duration_s=sum(_bucket_duration(item) for item in selected),
    )


def _bucket_distance(activity: WeeklyActivity) -> float:
    if (
        activity.training_type in (HIGH_INTENSITY_TYPES | STEADY_TYPES)
        and activity.intensity_distance_km is not None
    ):
        return activity.intensity_distance_km
    return activity.distance_km


def _bucket_duration(activity: WeeklyActivity) -> float:
    if (
        activity.training_type in (HIGH_INTENSITY_TYPES | STEADY_TYPES)
        and activity.intensity_duration_s is not None
    ):
        return activity.intensity_duration_s
    return activity.duration_s


def _auxiliary_bucket(
    activities: list[WeeklyActivity],
    grouped_types: set[str],
) -> IntensityBucket:
    distance = 0.0
    duration = 0.0
    for activity in activities:
        if activity.training_type not in grouped_types:
            distance += activity.distance_km
            duration += activity.duration_s
            continue
        if activity.intensity_distance_km is not None:
            distance += max(0.0, activity.distance_km - activity.intensity_distance_km)
        if activity.intensity_duration_s is not None:
            duration += max(0.0, activity.duration_s - activity.intensity_duration_s)
    return IntensityBucket(distance_km=round(distance, 2), duration_s=duration)


def _delta(current: float, baseline: float | None) -> tuple[float | None, float | None]:
    if baseline is None or baseline <= 0:
        return None, None
    delta_km = round(current - baseline, 2)
    delta_pct = round(delta_km / baseline * 100, 1)
    return delta_km, delta_pct


def _risk_signals(
    activities: list[WeeklyActivity],
    high_ratio: float,
    previous_delta_pct: float | None,
    recent_delta_pct: float | None,
    structure: WeeklyTrainingStructure,
) -> list[str]:
    signals: list[str] = []
    if not activities:
        return ["本周没有跑步记录，属于空周或未同步数据"]

    dates_by_type = {item.activity_date: item.training_type for item in activities}
    medium_high_dates = sorted(
        {item.activity_date for item in activities if item.training_type in MEDIUM_HIGH_TYPES}
    )
    if any(
        (current - previous).days == 1
        for previous, current in zip(medium_high_dates, medium_high_dates[1:])
    ):
        signals.append("存在连续中高强度训练")

    high_dates = sorted(
        {item.activity_date for item in activities if item.training_type in HIGH_INTENSITY_TYPES}
    )
    if any((current - previous).days < 2 for previous, current in zip(high_dates, high_dates[1:])):
        signals.append("强度课之间间隔不足")

    for item in activities:
        if item.training_type != "长距离":
            continue
        next_day = item.activity_date + timedelta(days=1)
        if dates_by_type.get(next_day) in MEDIUM_HIGH_TYPES:
            signals.append("长距离后恢复不足")
            break

    if (previous_delta_pct is not None and previous_delta_pct > 15) or (
        recent_delta_pct is not None and recent_delta_pct > 20
    ):
        signals.append("周跑量突增")

    rest_day_date = _rest_day_date(activities[0].activity_date, structure.rest_day)
    if any(item.activity_date == rest_day_date for item in activities):
        signals.append("周一没有全休" if structure.rest_day == "monday" else "固定休息日没有全休")

    if high_ratio > 0.25:
        signals.append("高强度占比过高")

    return signals or ["未发现明显风险信号"]


def _rest_day_date(any_week_date: date, rest_day: str) -> date:
    monday = any_week_date - timedelta(days=any_week_date.weekday())
    offsets = {
        "monday": 0,
        "tuesday": 1,
        "wednesday": 2,
        "thursday": 3,
        "friday": 4,
        "saturday": 5,
        "sunday": 6,
    }
    return monday + timedelta(days=offsets.get(rest_day.lower(), 0))


def _conclusion(
    total_distance: float,
    high_count: int,
    long_run_distance: float,
    risks: list[str],
    context: WeeklyContext,
) -> str:
    if not context.activities:
        return "减量周"
    if any(signal in risks for signal in ("周跑量突增", "高强度占比过高", "存在连续中高强度训练")):
        return "过载风险"
    if any(signal in risks for signal in ("强度课之间间隔不足", "长距离后恢复不足", "周一没有全休")):
        return "恢复不足"
    if total_distance < context.structure.normal_volume_min_km * 0.65:
        return "减量周"
    if high_count >= 1 and long_run_distance > 0:
        return "有效刺激"
    return "稳定积累"


def _next_week_advice(
    conclusion: str,
    risks: list[str],
    structure: WeeklyTrainingStructure,
) -> NextWeekAdvice:
    if conclusion in {"过载风险", "恢复不足"}:
        direction = "减量或恢复"
        tuesday = "取消或降级为轻松跑，优先恢复。"
        friday = "只做短稳态或改为 E 跑，不追配速。"
        weekend = "长距离缩短 20-30%，跑完仍需有余量。"
    elif conclusion == "减量周":
        direction = "谨慎加量"
        tuesday = "可安排小剂量强度，但不要一次补回缺失训练。"
        friday = "安排可控稳态，时间短于常规。"
        weekend = "恢复正常长距离，但不叠加强度。"
    elif conclusion == "有效刺激":
        direction = "维持"
        tuesday = "保留强度日，但控制主训练总量。"
        friday = "保留稳态日，避免跑进阈值以上。"
        weekend = "安排长距离，后半程心率优先。"
    else:
        direction = "维持或小幅加量"
        tuesday = "按计划做强度，但先看周一恢复质量。"
        friday = "安排稳态或马配桥梁，保持可控。"
        weekend = "安排长距离，距离不超过近期上限。"

    if "周一没有全休" in risks or structure.rest_day == "monday":
        must_rest = True
    else:
        must_rest = False
    return NextWeekAdvice(
        direction=direction,
        tuesday_quality=tuesday,
        friday_steady=friday,
        weekend_long_run=weekend,
        must_rest_monday=must_rest,
    )


def _prohibited(conclusion: str, risks: list[str]) -> list[str]:
    prohibited = ["不要为了补跑量临时加双强度。"]
    if conclusion in {"过载风险", "恢复不足"}:
        prohibited.append("不要在恢复不足时继续叠加阈值或间歇。")
    if "周跑量突增" in risks:
        prohibited.append("不要继续增加周跑量。")
    if "高强度占比过高" in risks:
        prohibited.append("不要把稳态跑跑成第二次强度课。")
    if "周一没有全休" in risks:
        prohibited.append("不要取消周一固定全休。")
    return prohibited


def _key_workouts(activities: list[WeeklyActivity]) -> list[KeyWorkout]:
    scored: list[tuple[int, WeeklyActivity]] = []
    for item in activities:
        priority = 0
        if item.training_type in HIGH_INTENSITY_TYPES:
            priority = 40
        elif item.training_type == "长距离":
            priority = 35
        elif item.training_type in STEADY_TYPES:
            priority = 25
        elif item.distance_km >= 12:
            priority = 15
        if priority:
            scored.append((priority + int(item.distance_km), item))
    selected = [item for _priority, item in sorted(scored, key=lambda pair: pair[0], reverse=True)[:3]]
    return [KeyWorkout(activity=item, comment=_key_workout_comment(item)) for item in selected]


def _key_workout_comment(activity: WeeklyActivity) -> str:
    if activity.training_type in HIGH_INTENSITY_TYPES:
        return "本周主要质量刺激，重点看恢复间隔是否足够。"
    if activity.training_type == "长距离":
        return "本周耐力支柱，关注后半程心率和补给。"
    if activity.training_type in STEADY_TYPES:
        return "提供稳定有氧刺激，但不能跑成阈值课。"
    return "有氧积累训练，主要价值是稳定完成。"
