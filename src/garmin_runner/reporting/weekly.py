from __future__ import annotations

from pathlib import Path

from garmin_runner.analysis.weekly import WeeklyAnalysis


def write_weekly_report(analysis: WeeklyAnalysis, reports_dir: Path) -> Path:
    output_dir = Path(reports_dir) / "weekly"
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{analysis.iso_year}-W{analysis.iso_week:02d}.md"
    path.write_text(render_weekly_report(analysis), encoding="utf-8")
    return path


def render_weekly_report(analysis: WeeklyAnalysis) -> str:
    key_workouts = "\n".join(
        "- "
        + f"{item.activity.activity_date.isoformat()} "
        + f"{item.activity.training_type} "
        + _format_key_workout_distance(item.activity)
        + "，"
        + f"{item.comment}"
        for item in analysis.key_workouts
    ) or "- 无关键训练"
    risks = "\n".join(f"- {signal}" for signal in analysis.risk_signals)
    prohibited = "\n".join(f"- {item}" for item in analysis.prohibited)
    return f"""# {analysis.iso_year}-W{analysis.iso_week:02d} 周训练报告

周期：{analysis.week_start.isoformat()} 至 {analysis.week_end.isoformat()}

## 本周结论

{analysis.conclusion}

目标背景：{analysis.structure.marathon_goal}；{analysis.structure.b_race_note}。

## 训练量

- 周跑量：{analysis.total_distance_km:.1f} km
- 周跑步时长：{_format_duration(analysis.total_duration_s)}
- 跑步天数：{analysis.running_days}
- 休息天数：{analysis.rest_days}
- 最长单次距离：{analysis.longest_distance_km:.1f} km
- 长距离占周跑量比例：{_format_pct(analysis.long_run_distance_ratio)}
- 与上周相比：{_format_delta(analysis.previous_week_delta_km, analysis.previous_week_delta_pct)}
- 与近 4 周均值相比：{_format_delta(analysis.recent_4w_delta_km, analysis.recent_4w_delta_pct)}

## 强度结构

- 恢复跑/MAF/E 跑：{analysis.easy.distance_km:.1f} km，{_format_duration(analysis.easy.duration_s)}
- 稳态跑：{analysis.steady.distance_km:.1f} km，{_format_duration(analysis.steady.duration_s)}
- 阈值/间歇主训练段：{analysis.high_intensity.distance_km:.1f} km，{_format_duration(analysis.high_intensity.duration_s)}
- 长距离：{analysis.long_run.distance_km:.1f} km，{_format_duration(analysis.long_run.duration_s)}
- 辅助/热身冷身：{analysis.auxiliary.distance_km:.1f} km，{_format_duration(analysis.auxiliary.duration_s)}
- 高强度次数：{analysis.high_intensity_count}
- 高强度时间占比：{_format_pct(analysis.high_intensity_time_ratio)}

## 关键训练

{key_workouts}

## 风险信号

{risks}

## 下周建议

- 方向：{analysis.next_week.direction}
- 周二强度建议：{analysis.next_week.tuesday_quality}
- 周五稳态建议：{analysis.next_week.friday_steady}
- 周末长距离建议：{analysis.next_week.weekend_long_run}
- 周一是否必须全休：{"是" if analysis.next_week.must_rest_monday else "否"}

## 禁止事项

{prohibited}
"""


def _format_duration(seconds: float) -> str:
    seconds = int(round(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _format_pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _format_delta(delta_km: float | None, delta_pct: float | None) -> str:
    if delta_km is None or delta_pct is None:
        return "N/A"
    sign = "+" if delta_km >= 0 else ""
    return f"{sign}{delta_km:.1f} km（{sign}{delta_pct:.1f}%）"


def _format_key_workout_distance(activity: object) -> str:
    intensity_distance = getattr(activity, "intensity_distance_km", None)
    if intensity_distance is not None:
        return f"总 {activity.distance_km:.1f} km / 主训练 {intensity_distance:.1f} km"
    return f"{activity.distance_km:.1f} km"
