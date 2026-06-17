from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
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
    key_workouts: list[KeyWorkout]
    risk_signals: list[str]
    next_week: NextWeekAdvice
    prohibited: list[str]
    structure: WeeklyTrainingStructure


def analyze_week(context: WeeklyContext) -> WeeklyAnalysis:
    activities = sorted(context.activities, key=lambda item: item.activity_date)
    total_distance = round(sum(item.distance_km for item in activities), 2)
    total_duration = sum(item.duration_s for item in activities)
    running_days = len({item.activity_date for item in activities})
    rest_days = max(0, 7 - running_days)
    longest = max((item.distance_km for item in activities), default=0.0)

    easy = _bucket(activities, EASY_TYPES)
    steady = _bucket(activities, STEADY_TYPES)
    high = _bucket(activities, HIGH_INTENSITY_TYPES)
    long_run = _bucket(activities, LONG_RUN_TYPES)
    grouped_types = EASY_TYPES | STEADY_TYPES | HIGH_INTENSITY_TYPES | LONG_RUN_TYPES
    auxiliary = _bucket(
        [item for item in activities if item.training_type not in grouped_types],
        None,
    )

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
        key_workouts=_key_workouts(activities),
        risk_signals=risks,
        next_week=next_week,
        prohibited=prohibited,
        structure=context.structure,
    )


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
        distance_km=round(sum(item.distance_km for item in selected), 2),
        duration_s=sum(item.duration_s for item in selected),
    )


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
