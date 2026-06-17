from __future__ import annotations

from datetime import date
from pathlib import Path

from typer.testing import CliRunner

from garmin_runner.analysis.monthly import MonthlyContext, analyze_month
from garmin_runner.cli import app
from garmin_runner.analysis.weekly import WeeklyActivity, WeeklyTrainingStructure
from garmin_runner.reporting.monthly import write_monthly_report


runner = CliRunner()


def test_monthly_report_generates_volume_structure_and_advice(tmp_path: Path) -> None:
    analysis = analyze_month(
        MonthlyContext(
            month_start=date(2026, 6, 1),
            month_end=date(2026, 6, 30),
            activities=[
                _activity("easy1", date(2026, 6, 2), 14, 5400, "E 跑", average_hr=136),
                _activity("easy2", date(2026, 6, 9), 14, 5200, "E 跑", average_hr=136),
                _activity("steady", date(2026, 6, 12), 16, 5400, "稳态跑"),
                _activity("quality", date(2026, 6, 16), 17, 5220, "阈值间歇", 11, 3000),
                _activity("long25", date(2026, 6, 21), 28, 9300, "长距离"),
                _activity("long30", date(2026, 6, 28), 31, 10800, "长距离"),
            ],
            structure=WeeklyTrainingStructure(),
        )
    )

    assert analysis.total_distance_km == 120
    assert analysis.running_days == 6
    assert analysis.rest_days == 24
    assert analysis.count_25k_plus == 2
    assert analysis.count_30k_plus == 1
    assert analysis.high_intensity_count == 1
    assert analysis.intensity.high_intensity.distance_km == 11
    assert analysis.trends.same_hr_pace == "改善"
    assert analysis.conclusion in {"基础积累", "专项推进"}

    path = write_monthly_report(analysis, tmp_path)
    assert path == tmp_path / "monthly" / "2026-06.md"
    content = path.read_text(encoding="utf-8")
    assert "## 月度总判断" in content
    assert "## 训练量" in content
    assert "## 强度结构" in content
    assert "## 能力趋势" in content
    assert "## 疲劳趋势" in content
    assert "## 对目标的意义" in content
    assert "## 下月建议" in content


def test_monthly_report_handles_empty_month() -> None:
    analysis = analyze_month(
        MonthlyContext(
            month_start=date(2026, 6, 1),
            month_end=date(2026, 6, 30),
            activities=[],
            structure=WeeklyTrainingStructure(),
        )
    )

    assert analysis.conclusion == "恢复调整"
    assert analysis.total_distance_km == 0
    assert analysis.rest_days == 30
    assert "本月没有跑步记录" in analysis.fatigue.volume_trend


def test_monthly_report_identifies_deload_week() -> None:
    analysis = analyze_month(
        MonthlyContext(
            month_start=date(2026, 6, 1),
            month_end=date(2026, 6, 30),
            activities=[
                _activity("w1", date(2026, 6, 2), 60, 21600, "E 跑"),
                _activity("w2", date(2026, 6, 9), 62, 22320, "E 跑"),
                _activity("w3", date(2026, 6, 16), 30, 10800, "E 跑"),
                _activity("w4", date(2026, 6, 23), 65, 23400, "E 跑"),
            ],
            structure=WeeklyTrainingStructure(normal_volume_min_km=80),
        )
    )

    assert analysis.has_deload_week is True
    assert any(item.is_deload for item in analysis.weekly_distribution)


def test_monthly_report_flags_overload_risk() -> None:
    analysis = analyze_month(
        MonthlyContext(
            month_start=date(2026, 6, 1),
            month_end=date(2026, 6, 30),
            activities=[
                _activity("q1", date(2026, 6, 2), 18, 5400, "阈值间歇"),
                _activity("q2", date(2026, 6, 4), 16, 4800, "间歇课"),
                _activity("q3", date(2026, 6, 9), 18, 5400, "阈值课"),
                _activity("q4", date(2026, 6, 11), 16, 4800, "间歇课"),
                _activity("q5", date(2026, 6, 16), 18, 5400, "阈值课"),
                _activity("q6", date(2026, 6, 18), 16, 4800, "间歇课"),
            ],
            structure=WeeklyTrainingStructure(normal_volume_max_km=80),
        )
    )

    assert analysis.conclusion == "过载风险"
    assert "强度堆积" in analysis.fatigue.intensity_stack


def test_monthly_report_calculates_training_trends() -> None:
    analysis = analyze_month(
        MonthlyContext(
            month_start=date(2026, 6, 1),
            month_end=date(2026, 6, 30),
            activities=[
                _activity("easy1", date(2026, 6, 3), 10, 3600, "E 跑", average_hr=136),
                _activity("easy2", date(2026, 6, 24), 10, 3300, "E 跑", average_hr=136),
                _activity("steady1", date(2026, 6, 10), 12, 3900, "稳态跑"),
                _activity("steady2", date(2026, 6, 26), 12, 3600, "稳态跑"),
                _activity("quality", date(2026, 6, 16), 16, 4800, "阈值间歇", 10, 2700, 88),
                _activity("long", date(2026, 6, 28), 28, 9300, "长距离", score=92),
            ],
            structure=WeeklyTrainingStructure(),
        )
    )

    assert analysis.trends.same_hr_pace == "改善"
    assert analysis.trends.maf_low_hr == "改善"
    assert analysis.trends.steady == "改善"
    assert "完成较好" in analysis.trends.threshold_completion
    assert "稳定" in analysis.trends.long_run_stability


def test_monthly_report_missing_config_is_clear(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "report",
            "monthly",
            "--month",
            "2026-06",
            "--config",
            str(tmp_path / "config" / "athlete.yaml"),
        ],
    )

    assert result.exit_code == 1
    assert "缺少本地配置文件" in result.output


def _activity(
    activity_id: str,
    activity_date: date,
    distance_km: float,
    duration_s: float,
    training_type: str,
    intensity_distance_km: float | None = None,
    intensity_duration_s: float | None = None,
    score: int = 90,
    average_hr: float | None = None,
) -> WeeklyActivity:
    return WeeklyActivity(
        activity_id=activity_id,
        activity_date=activity_date,
        activity_name="跑步",
        distance_km=distance_km,
        duration_s=duration_s,
        average_hr=average_hr,
        training_type=training_type,
        execution_score=score,
        report_path=Path(f"reports/daily/{activity_id}.md"),
        intensity_distance_km=intensity_distance_km,
        intensity_duration_s=intensity_duration_s,
    )
