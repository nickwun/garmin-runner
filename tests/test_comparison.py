from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3

import pytest
from typer.testing import CliRunner

from garmin_runner.analysis.comparison import (
    ComparisonError, build_comparison, build_sample, compare_pair, load_context, range_metrics, lap_work_ranges,
)
from garmin_runner.analysis.single_activity import TimeSeriesPoint
from garmin_runner.cli import app
from garmin_runner.config import AppSettings, GarminSettings, StorageSettings, TrainingSettings
from garmin_runner.reporting.comparison import write_comparison_report
from garmin_runner.storage import ActivityRecord, ActivityStore


def points(km=10, pace=360, hr=130):
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    return [TimeSeriesPoint(start + timedelta(seconds=i * pace / 100), i * pace / 100,
                            i * 10, hr, 1000 / pace, 180, 1) for i in range(km * 100 + 1)]


def sample(id="target", pace=360, kind="E 跑"):
    return {"activity_id": id, "start": "2026-09-22T06:00:00", "training_type": kind,
            "type_source": "本地明确校准", "total_distance_km": 10, "timer_duration_s": 3600,
            "elapsed_duration_s": 3600, "moving_duration_s": None,
            "selection": "明确主训练段", "ranges_km": [[0, 10]],
            "metrics": range_metrics(points(pace=pace), [[0, 10000]]),
            "ascent_m_per_km": 1, "warnings": [], "drift_notes": [], "work_intervals": [],
            "recovery_intervals": [], "context": {"temperature_c": 23, "humidity_pct": 80,
              "weather_source": "test", "route": "A", "surface": "road", "hr_source": "belt"}}


def settings_at(tmp_path):
    return AppSettings(GarminSettings(), StorageSettings(database_path=tmp_path / "test.sqlite",
        data_dir=tmp_path / "data", reports_dir=tmp_path / "reports"), TrainingSettings())


def test_range_integrates_exact_boundaries_and_weights_hr_by_time():
    p = points(km=3)
    m = range_metrics(p, [[500, 2500]])
    assert m["distance_km"] == pytest.approx(2)
    assert m["duration_s"] == pytest.approx(720)
    assert m["pace_s_per_km"] == pytest.approx(360)
    assert m["hr"] == pytest.approx(130)
    assert m["drift_pct"] == pytest.approx(0)
    p = [TimeSeriesPoint(None, t, d, h, 3, None, None)
         for t, d, h in [(0, 0, 100), (10, 30, 160), (30, 90, 160)]]
    assert range_metrics(p, [[0, 90]])["hr"] == pytest.approx(140)


def test_gap_is_not_interpolated_and_missing_hr_not_filled():
    p = [TimeSeriesPoint(None, t, d, None, 3, None, None)
         for t, d in [(0, 0), (10, 30), (120, 360), (130, 390)]]
    m = range_metrics(p, [[0, 390]])
    assert m["distance_coverage"] < 0.2
    assert m["pause_s"] == 110
    assert m["hr"] is None and m["drift_pct"] is None


def test_isolated_distance_jitter_not_misreported_as_obvious_pause():
    p = []
    distance = 0
    for i in range(1000):
        p.append(TimeSeriesPoint(None, i, distance, 130, 3, None, None))
        distance += 0.2 if i % 10 == 0 else 3
    assert range_metrics(p, [[0, distance]])["pause_s"] == 0
    p = [TimeSeriesPoint(None, i, max(0, i - 15) * 3, 130, 3, None, None) for i in range(100)]
    assert range_metrics(p, [[0, 300]])["pause_s"] == 15


def test_pair_matches_and_does_not_reward_faster_running():
    result = compare_pair(sample(pace=380), sample("old", pace=360))
    assert result["tier"] == "comparable"
    assert result["pace_delta_s_per_km"] == pytest.approx(20)
    assert "score" not in result


@pytest.mark.parametrize("field,value,reason", [("temperature_c", 30, "气温差异"),
    ("humidity_pct", 99, "湿度差异"), ("route", "B", "路线不同"), ("hr_source", "wrist", "心率来源不同")])
def test_condition_mismatch_is_reference(field, value, reason):
    old = sample("old")
    old["context"][field] = value
    result = compare_pair(sample(), old)
    assert result["tier"] == "reference"
    assert any(reason in r for r in result["reasons"])


def test_unknown_weather_and_route_never_pass_as_equal():
    old = sample("old")
    old["context"] = {}
    result = compare_pair(sample(), old)
    assert result["tier"] == "reference"
    assert len(result["reasons"]) >= 5


@pytest.mark.parametrize("change", ["type", "distance", "hr", "coverage"])
def test_hard_exclusions(change):
    old = sample("old")
    if change == "type": old["training_type"] = "稳态跑"
    if change == "distance": old["metrics"]["requested_km"] = 20
    if change == "hr": old["metrics"]["hr"] = None
    if change == "coverage": old["warnings"] = ["缺失 distance"]
    assert compare_pair(sample(), old)["tier"] == "excluded"


def test_intervals_without_work_ranges_never_compare_overall_pace():
    r = compare_pair(sample(kind="阈值间歇"), sample("old", kind="阈值间歇"))
    assert r["tier"] == "reference" and r["pace_delta_s_per_km"] is None


def test_interval_repetitions_and_recovery_must_match():
    a = sample(kind="阈值间歇")
    a["work_intervals"] = [range_metrics(points(km=2), [[0, 2000]])] * 4
    a["recovery_intervals"] = [{"duration_s": 120, "pause_s": 0}] * 3
    b = deepcopy(a)
    assert compare_pair(a, b)["tier"] == "comparable"
    b["work_intervals"] = b["work_intervals"][:3]
    assert compare_pair(a, b)["tier"] == "excluded"
    b = deepcopy(a)
    b["recovery_intervals"][0]["duration_s"] = 180
    assert compare_pair(a, b)["tier"] == "reference"


@pytest.mark.parametrize("item", [{"humidity_pct": 120, "weather_source": "user"},
    {"temperature_c": 22}, {"main_range_km": [8, 3]}, {"work_ranges_km": [[0, 3], [2, 4]]},
    {"training_type": "unknown"}, {"token": "secret"}])
def test_context_invalid_is_sanitized(tmp_path, item):
    path = tmp_path / "context.json"
    path.write_text(json.dumps({"version": 1, "activities": {"a": item}}))
    with pytest.raises(ComparisonError) as error:
        load_context(path)
    assert "secret" not in str(error.value)


def seeded(tmp_path, monkeypatch):
    settings = settings_at(tmp_path)
    store = ActivityStore(settings.storage.database_path)
    store.initialize()
    for id, day in (("target", "2026-09-22"), ("old", "2026-09-21"), ("duplicate", "2026-09-21"),
                    ("future", "2026-09-23"), ("same_day", "2026-09-22")):
        path = tmp_path / f"{id}.json"
        path.write_text(json.dumps({"activityId": id, "startTimeLocal": day + "T06:00:00", "distance": 10000,
                                   "duration": 3600, "averageHR": 130, "maxHR": 140}))
        fit = tmp_path / f"{id}.fit"
        fit.touch()
        store.upsert_activity(ActivityRecord(id, "running", day + "T06:00:00", "Synthetic", 10000, 3600,
                               str(path), str(fit), day + "T22:00:00", 130, 140))
    monkeypatch.setattr("garmin_runner.analysis.comparison.decode_fit_messages", lambda _: ({}, []))
    monkeypatch.setattr("garmin_runner.analysis.comparison.extract_time_series", lambda _: points())
    return settings, store


def test_build_deduplicates_and_excludes_future_without_writing_db(tmp_path, monkeypatch):
    settings, store = seeded(tmp_path, monkeypatch)
    before = settings.storage.database_path.read_bytes()
    result = build_comparison(settings, "target")
    assert len(result["matches"]) == 1
    assert len(result["duplicates_excluded"]) == 1
    assert settings.storage.database_path.read_bytes() == before
    assert len(store.list_activities()) == 5
    assert "不能" in result["conclusion"]
    path = write_comparison_report(result, settings.storage.reports_dir)
    assert "仅供参考" in path.read_text()
    assert path.with_suffix(".json").exists()
    assert path.stat().st_mode & 0o777 == 0o600


def test_explicit_main_range_and_interval_drift_suppression(tmp_path, monkeypatch):
    settings, store = seeded(tmp_path, monkeypatch)
    context = {"target": {"training_type": "阈值间歇", "work_ranges_km": [[2, 4], [5, 7]]}}
    s = build_sample(store.get_activity("target"), settings, context)
    assert s["metrics"]["distance_km"] == pytest.approx(4)
    assert len(s["work_intervals"]) == 2
    assert s["metrics"]["drift_pct"] is None
    assert s["drift_notes"]


def test_missing_fit_is_clear(tmp_path, monkeypatch):
    settings, store = seeded(tmp_path, monkeypatch)
    Path(store.get_activity("target")["fit_path"]).unlink()
    with pytest.raises(ComparisonError, match="FIT 缺失"):
        build_comparison(settings, "target")


def test_empty_db_missing_target_and_future_against(tmp_path, monkeypatch):
    settings = settings_at(tmp_path)
    with pytest.raises(ComparisonError, match="先运行 sync"):
        build_comparison(settings, "a")
    assert not settings.storage.database_path.exists()
    ActivityStore(settings.storage.database_path).initialize()
    with pytest.raises(ComparisonError, match="找不到"):
        build_comparison(settings, "a")
    settings, _ = seeded(tmp_path, monkeypatch)
    with pytest.raises(ComparisonError, match="早于"):
        build_comparison(settings, "target", against="future")


def test_cli_help_missing_config_and_invalid_limit(tmp_path):
    runner = CliRunner()
    assert runner.invoke(app, ["compare", "--help"]).exit_code == 0
    result = runner.invoke(app, ["compare", "x", "--config", str(tmp_path / "missing.yaml")])
    assert result.exit_code == 1 and "缺少" in result.output and "Traceback" not in result.output
    assert runner.invoke(app, ["compare", "x", "--days", "0"]).exit_code != 0


def test_cli_success_and_private_outputs(tmp_path, monkeypatch):
    settings, _ = seeded(tmp_path, monkeypatch)
    config = tmp_path / "athlete.yaml"
    config.touch()
    monkeypatch.setattr("garmin_runner.comparison_cli.load_settings", lambda _: settings)
    result = CliRunner().invoke(app, ["compare", "target", "--config", str(config)])
    assert result.exit_code == 0, result.output
    assert "[查看报告](" in result.output
    assert "Traceback" not in result.output


def test_lap_roles_join_kilometers_into_work_groups_and_preserve_rest():
    def lap(distance, role, seconds=240):
        return {"total_distance": distance, "total_timer_time": seconds, "intensity": role}
    laps = [lap(1000, "warmup")]
    for _ in range(3):
        laps += [lap(1000, "active")] * 3 + [lap(300, "recovery", 150)]
    laps += [lap(1.5, "active", 1), lap(100, "cooldown")]
    ranges, rest = lap_work_ranges(laps)
    assert ranges == [[1000, 4000], [4300, 7300], [7600, 10600]]
    assert [r["duration_s"] for r in rest] == [150, 150]
    assert lap_work_ranges([{"total_distance": 1000, "total_timer_time": 240}]) == ([], [])


def test_realistic_lap_structure_overrides_coarse_breakdown(tmp_path, monkeypatch):
    settings, store = seeded(tmp_path, monkeypatch)
    laps = [{"total_distance": 1000, "total_timer_time": 360, "intensity": role}
            for role in ["warmup", "active", "active", "recovery", "active", "active", "cooldown"]]
    monkeypatch.setattr("garmin_runner.analysis.comparison.decode_fit_messages", lambda _: ({"lap_mesgs": laps}, []))
    s = build_sample(store.get_activity("target"), settings, {"target": {"training_type": "阈值间歇"}})
    assert s["selection"] == "FIT 标记快段（不含组间恢复）"
    assert s["metrics"]["requested_km"] == 4
    assert [w["requested_km"] for w in s["work_intervals"]] == [2, 2]
    assert s["recovery_intervals"][0]["duration_s"] == 360


def test_progression_and_missing_hr_disable_drift(tmp_path, monkeypatch):
    settings, store = seeded(tmp_path, monkeypatch)
    row = store.get_activity("target")
    result = build_sample(row, settings, {"target": {"variable_effort": True}})
    assert result["metrics"]["drift_pct"] is None
    assert any("计划" in r for r in result["drift_notes"])
    p = points(hr=None)
    monkeypatch.setattr("garmin_runner.analysis.comparison.extract_time_series", lambda _: p)
    result = build_sample(row, settings, {})
    assert result["metrics"]["hr"] is None
    assert result["warnings"] and result["metrics"]["drift_pct"] is None


def test_short_selected_segment_not_saved_by_many_whole_activity_records(tmp_path, monkeypatch):
    settings, store = seeded(tmp_path, monkeypatch)
    result = build_sample(store.get_activity("target"), settings, {"target": {"main_range_km": [0, 0.1]}})
    assert "FIT records 太少" in result["warnings"]
    assert result["metrics"]["drift_pct"] is None


def test_linked_session_does_not_dilute_main_distance(tmp_path, monkeypatch):
    settings, _ = seeded(tmp_path, monkeypatch)
    context = tmp_path / "context.json"
    context.write_text(json.dumps({"version": 1, "activities": {"target": {"session_activity_ids": ["same_day"]}}}))
    result = build_comparison(settings, "target", context_path=context)
    assert len(result["linked_session"]) == 1
    assert result["target"]["metrics"]["requested_km"] == 10
    context.write_text(json.dumps({"version": 1, "activities": {"target": {"session_activity_ids": ["old"]}}}))
    with pytest.raises(ComparisonError, match="同一当地日期"):
        build_comparison(settings, "target", context_path=context)


def test_sanitizes_unexpected_cli_exception(tmp_path, monkeypatch):
    config = tmp_path / "athlete.yaml"
    config.touch()
    def broken(_):
        raise ValueError("password=secret token=private")
    monkeypatch.setattr("garmin_runner.comparison_cli.load_settings", broken)
    result = CliRunner().invoke(app, ["compare", "a", "--config", str(config)])
    assert result.exit_code == 1
    assert "secret" not in result.output and "Traceback" not in result.output


def test_summary_requires_three_matched_sessions(tmp_path, monkeypatch):
    settings, store = seeded(tmp_path, monkeypatch)
    def load(row, *_):
        result = sample(row["activity_id"], pace=350 if row["activity_id"] == "target" else 360)
        result["start"] = row["start_time_local"]
        return result
    monkeypatch.setattr("garmin_runner.analysis.comparison.build_sample", load)
    assert "少于 3" in build_comparison(settings, "target")["conclusion"]
    for i in range(2):
        day = f"2026-09-{18 + i}T06:00:00"
        store.upsert_activity(ActivityRecord(f"historical-{i}", "running", day, "Synthetic", 10000, 3600,
                                            "not-read.json", "not-read.fit", day, 130, 140))
    result = build_comparison(settings, "target")
    assert result["strict_count"] == 3
    assert "-10.0" in result["conclusion"]
    assert "不直接等同" in result["conclusion"]
