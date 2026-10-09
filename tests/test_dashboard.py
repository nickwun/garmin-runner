from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3

import pytest
from typer.testing import CliRunner

from garmin_runner.cli import app
from garmin_runner.config import AppSettings, GarminSettings, StorageSettings, TrainingSettings
from garmin_runner.dashboard import build_dashboard, write_dashboard, _period
from garmin_runner.health_storage import HealthStore
from garmin_runner.storage import ActivityRecord, ActivityStore


DAY = date(2026, 9, 22)


def settings(tmp_path):
    return AppSettings(GarminSettings(), StorageSettings(database_path=tmp_path / "data/test.sqlite",
        data_dir=tmp_path / "data", reports_dir=tmp_path / "reports"), TrainingSettings())


def report(s, name, content):
    path = s.storage.reports_dir / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_empty_dashboard_does_not_create_database(tmp_path):
    s = settings(tmp_path)
    d = build_dashboard(s, DAY, 7)
    assert len(d["days"]) == 7 and not d["activities"]
    assert all(row["km"] is None for row in d["days"])
    assert not s.storage.database_path.exists()
    assert d["warnings"] and d["readiness"] is None
    path = write_dashboard(d, s.storage.reports_dir)
    assert path.exists() and path.stat().st_mode & 0o777 == 0o600
    assert not s.storage.database_path.exists()


def add_activity(store, id, day="2026-09-22", kind="running"):
    store.upsert_activity(ActivityRecord(id, kind, day + "T06:00:00", "Synthetic", 10000, 3600,
                        "PRIVATE_SUMMARY_PATH", "PRIVATE_FIT_PATH", day + "T22:00:00", 130, 140))


def test_readonly_dedup_sport_filter_and_date_window(tmp_path):
    s = settings(tmp_path)
    store = ActivityStore(s.storage.database_path)
    store.initialize()
    for id in ("run", "copy"):
        add_activity(store, id)
    add_activity(store, "future", "2026-09-23")
    add_activity(store, "past", "2026-08-01")
    add_activity(store, "ride", "2026-09-21", "cycling")
    before = hashlib.sha256(s.storage.database_path.read_bytes()).hexdigest()
    d = build_dashboard(s, DAY, 7)
    assert len(d["activities"]) == 1 and d["duplicates_excluded"] == 1
    assert d["days"][-1]["km"] == 10
    assert d["days"][-2]["km"] is None
    assert "PRIVATE" not in json.dumps(d)
    assert hashlib.sha256(s.storage.database_path.read_bytes()).hexdigest() == before


@pytest.mark.parametrize("name,first,last", [("2026-W39.md", "2026-09-21", "2026-09-27"),
    ("2024-02.md", "2024-02-01", "2024-02-29"), ("2025.md", "2025-01-01", "2025-12-31"),
    ("2026-09-22_a.md", "2026-09-22", "2026-09-22")])
def test_report_periods(name, first, last):
    assert _period(name) == (date.fromisoformat(first), date.fromisoformat(last))


def test_report_discovery_excludes_future_prompts_symlinks_and_oversize(tmp_path):
    s = settings(tmp_path)
    report(s, "daily/2026-09-22_a.md", "# Valid\n\nTest")
    report(s, "daily/2026-09-23_a.md", "FUTURE")
    report(s, "coach/2026-09-22_prompt.md", "PRIVATE_PROMPT")
    report(s, "daily/2026-09-22_big.md", "x" * 256001)
    private = tmp_path / "secret.md"
    private.write_text("PRIVATE_SECRET")
    (s.storage.reports_dir / "daily/2026-09-22_link.md").symlink_to(private)
    d = build_dashboard(s, DAY, 7)
    assert len(d["reports"]) == 1
    assert d["reports"][0]["key"] == "daily/2026-09-22_a.md"
    assert "PRIVATE" not in json.dumps(d) and "FUTURE" not in json.dumps(d)
    assert any("未收录" in w for w in d["warnings"])


def test_report_count_limit_is_explicit(tmp_path, monkeypatch):
    s = settings(tmp_path)
    for i in range(3):
        report(s, f"daily/2026-09-22_{i}.md", "# Test")
    monkeypatch.setattr("garmin_runner.dashboard.MAX_REPORTS", 2)
    d = build_dashboard(s, DAY, 7)
    assert len(d["reports"]) == 2
    assert any("1 个" in w for w in d["warnings"])


def test_confirmed_no_run_is_not_unknown_or_rest(tmp_path):
    s = settings(tmp_path)
    health = HealthStore(s.storage.database_path, s.storage.data_dir)
    health.initialize()
    health.add_feedback(DAY.isoformat(), {"ran": False, "note": "其他运动，没有跑步", "private": "SECRET"})
    before = hashlib.sha256(s.storage.database_path.read_bytes()).hexdigest()
    d = build_dashboard(s, DAY, 7)
    assert d["days"][-1]["km"] == 0
    assert d["days"][-1]["confirmed_no_run"] is True
    assert d["days"][-1]["run_note"] == "其他运动，没有跑步"
    assert d["days"][-2]["km"] is None
    assert "SECRET" not in json.dumps(d)
    assert hashlib.sha256(s.storage.database_path.read_bytes()).hexdigest() == before
    health.add_feedback(DAY.isoformat(), {"ran": True})
    d = build_dashboard(s, DAY, 7)
    assert not d["days"][-1]["confirmed_no_run"]
    assert d["days"][-1]["km"] is None


def test_no_run_feedback_does_not_erase_conflicting_activity(tmp_path):
    s = settings(tmp_path)
    health = HealthStore(s.storage.database_path, s.storage.data_dir)
    health.initialize()
    store = ActivityStore(s.storage.database_path)
    store.initialize()
    add_activity(store, "a")
    health.add_feedback(DAY.isoformat(), {"ran": False, "note": "<script>bad()</script>"})
    d = build_dashboard(s, DAY, 7)
    assert d["days"][-1]["km"] == 10
    assert not d["days"][-1]["confirmed_no_run"]
    assert len(d["activities"]) == 1
    assert any("冲突" in warning for warning in d["warnings"])
    assert "<script>bad()</script>" not in write_dashboard(d, s.storage.reports_dir).read_text()


def test_health_metric_whitelist_missing_values_and_failed_refresh(tmp_path):
    s = settings(tmp_path)
    health = HealthStore(s.storage.database_path, s.storage.data_dir)
    health.initialize()
    stamp = "2026-09-22T08:00:00+00:00"
    for kind, metrics in [("hrv", {"last_night_ms": 50, "weekly_ms": 48, "baseline_low_ms": 40, "baseline_high_ms": 55}),
                          ("sleep", {"duration_s": 28800}), ("rhr", {"bpm": 49}), ("stress", {"average": 0}),
                          ("battery", {"samples": [{"at": "2026-09-22T00:00:00+00:00", "value": 90},
                               {"at": "2026-09-22T08:00:00+00:00", "value": 40},
                               {"at": "2026-09-23T00:00:00+00:00", "value": 100}]})]:
        metrics["private"] = "SECRET_RAW"
        health.save(DAY.isoformat(), kind, "available", "ok", stamp, raw={"cookie": "SECRET_RAW"}, metrics=metrics)
    health.save(DAY.isoformat(), "hrv", "error", "SECRET_ERROR", stamp)
    d = build_dashboard(s, DAY, 7)
    last = d["days"][-1]
    assert last["hrv"] == 50 and last["sleep_hours"] == 8 and last["stress"] == 0
    assert last["battery"] == 40 and "失败" in last["status"]
    assert d["days"][0]["hrv"] is None
    assert "SECRET" not in json.dumps(d)


def test_stale_readiness_and_classification_are_not_recomputed(tmp_path):
    s = settings(tmp_path)
    store = ActivityStore(s.storage.database_path)
    store.initialize()
    add_activity(store, "a")
    report(s, "daily/2026-09-22_a.md", "# Test\n- 训练类型：E 跑")
    report(s, "readiness/2026-09-21.md", "# Old")
    report(s, "readiness/2026-09-21.json", json.dumps({"date": "2026-09-21", "action_label": "维持原计划",
        "reason": "test", "confidence": "high", "cutoff": "2026-09-21T07:00:00+08:00", "private": "SECRET"}))
    d = build_dashboard(s, DAY, 7)
    assert d["activities"][0]["type"] == "E 跑"
    assert d["readiness"]["stale"]
    assert "SECRET" not in json.dumps(d)


def test_script_injection_is_encoded_and_no_external_assets(tmp_path):
    s = settings(tmp_path)
    attack = '</script><script>window.injected=1</script><img src="https://example.invalid/x">'
    report(s, "daily/2026-09-22_test.md", "# " + attack)
    d = build_dashboard(s, DAY, 7)
    output = write_dashboard(d, s.storage.reports_dir).read_text()
    assert attack not in output
    assert "\\u003c/script\\u003e" in output
    assert "connect-src 'none'" in output
    assert not re.search(r'<(?:script|link|img)[^>]+(?:src|href)=["\']https?://', output)
    assert json.loads(re.search(r'<script id="dashboard-data" type="application/json">(.*?)</script>', output, re.S)[1]) == d


def test_cli_help_and_date_errors(tmp_path):
    runner = CliRunner()
    assert runner.invoke(app, ["report", "dashboard", "--help"]).exit_code == 0
    config = tmp_path / "athlete.yaml"
    config.write_text("{}")
    for date_value in ("bad", "2026-02-30", "2999-01-01"):
        result = runner.invoke(app, ["report", "dashboard", "--config", str(config), "--until", date_value])
        assert result.exit_code == 2 and "Traceback" not in result.output
    result = runner.invoke(app, ["report", "dashboard", "--config", str(tmp_path / "missing")])
    assert result.exit_code == 1 and "缺少" in result.output


def test_cli_empty_and_unexpected_error_are_safe(tmp_path, monkeypatch):
    s = settings(tmp_path)
    config = tmp_path / "athlete.yaml"
    config.touch()
    monkeypatch.setattr("garmin_runner.dashboard_cli.load_settings", lambda _: s)
    result = CliRunner().invoke(app, ["report", "dashboard", "--config", str(config), "--until", DAY.isoformat()])
    assert result.exit_code == 0 and "[打开训练看板]" in result.output
    assert not s.storage.database_path.exists()
    def fail(*args):
        raise RuntimeError("SECRET_TOKEN")
    monkeypatch.setattr("garmin_runner.dashboard_cli.build_dashboard", fail)
    result = CliRunner().invoke(app, ["report", "dashboard", "--config", str(config)])
    assert result.exit_code == 1 and "SECRET" not in result.output and "Traceback" not in result.output
