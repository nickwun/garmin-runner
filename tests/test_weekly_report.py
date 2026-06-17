from __future__ import annotations

from datetime import date
from pathlib import Path

from typer.testing import CliRunner

from garmin_runner.analysis.weekly import (
    WeeklyActivity,
    WeeklyContext,
    WeeklyTrainingStructure,
    analyze_week,
)
from garmin_runner.cli import app
from garmin_runner.reporting.weekly import write_weekly_report


runner = CliRunner()


def test_weekly_report_calculates_volume_structure_and_risks(tmp_path: Path) -> None:
    analysis = analyze_week(
        WeeklyContext(
            week_start=date(2026, 6, 15),
            week_end=date(2026, 6, 21),
            activities=[
                _activity("easy", date(2026, 6, 16), 14, 5400, "E 跑"),
                _activity("quality", date(2026, 6, 17), 16, 4800, "阈值间歇"),
                _activity("steady", date(2026, 6, 19), 12, 3600, "稳态跑"),
                _activity("long", date(2026, 6, 21), 28, 9000, "长距离"),
            ],
            previous_week_distance_km=64,
            recent_4w_avg_distance_km=68,
            structure=WeeklyTrainingStructure(),
        )
    )

    assert analysis.total_distance_km == 70
    assert analysis.running_days == 4
    assert analysis.rest_days == 3
    assert analysis.long_run_distance_ratio == 0.4
    assert analysis.high_intensity_count == 1
    assert analysis.high_intensity_time_ratio > 0
    assert analysis.conclusion in {"有效刺激", "稳定积累"}
    assert len(analysis.key_workouts) == 3

    path = write_weekly_report(analysis, tmp_path)
    assert path == tmp_path / "weekly" / "2026-W25.md"
    content = path.read_text(encoding="utf-8")
    assert "## 本周结论" in content
    assert "## 训练量" in content
    assert "## 强度结构" in content
    assert "## 下周建议" in content
    assert "## 禁止事项" in content


def test_weekly_report_handles_empty_week() -> None:
    analysis = analyze_week(
        WeeklyContext(
            week_start=date(2026, 6, 15),
            week_end=date(2026, 6, 21),
            activities=[],
            previous_week_distance_km=80,
            recent_4w_avg_distance_km=75,
            structure=WeeklyTrainingStructure(),
        )
    )

    assert analysis.conclusion == "减量周"
    assert analysis.total_distance_km == 0
    assert analysis.running_days == 0
    assert any("本周没有跑步记录" in signal for signal in analysis.risk_signals)


def test_weekly_report_missing_config_is_clear(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "report",
            "weekly",
            "--week",
            "2026-W25",
            "--config",
            str(tmp_path / "config" / "athlete.yaml"),
        ],
    )

    assert result.exit_code == 1
    assert "缺少本地配置文件" in result.output


def test_weekly_report_flags_excessive_intensity() -> None:
    analysis = analyze_week(
        WeeklyContext(
            week_start=date(2026, 6, 15),
            week_end=date(2026, 6, 21),
            activities=[
                _activity("q1", date(2026, 6, 17), 16, 4800, "阈值间歇"),
                _activity("q2", date(2026, 6, 19), 14, 4200, "间歇课"),
                _activity("easy", date(2026, 6, 21), 10, 3600, "E 跑"),
            ],
            previous_week_distance_km=42,
            recent_4w_avg_distance_km=45,
            structure=WeeklyTrainingStructure(),
        )
    )

    assert analysis.conclusion == "过载风险"
    assert any("高强度占比过高" in signal for signal in analysis.risk_signals)


def test_weekly_report_flags_monday_not_resting() -> None:
    analysis = analyze_week(
        WeeklyContext(
            week_start=date(2026, 6, 15),
            week_end=date(2026, 6, 21),
            activities=[_activity("monday", date(2026, 6, 15), 8, 3000, "E 跑")],
            previous_week_distance_km=40,
            recent_4w_avg_distance_km=45,
            structure=WeeklyTrainingStructure(rest_day="monday"),
        )
    )

    assert any("周一没有全休" in signal for signal in analysis.risk_signals)
    assert analysis.next_week.must_rest_monday is True


def test_weekly_report_flags_mileage_surge() -> None:
    analysis = analyze_week(
        WeeklyContext(
            week_start=date(2026, 6, 15),
            week_end=date(2026, 6, 21),
            activities=[
                _activity("a", date(2026, 6, 16), 20, 7200, "E 跑"),
                _activity("b", date(2026, 6, 18), 20, 7200, "E 跑"),
                _activity("c", date(2026, 6, 21), 25, 9000, "长距离"),
            ],
            previous_week_distance_km=40,
            recent_4w_avg_distance_km=42,
            structure=WeeklyTrainingStructure(),
        )
    )

    assert analysis.conclusion == "过载风险"
    assert any("周跑量突增" in signal for signal in analysis.risk_signals)


def _activity(
    activity_id: str,
    activity_date: date,
    distance_km: float,
    duration_s: float,
    training_type: str,
) -> WeeklyActivity:
    return WeeklyActivity(
        activity_id=activity_id,
        activity_date=activity_date,
        activity_name="跑步",
        distance_km=distance_km,
        duration_s=duration_s,
        average_hr=None,
        training_type=training_type,
        execution_score=90,
        report_path=Path(f"reports/daily/{activity_id}.md"),
    )
