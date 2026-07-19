from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

import garmin_runner.cli as cli
from garmin_runner.analysis.single_activity import WorkoutBreakdown, WorkoutPhase
from garmin_runner.analysis.weekly import (
    WeeklyActivity,
    WeeklyContext,
    WeeklyTrainingStructure,
    WeeklyWorkoutPhase,
    WeeklyWorkoutPhaseRole,
    analyze_week,
)
from garmin_runner.cli import app
from garmin_runner.reporting.weekly import write_weekly_report


runner = CliRunner()


def test_weekly_activity_conversion_preserves_start_time_and_non_overlapping_phases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    summary_path = tmp_path / "summary.json"
    fit_path = tmp_path / "activity.fit"
    summary_path.write_text("{}", encoding="utf-8")
    fit_path.write_bytes(b"fit")
    breakdown = WorkoutBreakdown(
        warmup=WorkoutPhase("热身", 3.0, 1200.0, 400.0, 130.0),
        main=WorkoutPhase("阈值间歇训练", 8.0, 2400.0, 300.0, 170.0),
        cooldown=WorkoutPhase("冷身", 4.0, 1800.0, 450.0, 135.0),
        quality=WorkoutPhase("重叠质量段", 6.0, 1800.0, 300.0, 172.0),
    )
    analysis = SimpleNamespace(
        basic=SimpleNamespace(
            activity_id="123",
            activity_date=date(2026, 6, 19),
            activity_name="阈值训练",
            distance_km=15.0,
            duration_s=5400.0,
            average_hr=150.0,
        ),
        training_type="阈值间歇",
        execution_score=88,
        workout_breakdown=breakdown,
    )
    report_path = tmp_path / "reports" / "daily" / "2026-06-19_123.md"
    monkeypatch.setattr(cli, "decode_fit_messages", lambda path: ({}, []))
    monkeypatch.setattr(cli, "extract_time_series", lambda messages: [])
    monkeypatch.setattr(cli, "analyze_activity", lambda summary, points, config: analysis)
    monkeypatch.setattr(cli, "write_daily_report", lambda result, reports_dir: report_path)

    converted = cli._weekly_activity_from_row(
        {
            "activity_id": "123",
            "start_time_local": "2026-06-19T06:10:00",
            "summary_path": str(summary_path),
            "fit_path": str(fit_path),
        },
        tmp_path / "reports",
        object(),
    )

    assert converted.start_time_local == datetime(2026, 6, 19, 6, 10)
    assert converted.workout_phases == (
        WeeklyWorkoutPhase(WeeklyWorkoutPhaseRole.WARMUP, "热身", 3.0, 1200.0),
        WeeklyWorkoutPhase(WeeklyWorkoutPhaseRole.MAIN, "阈值间歇训练", 8.0, 2400.0),
        WeeklyWorkoutPhase(WeeklyWorkoutPhaseRole.COOLDOWN, "冷身", 4.0, 1800.0),
    )
    assert converted.intensity_distance_km == 8.0
    assert converted.intensity_duration_s == 2400.0


def test_weekly_workout_phase_conversion_omits_non_finite_zero_and_missing_values() -> None:
    breakdown = WorkoutBreakdown(
        warmup=WorkoutPhase("热身", float("nan"), 1200.0, None),
        main=WorkoutPhase("主训练", 0.0, 2400.0, None),
        cooldown=WorkoutPhase("冷身", 4.0, None, None),  # type: ignore[arg-type]
        quality=WorkoutPhase("重叠质量段", 6.0, 1800.0, None),
    )

    assert cli._weekly_workout_phases(breakdown) == ()


@pytest.mark.parametrize("start_time_local", ["   ", "not-a-timestamp"])
def test_weekly_activity_start_time_tolerates_invalid_optional_values(
    start_time_local: str,
) -> None:
    assert cli._activity_start_time({"start_time_local": start_time_local}) is None


def test_weekly_analysis_builds_seven_daily_summaries() -> None:
    analysis = analyze_week(
        WeeklyContext(
            week_start=date(2026, 6, 15),
            week_end=date(2026, 6, 21),
            activities=[
                _activity(
                    "easy",
                    date(2026, 6, 16),
                    12,
                    3600,
                    "E 跑",
                    average_hr=134,
                )
            ],
            previous_week_distance_km=80,
            recent_4w_avg_distance_km=75,
            structure=WeeklyTrainingStructure(),
        )
    )

    assert len(analysis.daily_summaries) == 7
    summary = analysis.daily_summaries[1]
    assert summary.activity_date == date(2026, 6, 16)
    assert summary.training_type == "E 跑"
    assert summary.total_distance_km == 12
    assert summary.total_duration_s == 3600
    assert summary.combined_pace_s_per_km == 300
    assert summary.average_hr == 134
    assert summary.is_rest_day is False
    assert analysis.daily_summaries[0].is_rest_day is True


def test_weekly_analysis_groups_same_day_warmup_main_and_cooldown() -> None:
    analysis = analyze_week(
        WeeklyContext(
            week_start=date(2026, 6, 15),
            week_end=date(2026, 6, 21),
            activities=[
                _activity(
                    "warmup",
                    date(2026, 6, 16),
                    3,
                    1200,
                    "热身/冷身",
                    start_time_local=datetime(2026, 6, 16, 6, 0),
                ),
                _activity(
                    "steady",
                    date(2026, 6, 16),
                    7,
                    2100,
                    "稳态跑",
                    start_time_local=datetime(2026, 6, 16, 6, 25),
                ),
                _activity(
                    "cooldown",
                    date(2026, 6, 16),
                    5,
                    1800,
                    "热身/冷身",
                    start_time_local=datetime(2026, 6, 16, 7, 5),
                ),
            ],
            previous_week_distance_km=80,
            recent_4w_avg_distance_km=75,
            structure=WeeklyTrainingStructure(),
        )
    )

    summary = analysis.daily_summaries[1]
    assert summary.training_type == "稳态跑（含热身冷身）"
    assert [item.label for item in summary.composition] == ["热身", "稳态跑", "冷身"]
    assert summary.total_distance_km == 15
    assert summary.combined_pace_s_per_km == 340


def test_weekly_analysis_weights_heart_rate_and_ignores_missing_values() -> None:
    analysis = analyze_week(
        WeeklyContext(
            week_start=date(2026, 6, 15),
            week_end=date(2026, 6, 21),
            activities=[
                _activity("a", date(2026, 6, 16), 4, 1200, "E 跑", average_hr=120),
                _activity("b", date(2026, 6, 16), 2, 600, "E 跑"),
                _activity("c", date(2026, 6, 16), 8, 2400, "E 跑", average_hr=150),
            ],
            previous_week_distance_km=80,
            recent_4w_avg_distance_km=75,
            structure=WeeklyTrainingStructure(),
        )
    )

    assert analysis.daily_summaries[1].average_hr == 140


def test_weekly_analysis_returns_none_for_all_missing_heart_rate() -> None:
    analysis = analyze_week(
        WeeklyContext(
            week_start=date(2026, 6, 15),
            week_end=date(2026, 6, 21),
            activities=[_activity("easy", date(2026, 6, 16), 10, 3000, "E 跑")],
            previous_week_distance_km=80,
            recent_4w_avg_distance_km=75,
            structure=WeeklyTrainingStructure(),
        )
    )

    assert analysis.daily_summaries[1].average_hr is None


def test_weekly_analysis_custom_range_uses_inclusive_day_count() -> None:
    analysis = analyze_week(
        WeeklyContext(
            week_start=date(2026, 6, 15),
            week_end=date(2026, 6, 17),
            activities=[_activity("easy", date(2026, 6, 16), 10, 3000, "E 跑")],
            previous_week_distance_km=80,
            recent_4w_avg_distance_km=75,
            structure=WeeklyTrainingStructure(),
        )
    )

    assert len(analysis.daily_summaries) == 3
    assert analysis.rest_days == 2


def test_weekly_analysis_keeps_labels_for_auxiliary_only_day() -> None:
    analysis = analyze_week(
        WeeklyContext(
            week_start=date(2026, 6, 15),
            week_end=date(2026, 6, 21),
            activities=[
                _activity("aux-1", date(2026, 6, 16), 3, 1200, "热身/冷身"),
                _activity("aux-2", date(2026, 6, 16), 4, 1500, "热身/冷身"),
            ],
            previous_week_distance_km=80,
            recent_4w_avg_distance_km=75,
            structure=WeeklyTrainingStructure(),
        )
    )

    assert [item.label for item in analysis.daily_summaries[1].composition] == [
        "热身/冷身",
        "热身/冷身",
    ]


def test_weekly_analysis_expands_only_exclusive_structured_phases() -> None:
    phases = (
        WeeklyWorkoutPhase(WeeklyWorkoutPhaseRole.WARMUP, "热身", 3, 1200),
        WeeklyWorkoutPhase(WeeklyWorkoutPhaseRole.MAIN, "2 公里阈值间歇", 8, 2400),
        WeeklyWorkoutPhase(WeeklyWorkoutPhaseRole.COOLDOWN, "冷身", 4, 1500),
    )
    analysis = analyze_week(
        WeeklyContext(
            week_start=date(2026, 6, 15),
            week_end=date(2026, 6, 21),
            activities=[
                _activity(
                    "structured",
                    date(2026, 6, 16),
                    15,
                    5100,
                    "阈值间歇",
                    workout_phases=phases,
                )
            ],
            previous_week_distance_km=80,
            recent_4w_avg_distance_km=75,
            structure=WeeklyTrainingStructure(),
        )
    )

    assert [
        (item.label, item.distance_km)
        for item in analysis.daily_summaries[1].composition
    ] == [("热身", 3), ("2 公里阈值间歇", 8), ("冷身", 4)]

    with pytest.raises(ValueError, match="互斥阶段"):
        WeeklyWorkoutPhase("quality", "重叠质量段", 8, 2400)  # type: ignore[arg-type]


def test_weekly_analysis_combined_pace_uses_unrounded_distance() -> None:
    analysis = analyze_week(
        WeeklyContext(
            week_start=date(2026, 6, 15),
            week_end=date(2026, 6, 21),
            activities=[
                _activity("a", date(2026, 6, 16), 1.234, 300, "E 跑"),
                _activity("b", date(2026, 6, 16), 1.234, 300, "E 跑"),
            ],
            previous_week_distance_km=80,
            recent_4w_avg_distance_km=75,
            structure=WeeklyTrainingStructure(),
        )
    )

    summary = analysis.daily_summaries[1]
    assert summary.total_distance_km == 2.47
    assert summary.combined_pace_s_per_km == pytest.approx(600 / 2.468)


def test_weekly_analysis_orders_activities_by_time_missing_state_and_id() -> None:
    shared_time = datetime(2026, 6, 16, 6, 30)
    analysis = analyze_week(
        WeeklyContext(
            week_start=date(2026, 6, 15),
            week_end=date(2026, 6, 21),
            activities=[
                _activity("missing", date(2026, 6, 16), 2, 600, "E 跑"),
                _activity("same-b", date(2026, 6, 16), 2, 600, "E 跑", start_time_local=shared_time),
                _activity("earlier", date(2026, 6, 16), 2, 600, "E 跑", start_time_local=datetime(2026, 6, 16, 6, 0)),
                _activity("same-a", date(2026, 6, 16), 2, 600, "E 跑", start_time_local=shared_time),
            ],
            previous_week_distance_km=80,
            recent_4w_avg_distance_km=75,
            structure=WeeklyTrainingStructure(),
        )
    )

    assert [item.activity_id for item in analysis.daily_summaries[1].activities] == [
        "earlier",
        "same-a",
        "same-b",
        "missing",
    ]


def test_weekly_analysis_orders_mixed_timezone_times_by_local_wall_clock() -> None:
    analysis = analyze_week(
        WeeklyContext(
            week_start=date(2026, 6, 15),
            week_end=date(2026, 6, 21),
            activities=[
                _activity(
                    "naive-later",
                    date(2026, 6, 16),
                    2,
                    600,
                    "E 跑",
                    start_time_local=datetime(2026, 6, 16, 6, 30),
                ),
                _activity(
                    "aware-earlier",
                    date(2026, 6, 16),
                    2,
                    600,
                    "E 跑",
                    start_time_local=datetime(
                        2026,
                        6,
                        16,
                        6,
                        0,
                        tzinfo=timezone(timedelta(hours=8)),
                    ),
                ),
            ],
            previous_week_distance_km=80,
            recent_4w_avg_distance_km=75,
            structure=WeeklyTrainingStructure(),
        )
    )

    assert [item.activity_id for item in analysis.daily_summaries[1].activities] == [
        "aware-earlier",
        "naive-later",
    ]


def test_weekly_analysis_excludes_out_of_range_activities_everywhere() -> None:
    analysis = analyze_week(
        WeeklyContext(
            week_start=date(2026, 6, 15),
            week_end=date(2026, 6, 21),
            activities=[
                _activity("inside", date(2026, 6, 16), 10, 3000, "E 跑"),
                _activity("outside", date(2026, 6, 22), 30, 9000, "间歇课"),
            ],
            previous_week_distance_km=10,
            recent_4w_avg_distance_km=10,
            structure=WeeklyTrainingStructure(),
        )
    )

    assert analysis.total_distance_km == 10
    assert analysis.total_duration_s == 3000
    assert analysis.running_days == 1
    assert analysis.rest_days == 6
    assert analysis.high_intensity_count == 0
    assert analysis.high_intensity.distance_km == 0
    assert all("outside" not in workout.activity.activity_id for workout in analysis.key_workouts)
    assert [
        activity.activity_id
        for summary in analysis.daily_summaries
        for activity in summary.activities
    ] == ["inside"]
    assert analysis.risk_signals == ["未发现明显风险信号"]


def test_weekly_analysis_detects_warmup_and_cooldown_by_phase_role() -> None:
    phases = (
        WeeklyWorkoutPhase(WeeklyWorkoutPhaseRole.WARMUP, "慢跑", 3, 1200),
        WeeklyWorkoutPhase(WeeklyWorkoutPhaseRole.MAIN, "稳态主体", 7, 2100),
        WeeklyWorkoutPhase(WeeklyWorkoutPhaseRole.COOLDOWN, "放松", 5, 1800),
    )
    analysis = analyze_week(
        WeeklyContext(
            week_start=date(2026, 6, 15),
            week_end=date(2026, 6, 21),
            activities=[
                _activity(
                    "structured",
                    date(2026, 6, 16),
                    15,
                    5100,
                    "稳态跑",
                    workout_phases=phases,
                )
            ],
            previous_week_distance_km=80,
            recent_4w_avg_distance_km=75,
            structure=WeeklyTrainingStructure(),
        )
    )

    assert analysis.daily_summaries[1].training_type == "稳态跑（含热身冷身）"


def test_weekly_analysis_dominant_tie_prefers_duration_then_start_time() -> None:
    longer_analysis = analyze_week(
        WeeklyContext(
            week_start=date(2026, 6, 15),
            week_end=date(2026, 6, 21),
            activities=[
                _activity(
                    "earlier-short",
                    date(2026, 6, 16),
                    4,
                    1200,
                    "稳态跑",
                    start_time_local=datetime(2026, 6, 16, 6, 0),
                ),
                _activity(
                    "later-long",
                    date(2026, 6, 16),
                    6,
                    1800,
                    "马配桥梁",
                    start_time_local=datetime(2026, 6, 16, 7, 0),
                ),
            ],
            previous_week_distance_km=80,
            recent_4w_avg_distance_km=75,
            structure=WeeklyTrainingStructure(),
        )
    )

    assert longer_analysis.daily_summaries[1].training_type == "马配桥梁"

    earlier_analysis = analyze_week(
        WeeklyContext(
            week_start=date(2026, 6, 15),
            week_end=date(2026, 6, 21),
            activities=[
                _activity(
                    "earlier",
                    date(2026, 6, 16),
                    6,
                    1800,
                    "稳态跑",
                    start_time_local=datetime(2026, 6, 16, 6, 0),
                ),
                _activity(
                    "later",
                    date(2026, 6, 16),
                    6,
                    1800,
                    "马配桥梁",
                    start_time_local=datetime(2026, 6, 16, 7, 0),
                ),
            ],
            previous_week_distance_km=80,
            recent_4w_avg_distance_km=75,
            structure=WeeklyTrainingStructure(),
        )
    )

    assert earlier_analysis.daily_summaries[1].training_type == "稳态跑"

    shared_time = datetime(2026, 6, 16, 6, 0)
    id_analysis = analyze_week(
        WeeklyContext(
            week_start=date(2026, 6, 15),
            week_end=date(2026, 6, 21),
            activities=[
                _activity(
                    "z",
                    date(2026, 6, 16),
                    6,
                    1800,
                    "稳态跑",
                    start_time_local=shared_time,
                ),
                _activity(
                    "a",
                    date(2026, 6, 16),
                    6,
                    1800,
                    "马配桥梁",
                    start_time_local=shared_time,
                ),
            ],
            previous_week_distance_km=80,
            recent_4w_avg_distance_km=75,
            structure=WeeklyTrainingStructure(),
        )
    )

    assert id_analysis.daily_summaries[1].training_type == "马配桥梁"


def test_weekly_report_calculates_volume_structure_and_risks(tmp_path: Path) -> None:
    analysis = analyze_week(
        WeeklyContext(
            week_start=date(2026, 6, 15),
            week_end=date(2026, 6, 21),
            activities=[
                _activity("easy", date(2026, 6, 16), 14, 5400, "E 跑"),
                _activity(
                    "quality",
                    date(2026, 6, 17),
                    16,
                    4800,
                    "阈值间歇",
                    intensity_distance_km=8,
                    intensity_duration_s=2400,
                ),
                _activity(
                    "steady",
                    date(2026, 6, 19),
                    12,
                    3600,
                    "稳态跑",
                    intensity_distance_km=8,
                    intensity_duration_s=2400,
                ),
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
    assert analysis.high_intensity.distance_km == 8
    assert analysis.high_intensity.duration_s == 2400
    assert analysis.steady.distance_km == 8
    assert analysis.steady.duration_s == 2400
    assert analysis.auxiliary.distance_km == 12
    assert analysis.auxiliary.duration_s == 3600
    assert analysis.high_intensity_time_ratio > 0
    assert analysis.conclusion in {"有效刺激", "稳定积累"}
    assert len(analysis.key_workouts) == 3

    path = write_weekly_report(analysis, tmp_path)
    assert path == tmp_path / "weekly" / "2026-W25.md"
    content = path.read_text(encoding="utf-8")
    assert "## 本周结论" in content
    assert "## 训练量" in content
    assert "## 强度结构" in content
    assert "阈值/间歇主训练段：8.0 km" in content
    assert "总 16.0 km / 主训练 8.0 km" in content
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
    intensity_distance_km: float | None = None,
    intensity_duration_s: float | None = None,
    average_hr: float | None = None,
    start_time_local: datetime | None = None,
    workout_phases: tuple[WeeklyWorkoutPhase, ...] = (),
) -> WeeklyActivity:
    return WeeklyActivity(
        activity_id=activity_id,
        activity_date=activity_date,
        activity_name="跑步",
        distance_km=distance_km,
        duration_s=duration_s,
        average_hr=average_hr,
        training_type=training_type,
        execution_score=90,
        report_path=Path(f"reports/daily/{activity_id}.md"),
        intensity_distance_km=intensity_distance_km,
        intensity_duration_s=intensity_duration_s,
        start_time_local=start_time_local,
        workout_phases=workout_phases,
    )
