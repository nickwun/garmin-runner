from __future__ import annotations

import json
import os
import sys
import tomllib
from datetime import date, datetime
from pathlib import Path
from typing import Annotated

from dotenv import load_dotenv
import typer

from garmin_runner.analysis.single_activity import (
    analyze_activity,
    training_config_from_settings,
)
from garmin_runner.config import load_settings
from garmin_runner.fit import decode_fit_messages, extract_time_series, record_messages
from garmin_runner.garmin_client import GarminRunnerLoginError, create_garmin_client
from garmin_runner.reporting.daily import write_daily_report
from garmin_runner.storage import ActivityStore
from garmin_runner.sync import sync_running_activities

app = typer.Typer(help="Local-first Garmin running data sync and analysis toolkit.")


@app.callback()
def main() -> None:
    """Local-first Garmin running data sync and analysis toolkit."""


@app.command()
def sync(
    since: Annotated[
        str,
        typer.Option(
            "--since",
            help="同步这个日期以来的跑步活动，格式 YYYY-MM-DD。",
        ),
    ],
    config: Annotated[
        Path,
        typer.Option("--config", help="本地配置文件路径。"),
    ] = Path("config/athlete.yaml"),
) -> None:
    """Sync Garmin running activities into local files and SQLite."""
    settings = load_settings(config)
    since_date = _parse_date(since)
    try:
        api = create_garmin_client(settings.garmin, mfa_callback=_prompt_mfa)
        result = sync_running_activities(api, settings, since_date)
    except GarminRunnerLoginError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc
    except Exception as exc:
        typer.secho(f"同步失败：{exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(
        f"同步完成：扫描 {result.scanned} 条，下载 {result.downloaded} 条，跳过 {result.skipped} 条。"
    )


@app.command()
def analyze(
    activity_id: Annotated[str, typer.Argument(help="要分析的 Garmin activity_id。")],
    config: Annotated[
        Path,
        typer.Option("--config", help="本地配置文件路径。"),
    ] = Path("config/athlete.yaml"),
) -> None:
    """Analyze one synced activity and write a Chinese Markdown report."""
    settings = load_settings(config)
    try:
        training_config = training_config_from_settings(settings)
        store = ActivityStore(settings.storage.database_path)
        store.initialize()
        activity = store.get_activity(activity_id)
        if activity is None:
            raise ValueError(f"SQLite 中找不到 activity_id={activity_id}，请先运行 sync。")

        summary_path = _resolve_local_path(activity["summary_path"])
        fit_path = _resolve_local_path(activity["fit_path"])
        if not summary_path.exists():
            raise FileNotFoundError(f"找不到 summary JSON：{summary_path}")
        if not fit_path.exists():
            raise FileNotFoundError(f"找不到 FIT 文件：{fit_path}")

        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        messages, errors = decode_fit_messages(fit_path)
        if errors:
            raise ValueError(f"FIT 解析失败，activity_id={activity_id}")
        points = extract_time_series(messages)
        analysis = analyze_activity(summary, points, training_config)
        report_path = write_daily_report(analysis, settings.storage.reports_dir)
    except Exception as exc:
        typer.secho(f"分析失败：{exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"报告已生成：{report_path}")


@app.command()
def doctor(
    config: Annotated[
        Path,
        typer.Option("--config", help="本地配置文件路径。"),
    ] = Path("config/athlete.yaml"),
    skip_login: Annotated[
        bool,
        typer.Option("--skip-login", help="跳过 Garmin 登录检查，主要用于本地离线诊断。"),
    ] = False,
) -> None:
    """Check whether the local project can run safely."""
    project_root = _project_root_from_config(config)
    env_path = project_root / ".env"
    if env_path.exists():
        load_dotenv(env_path)
    settings = load_settings(config)
    failed = False

    typer.echo("garmin-runner doctor")
    failed |= _doctor_item(
        "Python 版本",
        _python_version_ok(),
        f"当前 {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "pyproject.toml 要求 Python >=3.11",
    )
    failed |= _doctor_item(
        "项目目录",
        _looks_like_project(Path.cwd()),
        str(Path.cwd()),
        "当前目录不像 garmin-runner 项目",
    )
    failed |= _doctor_item(".env 文件", env_path.exists(), str(env_path), "未找到 .env")
    failed |= _doctor_item(
        "config/athlete.yaml",
        config.exists(),
        str(config),
        "未找到 config/athlete.yaml",
    )
    failed |= _doctor_writable_dir("data/ 目录", settings.storage.data_dir)
    failed |= _doctor_writable_dir("reports/ 目录", settings.storage.reports_dir)
    failed |= _doctor_sqlite(settings.storage.database_path)

    email_set = bool(os.getenv(settings.garmin.email_env))
    password_set = bool(os.getenv(settings.garmin.password_env))
    failed |= _doctor_item(
        "Garmin 环境变量",
        email_set and password_set,
        f"{settings.garmin.email_env}=已设置, {settings.garmin.password_env}=已设置"
        if email_set and password_set
        else "账号或密码环境变量未完整设置",
        "请在 .env 中设置 Garmin 账号环境变量",
    )

    if skip_login:
        _print_status("WARN", "Garmin 登录", "已跳过登录检查")
    else:
        login_failed = _doctor_garmin_login(settings)
        failed |= login_failed

    if failed:
        raise typer.Exit(code=1)


@app.command("list")
def list_activities_command(
    since: Annotated[
        str | None,
        typer.Option("--since", help="只列出这个日期以来的活动，格式 YYYY-MM-DD。"),
    ] = None,
    limit: Annotated[int, typer.Option("--limit", min=1, max=200)] = 20,
    config: Annotated[
        Path,
        typer.Option("--config", help="本地配置文件路径。"),
    ] = Path("config/athlete.yaml"),
) -> None:
    """List recently synced running activities from SQLite."""
    since_value = None
    if since is not None:
        since_value = _parse_date(since).isoformat()

    settings = load_settings(config)
    if not settings.storage.database_path.exists():
        typer.secho("SQLite 数据库不存在，请先运行 sync。", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    store = ActivityStore(settings.storage.database_path)
    activities = store.list_recent_activities(limit=limit, since=since_value)
    if not activities:
        typer.echo("没有活动。")
        return

    typer.echo(
        "activity_id  start_time           name                 distance_km  duration  avg_pace  avg_hr"
    )
    for activity in activities:
        typer.echo(
            f"{activity['activity_id']:<12} "
            f"{_short_value(activity.get('start_time_local'), 19):<19} "
            f"{_short_value(activity.get('activity_name'), 20):<20} "
            f"{_format_km(activity.get('distance_m')):<11} "
            f"{_format_duration(activity.get('duration_s')):<9} "
            f"{_format_pace(activity.get('duration_s'), activity.get('distance_m')):<9} "
            f"{_format_int(activity.get('average_hr'))}"
        )


@app.command()
def inspect(
    activity_id: Annotated[str, typer.Argument(help="要检查的 Garmin activity_id。")],
    config: Annotated[
        Path,
        typer.Option("--config", help="本地配置文件路径。"),
    ] = Path("config/athlete.yaml"),
) -> None:
    """Inspect one synced activity's local data completeness."""
    settings = load_settings(config)
    if not settings.storage.database_path.exists():
        typer.secho("SQLite 数据库不存在，请先运行 sync。", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    store = ActivityStore(settings.storage.database_path)
    activity = store.get_activity(activity_id)
    if activity is None:
        _print_status("FAIL", "SQLite", f"找不到 activity_id={activity_id}")
        raise typer.Exit(code=1)

    _print_status("PASS", "SQLite", f"找到 activity_id={activity_id}")
    summary_path = _resolve_local_path(activity["summary_path"])
    fit_path = _resolve_local_path(activity["fit_path"])
    summary: dict[str, object] | None = None

    if summary_path.exists():
        _print_status("PASS", "summary JSON", str(summary_path))
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        _print_summary_overview(summary)
    else:
        _print_status("FAIL", "summary JSON", f"不存在: {summary_path}")

    if fit_path.exists():
        _print_status("PASS", "FIT 文件", str(fit_path))
        try:
            messages, errors = decode_fit_messages(fit_path)
            if errors:
                _print_status("WARN", "FIT 解析", "解析返回错误，未展开原始记录")
            records = record_messages(messages)
            typer.echo(f"FIT records 数量: {len(records)}")
            fields = sorted({key for record in records for key in record})
            typer.echo("FIT 可用字段: " + (", ".join(fields) if fields else "N/A"))
            _print_missing_fit_warnings(fields)
        except Exception as exc:
            _print_status("WARN", "FIT 解析", f"无法解析 FIT: {exc}")
    else:
        _print_status("WARN", "FIT 文件", f"不存在: {fit_path}")
        typer.echo("FIT records 数量: N/A")
        typer.echo("FIT 可用字段: N/A")


def _prompt_mfa() -> str:
    return typer.prompt("请输入 Garmin MFA 验证码", hide_input=False)


def _parse_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise typer.BadParameter("日期格式必须是 YYYY-MM-DD") from exc


def _resolve_local_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return Path.cwd() / path


def _project_root_from_config(config: Path) -> Path:
    if config.parent.name == "config":
        return config.parent.parent
    return Path.cwd()


def _python_version_ok() -> bool:
    pyproject = Path("pyproject.toml")
    if not pyproject.exists():
        return False
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    requires = str(data.get("project", {}).get("requires-python", ">=3.11"))
    if not requires.startswith(">="):
        return True
    version = requires.removeprefix(">=").split(".")
    major = int(version[0])
    minor = int(version[1]) if len(version) > 1 else 0
    return sys.version_info >= (major, minor)


def _looks_like_project(path: Path) -> bool:
    return (
        (path / "pyproject.toml").exists()
        and (path / "src" / "garmin_runner").is_dir()
        and (path / "config" / "athlete.example.yaml").exists()
    )


def _doctor_item(label: str, ok: bool, detail: str, fail_detail: str) -> bool:
    if ok:
        _print_status("PASS", label, detail)
        return False
    _print_status("FAIL", label, fail_detail)
    return True


def _doctor_writable_dir(label: str, path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".garmin-runner-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        _print_status("PASS", label, f"可写: {path}")
        return False
    except OSError as exc:
        _print_status("FAIL", label, f"不可写: {path} ({exc})")
        return True


def _doctor_sqlite(path: Path) -> bool:
    try:
        store = ActivityStore(path)
        store.initialize()
        _print_status("PASS", "SQLite 数据库", f"可连接: {path}")
        return False
    except Exception as exc:
        _print_status("FAIL", "SQLite 数据库", f"不可连接: {exc}")
        return True


def _doctor_garmin_login(settings: object) -> bool:
    try:
        api = create_garmin_client(settings.garmin, mfa_callback=_prompt_mfa)  # type: ignore[attr-defined]
        _print_status("PASS", "Garmin 登录", "登录可用")
        try:
            activities = api.get_activities(0, 1, activitytype="running")
            count = len(activities) if isinstance(activities, list) else 0
            _print_status("PASS", "最近 running activity", f"可读取 {count} 条摘要")
        except Exception as exc:
            _print_status("WARN", "最近 running activity", f"登录成功，但读取失败: {exc}")
        return False
    except GarminRunnerLoginError as exc:
        _print_status("FAIL", "Garmin 登录", str(exc))
        return True
    except Exception as exc:
        _print_status("FAIL", "Garmin 登录", f"登录检查失败: {exc}")
        return True


def _print_status(status: str, label: str, detail: str) -> None:
    typer.echo(f"{label}: {status} - {detail}")


def _short_value(value: object, width: int) -> str:
    text = "" if value is None else str(value)
    return text if len(text) <= width else text[: width - 1] + "…"


def _format_km(distance_m: object) -> str:
    try:
        return f"{float(distance_m) / 1000:.2f}"
    except (TypeError, ValueError):
        return "N/A"


def _format_duration(seconds: object) -> str:
    try:
        total = int(round(float(seconds)))
    except (TypeError, ValueError):
        return "N/A"
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _format_pace(duration_s: object, distance_m: object) -> str:
    try:
        duration = float(duration_s)
        distance = float(distance_m)
    except (TypeError, ValueError):
        return "N/A"
    if distance <= 0:
        return "N/A"
    pace = int(round(duration / (distance / 1000)))
    minutes, secs = divmod(pace, 60)
    return f"{minutes}:{secs:02d} /km"


def _format_int(value: object) -> str:
    try:
        return str(round(float(value)))
    except (TypeError, ValueError):
        return "N/A"


def _print_summary_overview(summary: dict[str, object]) -> None:
    activity_type = summary.get("activityType")
    if isinstance(activity_type, dict):
        activity_type_value = activity_type.get("typeKey") or activity_type.get("typeId")
    else:
        activity_type_value = activity_type
    fields = {
        "activity_id": summary.get("activityId"),
        "start_time": summary.get("startTimeLocal") or summary.get("startTimeGMT"),
        "activity_type": activity_type_value,
        "name": summary.get("activityName"),
        "distance": summary.get("distance"),
        "duration": summary.get("duration"),
        "moving_duration": summary.get("movingDuration"),
        "average_hr": summary.get("averageHR") or summary.get("avgHR"),
        "max_hr": summary.get("maxHR"),
        "average_speed": summary.get("averageSpeed"),
        "average_pace": summary.get("averagePace"),
    }
    typer.echo("summary 关键字段:")
    for key, value in fields.items():
        typer.echo(f"- {key}: {_short_value(value, 80) if value is not None else 'N/A'}")
    missing = [key for key, value in fields.items() if value is None]
    if missing:
        _print_status("WARN", "summary 缺失字段", ", ".join(missing))


def _print_missing_fit_warnings(fields: list[str]) -> None:
    interesting = [
        "timestamp",
        "distance",
        "heart_rate",
        "speed",
        "enhanced_speed",
        "cadence",
        "altitude",
        "position_lat",
        "position_long",
    ]
    missing = [field for field in interesting if field not in fields]
    if missing:
        _print_status("WARN", "FIT 缺失字段", ", ".join(missing))
