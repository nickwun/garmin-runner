import copy
import hashlib
import json
import subprocess
from datetime import date

import pytest
from typer.testing import CliRunner

from garmin_runner.cli import app
from garmin_runner.config import AppSettings, GarminSettings, StorageSettings, TrainingSettings
from garmin_runner.knowledge_coach import build_context, plan_for, record_plan, validate_plan, write_context
from garmin_runner.storage import ActivityRecord, ActivityStore
from garmin_runner.health_storage import HealthStore


DAY = date(2026, 9, 22)


@pytest.fixture
def local(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("knowledge/\ndata/\nreports/\n")
    s = AppSettings(GarminSettings(), StorageSettings(database_path=tmp_path / "data/test.sqlite",
        data_dir=tmp_path / "data", reports_dir=tmp_path / "reports"), TrainingSettings())
    store = ActivityStore(s.storage.database_path)
    store.initialize()
    source = tmp_path / "knowledge/sources/feedback.md"
    source.parent.mkdir(parents=True)
    source.write_text("Synthetic user confirmation of schedule; not a medical assessment.")
    p = {"schema_version": 1, "plan_id": "test-v1", "recorded_on": "2026-09-21",
         "effective_since": "2026-09-21", "effective_until": "2026-10-18", "status": "user_confirmed",
         "scope": "structure", "cycle_anchor": "2026-09-21", "supersedes": None, "reason": "Synthetic plan",
         "sources": [{"path": "knowledge/sources/feedback.md", "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                      "quote": "Synthetic user confirmation", "kind": "user_report"}],
         "weeks": [{str(i): {"kind": "unknown"} for i in range(7)} for _ in range(2)]}
    p["weeks"][0]["0"] = {"kind": "rest"}
    p["weeks"][0]["2"] = {"kind": "threshold"}
    p["weeks"][0]["5"] = {"kind": "specific_long"}
    p["weeks"][1]["0"] = {"kind": "rest"}
    p["weeks"][1]["1"] = {"kind": "quality"}
    p["weeks"][1]["4"] = {"kind": "steady_or_mp"}
    return tmp_path, s, store, p


def record(root, plan):
    file = root / "knowledge/input.json"
    file.write_text(json.dumps(plan))
    return record_plan(root, file, DAY)


def test_plan_is_private_idempotent_and_rejects_overwrite(local):
    root, s, store, p = local
    target = record(root, p)
    before = target.read_bytes()
    assert record(root, p) == target
    assert target.stat().st_mode & 0o777 == 0o600
    p["reason"] = "Changed"
    with pytest.raises(ValueError, match="不可覆盖"):
        record(root, p)
    assert target.read_bytes() == before


def test_alternating_weeks_and_future_knowledge(local):
    root, _, _, p = local
    assert plan_for(date(2026, 9, 23), DAY, [p])["kind"] == "threshold"
    assert plan_for(date(2026, 9, 26), DAY, [p])["kind"] == "specific_long"
    assert plan_for(date(2026, 9, 29), DAY, [p])["kind"] == "quality"
    assert plan_for(date(2026, 10, 2), DAY, [p])["kind"] == "steady_or_mp"
    assert plan_for(DAY, date(2026, 9, 20), [p])["state"] == "unknown"
    assert plan_for(date(2026, 10, 19), DAY, [p])["state"] == "unknown"


def test_context_shows_two_week_cycle(local):
    root, s, _, p = local
    record(root, p)
    rows = {r["day"]: r for r in build_context(s, root, DAY)["upcoming"]}
    assert len(rows) == 14
    assert rows["2026-09-23"]["plan"]["kind"] == "threshold"
    assert rows["2026-09-26"]["plan"]["kind"] == "specific_long"
    assert rows["2026-09-29"]["plan"]["kind"] == "quality"
    assert rows["2026-10-02"]["plan"]["kind"] == "steady_or_mp"


@pytest.mark.parametrize("has_run", [False, True])
def test_confirmed_no_run_feedback_preserved_not_full_rest(local, has_run):
    root, s, store, _ = local
    health = HealthStore(s.storage.database_path, s.storage.data_dir)
    health.initialize()
    health.add_feedback("2026-09-22", {"ran": False, "note": "Synthetic other sport", "secret": "SECRET"},
                        "2026-09-22T01:00:00+00:00")
    health.add_feedback("2026-09-22", {"effort": "easy", "note": "Synthetic later note"},
                        "2026-09-22T02:00:00+00:00")
    if has_run:
        store.upsert_activity(ActivityRecord("run", "running", "2026-09-22T06:00:00", "Synthetic", 10000,
                                            3600, "data/s.json", "data/a.fit", "2026-09-22T22:00:00", 130, 140))
    before = s.storage.database_path.read_bytes()
    d = build_context(s, root, DAY, 1)
    assert d["review"][0]["reported_ran"] is False
    assert len(d["feedback"]) == 2 and "SECRET" not in json.dumps(d)
    assert ("冲突" if has_run else "不等于全休") in d["review"][0]["assessment"]
    assert "Synthetic other sport" in write_context(s, root, DAY, 1).read_text()
    assert s.storage.database_path.read_bytes() == before


def test_feedback_cutoff_uses_local_date(local):
    root, s, _, _ = local
    health = HealthStore(s.storage.database_path, s.storage.data_dir)
    health.initialize()
    health.add_feedback("2026-09-22", {"ran": True}, "2026-09-22T15:59:00+00:00")
    health.add_feedback("2026-09-22", {"ran": False}, "2026-09-22T16:01:00+00:00")
    d = build_context(s, root, DAY, 1)
    assert len(d["feedback"]) == 1
    assert "反馈已跑步" in d["review"][0]["assessment"]


def test_ai_proposal_never_approved_by_import(local):
    root, s, _, p = local
    p["sources"][0]["kind"] = "historical_ai_proposal"
    with pytest.raises(ValueError):
        record(root, p)
    p["status"] = "proposal"
    record(root, p)
    d = build_context(s, root, DAY, 7)
    assert all(r["plan"]["state"] == "unknown" for r in d["upcoming"])
    assert any("没有已确认计划" in w for w in d["warnings"])


def test_revision_and_cancel_effective_dates(local):
    root, _, _, p = local
    old = record(root, p).read_bytes()
    v2 = copy.deepcopy(p)
    v2.update(plan_id="test-v2", supersedes="test-v1", recorded_on="2026-09-22", effective_since="2026-09-28")
    v2["weeks"][0]["2"] = {"kind": "easy"}
    record(root, v2)
    assert plan_for(date(2026, 9, 23), DAY, [p, v2])["plan_id"] == "test-v1"
    assert plan_for(date(2026, 9, 30), DAY, [p, v2])["plan_id"] == "test-v2"
    assert plan_for(date(2026, 9, 30), date(2026, 9, 21), [p, v2])["plan_id"] == "test-v1"
    cancel = copy.deepcopy(v2)
    cancel.update(plan_id="test-v3", supersedes="test-v2", status="cancelled", weeks=[])
    record(root, cancel)
    assert plan_for(date(2026, 9, 30), DAY, [p, v2, cancel])["state"] == "unknown"
    assert (root / "knowledge/plans/versions/test-v1.json").read_bytes() == old


def test_unrelated_confirmed_plans_conflict_not_silently_selected(local):
    root, s, _, p = local
    record(root, p)
    other = copy.deepcopy(p)
    other["plan_id"] = "other"
    record(root, other)
    d = build_context(s, root, DAY, 3)
    assert d["review"][-1]["plan"]["state"] == "conflict"


@pytest.mark.parametrize("change", [
    {"cycle_anchor": "2026-09-22"}, {"recorded_on": "20260921"}, {"supersedes": "missing"},
    {"plan_id": "../unsafe"}, {"status": "approved"}, {"extra_secret": "SECRET"},
    {"recorded_on": "2999-01-01"}, {"weeks": []},
])
def test_invalid_plan_rejected_without_version(local, change):
    root, _, _, p = local
    p.update(change)
    with pytest.raises((ValueError, TypeError)):
        record(root, p)
    assert not list((root / "knowledge/plans/versions").glob("*.json"))


def test_structure_cannot_imply_numeric_dose(local):
    root, _, _, p = local
    p["weeks"][0]["2"]["target_km"] = 15
    with pytest.raises(ValueError):
        validate_plan(p, root)


def test_open_ended_structure_does_not_invent_expiry(local):
    root, _, _, p = local
    p["effective_until"] = None
    validate_plan(p, root)
    assert plan_for(date(2026, 10, 19), DAY, [p])["state"] == "confirmed"


def test_source_hash_quote_and_external_path_gates(local):
    root, _, _, p = local
    for path in ("../secret.md", str(root / "knowledge/sources/feedback.md"), ".env"):
        bad = copy.deepcopy(p)
        bad["sources"][0]["path"] = path
        with pytest.raises(ValueError):
            validate_plan(bad, root)
    p["sources"][0]["quote"] = "not in original"
    with pytest.raises(ValueError):
        record(root, p)


def test_symlink_parent_and_privacy_gate(local):
    root, s, _, p = local
    outside = root / "elsewhere"
    outside.mkdir()
    (root / "knowledge/plans").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        record(root, p)
    assert not list(outside.iterdir())
    (root / ".gitignore").write_text("")
    with pytest.raises(ValueError):
        write_context(s, root, DAY)


def test_readonly_facts_dedup_unknown_days_and_rest_conflict(local):
    root, s, store, p = local
    record(root, p)
    for aid in ("run", "copy"):
        store.upsert_activity(ActivityRecord(aid, "running", "2026-09-21T06:00:00", "Synthetic", 10000, 3600,
            "data/summary.json", "data/activity.fit", "2026-09-21T22:00:00", 130, 140))
    before = s.storage.database_path.read_bytes()
    d = build_context(s, root, DAY, 3)
    assert d["review"][-2]["distance_km"] == 10
    assert "冲突" in d["review"][-2]["assessment"]
    assert d["review"][-1]["distance_km"] is None
    assert len(d["activities"]) == 2
    a = write_context(s, root, DAY, 3)
    b = write_context(s, root, DAY, 3)
    assert a != b and a.exists() and a.stat().st_mode & 0o777 == 0o600
    assert s.storage.database_path.read_bytes() == before


def test_catalog_roles_hash_changes_and_recorded_dates(local):
    root, s, _, p = local
    folder = root / "knowledge/coach_context"
    folder.mkdir()
    entry = {k: v for k, v in p["sources"][0].items() if k != "quote"}
    entry["recorded_on"] = "2026-09-22"
    (folder / "catalog.json").write_text(json.dumps([entry]))
    assert len(build_context(s, root, DAY, 1)["sources"]) == 1
    assert not build_context(s, root, date(2026, 9, 21), 1)["sources"]
    (root / entry["path"]).write_text("CHANGED_PRIVATE_TEXT")
    d = build_context(s, root, DAY, 1)
    assert not d["sources"] and any("已变更" in w for w in d["warnings"])
    assert "CHANGED_PRIVATE_TEXT" not in json.dumps(d)


def test_missing_database_does_not_create_empty_file(local):
    root, s, _, _ = local
    s.storage.database_path.unlink()
    with pytest.raises(ValueError):
        write_context(s, root, DAY)
    assert not s.storage.database_path.exists()


def test_context_cli_help_date_errors_and_safe_failures(local, monkeypatch):
    root, s, _, _ = local
    runner = CliRunner()
    for cmd in ("context", "plan-record"):
        assert runner.invoke(app, ["knowledge", cmd, "--help"]).exit_code == 0
    monkeypatch.chdir(root)
    config = root / "athlete.yaml"
    config.write_text("{}")
    monkeypatch.setattr("garmin_runner.knowledge_coach.load_settings", lambda _: s)
    for day in ("wrong", "20260922", "2999-01-01"):
        r = runner.invoke(app, ["knowledge", "context", "--as-of", day, "--config", str(config)])
        assert r.exit_code == 2 and "日期" in r.output
    def fail(*args):
        raise ValueError("SECRET_TOKEN")
    monkeypatch.setattr("garmin_runner.knowledge_coach.write_context", fail)
    r = runner.invoke(app, ["knowledge", "context", "--config", str(config)])
    assert r.exit_code == 1 and "SECRET_TOKEN" not in r.output and "Traceback" not in r.output
    assert runner.invoke(app, ["knowledge", "plan-record", "--file", ".env"]).exit_code == 1
