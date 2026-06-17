from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta

from garmin_runner.analysis.weekly import (
    EASY_TYPES,
    HIGH_INTENSITY_TYPES,
    LONG_RUN_TYPES,
    STEADY_TYPES,
    IntensityBucket,
    WeeklyActivity,
    WeeklyTrainingStructure,
)


@dataclass(frozen=True)
class MonthlyContext:
    month_start: date
    month_end: date
    activities: list[WeeklyActivity]
    structure: WeeklyTrainingStructure


@dataclass(frozen=True)
class MonthlyWeekSummary:
    label: str
    week_start: date
    week_end: date
    distance_km: float
    duration_s: float
    high_intensity_count: int
    is_deload: bool
    is_overload: bool


@dataclass(frozen=True)
class MonthlyIntensity:
    easy: IntensityBucket
    steady: IntensityBucket
    high_intensity: IntensityBucket
    long_run: IntensityBucket
    auxiliary: IntensityBucket
    easy_ratio: float
    steady_ratio: float
    high_intensity_ratio: float
    long_run_ratio: float


@dataclass(frozen=True)
class MonthlyTrends:
    same_hr_pace: str
    maf_low_hr: str
    steady: str
    threshold_completion: str
    long_run_stability: str


@dataclass(frozen=True)
class MonthlyFatigue:
    volume_trend: str
    intensity_stack: str
    rest: str
    consecutive_overload: str


@dataclass(frozen=True)
class MonthlyAdvice:
    volume: str
    intensity: str
    long_run: str
    steady_threshold: str
    recovery: str
    prohibited: list[str]


@dataclass(frozen=True)
class MonthlyAnalysis:
    month_start: date
    month_end: date
    year: int
    month: int
    conclusion: str
    total_distance_km: float
    total_duration_s: float
    running_days: int
    rest_days: int
    weekly_distribution: list[MonthlyWeekSummary]
    has_deload_week: bool
    count_25k_plus: int
    count_30k_plus: int
    intensity: MonthlyIntensity
    high_intensity_count: int
    trends: MonthlyTrends
    fatigue: MonthlyFatigue
    goal_meaning: str
    next_month: MonthlyAdvice
    structure: WeeklyTrainingStructure


def analyze_month(context: MonthlyContext) -> MonthlyAnalysis:
    activities = sorted(context.activities, key=lambda item: item.activity_date)
    total_distance = round(sum(item.distance_km for item in activities), 2)
    total_duration = sum(item.duration_s for item in activities)
    running_days = len({item.activity_date for item in activities})
    month_days = (context.month_end - context.month_start).days + 1
    rest_days = max(0, month_days - running_days)
    weekly_distribution = _weekly_distribution(context, activities)
    has_deload_week = any(item.is_deload for item in weekly_distribution)
    intensity = _monthly_intensity(activities)
    high_count = sum(1 for item in activities if item.training_type in HIGH_INTENSITY_TYPES)
    trends = _monthly_trends(activities)
    fatigue = _monthly_fatigue(context, weekly_distribution, activities, intensity)
    conclusion = _monthly_conclusion(
        activities=activities,
        total_distance=total_distance,
        has_deload_week=has_deload_week,
        fatigue=fatigue,
        intensity=intensity,
    )
    advice = _monthly_advice(conclusion, fatigue, context.structure)
    return MonthlyAnalysis(
        month_start=context.month_start,
        month_end=context.month_end,
        year=context.month_start.year,
        month=context.month_start.month,
        conclusion=conclusion,
        total_distance_km=total_distance,
        total_duration_s=total_duration,
        running_days=running_days,
        rest_days=rest_days,
        weekly_distribution=weekly_distribution,
        has_deload_week=has_deload_week,
        count_25k_plus=sum(1 for item in activities if item.distance_km >= 25),
        count_30k_plus=sum(1 for item in activities if item.distance_km >= 30),
        intensity=intensity,
        high_intensity_count=high_count,
        trends=trends,
        fatigue=fatigue,
        goal_meaning=_goal_meaning(conclusion, intensity, weekly_distribution, context.structure),
        next_month=advice,
        structure=context.structure,
    )


def _weekly_distribution(
    context: MonthlyContext,
    activities: list[WeeklyActivity],
) -> list[MonthlyWeekSummary]:
    summaries: list[MonthlyWeekSummary] = []
    start = context.month_start - timedelta(days=context.month_start.weekday())
    while start <= context.month_end:
        end = start + timedelta(days=6)
        in_week = [
            item
            for item in activities
            if max(context.month_start, start) <= item.activity_date <= min(context.month_end, end)
        ]
        distance = round(sum(item.distance_km for item in in_week), 2)
        duration = sum(item.duration_s for item in in_week)
        high_count = sum(1 for item in in_week if item.training_type in HIGH_INTENSITY_TYPES)
        is_deload = bool(in_week) and distance < context.structure.normal_volume_min_km * 0.65
        is_overload = (
            distance > context.structure.normal_volume_max_km * 1.1 or high_count >= 3
        )
        iso = start.isocalendar()
        summaries.append(
            MonthlyWeekSummary(
                label=f"{iso.year}-W{iso.week:02d}",
                week_start=max(context.month_start, start),
                week_end=min(context.month_end, end),
                distance_km=distance,
                duration_s=duration,
                high_intensity_count=high_count,
                is_deload=is_deload,
                is_overload=is_overload,
            )
        )
        start += timedelta(days=7)
    return summaries


def _monthly_intensity(activities: list[WeeklyActivity]) -> MonthlyIntensity:
    total_duration = sum(item.duration_s for item in activities)
    easy = _bucket(activities, EASY_TYPES)
    steady = _bucket(activities, STEADY_TYPES)
    high = _bucket(activities, HIGH_INTENSITY_TYPES)
    long_run = _bucket(activities, LONG_RUN_TYPES)
    grouped = EASY_TYPES | STEADY_TYPES | HIGH_INTENSITY_TYPES | LONG_RUN_TYPES
    auxiliary = _auxiliary_bucket(activities, grouped)
    return MonthlyIntensity(
        easy=easy,
        steady=steady,
        high_intensity=high,
        long_run=long_run,
        auxiliary=auxiliary,
        easy_ratio=_ratio(easy.duration_s, total_duration),
        steady_ratio=_ratio(steady.duration_s, total_duration),
        high_intensity_ratio=_ratio(high.duration_s, total_duration),
        long_run_ratio=_ratio(long_run.duration_s, total_duration),
    )


def _bucket(
    activities: list[WeeklyActivity],
    training_types: set[str],
) -> IntensityBucket:
    selected = [item for item in activities if item.training_type in training_types]
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
    return IntensityBucket(round(distance, 2), duration)


def _monthly_trends(activities: list[WeeklyActivity]) -> MonthlyTrends:
    low_hr = [
        item
        for item in activities
        if item.training_type in EASY_TYPES and item.average_hr is not None and item.average_hr <= 145
    ]
    steady = [item for item in activities if item.training_type in STEADY_TYPES]
    threshold = [item for item in activities if item.training_type in HIGH_INTENSITY_TYPES]
    long_runs = [item for item in activities if item.training_type in LONG_RUN_TYPES]
    return MonthlyTrends(
        same_hr_pace=_pace_trend(low_hr, require_similar_hr=True),
        maf_low_hr=_pace_trend(low_hr, require_similar_hr=False),
        steady=_pace_trend(steady, require_similar_hr=False),
        threshold_completion=_completion_trend(threshold, "阈值/间歇课"),
        long_run_stability=_long_run_stability(long_runs),
    )


def _pace_trend(
    activities: list[WeeklyActivity],
    require_similar_hr: bool,
) -> str:
    if len(activities) < 2:
        return "数据不足"
    first = activities[: max(1, len(activities) // 2)]
    second = activities[max(1, len(activities) // 2) :]
    if require_similar_hr:
        first_hr = _avg([item.average_hr for item in first])
        second_hr = _avg([item.average_hr for item in second])
        if first_hr is None or second_hr is None or abs(first_hr - second_hr) > 5:
            return "心率区间不够接近，暂不判断"
    first_pace = _avg_pace(first)
    second_pace = _avg_pace(second)
    if first_pace is None or second_pace is None:
        return "数据不足"
    delta = (second_pace - first_pace) / first_pace
    if delta <= -0.03:
        return "改善"
    if delta >= 0.03:
        return "下降"
    return "稳定"


def _completion_trend(activities: list[WeeklyActivity], label: str) -> str:
    if not activities:
        return f"{label}不足"
    good = sum(1 for item in activities if item.execution_score >= 80)
    if good == len(activities):
        return f"{label}完成较好"
    if good / len(activities) >= 0.6:
        return f"{label}基本完成"
    return f"{label}完成质量不足"


def _long_run_stability(activities: list[WeeklyActivity]) -> str:
    if not activities:
        return "长距离不足"
    good = sum(1 for item in activities if item.execution_score >= 80)
    if good == len(activities):
        return "长距离后段稳定"
    if good / len(activities) >= 0.6:
        return "长距离后段基本稳定"
    return "长距离后段稳定性不足"


def _monthly_fatigue(
    context: MonthlyContext,
    weeks: list[MonthlyWeekSummary],
    activities: list[WeeklyActivity],
    intensity: MonthlyIntensity,
) -> MonthlyFatigue:
    non_empty = [week for week in weeks if week.distance_km > 0]
    distances = [week.distance_km for week in non_empty]
    increasing = len(distances) >= 3 and all(
        current > previous for previous, current in zip(distances, distances[1:])
    )
    overload_streak = _longest_streak([week.is_overload for week in non_empty])
    high_count = sum(1 for item in activities if item.training_type in HIGH_INTENSITY_TYPES)
    month_days = (context.month_end - context.month_start).days + 1
    running_days = len({item.activity_date for item in activities})
    rest_days = max(0, month_days - running_days)
    return MonthlyFatigue(
        volume_trend=(
            "本月没有跑步记录"
            if not activities
            else "跑量持续升高"
            if increasing
            else "跑量未持续升高"
        ),
        intensity_stack=(
            "强度堆积"
            if high_count >= 4 or intensity.high_intensity_ratio > 0.22
            else "强度分布可控"
        ),
        rest="休息不足" if rest_days < max(4, month_days // 5) else "休息基本够用",
        consecutive_overload=(
            f"连续 {overload_streak} 周过载" if overload_streak >= 2 else "未出现连续多周过载"
        ),
    )


def _monthly_conclusion(
    activities: list[WeeklyActivity],
    total_distance: float,
    has_deload_week: bool,
    fatigue: MonthlyFatigue,
    intensity: MonthlyIntensity,
) -> str:
    if any(item.training_type == "比赛" for item in activities):
        return "比赛月"
    if "强度堆积" in fatigue.intensity_stack or fatigue.consecutive_overload.startswith("连续"):
        return "过载风险"
    if intensity.high_intensity.distance_km > 0 and intensity.long_run.distance_km > 0:
        return "专项推进"
    if not activities or (has_deload_week and total_distance < 220):
        return "恢复调整"
    return "基础积累"


def _monthly_advice(
    conclusion: str,
    fatigue: MonthlyFatigue,
    structure: WeeklyTrainingStructure,
) -> MonthlyAdvice:
    if conclusion == "过载风险":
        volume = "下月先降到常态周跑量下沿或再低 10%。"
        intensity = "最多保留一次阈值/间歇，其他质量日降级。"
        recovery = "优先补足周一全休和强度后恢复。"
    elif conclusion == "恢复调整":
        volume = "下月谨慎回到常态周跑量下沿。"
        intensity = "先用短稳态恢复节奏，再考虑阈值课。"
        recovery = "保持固定全休，不要急着补课。"
    else:
        volume = f"围绕常态周跑量 {structure.normal_volume_min_km:.0f}-{structure.normal_volume_max_km:.0f} km 推进。"
        intensity = "保留周二强度，但控制主训练总量。"
        recovery = "周一保持全休，质量课后至少一天低强度。"
    prohibited = ["不要用连续强度课补月跑量。"]
    if conclusion == "过载风险":
        prohibited.append("不要在疲劳未消退时增加长距离或阈值总量。")
    if "休息不足" in fatigue.rest:
        prohibited.append("不要取消固定休息日。")
    return MonthlyAdvice(
        volume=volume,
        intensity=intensity,
        long_run="周末长距离继续服务马拉松目标，后半程稳定性优先。",
        steady_threshold="周五稳态和周二阈值不要同时加码。",
        recovery=recovery,
        prohibited=prohibited,
    )


def _goal_meaning(
    conclusion: str,
    intensity: MonthlyIntensity,
    weeks: list[MonthlyWeekSummary],
    structure: WeeklyTrainingStructure,
) -> str:
    if conclusion == "过载风险":
        return f"当前刺激对 {structure.marathon_goal} 有帮助但恢复风险偏高，东营测试前需要控强度。"
    if conclusion == "比赛月":
        return f"本月以比赛兑现或测试为主，需要结合 {structure.b_race_note} 复盘。"
    if intensity.long_run.distance_km > 0 and intensity.high_intensity.distance_km > 0:
        return f"长距离和阈值/间歇都有覆盖，方向服务 {structure.marathon_goal}，也支持 {structure.b_race_note}。"
    if any(week.is_deload for week in weeks):
        return f"本月有调整周，对 {structure.marathon_goal} 是恢复窗口，下月再推进专项。"
    return f"本月偏基础积累，继续为 {structure.marathon_goal} 打底。"


def month_bounds(value: str, today: date | None = None) -> tuple[date, date]:
    if value == "current":
        current = today or date.today()
        start = date(current.year, current.month, 1)
    else:
        try:
            year_text, month_text = value.split("-", 1)
            start = date(int(year_text), int(month_text), 1)
        except (TypeError, ValueError) as exc:
            raise ValueError("--month 必须是 current 或 YYYY-MM，例如 2026-06。") from exc
    end = date(start.year, start.month, calendar.monthrange(start.year, start.month)[1])
    return start, end


def _avg_pace(activities: list[WeeklyActivity]) -> float | None:
    distance = sum(item.distance_km for item in activities)
    duration = sum(item.duration_s for item in activities)
    return duration / distance if distance else None


def _avg(values: list[float | None]) -> float | None:
    clean = [value for value in values if value is not None]
    return sum(clean) / len(clean) if clean else None


def _ratio(value: float, total: float) -> float:
    return round(value / total, 3) if total else 0.0


def _longest_streak(values: list[bool]) -> int:
    longest = 0
    current = 0
    for value in values:
        if value:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest
