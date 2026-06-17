from __future__ import annotations

from pathlib import Path

from garmin_runner.analysis.single_activity import SingleActivityAnalysis


ZONE_LABELS = {
    "below_range": "低于训练区间",
    "very_easy": "恢复跑 / Very Easy",
    "easy": "轻松跑 / E 跑",
    "aerobic": "中长有氧 / 稍稳有氧",
    "steady": "稳态跑 / Steady",
    "mp_bridge": "马配桥梁 / MP Bridge",
    "threshold": "阈值跑 / Tempo / T",
    "vo2": "10km / 5km 强度",
    "sprint": "冲刺 / 极限末段",
}


def write_daily_report(analysis: SingleActivityAnalysis, reports_dir: Path) -> Path:
    activity_date = analysis.basic.activity_date.isoformat()
    activity_id = analysis.basic.activity_id
    output_dir = Path(reports_dir) / "daily"
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{activity_date}_{activity_id}.md"
    path.write_text(render_daily_report(analysis), encoding="utf-8")
    return path


def render_daily_report(analysis: SingleActivityAnalysis) -> str:
    basic = analysis.basic
    zone_lines = "\n".join(
        f"- {ZONE_LABELS[key]}：{_format_duration(seconds)}"
        for key, seconds in analysis.hr_zones.seconds_by_zone.items()
    )
    return f"""# {basic.activity_date.isoformat()} 单次训练报告

活动：{basic.activity_name or basic.activity_id}

## 数据面

- 距离：{_format_float(basic.distance_km, " km")}
- 时间：{_format_duration(basic.duration_s)}
- 平均配速：{_format_pace(basic.average_pace_s_per_km)}
- 平均心率：{_format_float(basic.average_hr, " bpm")}
- 最大心率：{_format_float(basic.max_hr, " bpm")}
- 爬升：{_format_float(basic.elevation_gain_m, " m")}
- 步频：{_format_float(basic.average_cadence_spm, " spm")}

## 生理面

- 训练类型：{analysis.training_type}
- 配速稳定性：{analysis.pace_stability.label}（CV {_format_float(analysis.pace_stability.cv_pct, "%")}）
- 心率漂移：{analysis.heart_rate_drift.label}（{_format_float(analysis.heart_rate_drift.drift_pct, "%")}）

{zone_lines}

## 执行打分

{analysis.execution_score} / 100

## 教练指令

{analysis.coach_instruction}
"""


def _format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "N/A"
    seconds = int(round(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _format_pace(seconds_per_km: float | None) -> str:
    if seconds_per_km is None:
        return "N/A"
    seconds = int(round(seconds_per_km))
    minutes, secs = divmod(seconds, 60)
    return f"{minutes}:{secs:02d} /km"


def _format_float(value: float | None, suffix: str) -> str:
    if value is None:
        return "N/A"
    return f"{value:.1f}{suffix}"
