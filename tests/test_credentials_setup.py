from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

import garmin_runner.cli as cli
from garmin_runner.cli import app


runner = CliRunner()


def test_setup_credentials_writes_env_without_printing_password(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_gitignore(tmp_path)
    config = _write_config(tmp_path)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: "super-secret")

    result = runner.invoke(
        app,
        ["setup-credentials", "--config", str(config)],
        input="runner@example.com\n",
    )

    assert result.exit_code == 0
    env_content = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "GARMIN_EMAIL=runner@example.com" in env_content
    assert "GARMIN_PASSWORD=super-secret" in env_content
    assert "super-secret" not in result.output
    assert "garmin-runner doctor" in result.output


def test_setup_credentials_keeps_existing_env(tmp_path: Path, monkeypatch) -> None:
    _write_gitignore(tmp_path)
    config = _write_config(tmp_path)
    env_path = tmp_path / ".env"
    env_path.write_text("GARMIN_EMAIL=old@example.com\n", encoding="utf-8")
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: "new-secret")

    result = runner.invoke(
        app,
        ["setup-credentials", "--config", str(config)],
        input="new@example.com\nkeep\n",
    )

    assert result.exit_code == 0
    assert env_path.read_text(encoding="utf-8") == "GARMIN_EMAIL=old@example.com\n"
    assert "new-secret" not in result.output
    assert "保留现有 .env" in result.output


def test_setup_credentials_backup_and_overwrite(tmp_path: Path, monkeypatch) -> None:
    _write_gitignore(tmp_path)
    config = _write_config(tmp_path)
    env_path = tmp_path / ".env"
    env_path.write_text("GARMIN_EMAIL=old@example.com\n", encoding="utf-8")
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: "new-secret")

    result = runner.invoke(
        app,
        ["setup-credentials", "--config", str(config)],
        input="new@example.com\nbackup and overwrite\n",
    )

    assert result.exit_code == 0
    assert "GARMIN_EMAIL=new@example.com" in env_path.read_text(encoding="utf-8")
    assert "GARMIN_PASSWORD=new-secret" in env_path.read_text(encoding="utf-8")
    assert list(tmp_path.glob(".env.backup-*"))
    assert "new-secret" not in result.output


def test_setup_credentials_email_only(tmp_path: Path) -> None:
    _write_gitignore(tmp_path)
    config = _write_config(tmp_path)

    result = runner.invoke(
        app,
        ["setup-credentials", "--email-only", "--config", str(config)],
        input="runner@example.com\n",
    )

    assert result.exit_code == 0
    env_content = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "GARMIN_EMAIL=runner@example.com" in env_content
    assert "GARMIN_PASSWORD" not in env_content
    assert "密码未保存" in result.output


def _write_gitignore(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text(".env\n", encoding="utf-8")


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
""",
        encoding="utf-8",
    )
    return config
