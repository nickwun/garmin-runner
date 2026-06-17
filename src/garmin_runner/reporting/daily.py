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
    confidence_reasons = "\n".join(
        f"- {reason}" for reason in analysis.confidence.reasons
    ) or "- 无"
    not_applicable_notes = "\n".join(
        f"- {note}" for note in analysis.not_applicable_notes
    ) or "- 无"
    drift_line = (
        f"- 心率漂移：{analysis.heart_rate_drift.label}（{_format_float(analysis.heart_rate_drift.drift_pct, '%')}）"
        if analysis.heart_rate_drift.applicable
        else f"- 心率漂移：{analysis.heart_rate_drift.label}（{analysis.heart_rate_drift.reason or '不适用于本次训练'}）"
    )
    breakdown = _render_workout_breakdown(analysis)
    return f"""# {basic.activity_date.isoformat()} 单次训练报告

活动：{basic.activity_name or basic.activity_id}

## 数据面

- 距离：{_format_float(basic.distance_km, " km")}
- 时间：{_format_duration(basic.duration_s)}
- 移动时间：{_format_duration(basic.moving_duration_s)}
- 平均配速：{_format_pace(basic.average_pace_s_per_km)}
- 平均心率：{_format_float(basic.average_hr, " bpm")}
- 最大心率：{_format_float(basic.max_hr, " bpm")}
- 爬升：{_format_float(basic.elevation_gain_m, " m")}
- 步频：{_format_float(basic.average_cadence_spm, " spm")}
- 数据可信度：{analysis.confidence.level}

数据可信度说明：
{confidence_reasons}

## 生理面

- 训练类型：{analysis.training_type}
- 配速稳定性：{analysis.pace_stability.label}（CV {_format_float(analysis.pace_stability.cv_pct, "%")}）
- 后半程配速变化：{_format_float(analysis.pace_stability.late_slowdown_pct, "%")}
{drift_line}

{zone_lines}

{breakdown}

## 不适用指标说明

{not_applicable_notes}

## 执行打分

{analysis.execution_score} / 100

## 教练指令

### 明日训练建议

{analysis.guidance.tomorrow}

### 未来 48-72 小时建议

{analysis.guidance.next_48_72_hours}

### 禁止事项

{analysis.guidance.prohibited}

### 规则说明

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


def _render_workout_breakdown(analysis: SingleActivityAnalysis) -> str:
    breakdown = analysis.workout_breakdown
    if breakdown is None:
        return ""
    phases = [breakdown.warmup, breakdown.main, breakdown.quality, breakdown.cooldown]
    lines = [
        f"- {phase.name}：{phase.distance_km:.1f} km，{_format_duration(phase.duration_s)}，{_format_pace(phase.average_pace_s_per_km)}"
        for phase in phases
    ]
    return "## 分段拆解\n\n" + "\n".join(lines)
