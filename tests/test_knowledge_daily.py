import hashlib
import json
import subprocess
from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from garmin_runner.cli import app
from garmin_runner.config import AppSettings, GarminSettings, StorageSettings, TrainingSettings
from garmin_runner.dashboard import build_dashboard, write_dashboard
from garmin_runner.knowledge_daily import run_daily
from garmin_runner.knowledge_dashboard import dashboard_knowledge
from garmin_runner.storage import ActivityStore


DAY = date(2026, 9, 22)


@pytest.fixture
def local(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("knowledge/\ndata/\nreports/\n")
    settings = AppSettings(GarminSettings(), StorageSettings(database_path=tmp_path / "data/test.sqlite",
        data_dir=tmp_path / "data", reports_dir=tmp_path / "reports"), TrainingSettings())
    ActivityStore(settings.storage.database_path).initialize()
    root = tmp_path / "knowledge"
    for relative in ("profile/current.md", "plans/README.md", "races/README.md", "insights/open_questions.md",
                     "sessions/2026/sample.md", "sources/raw.md"):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Synthetic local document\n\nNot a training prescription.")
    catalog = root / "coach_context/catalog.json"
    catalog.parent.mkdir(parents=True)
    source = root / "sources/raw.md"
    catalog.write_text(json.dumps([{"path": "knowledge/sources/raw.md", "recorded_on": "2026-09-21",
        "kind": "user_report", "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}]))
    (root / "sessions/catalog.json").write_text(json.dumps({"sessions": [
        {"day": "2026-09-22", "recorded_at": "2026-09-22", "document": "2026/sample.md"}]}))
    plan = {"schema_version": 1, "plan_id": "synthetic", "recorded_on": "2026-09-21", "effective_since": "2026-09-21",
            "effective_until": None, "status": "user_confirmed", "scope": "structure", "cycle_anchor": "2026-09-21",
            "weeks": [{str(i): {"kind": "rest" if i == 0 else "unknown"} for i in range(7)}],
            "reason": "Synthetic", "sources": [{"path": "knowledge/sources/raw.md", "kind": "user_report",
              "quote": "Not a training prescription.", "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}], "supersedes": None}
    versions = root / "plans/versions"
    versions.mkdir()
    (versions / "synthetic.json").write_text(json.dumps(plan))
    return tmp_path, settings


def test_daily_refresh_preserves_originals_and_keeps_packages(local):
    root, settings = local
    before = settings.storage.database_path.read_bytes()
    old = settings.storage.reports_dir / "daily/2026-09-22_a.md"
    old.parent.mkdir(parents=True)
    old.write_text("Personalized existing report")
    result = run_daily(settings, root, DAY)
    assert result["status"] == "ready"
    assert result["coach_status"] == "awaiting_review" and result["sync_status"] == "not_checked"
    assert all(v == "success" for v in result["stages"].values())
    assert Path(result["dashboard"]).is_file()
    second = run_daily(settings, root, DAY)
    assert second["context"]["path"] != result["context"]["path"]
    assert (root / result["context"]["path"]).exists()
    assert settings.storage.database_path.read_bytes() == before
    assert old.read_text() == "Personalized existing report"
    assert (root / "knowledge/coach_context/daily/latest.json").stat().st_mode & 0o777 == 0o600


def test_partial_failure_refreshes_dashboard_without_old_context(local, monkeypatch):
    root, settings = local
    run_daily(settings, root, DAY)
    def fail(*args):
        raise ValueError("SECRET_COOKIE")
    monkeypatch.setattr("garmin_runner.knowledge_daily.write_context", fail)
    result = run_daily(settings, root, DAY)
    assert result["status"] == "partial" and result["context"] is None
    assert result["stages"]["dashboard"] == "success"
    assert "SECRET_COOKIE" not in json.dumps(result)
    kb = dashboard_knowledge(root, settings.storage.reports_dir, DAY)
    assert kb["status"] == "partial" and not kb["plan"]
    assert not any(d["category"] == "每日教练" for d in kb["documents"])


def test_missing_database_and_missing_sources_stay_explicit(local):
    root, settings = local
    settings.storage.database_path.unlink()
    result = run_daily(settings, root, DAY)
    assert result["status"] == "partial" and result["stages"]["dashboard"] == "success"
    assert not settings.storage.database_path.exists()


def test_missing_source_catalog_is_not_complete_success(local):
    root, settings = local
    (root / "knowledge/coach_context/catalog.json").unlink()
    result = run_daily(settings, root, DAY)
    assert result["status"] == "partial"
    assert result["stages"]["context"] == "success"
    assert result["stages"]["dashboard"] == "success"
    assert result["warnings"]


def test_dashboard_failure_keeps_previous_html(local, monkeypatch):
    root, settings = local
    path = Path(run_daily(settings, root, DAY)["dashboard"])
    before = path.read_bytes()
    monkeypatch.setattr("garmin_runner.dashboard.write_dashboard", lambda *args: (_ for _ in ()).throw(ValueError("SECRET")))
    result = run_daily(settings, root, DAY)
    assert result["status"] == "partial" and result["stages"]["dashboard"] == "failed"
    assert "SECRET" not in json.dumps(result) and path.read_bytes() == before


def test_knowledge_privacy_gate(local):
    root, settings = local
    (root / ".gitignore").write_text("")
    with pytest.raises(ValueError):
        run_daily(settings, root, DAY)
    assert dashboard_knowledge(root, settings.storage.reports_dir, DAY)["status"] == "unavailable"
    assert not (root / "knowledge/coach_context/daily/latest.json").exists()


def test_dashboard_knowledge_readonly_links_staleness_and_hash(local):
    root, settings = local
    result = run_daily(settings, root, DAY)
    kb = dashboard_knowledge(root, settings.storage.reports_dir, DAY)
    assert kb["status"] == "ready" and len(kb["plan"]) == 29
    assert any(d["category"] == "完整训练" for d in kb["documents"])
    assert all((settings.storage.reports_dir / "dashboard" / d["href"]).is_file() for d in kb["documents"])
    assert dashboard_knowledge(root, settings.storage.reports_dir, date(2026, 9, 23))["status"] == "stale"
    old = dashboard_knowledge(root, settings.storage.reports_dir, date(2026, 9, 21))
    assert not old["plan"] and not old["documents"]
    (root / result["context"]["path"]).write_text("{}")
    invalid = dashboard_knowledge(root, settings.storage.reports_dir, DAY)
    assert invalid["status"] == "invalid" and not invalid["plan"]


def test_source_changes_and_unsafe_document_paths(local):
    root, settings = local
    run_daily(settings, root, DAY)
    (root / "knowledge/sources/raw.md").write_text("Changed source")
    assert dashboard_knowledge(root, settings.storage.reports_dir, DAY)["status"] == "stale"
    (root / ".env").write_text("PRIVATE_CREDENTIAL")
    p = root / "knowledge/profile/current.md"
    p.unlink()
    p.symlink_to(root / ".env")
    (root / "knowledge/sessions/catalog.json").write_text(json.dumps({"sessions": [
        {"day": "2026-09-22", "recorded_at": "2026-09-22", "document": "../../.env"}]}))
    kb = dashboard_knowledge(root, settings.storage.reports_dir, DAY)
    assert "PRIVATE_CREDENTIAL" not in json.dumps(kb) and kb["warnings"]


def test_knowledge_html_does_not_execute_source_markup(local):
    root, settings = local
    attack = '</script><script>window.injected=1</script>'
    (root / "knowledge/profile/current.md").write_text(attack)
    result = run_daily(settings, root, DAY)
    html = Path(result["dashboard"]).read_text()
    assert attack not in html and 'data-view="knowledge"' in html
    assert "connect-src 'none'" in html


def test_future_schedule_and_proposal_are_separate(local):
    root, settings = local
    source = root / "knowledge/sources/raw.md"
    item = {"kind": "historical_proposal", "date": "2026-09-23", "title": "Synthetic draft",
            "detail": "Synthetic dose", "conditions": "Pending confirmation", "source": {
              "path": "knowledge/sources/raw.md", "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
              "quote": "Not a training prescription.", "kind": "historical_ai_proposal"}}
    path = root / "knowledge/plans/outlook.json"
    path.write_text(json.dumps({"schema_version": 1, "recorded_on": "2026-09-22", "items": [item]}))
    run_daily(settings, root, DAY)
    kb = dashboard_knowledge(root, settings.storage.reports_dir, DAY)
    assert kb["plan"][-1]["day"] == "2026-10-20"
    assert kb["outlook"][0]["status"] == "历史建议，待确认"
    row = next(r for r in kb["plan"] if r["day"] == item["date"])
    assert row["kind"] == "未确定" and row["dose"] == "未确认"
    assert not dashboard_knowledge(root, settings.storage.reports_dir, date(2026, 9, 24))["outlook"]
    item["date"] = None
    path.write_text(json.dumps({"schema_version": 1, "recorded_on": "2026-09-22", "items": [item]}))
    assert dashboard_knowledge(root, settings.storage.reports_dir, DAY)["outlook"][0]["date"] is None
    item["source"]["sha256"] = "bad"
    path.write_text(json.dumps({"schema_version": 1, "recorded_on": "2026-09-22", "items": [item]}))
    bad = dashboard_knowledge(root, settings.storage.reports_dir, DAY)
    assert not bad["outlook"] and any("未来计划" in w for w in bad["warnings"])


def test_confirmed_session_dose_is_whole_session_and_readonly(local):
    root, settings = local
    p = root / "knowledge/plans/versions/synthetic.json"
    plan = json.loads(p.read_text())
    plan["scope"] = "sessions"
    plan["weeks"][0]["2"] = {"kind": "easy", "target_km": 10}
    p.write_text(json.dumps(plan))
    before = p.read_bytes()
    run_daily(settings, root, DAY)
    kb = dashboard_knowledge(root, settings.storage.reports_dir, DAY)
    row = next(r for r in kb["plan"] if r["day"] == "2026-09-23")
    assert row["dose"].startswith("整堂 10 km") and row["source_key"] == str(p.relative_to(root))
    assert p.read_bytes() == before


def test_daily_cli_validation_and_safe_failures(local, monkeypatch):
    root, settings = local
    monkeypatch.chdir(root)
    config = root / "config.yaml"
    config.write_text("{}")
    monkeypatch.setattr("garmin_runner.knowledge_daily.load_settings", lambda _: settings)
    runner = CliRunner()
    assert runner.invoke(app, ["knowledge", "daily", "--help"]).exit_code == 0
    for day in ("20260922", "wrong", "2999-01-01"):
        r = runner.invoke(app, ["knowledge", "daily", "--as-of", day, "--config", str(config)])
        assert r.exit_code == 2 and "Traceback" not in r.output
    r = runner.invoke(app, ["knowledge", "daily", "--as-of", DAY.isoformat(), "--config", str(config)])
    assert r.exit_code == 0 and "打开训练看板" in r.output
    settings.storage.database_path.unlink()
    r = runner.invoke(app, ["knowledge", "daily", "--as-of", DAY.isoformat(), "--config", str(config)])
    assert r.exit_code == 1 and "FAIL" in r.output and "Traceback" not in r.output
