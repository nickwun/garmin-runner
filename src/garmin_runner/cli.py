from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Annotated

import typer

from garmin_runner.analysis.single_activity import (
    analyze_activity,
    training_config_from_settings,
)
from garmin_runner.config import load_settings
from garmin_runner.fit import decode_fit_messages, extract_time_series
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
