import hashlib
import json
import subprocess
from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from garmin_runner.cli import app
from garmin_runner.config import AppSettings, GarminSettings, StorageSettings, TrainingSettings
from garmin_runner.knowledge import collect_index, write_index
from garmin_runner.storage import ActivityRecord, ActivityStore


DAY = date(2026, 10, 9)


def setup(tmp_path):
    settings = AppSettings(GarminSettings(), StorageSettings(database_path=tmp_path / "data/test.sqlite",
        data_dir=tmp_path / "data", reports_dir=tmp_path / "reports"), TrainingSettings())
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("knowledge/\ndata/\nreports/\n")
    store = ActivityStore(settings.storage.database_path)
    store.initialize()
    return settings, store


def add(store, aid="a", day="2026-10-09", kind="running", name="Synthetic"):
    store.upsert_activity(ActivityRecord(aid, kind, day + "T06:00:00", name, 10000, 3600,
        "data/summary.json", "data/activity.fit", day + "T22:00:00", 130, 140))


def test_all_ids_preserved_duplicates_marked_and_database_unchanged(tmp_path):
    s, store = setup(tmp_path)
    add(store)
    add(store, "copy")
    add(store, "ride", kind="cycling")
    add(store, "future", day="2026-10-10")
    before = hashlib.sha256(s.storage.database_path.read_bytes()).hexdigest()
    d = collect_index(s, tmp_path, DAY)
    assert len(d["activities"]) == 2
    assert d["activities"][1]["duplicate_of"] == "a"
    assert d["activities"][0]["summary_path"] is None
    assert hashlib.sha256(s.storage.database_path.read_bytes()).hexdigest() == before


def test_index_is_private_repeatable_and_leaves_manual_sources_alone(tmp_path):
    s, store = setup(tmp_path)
    add(store, name="a|b\n<script>")
    source = tmp_path / "knowledge/sources/original.md"
    source.parent.mkdir(parents=True)
    source.write_text("原话  \n保持不变")
    report = tmp_path / "reports/daily/2026-10-09_a.md"
    report.parent.mkdir(parents=True)
    report.write_text("# Original report")
    for _ in range(2):
        path = write_index(s, tmp_path, DAY)
        assert path.stat().st_mode & 0o777 == 0o600
    data = json.loads((path.parent / "activities.json").read_text())
    assert data["activities"][0]["daily_report"] == "reports/daily/2026-10-09_a.md"
    assert "<script>" not in (path.parent / "2026.md").read_text()
    assert "a&#124;b" in (path.parent / "2026.md").read_text()
    assert source.read_text() == "原话  \n保持不变"
    assert report.read_text() == "# Original report"


def test_privacy_gate_rejects_not_ignored_or_tracked(tmp_path):
    s, store = setup(tmp_path)
    (tmp_path / ".gitignore").write_text("")
    with pytest.raises(ValueError):
        write_index(s, tmp_path, DAY)
    assert not (tmp_path / "knowledge").exists()
    p = tmp_path / "knowledge/private.md"
    p.parent.mkdir()
    p.write_text("private")
    subprocess.run(["git", "add", "knowledge/private.md"], cwd=tmp_path, check=True)
    (tmp_path / ".gitignore").write_text("knowledge/\n")
    with pytest.raises(ValueError):
        write_index(s, tmp_path, DAY)


def test_missing_database_not_created(tmp_path):
    s, _ = setup(tmp_path)
    s.storage.database_path.unlink()
    with pytest.raises(ValueError):
        collect_index(s, tmp_path, DAY)
    assert not s.storage.database_path.exists()


def test_single_file_ignore_does_not_authorize_exporting_directory(tmp_path):
    s, _ = setup(tmp_path)
    (tmp_path / ".gitignore").write_text("knowledge/index/activities.json\n")
    with pytest.raises(ValueError):
        write_index(s, tmp_path, DAY)
    assert not (tmp_path / "knowledge").exists()


def test_symlink_output_is_rejected(tmp_path):
    s, _ = setup(tmp_path)
    target = tmp_path / "other"
    target.mkdir()
    (tmp_path / "knowledge").symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError):
        write_index(s, tmp_path, DAY)
    assert not list(target.iterdir())


def test_empty_database_and_external_symlink(tmp_path):
    s, store = setup(tmp_path)
    assert collect_index(s, tmp_path, DAY)["activities"] == []
    add(store)
    secret = tmp_path / "secret"
    secret.write_text("not read")
    (tmp_path / "data/activity.fit").symlink_to(secret)
    assert collect_index(s, tmp_path, DAY)["activities"][0]["fit_path"] is None


def test_cli_help_date_and_safe_failure(tmp_path, monkeypatch):
    runner = CliRunner()
    assert runner.invoke(app, ["knowledge", "index", "--help"]).exit_code == 0
    s, _ = setup(tmp_path)
    config = tmp_path / "config.yaml"
    config.write_text("{}")
    monkeypatch.setattr("garmin_runner.knowledge.load_settings", lambda _: s)
    result = runner.invoke(app, ["knowledge", "index", "--config", str(config), "--as-of", "bad"])
    assert result.exit_code == 2 and "日期" in result.output
    def fail(*args):
        raise RuntimeError("SECRET")
    monkeypatch.setattr("garmin_runner.knowledge.write_index", fail)
    result = runner.invoke(app, ["knowledge", "index", "--config", str(config)])
    assert result.exit_code == 1 and "SECRET" not in result.output
