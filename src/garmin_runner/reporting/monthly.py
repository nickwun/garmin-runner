from __future__ import annotations

from pathlib import Path

from garmin_runner.analysis.monthly import MonthlyAnalysis


def write_monthly_report(analysis: MonthlyAnalysis, reports_dir: Path) -> Path:
    output_dir = Path(reports_dir) / "monthly"
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{analysis.year}-{analysis.month:02d}.md"
    path.write_text(render_monthly_report(analysis), encoding="utf-8")
    return path


def render_monthly_report(analysis: MonthlyAnalysis) -> str:
    weeks = "\n".join(
        "- "
        + f"{week.label}（{week.week_start.isoformat()} 至 {week.week_end.isoformat()}）："
        + f"{week.distance_km:.1f} km，{_format_duration(week.duration_s)}，"
        + f"高强度 {week.high_intensity_count} 次，"
        + ("减量周" if week.is_deload else "过载周" if week.is_overload else "常规周")
        for week in analysis.weekly_distribution
    )
    prohibited = "\n".join(f"- {item}" for item in analysis.next_month.prohibited)
    return f"""# {analysis.year}-{analysis.month:02d} 月训练报告

周期：{analysis.month_start.isoformat()} 至 {analysis.month_end.isoformat()}

## 月度总判断

{analysis.conclusion}

目标背景：{analysis.structure.marathon_goal}；{analysis.structure.b_race_note}。

## 训练量

- 月跑量：{analysis.total_distance_km:.1f} km
- 月跑步时长：{_format_duration(analysis.total_duration_s)}
- 跑步天数：{analysis.running_days}
- 休息天数：{analysis.rest_days}
- 周跑量分布：
{weeks or "- 无跑步记录"}
- 是否有减量周：{"是" if analysis.has_deload_week else "否"}
- 25km+ 次数：{analysis.count_25k_plus}
- 30km+ 次数：{analysis.count_30k_plus}

## 强度结构

- E/MAF 跑：{analysis.intensity.easy.distance_km:.1f} km，{_format_duration(analysis.intensity.easy.duration_s)}，{_format_pct(analysis.intensity.easy_ratio)}
- 稳态跑：{analysis.intensity.steady.distance_km:.1f} km，{_format_duration(analysis.intensity.steady.duration_s)}，{_format_pct(analysis.intensity.steady_ratio)}
- 阈值/间歇主训练段：{analysis.intensity.high_intensity.distance_km:.1f} km，{_format_duration(analysis.intensity.high_intensity.duration_s)}，{_format_pct(analysis.intensity.high_intensity_ratio)}
- 长距离：{analysis.intensity.long_run.distance_km:.1f} km，{_format_duration(analysis.intensity.long_run.duration_s)}，{_format_pct(analysis.intensity.long_run_ratio)}
- 辅助/热身冷身：{analysis.intensity.auxiliary.distance_km:.1f} km，{_format_duration(analysis.intensity.auxiliary.duration_s)}
- 高强度次数：{analysis.high_intensity_count}

## 能力趋势

- 同心率配速是否改善：{analysis.trends.same_hr_pace}
- MAF 或低心率表现趋势：{analysis.trends.maf_low_hr}
- 稳态跑表现趋势：{analysis.trends.steady}
- 阈值课完成情况：{analysis.trends.threshold_completion}
- 长距离后段稳定性：{analysis.trends.long_run_stability}

## 疲劳趋势

- 跑量是否持续升高：{analysis.fatigue.volume_trend}
- 强度是否堆积：{analysis.fatigue.intensity_stack}
- 是否休息不足：{analysis.fatigue.rest}
- 是否存在连续多周过载：{analysis.fatigue.consecutive_overload}

## 对目标的意义

{analysis.goal_meaning}

## 下月建议

- 跑量建议：{analysis.next_month.volume}
- 强度建议：{analysis.next_month.intensity}
- 长距离建议：{analysis.next_month.long_run}
- 稳态/阈值建议：{analysis.next_month.steady_threshold}
- 恢复建议：{analysis.next_month.recovery}

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
