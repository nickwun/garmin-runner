from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from typer.testing import CliRunner

from garmin_runner.analysis.readiness import analyze_readiness
from garmin_runner.cli import app
from garmin_runner.config import AppSettings, GarminSettings, StorageSettings, TrainingSettings, load_settings
from garmin_runner.health import HealthSchemaError, SOURCES, normalize_health, safe_error, sync_health
from garmin_runner.health_storage import HealthStore
from garmin_runner.reporting.readiness import write_readiness_report
from garmin_runner.storage import ActivityRecord, ActivityStore


ZONE = ZoneInfo("Asia/Shanghai")
DAY = date(2026, 9, 22)
CUTOFF = datetime(2026, 9, 22, 7, tzinfo=ZONE)
runner = CliRunner()


def settings_at(tmp_path):
    return AppSettings(GarminSettings(), StorageSettings(data_dir=tmp_path / "data",
        database_path=tmp_path / "data" / "test.sqlite", reports_dir=tmp_path / "reports"), TrainingSettings())


def save_metric(store, day, kind, metrics, stamp=None):
    store.save(day.isoformat(), kind, "available", "ok", (stamp or CUTOFF - timedelta(minutes=1)).isoformat(),
               raw={"synthetic": metrics}, metrics=metrics)


def seeded(tmp_path):
    settings = settings_at(tmp_path)
    activities = ActivityStore(settings.storage.database_path)
    activities.initialize()
    store = HealthStore(settings.storage.database_path, settings.storage.data_dir)
    store.initialize()
    for offset in range(29):
        day = DAY - timedelta(days=offset)
        observed = datetime.combine(day, datetime.min.time(), ZONE) + timedelta(hours=5)
        save_metric(store, day, "hrv", {"last_night_ms": 60, "weekly_ms": 60,
            "baseline_low_ms": 40, "baseline_high_ms": 80, "status": "BALANCED", "observed_at": observed.isoformat()})
        save_metric(store, day, "sleep", {"duration_s": 28800, "observed_at": observed.isoformat()})
        save_metric(store, day, "rhr", {"bpm": 50})
    store.add_feedback(DAY.isoformat(), {"fatigue": 1, "legs": 1, "pain": "no", "plan": "quality"},
                       (CUTOFF - timedelta(minutes=2)).isoformat())
    return settings, store


def test_store_backup_idempotence_and_failed_request_preserves_success(tmp_path):
    settings, store = seeded(tmp_path)
    snapshots = store.snapshots(DAY.isoformat(), DAY.isoformat())
    before = len(snapshots)
    first = snapshots[0]
    store.save(first["day"], first["kind"], "available", "ok", CUTOFF.isoformat(),
               {"synthetic": first["metrics"]}, first["metrics"])
    store.save(first["day"], first["kind"], "error", "network", CUTOFF.isoformat())
    assert len(store.snapshots(DAY.isoformat(), DAY.isoformat())) == before
    assert len(list((settings.storage.data_dir / "backups").glob("*.sqlite"))) == 1
    assert store.initialize() is None
    assert Path(first["raw_path"]).stat().st_mode & 0o777 == 0o600
    assert settings.storage.database_path.stat().st_mode & 0o777 == 0o600
    assert store.statuses(DAY.isoformat(), DAY.isoformat())[0]["status"] in {"available", "error"}


def test_normalize_sleep_assigns_wake_date_and_validates_mismatch():
    wake = datetime(2026, 9, 22, 5, tzinfo=ZONE).timestamp() * 1000
    raw = {"dailySleepDTO": {"calendarDate": "2026-09-22", "sleepTimeSeconds": 25000,
                            "sleepEndTimestampGMT": wake}}
    assert normalize_health("sleep", raw, "2026-09-22", "Asia/Shanghai")["duration_s"] == 25000
    with pytest.raises(HealthSchemaError):
        normalize_health("sleep", raw, "2026-09-21", "Asia/Shanghai")
    raw["dailySleepDTO"]["calendarDate"] = "2026-09-21"
    with pytest.raises(HealthSchemaError):
        normalize_health("sleep", raw, "2026-09-21", "Asia/Shanghai")


@pytest.mark.parametrize("raw", [None, {}, []])
@pytest.mark.parametrize("kind", list(SOURCES))
def test_missing_is_not_zero(kind, raw):
    assert normalize_health(kind, raw, DAY.isoformat(), "Asia/Shanghai") == {}


def test_numeric_zero_is_preserved_and_negative_sentinel_is_missing():
    assert normalize_health("stress", {"avgStressLevel": 0}, DAY.isoformat(), "Asia/Shanghai") == {"average": 0}
    assert normalize_health("stress", {"avgStressLevel": -1}, DAY.isoformat(), "Asia/Shanghai") == {}
    assert normalize_health("rhr", {"allMetrics": {"metricsMap": {"WELLNESS_RESTING_HEART_RATE": [
        {"calendarDate": DAY.isoformat(), "value": 50}]}}}, DAY.isoformat(), "Asia/Shanghai") == {"bpm": 50}
    with pytest.raises(HealthSchemaError):
        normalize_health("hrv", {"changed": "shape"}, DAY.isoformat(), "Asia/Shanghai")


def test_battery_uses_descriptors_and_ignores_invalid():
    raw = [{"date": DAY.isoformat(), "bodyBatteryValueDescriptorDTOList": [
        {"bodyBatteryValueDescriptorKey": "timestamp", "bodyBatteryValueDescriptorIndex": 1},
        {"bodyBatteryValueDescriptorKey": "bodyBatteryLevel", "bodyBatteryValueDescriptorIndex": 0}],
        "bodyBatteryValuesArray": [[0, CUTOFF.timestamp() * 1000], [-1, CUTOFF.timestamp() * 1000]]}]
    metrics = normalize_health("battery", raw, DAY.isoformat(), "Asia/Shanghai")
    assert len(metrics["samples"]) == 1
    assert metrics["samples"][0]["value"] == 0


def test_request_errors_are_redacted_and_not_unsupported():
    error = RuntimeError("PASSWORD=private COOKIE=private TOKEN=private")
    error.response = SimpleNamespace(status_code=403)
    status, detail = safe_error(error)
    assert status == "error" and "403" in detail and "private" not in detail
    error.response.status_code = 404
    assert safe_error(error)[0] == "error"


def test_sync_partial_failure_and_cache(tmp_path):
    store = HealthStore(tmp_path / "data" / "test.sqlite", tmp_path / "data")
    class Api:
        calls = 0
        def get_stress_data(self, day):
            self.calls += 1
            return {"avgStressLevel": 0, "calendarDate": day}
        def get_hrv_data(self, day):
            raise RuntimeError("secret")
        def get_training_readiness(self, day):
            return []
    api = Api()
    day = date(2020, 1, 1)
    first = sync_health(api, store, day, day, "Asia/Shanghai", delay=0)
    assert {row.status for row in first} == {"error", "unsupported", "available", "no_data"}
    second = sync_health(api, store, day, day, "Asia/Shanghai", delay=0)
    assert api.calls == 1
    assert any(row.status == "cached" for row in second)
    assert "secret" not in json.dumps([row.detail for row in first])


def test_rate_limit_stops_requests(tmp_path):
    from garminconnect import GarminConnectTooManyRequestsError
    class Api:
        def get_hrv_data(self, day):
            raise GarminConnectTooManyRequestsError("secret")
    store = HealthStore(tmp_path / "test.sqlite", tmp_path)
    rows = sync_health(Api(), store, DAY, DAY + timedelta(days=2), "Asia/Shanghai", delay=0)
    assert len(rows) == 1 and rows[0].status == "rate_limited"


def test_readiness_maintains_only_with_context(tmp_path):
    settings, store = seeded(tmp_path)
    result = analyze_readiness(store, settings, DAY, CUTOFF)
    assert result["action"] == "maintain" and result["confidence"] == "high"
    assert result["mode"] == "as_of"
    path = write_readiness_report(result, settings.storage.reports_dir)
    assert path.exists() and path.with_suffix(".json").exists()
    assert "禁止事项" in path.read_text()
    assert json.loads(path.with_suffix(".json").read_text())["rules_version"] == "readiness-v1"


def test_future_feedback_and_snapshots_do_not_leak_into_replay(tmp_path):
    settings, store = seeded(tmp_path)
    save_metric(store, DAY, "sleep", {"duration_s": 1000, "observed_at": (CUTOFF + timedelta(hours=1)).isoformat()}, CUTOFF + timedelta(hours=2))
    store.add_feedback(DAY.isoformat(), {"fatigue": 10, "pain": "yes"}, (CUTOFF + timedelta(hours=2)).isoformat())
    result = analyze_readiness(store, settings, DAY, CUTOFF)
    assert result["action"] == "maintain"
    assert result["metrics"]["sleep"]["duration_s"] == 28800


def test_retrospective_filters_post_cutoff_samples_and_daily_aggregates(tmp_path):
    settings = settings_at(tmp_path)
    store = HealthStore(settings.storage.database_path, settings.storage.data_dir)
    store.initialize()
    later = CUTOFF + timedelta(hours=10)
    save_metric(store, DAY, "rhr", {"bpm": 70}, later)
    save_metric(store, DAY, "stress", {"average": 70}, later)
    save_metric(store, DAY, "sleep", {"duration_s": 27000, "observed_at": (CUTOFF - timedelta(hours=1)).isoformat()}, later)
    save_metric(store, DAY, "battery", {"samples": [
        {"at": (CUTOFF - timedelta(minutes=5)).isoformat(), "value": 80},
        {"at": (CUTOFF + timedelta(hours=1)).isoformat(), "value": 10}]}, later)
    result = analyze_readiness(store, settings, DAY, CUTOFF)
    assert result["mode"] == "retrospective"
    assert result["metrics"]["battery"]["value"] == 80
    assert "rhr" not in result["metrics"] and "stress" not in result["metrics"]
    assert result["action"] == "insufficient"


def test_missing_db_does_not_create_it(tmp_path):
    settings = settings_at(tmp_path)
    store = HealthStore(settings.storage.database_path, settings.storage.data_dir)
    result = analyze_readiness(store, settings, DAY, CUTOFF)
    assert result["action"] == "insufficient" and result["confidence"] == "low"
    assert not settings.storage.database_path.exists()


def test_single_hrv_change_does_not_cancel_quality(tmp_path):
    settings, store = seeded(tmp_path)
    save_metric(store, DAY, "hrv", {"last_night_ms": 30, "weekly_ms": 30, "baseline_low_ms": 40,
                "observed_at": (CUTOFF - timedelta(hours=1)).isoformat()}, CUTOFF)
    assert analyze_readiness(store, settings, DAY, CUTOFF)["action"] == "maintain"


def test_heavy_legs_override_balanced_hrv(tmp_path):
    settings, store = seeded(tmp_path)
    store.add_feedback(DAY.isoformat(), {"fatigue": 4, "legs": 6, "pain": "no", "plan": "easy"}, CUTOFF.isoformat())
    result = analyze_readiness(store, settings, DAY, CUTOFF)
    assert result["action"] == "shorten" and result["conflicts"]


def test_single_night_is_not_compared_to_garmin_seven_day_band(tmp_path):
    settings, store = seeded(tmp_path)
    for offset in (0, 1):
        day = DAY - timedelta(days=offset)
        save_metric(store, day, "hrv", {"last_night_ms": 30, "weekly_ms": 60, "baseline_low_ms": 40,
            "observed_at": (CUTOFF - timedelta(days=offset, hours=1)).isoformat()}, CUTOFF)
    result = analyze_readiness(store, settings, DAY, CUTOFF)
    assert result["action"] == "maintain"
    assert not any("均值低于" in item for item in result["evidence"])


def test_missing_weekly_hrv_cannot_authorize_quality(tmp_path):
    settings, store = seeded(tmp_path)
    save_metric(store, DAY, "hrv", {"last_night_ms": 60, "baseline_low_ms": 40,
                "observed_at": (CUTOFF - timedelta(hours=1)).isoformat()}, CUTOFF)
    assert analyze_readiness(store, settings, DAY, CUTOFF)["action"] == "insufficient"


def test_completed_easy_run_with_mild_soreness_is_not_full_recovery_or_unknown_pain(tmp_path):
    settings, store = seeded(tmp_path)
    store.add_feedback(DAY.isoformat(), {"completed": True, "effort": "easy", "leg_feeling": "normal",
        "soreness": "mild", "pain": "unknown", "plan": "easy"}, CUTOFF.isoformat())
    result = analyze_readiness(store, settings, DAY, CUTOFF)
    assert result["action"] == "observe"
    assert result["mode"] == "post_activity" and result["confidence"] == "medium"
    assert result["feedback"].get("fatigue") is None
    assert result["conflicts"]


def test_qualitative_feedback_cli_does_not_invent_scores(tmp_path):
    config = config_file(tmp_path)
    result = runner.invoke(app, ["feedback", "--config", str(config), "--date", "2026-09-22",
        "--non-interactive", "--completed", "--effort", "easy", "--leg-feeling", "normal", "--soreness", "mild"])
    assert result.exit_code == 0
    store = HealthStore(tmp_path / "data/test.sqlite", tmp_path / "data")
    row = store.feedback("2026-09-22", datetime.now(timezone.utc) + timedelta(seconds=1))
    assert row["fatigue"] is None and row["legs"] is None and row["pain"] == "unknown"
    assert row["soreness"] == "mild" and row["completed"] is True


def test_sustained_autonomic_and_sleep_changes_downgrade(tmp_path):
    settings, store = seeded(tmp_path)
    for offset in (0, 1):
        day = DAY - timedelta(days=offset)
        save_metric(store, day, "rhr", {"bpm": 60}, CUTOFF)
        save_metric(store, day, "sleep", {"duration_s": 20000,
            "observed_at": (CUTOFF - timedelta(days=offset, hours=1)).isoformat()}, CUTOFF)
    result = analyze_readiness(store, settings, DAY, CUTOFF)
    assert result["action"] == "downgrade"
    assert result["conflicts"]


def test_monday_remains_rest_even_without_health(tmp_path):
    settings = settings_at(tmp_path)
    store = HealthStore(settings.storage.database_path, settings.storage.data_dir)
    day, cutoff = DAY - timedelta(days=1), CUTOFF - timedelta(days=1)
    result = analyze_readiness(store, settings, day, cutoff)
    assert result["action"] == "rest" and result["confidence"] == "low"


def test_pain_or_severe_fatigue_recommends_rest(tmp_path):
    settings, store = seeded(tmp_path)
    store.add_feedback(DAY.isoformat(), {"pain": "yes"}, CUTOFF.isoformat())
    assert analyze_readiness(store, settings, DAY, CUTOFF)["action"] == "rest"


def test_stale_health_and_feedback_cannot_authorize_training(tmp_path):
    settings, store = seeded(tmp_path)
    result = analyze_readiness(store, settings, DAY, CUTOFF + timedelta(hours=8))
    assert result["action"] == "insufficient"
    assert any("超过" in note for note in result["missing"])


def test_training_context_deduplicates_without_deleting_and_excludes_future(tmp_path):
    settings, store = seeded(tmp_path)
    activities = ActivityStore(settings.storage.database_path)
    for activity_id, day in (("a", "2026-09-21"), ("b", "2026-09-21"), ("future", "2026-09-23")):
        activities.upsert_activity(ActivityRecord(activity_id, "running", day + "T06:00:00", "Synthetic",
            22000, 7200, "sample.json", "sample.fit", day + "T22:00:00", 130, 150))
    with sqlite3.connect(store.database) as conn:
        conn.execute("UPDATE activities SET synced_at=?", ((CUTOFF - timedelta(hours=1)).isoformat(),))
    result = analyze_readiness(store, settings, DAY, CUTOFF)
    assert result["recent_training"]["distance_km"] == 22
    assert result["recent_training"]["duplicates_excluded"] == 1
    assert len(activities.list_activities()) == 3


def test_training_window_includes_exactly_seven_local_dates(tmp_path):
    settings, store = seeded(tmp_path)
    activities = ActivityStore(store.database)
    for offset in (6, 7):
        day = (DAY - timedelta(days=offset)).isoformat()
        activities.upsert_activity(ActivityRecord(str(offset), "running", day + "T06:00:00",
            "Synthetic", 10000, 3600, "missing.json", "missing.fit"))
    with sqlite3.connect(store.database) as conn:
        conn.execute("UPDATE activities SET synced_at=?", ((CUTOFF - timedelta(hours=1)).isoformat(),))
    result = analyze_readiness(store, settings, DAY, CUTOFF)
    assert result["recent_training"]["distance_km"] == 10
    assert [row["activity_id"] for row in result["recent_training"]["activities"]] == ["6"]


def test_post_cutoff_request_failure_does_not_change_known_decision(tmp_path):
    settings, store = seeded(tmp_path)
    store.save(DAY.isoformat(), "hrv", "error", "network", (CUTOFF + timedelta(hours=1)).isoformat())
    result = analyze_readiness(store, settings, DAY, CUTOFF)
    assert result["action"] == "maintain"


def test_unfinished_activity_does_not_contribute_future_distance(tmp_path):
    settings, store = seeded(tmp_path)
    ActivityStore(store.database).upsert_activity(ActivityRecord("current", "running", DAY.isoformat() + "T06:00:00",
        "Synthetic", 30000, 10800, "missing.json", "missing.fit"))
    result = analyze_readiness(store, settings, DAY, CUTOFF)
    assert result["recent_training"]["distance_km"] == 0
    assert result["recent_training"]["unfinished_excluded"] == 1
    assert result["trained_before_cutoff"]
    assert result["action"] == "insufficient"
    assert result["mode"] == "retrospective"


def test_zero_feedback_is_known_and_recent_no_data_blocks_positive_permission(tmp_path):
    settings, store = seeded(tmp_path)
    store.add_feedback(DAY.isoformat(), {"fatigue": 0, "legs": 0, "pain": "no", "plan": "easy"}, CUTOFF.isoformat())
    assert analyze_readiness(store, settings, DAY, CUTOFF)["action"] == "maintain"
    store.save(DAY.isoformat(), "hrv", "no_data", "empty", CUTOFF.isoformat(), raw={})
    result = analyze_readiness(store, settings, DAY, CUTOFF)
    assert result["action"] == "insufficient"
    assert store.snapshots(DAY.isoformat(), DAY.isoformat())


def config_file(tmp_path):
    config = tmp_path / "athlete.yaml"
    config.write_text(f"storage:\n  data_dir: {tmp_path / 'data'}\n  database_path: {tmp_path / 'data/test.sqlite'}\n  reports_dir: {tmp_path / 'reports'}\n")
    return config


@pytest.mark.parametrize("command", [["health", "doctor"], ["health", "sync"], ["feedback"], ["report", "readiness"]])
def test_cli_help(command):
    assert runner.invoke(app, command + ["--help"]).exit_code == 0


def test_cli_missing_config_and_bad_dates(tmp_path):
    result = runner.invoke(app, ["health", "doctor", "--config", str(tmp_path / "missing")])
    assert result.exit_code == 1 and "缺少" in result.output
    config = config_file(tmp_path)
    result = runner.invoke(app, ["report", "readiness", "--config", str(config), "--date", "2026/09/22"])
    assert result.exit_code == 2 and "YYYY-MM-DD" in result.output
    result = runner.invoke(app, ["report", "readiness", "--config", str(config), "--date", "2026-09-22", "--at", "25:00"])
    assert result.exit_code == 2 and "HH:MM" in result.output


def test_cli_missing_db_still_reports_insufficient(tmp_path):
    config = config_file(tmp_path)
    result = runner.invoke(app, ["report", "readiness", "--config", str(config), "--date", "2026-09-22"])
    assert result.exit_code == 0 and "证据不足" in result.output
    assert not (tmp_path / "data/test.sqlite").exists()


def test_cli_feedback_preserves_note_and_unknown_values(tmp_path):
    config = config_file(tmp_path)
    result = runner.invoke(app, ["feedback", "--config", str(config), "--date", "2026-09-22",
                               "--non-interactive", "--note", "  感觉一般\n没有追配速  "])
    assert result.exit_code == 0
    store = HealthStore(tmp_path / "data/test.sqlite", tmp_path / "data")
    row = store.feedback("2026-09-22", datetime.now(timezone.utc) + timedelta(seconds=1))
    assert row["fatigue"] is None and row["pain"] == "unknown"
    assert row["note"] == "  感觉一般\n没有追配速  "


def test_cli_doctor_empty_data_warns_without_claiming_unsupported(tmp_path, monkeypatch):
    config = config_file(tmp_path)
    api = SimpleNamespace(**{method: lambda day: None for _, method in SOURCES.values()})
    monkeypatch.setattr("garmin_runner.health_cli.create_garmin_client", lambda *a, **k: api)
    monkeypatch.setattr("garmin_runner.health.time.sleep", lambda n: None)
    result = runner.invoke(app, ["health", "doctor", "--config", str(config)])
    assert result.exit_code == 0 and "WARN HRV" in result.output
    assert "暂无数据 7" in result.output


def test_cli_sanitizes_unexpected_login_error(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("PASSWORD=secret TOKEN=cookie")
    monkeypatch.setattr("garmin_runner.health_cli.create_garmin_client", fail)
    result = runner.invoke(app, ["health", "doctor", "--config", str(config_file(tmp_path))])
    assert result.exit_code == 1 and "secret" not in result.output and "cookie" not in result.output


def test_recovery_config_validates_thresholds(tmp_path):
    config = tmp_path / "athlete.yaml"
    config.write_text("recovery:\n  min_baseline_days: 30\n  baseline_days: 28\n")
    with pytest.raises(ValueError):
        load_settings(config)
