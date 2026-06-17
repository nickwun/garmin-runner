from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from garmin_runner.analysis.single_activity import (
    HeartRateZones,
    TimeSeriesPoint,
    TrainingConfig,
    analyze_activity,
)
from garmin_runner.reporting.daily import write_daily_report


def test_analyze_activity_calculates_basic_metrics_zones_and_drift() -> None:
    summary = {
        "activityId": "12345",
        "activityName": "MAF Run",
        "startTimeLocal": "2026-06-01T06:30:00",
        "distance": 5000.0,
        "duration": 1800.0,
        "averageHR": 142,
        "maxHR": 172,
        "elevationGain": 38,
        "averageRunningCadenceInStepsPerMinute": 176,
    }
    points = _points(
        [
            (0, 0, 119, 3.0),
            (60, 180, 130, 3.0),
            (120, 360, 142, 3.0),
            (180, 540, 150, 3.0),
            (240, 720, 160, 3.0),
            (300, 900, 172, 3.0),
            (360, 1080, 172, 3.0),
        ]
    )
    config = TrainingConfig(
        heart_rate_zones=HeartRateZones(
            maf_low=120,
            maf_high=145,
            recovery_high=135,
            easy_low=133,
            easy_high=145,
            aerobic_high=155,
            steady_high=165,
            mp_bridge_high=170,
            threshold_high=178,
            vo2_high=188,
            sprint_high=194,
        )
    )

    analysis = analyze_activity(summary, points, config)

    assert analysis.basic.distance_km == 5.0
    assert analysis.basic.duration_s == 1800.0
    assert analysis.basic.average_pace_s_per_km == 360.0
    assert analysis.basic.average_hr == 142
    assert analysis.basic.max_hr == 172
    assert analysis.basic.elevation_gain_m == 38
    assert analysis.basic.average_cadence_spm == 176
    assert analysis.hr_zones.seconds_by_zone == {
        "below_range": 60.0,
        "very_easy": 60.0,
        "easy": 60.0,
        "aerobic": 60.0,
        "steady": 60.0,
        "mp_bridge": 0.0,
        "threshold": 60.0,
        "vo2": 0.0,
        "sprint": 0.0,
    }
    assert analysis.pace_stability.cv_pct == 0.0
    assert analysis.heart_rate_drift.drift_pct > 0


def test_analyze_activity_classifies_threshold_workout_and_scores_execution() -> None:
    summary = {
        "activityId": "67890",
        "activityName": "Threshold repeats",
        "startTimeLocal": "2026-06-02T06:30:00",
        "distance": 8000.0,
        "duration": 2400.0,
        "averageHR": 158,
        "maxHR": 176,
    }
    points = _points(
        [
            (0, 0, 135, 3.2),
            (300, 960, 164, 3.8),
            (600, 2100, 166, 3.8),
            (900, 3240, 150, 3.1),
            (1200, 4170, 168, 3.8),
            (1500, 5310, 176, 3.8),
            (1800, 6450, 145, 3.0),
            (2100, 7350, 138, 3.0),
            (2400, 8250, 138, 3.0),
        ]
    )
    config = TrainingConfig(
        heart_rate_zones=HeartRateZones(
            maf_low=120,
            maf_high=145,
            recovery_high=135,
            easy_low=133,
            easy_high=145,
            aerobic_high=155,
            steady_high=165,
            mp_bridge_high=170,
            threshold_high=178,
            vo2_high=188,
            sprint_high=194,
        )
    )

    analysis = analyze_activity(summary, points, config)

    assert analysis.training_type == "阈值课"
    assert 0 <= analysis.execution_score <= 100
    assert analysis.execution_score < 90
    assert analysis.coach_instruction


def test_daily_report_uses_required_path_and_sections(tmp_path: Path) -> None:
    summary = {
        "activityId": "12345",
        "activityName": "Recovery Run",
        "startTimeLocal": "2026-06-01T06:30:00",
        "distance": 3000.0,
        "duration": 1200.0,
        "movingDuration": 1188.0,
        "averageHR": 118,
        "maxHR": 132,
    }
    analysis = analyze_activity(
        summary,
        _points([(0, 0, 116, 2.5), (600, 1500, 118, 2.5), (1200, 3000, 120, 2.5)]),
        TrainingConfig(
            heart_rate_zones=HeartRateZones(
                maf_low=120,
                maf_high=145,
                recovery_high=135,
                easy_low=133,
                easy_high=145,
                aerobic_high=155,
                steady_high=165,
                mp_bridge_high=170,
                threshold_high=178,
                vo2_high=188,
                sprint_high=194,
            )
        ),
    )

    report_path = write_daily_report(analysis, tmp_path)

    assert report_path == tmp_path / "daily" / "2026-06-01_12345.md"
    content = report_path.read_text(encoding="utf-8")
    assert "## 数据面" in content
    assert "移动时间：19:48" in content
    assert "## 生理面" in content
    assert "## 执行打分" in content
    assert "## 教练指令" in content


def test_user_calibrated_easy_run_classification() -> None:
    summary = {
        "activityId": "601754283",
        "activityName": "福州市 跑步",
        "startTimeLocal": "2026-06-03T05:49:41",
        "distance": 14377.21,
        "duration": 4877.912,
        "averageHR": 134,
        "maxHR": 145,
    }

    analysis = analyze_activity(
        summary,
        _steady_points(distance_m=14377.21, duration_s=4877.912, heart_rate=134),
        _training_config_from_image(),
    )

    assert analysis.training_type == "E 跑"


def test_user_calibrated_medium_long_low_hr_classification() -> None:
    summary = {
        "activityId": "607025766",
        "activityName": "福州市 跑步",
        "startTimeLocal": "2026-06-17T05:47:22",
        "distance": 15926.13,
        "duration": 5599.562,
        "averageHR": 132,
        "maxHR": 157,
    }

    analysis = analyze_activity(
        summary,
        _steady_points(distance_m=15926.13, duration_s=5599.562, heart_rate=132),
        _training_config_from_image(),
    )

    assert analysis.training_type == "E 跑"


def test_long_run_uses_distance_and_duration_thresholds_together() -> None:
    summary = {
        "activityId": "longish",
        "activityName": "福州市 跑步",
        "startTimeLocal": "2026-06-18T05:47:22",
        "distance": 18500.0,
        "duration": 5700.0,
        "averageHR": 139,
        "maxHR": 150,
    }

    analysis = analyze_activity(
        summary,
        _steady_points(distance_m=18500.0, duration_s=5700.0, heart_rate=139),
        _training_config_from_image(),
    )

    assert analysis.training_type == "长距离"


def test_heart_rate_zone_time_ignores_long_pause_gaps() -> None:
    summary = {
        "activityId": "pause-gap",
        "activityName": "福州市 跑步",
        "startTimeLocal": "2026-06-18T05:47:22",
        "distance": 1000.0,
        "duration": 120.0,
        "averageHR": 150,
    }
    points = _points(
        [
            (0, 0, 145, 3.0),
            (60, 180, 145, 3.0),
            (660, 182, 150, 3.0),
            (720, 362, 150, 3.0),
        ]
    )

    analysis = analyze_activity(summary, points, _training_config_from_image())

    assert sum(analysis.hr_zones.seconds_by_zone.values()) == 120.0
    assert analysis.confidence.level == "medium"
    assert any("暂停" in reason for reason in analysis.confidence.reasons)


def test_heart_rate_drift_is_not_applicable_for_interval_workouts() -> None:
    summary = {
        "activityId": "interval",
        "activityName": "福州市 - 5×2 公里",
        "startTimeLocal": "2026-06-18T05:47:22",
        "distance": 12000.0,
        "duration": 3600.0,
        "averageHR": 150,
        "maxHR": 176,
    }

    analysis = analyze_activity(
        summary,
        _steady_points(distance_m=12000.0, duration_s=3600.0, heart_rate=150),
        _training_config_from_image(),
    )

    assert analysis.training_type == "阈值间歇"
    assert analysis.heart_rate_drift.applicable is False
    assert analysis.heart_rate_drift.drift_pct is None
    assert "不适用" in analysis.heart_rate_drift.label
    assert any("心率漂移" in note for note in analysis.not_applicable_notes)


def test_short_standalone_aerobic_record_is_warmup_or_cooldown() -> None:
    summary = {
        "activityId": "602516586",
        "activityName": "福州市 跑步",
        "startTimeLocal": "2026-06-05T06:39:17",
        "distance": 4510.76,
        "duration": 1578.1,
        "averageHR": 152,
        "maxHR": 165,
    }

    analysis = analyze_activity(
        summary,
        _steady_points(distance_m=4510.76, duration_s=1578.1, heart_rate=152),
        _training_config_from_image(),
    )

    assert analysis.training_type == "热身/冷身"
    assert "热身或冷身" in analysis.guidance.prohibited


def test_two_kilometer_repeats_are_threshold_intervals() -> None:
    summary = {
        "activityId": "606648954",
        "activityName": "福州市 - 5×2 公里",
        "startTimeLocal": "2026-06-16T05:44:56",
        "distance": 17431.64,
        "duration": 5220.863,
        "averageHR": 147,
        "maxHR": 175,
    }

    analysis = analyze_activity(
        summary,
        _steady_points(distance_m=17431.64, duration_s=5220.863, heart_rate=147),
        _training_config_from_image(),
    )

    assert analysis.training_type == "阈值间歇"
    assert analysis.heart_rate_drift.applicable is False


def test_threshold_intervals_split_warmup_main_and_cooldown() -> None:
    summary = {
        "activityId": "split-interval",
        "activityName": "福州市 - 3×2 公里",
        "startTimeLocal": "2026-06-18T05:47:22",
        "distance": 14000.0,
        "duration": 4800.0,
        "averageHR": 150,
        "maxHR": 176,
    }

    analysis = analyze_activity(
        summary,
        _interval_points(),
        _training_config_from_image(),
    )

    assert analysis.training_type == "阈值间歇"
    assert analysis.workout_breakdown is not None
    assert analysis.workout_breakdown.warmup.distance_km == 3.0
    assert analysis.workout_breakdown.main.distance_km == 8.0
    assert analysis.workout_breakdown.cooldown.distance_km == 3.0
    assert analysis.workout_breakdown.quality.distance_km == 6.0


def test_daily_report_renders_interval_breakdown(tmp_path: Path) -> None:
    summary = {
        "activityId": "split-report",
        "activityName": "福州市 - 3×2 公里",
        "startTimeLocal": "2026-06-18T05:47:22",
        "distance": 14000.0,
        "duration": 4800.0,
        "averageHR": 150,
        "maxHR": 176,
    }
    analysis = analyze_activity(summary, _interval_points(), _training_config_from_image())

    path = write_daily_report(analysis, tmp_path)
    content = path.read_text(encoding="utf-8")

    assert "## 分段拆解" in content
    assert "热身：3.0 km" in content
    assert "阈值间歇训练段：8.0 km" in content
    assert "阈值快段：6.0 km" in content
    assert "冷身：3.0 km" in content


def test_missing_fit_fields_lower_analysis_confidence() -> None:
    summary = {
        "activityId": "missing-fields",
        "activityName": "福州市 跑步",
        "startTimeLocal": "2026-06-18T05:47:22",
        "duration": 1200.0,
    }
    points = [
        TimeSeriesPoint(
            timestamp=datetime(2026, 6, 18, 6, 0, 0) + timedelta(seconds=i * 60),
            elapsed_s=float(i * 60),
            distance_m=None,
            heart_rate_bpm=None,
            speed_mps=None,
            cadence_spm=None,
            altitude_m=None,
        )
        for i in range(5)
    ]

    analysis = analyze_activity(summary, points, _training_config_from_image())

    assert analysis.confidence.level == "low"
    assert any("心率" in reason for reason in analysis.confidence.reasons)
    assert any("距离" in reason for reason in analysis.confidence.reasons)
    assert any("速度" in reason for reason in analysis.confidence.reasons)


def test_easy_runs_are_not_penalized_for_slow_pace() -> None:
    summary = {
        "activityId": "slow-easy",
        "activityName": "福州市 跑步",
        "startTimeLocal": "2026-06-18T05:47:22",
        "distance": 6000.0,
        "duration": 3600.0,
        "averageHR": 128,
        "maxHR": 132,
    }

    analysis = analyze_activity(
        summary,
        _steady_points(distance_m=6000.0, duration_s=3600.0, heart_rate=128),
        _training_config_from_image(),
    )

    assert analysis.training_type == "E 跑"
    assert analysis.execution_score >= 90


def test_easy_plan_run_as_steady_gets_penalized() -> None:
    summary = {
        "activityId": "easy-too-hard",
        "activityName": "Easy run",
        "startTimeLocal": "2026-06-18T05:47:22",
        "distance": 8000.0,
        "duration": 3000.0,
        "averageHR": 160,
        "maxHR": 168,
    }

    analysis = analyze_activity(
        summary,
        _steady_points(distance_m=8000.0, duration_s=3000.0, heart_rate=160),
        _training_config_from_image(),
    )

    assert analysis.training_type == "轻松跑跑成稳态"
    assert analysis.execution_score < 80
    assert "轻松" in analysis.guidance.prohibited


def test_report_includes_confidence_applicability_and_guidance(tmp_path: Path) -> None:
    summary = {
        "activityId": "interval-report",
        "activityName": "福州市 - 5×2 公里",
        "startTimeLocal": "2026-06-18T05:47:22",
        "distance": 12000.0,
        "duration": 3600.0,
        "averageHR": 150,
    }

    analysis = analyze_activity(
        summary,
        _steady_points(distance_m=12000.0, duration_s=3600.0, heart_rate=150),
        _training_config_from_image(),
    )

    report_path = write_daily_report(analysis, tmp_path)
    content = report_path.read_text(encoding="utf-8")

    assert "数据可信度" in content
    assert "不适用指标说明" in content
    assert "明日训练建议" in content
    assert "未来 48-72 小时建议" in content
    assert "禁止事项" in content


def _points(rows: list[tuple[int, float, int, float]]) -> list[TimeSeriesPoint]:
    start = datetime(2026, 6, 1, 6, 30, 0)
    return [
        TimeSeriesPoint(
            timestamp=start + timedelta(seconds=offset_s),
            elapsed_s=float(offset_s),
            distance_m=distance_m,
            heart_rate_bpm=heart_rate,
            speed_mps=speed_mps,
            cadence_spm=None,
            altitude_m=None,
        )
        for offset_s, distance_m, heart_rate, speed_mps in rows
    ]


def _steady_points(
    distance_m: float,
    duration_s: float,
    heart_rate: int,
) -> list[TimeSeriesPoint]:
    return _points(
        [
            (0, 0, heart_rate, distance_m / duration_s),
            (int(duration_s / 2), distance_m / 2, heart_rate, distance_m / duration_s),
            (int(duration_s), distance_m, heart_rate, distance_m / duration_s),
        ]
    )


def _interval_points() -> list[TimeSeriesPoint]:
    rows = [
        # 3 km warmup at 6:00/km
        (0, 0, 125, 2.78),
        (1080, 3000, 140, 2.78),
        # 3 x 2 km fast with 1 km easy recoveries between reps
        (1080, 3000, 150, 4.17),
        (1560, 5000, 170, 4.17),
        (1920, 6000, 145, 2.78),
        (2400, 8000, 172, 4.17),
        (2760, 9000, 145, 2.78),
        (3240, 11000, 174, 4.17),
        # 3 km cooldown
        (4320, 14000, 135, 2.78),
    ]
    start = datetime(2026, 6, 1, 6, 30, 0)
    return [
        TimeSeriesPoint(
            timestamp=start + timedelta(seconds=offset_s),
            elapsed_s=float(offset_s),
            distance_m=distance_m,
            heart_rate_bpm=heart_rate,
            speed_mps=speed_mps,
            cadence_spm=None,
            altitude_m=None,
        )
        for offset_s, distance_m, heart_rate, speed_mps in rows
    ]


def _training_config_from_image() -> TrainingConfig:
    return TrainingConfig(
        heart_rate_zones=HeartRateZones(
            maf_low=120,
            maf_high=145,
            recovery_high=135,
            easy_low=133,
            easy_high=145,
            aerobic_high=155,
            steady_high=165,
            mp_bridge_high=170,
            threshold_high=178,
            vo2_high=188,
            sprint_high=194,
        )
    )
