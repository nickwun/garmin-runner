from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from garmin_runner.cli import app
from garmin_runner.storage import ActivityRecord, ActivityStore


runner = CliRunner()


def test_list_reports_missing_database(tmp_path: Path) -> None:
    config = _write_config(tmp_path)

    result = runner.invoke(app, ["list", "--config", str(config)])

    assert result.exit_code == 1
    assert "请先运行 sync" in result.output


def test_list_reports_empty_database(tmp_path: Path) -> None:
    config = _write_config(tmp_path)
    settings_db = tmp_path / "data" / "garmin-runner.sqlite"
    ActivityStore(settings_db).initialize()

    result = runner.invoke(app, ["list", "--config", str(config)])

    assert result.exit_code == 0
    assert "没有活动" in result.output


def test_list_rejects_bad_since_date(tmp_path: Path) -> None:
    config = _write_config(tmp_path)

    result = runner.invoke(
        app,
        ["list", "--config", str(config), "--since", "2026/06/01"],
    )

    assert result.exit_code != 0
    assert "日期格式必须是 YYYY-MM-DD" in result.output


def test_list_outputs_recent_activities(tmp_path: Path) -> None:
    config = _write_config(tmp_path)
    store = ActivityStore(tmp_path / "data" / "garmin-runner.sqlite")
    store.initialize()
    store.upsert_activity(
        ActivityRecord(
            activity_id="12345",
            activity_type="running",
            start_time_local="2026-06-01T06:30:00",
            activity_name="Morning Run",
            distance_m=5000,
            duration_s=1500,
            average_hr=142,
            max_hr=170,
            summary_path="data/raw/summary/12345.json",
            fit_path="data/raw/fit/12345.fit",
        )
    )

    result = runner.invoke(app, ["list", "--config", str(config), "--limit", "20"])

    assert result.exit_code == 0
    assert "activity_id" in result.output
    assert "12345" in result.output
    assert "5.00" in result.output
    assert "5:00 /km" in result.output
    assert "142" in result.output


def test_inspect_reports_missing_activity(tmp_path: Path) -> None:
    config = _write_config(tmp_path)
    store = ActivityStore(tmp_path / "data" / "garmin-runner.sqlite")
    store.initialize()

    result = runner.invoke(app, ["inspect", "missing", "--config", str(config)])

    assert result.exit_code == 1
    assert "SQLite: FAIL" in result.output
    assert "找不到 activity_id=missing" in result.output


def test_inspect_warns_when_fit_file_is_missing(tmp_path: Path) -> None:
    summary_path = tmp_path / "data" / "raw" / "summary" / "12345.json"
    summary_path.parent.mkdir(parents=True)
    summary_path.write_text(
        """{
  "activityId": 12345,
  "activityName": "Morning Run",
  "activityType": {"typeKey": "running"},
  "startTimeLocal": "2026-06-01T06:30:00",
  "distance": 5000,
  "duration": 1500,
  "movingDuration": 1480,
  "averageHR": 142,
  "maxHR": 170,
  "averageSpeed": 3.33
}""",
        encoding="utf-8",
    )
    config = _write_config(tmp_path)
    store = ActivityStore(tmp_path / "data" / "garmin-runner.sqlite")
    store.initialize()
    store.upsert_activity(
        ActivityRecord(
            activity_id="12345",
            activity_type="running",
            start_time_local="2026-06-01T06:30:00",
            activity_name="Morning Run",
            distance_m=5000,
            duration_s=1500,
            average_hr=142,
            max_hr=170,
            summary_path=str(summary_path),
            fit_path=str(tmp_path / "data" / "raw" / "fit" / "12345.fit"),
        )
    )

    result = runner.invoke(app, ["inspect", "12345", "--config", str(config)])

    assert result.exit_code == 0
    assert "SQLite: PASS" in result.output
    assert "summary JSON: PASS" in result.output
    assert "FIT 文件: WARN" in result.output
    assert "FIT records 数量: N/A" in result.output
    assert "Morning Run" in result.output
    assert "完整原始 JSON" not in result.output


def test_doctor_reports_project_checks_without_secret_values(tmp_path: Path) -> None:
    config = _write_config(tmp_path)
    env_path = tmp_path / ".env"
    env_path.write_text(
        "GARMIN_EMAIL=runner@example.com\nGARMIN_PASSWORD=secret\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["doctor", "--config", str(config), "--skip-login"])

    assert result.exit_code == 0
    assert "Python 版本" in result.output
    assert "Garmin 环境变量" in result.output
    assert "runner@example.com" not in result.output
    assert "secret" not in result.output


def _write_config(tmp_path: Path) -> Path:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config = config_dir / "athlete.yaml"
    config.write_text(
        f"""
garmin:
  email_env: "GARMIN_EMAIL"
  password_env: "GARMIN_PASSWORD"
  token_store: "{tmp_path / "data" / "tokens"}"
storage:
  data_dir: "{tmp_path / "data"}"
  database_path: "{tmp_path / "data" / "garmin-runner.sqlite"}"
  summary_dir: "{tmp_path / "data" / "raw" / "summary"}"
  fit_dir: "{tmp_path / "data" / "raw" / "fit"}"
  reports_dir: "{tmp_path / "reports"}"
training:
  heart_rate_zones:
    maf_low: 120
    maf_high: 145
    steady_high: 155
    threshold_high: 170
""",
        encoding="utf-8",
    )
    return config
