from __future__ import annotations

import json
import os
import sys
import tomllib
import getpass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Annotated

from dotenv import load_dotenv
import typer

from garmin_runner.analysis.single_activity import (
    analyze_activity,
    training_config_from_settings,
)
from garmin_runner.analysis.monthly import (
    MonthlyContext,
    analyze_month,
    month_bounds,
)
from garmin_runner.analysis.weekly import (
    WeeklyActivity,
    WeeklyContext,
    WeeklyTrainingStructure,
    analyze_week,
)
from garmin_runner.config import load_settings
from garmin_runner.fit import decode_fit_messages, extract_time_series, record_messages
from garmin_runner.garmin_client import GarminRunnerLoginError, create_garmin_client
from garmin_runner.reporting.daily import write_daily_report
from garmin_runner.reporting.monthly import write_monthly_report
from garmin_runner.reporting.weekly import write_weekly_report
from garmin_runner.storage import ActivityStore
from garmin_runner.sync import sync_running_activities

app = typer.Typer(help="Local-first Garmin running data sync and analysis toolkit.")
report_app = typer.Typer(help="Generate deterministic training reports.")
app.add_typer(report_app, name="report")


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


@report_app.command("weekly")
def weekly_report(
    week: Annotated[
        str | None,
        typer.Option("--week", help="ISO 周，例如 current 或 2026-W25。"),
    ] = "current",
    since: Annotated[
        str | None,
        typer.Option("--since", help="自定义周期开始日期，格式 YYYY-MM-DD。"),
    ] = None,
    until: Annotated[
        str | None,
        typer.Option("--until", help="自定义周期结束日期，格式 YYYY-MM-DD。"),
    ] = None,
    config: Annotated[
        Path,
        typer.Option("--config", help="本地配置文件路径。"),
    ] = Path("config/athlete.yaml"),
) -> None:
    """Generate a Chinese Markdown weekly training report."""
    if not config.exists():
        typer.secho(f"缺少本地配置文件：{config}", fg=typer.colors.RED)
        raise typer.Exit(code=1)
    try:
        week_start, week_end = _weekly_range(week=week, since=since, until=until)
        settings = load_settings(config)
        store = ActivityStore(settings.storage.database_path)
        if not settings.storage.database_path.exists():
            raise ValueError("SQLite 数据库不存在，请先运行 sync。")
        training_config = training_config_from_settings(settings)
        activities = [
            _weekly_activity_from_row(row, settings.storage.reports_dir, training_config)
            for row in store.list_activities_between(
                week_start.isoformat(), week_end.isoformat()
            )
        ]
        previous_start = week_start - timedelta(days=7)
        previous_end = week_start - timedelta(days=1)
        previous_distance = store.sum_distance_between(
            previous_start.isoformat(), previous_end.isoformat()
        )
        four_week_start = week_start - timedelta(days=28)
        four_week_end = week_start - timedelta(days=1)
        recent_4w_distance = store.sum_distance_between(
            four_week_start.isoformat(), four_week_end.isoformat()
        )
        recent_4w_avg = recent_4w_distance / 4 if recent_4w_distance else None
        analysis = analyze_week(
            WeeklyContext(
                week_start=week_start,
                week_end=week_end,
                activities=activities,
                previous_week_distance_km=previous_distance,
                recent_4w_avg_distance_km=recent_4w_avg,
                structure=_weekly_structure_from_settings(settings),
            )
        )
        report_path = write_weekly_report(analysis, settings.storage.reports_dir)
    except Exception as exc:
        typer.secho(f"周报生成失败：{exc}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc

    typer.echo(f"周报已生成：{report_path}")


@report_app.command("monthly")
def monthly_report(
    month: Annotated[
        str,
        typer.Option("--month", help="月份，例如 current 或 2026-06。"),
    ] = "current",
    config: Annotated[
        Path,
        typer.Option("--config", help="本地配置文件路径。"),
    ] = Path("config/athlete.yaml"),
) -> None:
    """Generate a Chinese Markdown monthly training report."""
    if not config.exists():
        typer.secho(f"缺少本地配置文件：{config}", fg=typer.colors.RED)
        raise typer.Exit(code=1)
    try:
        month_start, month_end = month_bounds(month)
        settings = load_settings(config)
        store = ActivityStore(settings.storage.database_path)
        if not settings.storage.database_path.exists():
            raise ValueError("SQLite 数据库不存在，请先运行 sync。")
        training_config = training_config_from_settings(settings)
        activities = [
            _weekly_activity_from_row(row, settings.storage.reports_dir, training_config)
            for row in store.list_activities_between(
                month_start.isoformat(), month_end.isoformat()
            )
        ]
        analysis = analyze_month(
            MonthlyContext(
                month_start=month_start,
                month_end=month_end,
                activities=activities,
                structure=_weekly_structure_from_settings(settings),
            )
        )
        report_path = write_monthly_report(analysis, settings.storage.reports_dir)
    except Exception as exc:
        typer.secho(f"月报生成失败：{exc}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc

    typer.echo(f"月报已生成：{report_path}")


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
            fields = sorted({str(key) for record in records for key in record})
            typer.echo("FIT 可用字段: " + (", ".join(fields) if fields else "N/A"))
            _print_missing_fit_warnings(fields)
        except Exception as exc:
            _print_status("WARN", "FIT 解析", f"无法解析 FIT: {exc}")
    else:
        _print_status("WARN", "FIT 文件", f"不存在: {fit_path}")
        typer.echo("FIT records 数量: N/A")
        typer.echo("FIT 可用字段: N/A")


@app.command("setup-credentials")
def setup_credentials(
    config: Annotated[
        Path,
        typer.Option("--config", help="本地配置文件路径。"),
    ] = Path("config/athlete.yaml"),
    email_only: Annotated[
        bool,
        typer.Option("--email-only", help="只保存 Garmin email，不保存密码。"),
    ] = False,
) -> None:
    """Safely write local Garmin credentials to .env."""
    settings = load_settings(config)
    project_root = _project_root_from_config(config)
    env_path = project_root / ".env"
    if not _is_env_ignored(project_root):
        typer.secho(
            ".env 尚未被 .gitignore 忽略。请先修正 .gitignore，避免误提交凭证。",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)

    email = typer.prompt("请输入 Garmin email").strip()
    if not email:
        typer.secho("Garmin email 不能为空。", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    password: str | None = None
    if email_only:
        typer.echo("密码未保存。你可以用环境变量或稍后手动写入 GARMIN_PASSWORD。")
    else:
        password = getpass.getpass("请输入 Garmin password（输入会隐藏）: ")
        if not password:
            typer.secho("Garmin password 不能为空。", fg=typer.colors.RED)
            raise typer.Exit(code=1)

    action = "overwrite"
    if env_path.exists():
        action = _prompt_env_action()
        if action == "keep":
            typer.echo("保留现有 .env，未写入新凭证。")
            return

    backup_path = write_credentials_env(
        env_path=env_path,
        email_env=settings.garmin.email_env,
        password_env=settings.garmin.password_env,
        email=email,
        password=password,
        action=action,
    )
    if backup_path is not None:
        typer.echo(f"已备份现有 .env: {backup_path}")
    typer.echo(".env 已写入本地凭证。不会输出密码，也不会提交到 Git。")
    if email_only:
        typer.echo("密码未保存；运行 doctor 前请设置密码环境变量或手动补充 .env。")
    typer.echo("如果 Garmin 要求 MFA，doctor 或 sync 会提示你输入验证码。")
    typer.echo("下一步可以运行：garmin-runner doctor")


def _prompt_mfa() -> str:
    return typer.prompt("请输入 Garmin MFA 验证码", hide_input=False)


def _parse_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise typer.BadParameter("日期格式必须是 YYYY-MM-DD") from exc


def _weekly_range(
    week: str | None,
    since: str | None,
    until: str | None,
) -> tuple[date, date]:
    if since or until:
        if not since or not until:
            raise ValueError("--since 和 --until 必须同时提供。")
        start = _parse_date(since)
        end = _parse_date(until)
        if end < start:
            raise ValueError("--until 不能早于 --since。")
        return start, end

    value = week or "current"
    if value == "current":
        today = date.today()
        start = today - timedelta(days=today.weekday())
        return start, start + timedelta(days=6)
    try:
        year_text, week_text = value.split("-W", 1)
        start = date.fromisocalendar(int(year_text), int(week_text), 1)
    except (ValueError, TypeError) as exc:
        raise ValueError("--week 必须是 current 或 YYYY-Www，例如 2026-W25。") from exc
    return start, start + timedelta(days=6)


def _weekly_structure_from_settings(settings: object) -> WeeklyTrainingStructure:
    training = settings.training
    return WeeklyTrainingStructure(
        rest_day=training.weekly_rest_day,
        normal_volume_min_km=training.normal_weekly_volume_min_km,
        normal_volume_max_km=training.normal_weekly_volume_max_km,
        tuesday_quality=training.tuesday_quality,
        friday_steady=training.friday_steady,
        weekend_long_run=training.weekend_long_run,
        marathon_goal=training.marathon_goal,
        b_race_note=training.b_race_note,
    )


def _weekly_activity_from_row(
    activity: dict[str, object],
    reports_dir: Path,
    training_config: object,
) -> WeeklyActivity:
    summary_path = _resolve_local_path(str(activity["summary_path"]))
    fit_path = _resolve_local_path(str(activity["fit_path"]))
    if not summary_path.exists():
        raise FileNotFoundError(f"找不到 summary JSON：{summary_path}")
    if not fit_path.exists():
        raise FileNotFoundError(f"找不到 FIT 文件：{fit_path}")

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    messages, errors = decode_fit_messages(fit_path)
    if errors:
        raise ValueError(f"FIT 解析失败，activity_id={activity['activity_id']}")
    points = extract_time_series(messages)
    analysis = analyze_activity(summary, points, training_config)
    report_path = write_daily_report(analysis, reports_dir)
    intensity_distance = None
    intensity_duration = None
    if analysis.workout_breakdown is not None:
        intensity_distance = analysis.workout_breakdown.main.distance_km
        intensity_duration = analysis.workout_breakdown.main.duration_s
    return WeeklyActivity(
        activity_id=analysis.basic.activity_id,
        activity_date=analysis.basic.activity_date,
        activity_name=analysis.basic.activity_name,
        distance_km=analysis.basic.distance_km or 0.0,
        duration_s=analysis.basic.duration_s or 0.0,
        average_hr=analysis.basic.average_hr,
        training_type=analysis.training_type,
        execution_score=analysis.execution_score,
        report_path=report_path,
        intensity_distance_km=intensity_distance,
        intensity_duration_s=intensity_duration,
    )


def _resolve_local_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return Path.cwd() / path


def _project_root_from_config(config: Path) -> Path:
    if config.parent.name == "config":
        return config.parent.parent
    return Path.cwd()


def _is_env_ignored(project_root: Path) -> bool:
    gitignore = project_root / ".gitignore"
    if not gitignore.exists():
        return False
    patterns = [
        line.strip()
        for line in gitignore.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    return ".env" in patterns or "/.env" in patterns


def _prompt_env_action() -> str:
    typer.echo(".env 已存在，请选择处理方式：")
    typer.echo("- overwrite")
    typer.echo("- keep")
    typer.echo("- backup and overwrite")
    while True:
        action = typer.prompt("请输入选择").strip().lower()
        if action in {"overwrite", "keep", "backup and overwrite"}:
            return action
        typer.echo("无效选择，请输入 overwrite、keep 或 backup and overwrite。")


def write_credentials_env(
    env_path: Path,
    email_env: str,
    password_env: str,
    email: str,
    password: str | None,
    action: str,
) -> Path | None:
    env_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path = None
    if env_path.exists() and action == "backup and overwrite":
        backup_path = env_path.with_name(
            f".env.backup-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        )
        backup_path.write_text(env_path.read_text(encoding="utf-8"), encoding="utf-8")
    lines = [
        "# Local Garmin credentials. Do not commit this file.",
        f"{email_env}={email}",
    ]
    if password is not None:
        lines.append(f"{password_env}={password}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return backup_path


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
    values = _summary_values(summary)
    activity_type = summary.get("activityType") or summary.get("activityTypeDTO")
    if isinstance(activity_type, dict):
        activity_type_value = activity_type.get("typeKey") or activity_type.get("typeId")
    else:
        activity_type_value = activity_type
    fields = {
        "activity_id": summary.get("activityId"),
        "start_time": values.get("startTimeLocal") or values.get("startTimeGMT"),
        "activity_type": activity_type_value,
        "name": summary.get("activityName"),
        "distance": values.get("distance"),
        "duration": values.get("duration"),
        "moving_duration": values.get("movingDuration"),
        "average_hr": values.get("averageHR") or values.get("avgHR"),
        "max_hr": values.get("maxHR"),
        "average_speed": values.get("averageSpeed"),
        "average_pace": values.get("averagePace"),
    }
    typer.echo("summary 关键字段:")
    for key, value in fields.items():
        typer.echo(f"- {key}: {_short_value(value, 80) if value is not None else 'N/A'}")
    missing = [key for key, value in fields.items() if value is None]
    if fields.get("average_pace") is None and fields.get("average_speed") is not None:
        missing = [key for key in missing if key != "average_pace"]
    if missing:
        _print_status("WARN", "summary 缺失字段", ", ".join(missing))


def _summary_values(summary: dict[str, object]) -> dict[str, object]:
    nested = summary.get("summaryDTO")
    if isinstance(nested, dict):
        return nested
    return summary


def _print_missing_fit_warnings(fields: list[str]) -> None:
    missing = []
    for field in ("timestamp", "distance", "heart_rate", "cadence", "position_lat", "position_long"):
        if field not in fields:
            missing.append(field)
    if "speed" not in fields and "enhanced_speed" not in fields:
        missing.append("speed/enhanced_speed")
    if "altitude" not in fields and "enhanced_altitude" not in fields:
        missing.append("altitude/enhanced_altitude")
    if missing:
        _print_status("WARN", "FIT 缺失字段", ", ".join(missing))
