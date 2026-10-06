from __future__ import annotations

import getpass
import sys
from collections import Counter
from contextlib import contextmanager
from dataclasses import asdict
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo

import typer

from garmin_runner.analysis.readiness import analyze_readiness
from garmin_runner.config import load_settings
from garmin_runner.garmin_client import GarminRunnerLoginError, create_garmin_client
from garmin_runner.health import SOURCES, quiet_garmin, sync_health
from garmin_runner.health_storage import HealthStore, private_json, private_text
from garmin_runner.reporting.readiness import write_readiness_report


health_app = typer.Typer(help="同步和检查本地恢复健康数据；不输出完整健康响应。")
ConfigOption = Annotated[Path, typer.Option("--config", help="本地配置文件。")]
DayOption = Annotated[str, typer.Option("--date", help="today 或 YYYY-MM-DD（运动员当地日期）。")]


class NeedsMFA(RuntimeError):
    pass


@contextmanager
def friendly_errors():
    try:
        yield
    except typer.Exit:
        raise
    except NeedsMFA:
        typer.echo("需要 Garmin MFA。请在本地可交互终端安全输入，勿发送到对话。", err=True)
        raise typer.Exit(2) from None
    except GarminRunnerLoginError:
        typer.echo("Garmin 登录失败：请检查本地凭证、token、MFA 或网络；未输出原始异常。", err=True)
        raise typer.Exit(2) from None
    except Exception:
        typer.echo("操作失败：请检查配置、目录权限及本地数据格式；未输出可能含隐私的异常。", err=True)
        raise typer.Exit(1) from None


def _mfa() -> str:
    if not sys.stdin.isatty():
        raise NeedsMFA()
    return getpass.getpass("请输入 Garmin MFA 验证码（隐藏输入）：")


def _settings(config: Path):
    if not config.exists():
        typer.echo("缺少 config/athlete.yaml，请先初始化本地配置。", err=True)
        raise typer.Exit(1)
    return load_settings(config)


def _day(value: str, tz: str) -> date:
    today = datetime.now(ZoneInfo(tz)).date()
    try:
        parsed = today if value == "today" else date.fromisoformat(value)
        if value != "today" and parsed.isoformat() != value:
            raise ValueError
    except ValueError:
        typer.echo("日期格式必须是 YYYY-MM-DD 或 today。", err=True)
        raise typer.Exit(2) from None
    if parsed > today:
        typer.echo("不能使用未来日期。", err=True)
        raise typer.Exit(2)
    return parsed


def _link(label: str, path: Path):
    typer.echo(f"{label}：{path.resolve()}")


def _run_sync(days: int, config: Path, until: str, refresh: bool, doctor: bool):
    with friendly_errors():
        settings = _settings(config)
        end = _day(until, settings.recovery.timezone)
        start = end - timedelta(days=days - 1)
        store = HealthStore(settings.storage.database_path, settings.storage.data_dir)
        with quiet_garmin():
            api = create_garmin_client(settings.garmin, mfa_callback=_mfa)
        typer.echo(f"检查 {start} 至 {end}，只输出字段可用性与请求状态。")
        def progress(item):
            if item.kind == "readiness" or item.status in {"auth_error", "rate_limited"}:
                typer.echo(f"已处理日期：{item.day}")
        results = sync_health(api, store, start, end, settings.recovery.timezone,
                              refresh=refresh, progress=progress)
        counts = Counter(item.status for item in results)
        lines = ["# 健康数据能力检查" if doctor else "# 健康数据同步验收", "",
                 f"日期范围：{start} 至 {end}；时区：{settings.recovery.timezone}", "",
                 "| 数据源 | 有效 | 暂无数据 | 请求/结构失败 | 缓存 |", "| --- | ---: | ---: | ---: | ---: |"]
        failures = {"error", "schema_error", "auth_error", "rate_limited"}
        for kind, (label, _) in SOURCES.items():
            group = Counter(item.status for item in results if item.kind == kind)
            errors = sum(group[key] for key in failures)
            mark = "FAIL" if errors else ("PASS" if group["available"] or group["cached"] else "WARN")
            summary = f"有效 {group['available']}，暂无数据 {group['no_data']}，失败 {errors}，缓存 {group['cached']}"
            typer.echo(f"{mark} {label}：{summary}")
            lines.append(f"| {label} | {group['available']} | {group['no_data']} | {errors} | {group['cached']} |")
            for detail in sorted({item.detail for item in results if item.kind == kind and item.status in failures | {"unsupported"}}):
                typer.echo(f"  {detail}")
                lines.append(f"\n{label}：{detail}\n")
        incomplete = len(results) != days * len(SOURCES)
        if incomplete:
            typer.echo("FAIL 同步已提前停止，剩余日期未请求，不能视为完整验收。")
        path = settings.storage.reports_dir / "validation" / ("health_doctor.md" if doctor else "health_sync.md")
        lines += ["", "暂无数据不代表设备不支持；权限、网络和字段错误单独记录。",
                  "没有删除或覆盖原始活动；健康数据和本报告仅保存在本地。",
                  f"完整请求范围：{'否' if incomplete else '是'}", ""]
        private_text(path, "\n".join(lines))
        private_json(path.with_suffix(".json"), [asdict(item) for item in results])
        _link("验收报告", path)
        if incomplete or any(counts[key] for key in failures):
            raise typer.Exit(1)


@health_app.command("doctor")
def health_doctor(config: ConfigOption = Path("config/athlete.yaml")):
    """实际探测最近七天，保存私有响应供后续分析，不下载 FIT。"""
    _run_sync(7, config, "today", True, True)


@health_app.command("sync")
def health_sync(
    days: Annotated[int, typer.Option(min=1, max=366, help="回填天数，含结束日期。") ] = 56,
    until: Annotated[str, typer.Option(help="结束日期：today 或 YYYY-MM-DD。") ] = "today",
    refresh: Annotated[bool, typer.Option(help="重新查询全部日期；原始版本仍保留。") ] = False,
    config: ConfigOption = Path("config/athlete.yaml"),
):
    """回填健康数据；最近七天刷新，历史成功项跳过，失败和空数据可重试。"""
    _run_sync(days, config, until, refresh, False)


def feedback_command(
    date_value: DayOption = "today",
    fatigue: Annotated[int | None, typer.Option(min=0, max=10, help="疲劳 0-10，0 为不疲劳。") ] = None,
    legs: Annotated[int | None, typer.Option(min=0, max=10, help="腿沉 0-10。") ] = None,
    pain: Annotated[str, typer.Option(help="yes/no/unknown。") ] = "unknown",
    sleep_feeling: Annotated[str, typer.Option(help="good/poor/unknown。") ] = "unknown",
    plan: Annotated[str, typer.Option(help="easy/quality/steady/long/rest/unknown。") ] = "unknown",
    effort: Annotated[str, typer.Option(help="已跑训练的体感 easy/moderate/hard/unknown，不换算疲劳分。") ] = "unknown",
    leg_feeling: Annotated[str, typer.Option(help="腿部感觉 normal/heavy/unknown，不自动补数字分。") ] = "unknown",
    soreness: Annotated[str, typer.Option(help="酸痛 none/mild/marked/unknown，与未明确性质的疼痛分开。") ] = "unknown",
    completed: Annotated[bool, typer.Option("--completed", help="用户明确反馈今日训练已经完成。") ] = False,
    note: Annotated[str | None, typer.Option(help="原始体感文字，只存本地。") ] = None,
    non_interactive: Annotated[bool, typer.Option(help="只保存明确提供的字段，不提问、不补填。") ] = False,
    config: ConfigOption = Path("config/athlete.yaml"),
):
    """追加本地体感反馈；未知项保留未知，不覆盖旧反馈。"""
    with friendly_errors():
        settings = _settings(config)
        day = _day(date_value, settings.recovery.timezone)
        if not non_interactive:
            if fatigue is None:
                text = typer.prompt("疲劳 0-10（留空为未知）", default="", show_default=False)
                fatigue = int(text) if text else None
            if legs is None:
                text = typer.prompt("腿沉 0-10（留空为未知）", default="", show_default=False)
                legs = int(text) if text else None
            pain = typer.prompt("疼痛 yes/no/unknown", default=pain)
            sleep_feeling = typer.prompt("睡眠感受 good/poor/unknown", default=sleep_feeling)
            plan = typer.prompt("计划 easy/quality/steady/long/rest/unknown", default=plan)
            if note is None:
                note = typer.prompt("体感原话（可留空）", default="", show_default=False)
        valid = (pain in {"yes", "no", "unknown"} and sleep_feeling in {"good", "poor", "unknown"}
                 and plan in {"easy", "quality", "steady", "long", "rest", "unknown"}
                 and effort in {"easy", "moderate", "hard", "unknown"}
                 and leg_feeling in {"normal", "heavy", "unknown"}
                 and soreness in {"none", "mild", "marked", "unknown"}
                 and all(value is None or 0 <= value <= 10 for value in (fatigue, legs)))
        if not valid:
            typer.echo("反馈格式无效：分值需为 0-10，选项请使用 --help 中列出的值。", err=True)
            raise typer.Exit(2)
        store = HealthStore(settings.storage.database_path, settings.storage.data_dir)
        store.initialize()
        store.add_feedback(day.isoformat(), {"fatigue": fatigue, "legs": legs, "pain": pain,
            "sleep_feeling": sleep_feeling, "plan": plan, "note": note, "source": "explicit_user_feedback",
            "effort": effort, "leg_feeling": leg_feeling, "soreness": soreness,
            "completed": True if completed else None})
        typer.echo("反馈已追加到本地，原话未改写；没有回填成历史时点已知的信息。")


def readiness_command(
    date_value: DayOption = "today",
    at: Annotated[str | None, typer.Option(help="当地截止时刻 HH:MM；历史日期默认 07:00。") ] = None,
    config: ConfigOption = Path("config/athlete.yaml"),
):
    """生成可解释恢复决策；历史补采明确标为事后回放，不调用 LLM。"""
    with friendly_errors():
        settings = _settings(config)
        zone = ZoneInfo(settings.recovery.timezone)
        now = datetime.now(zone)
        day = _day(date_value, settings.recovery.timezone)
        try:
            if at is None:
                clock = now.timetz() if day == now.date() else time(7)
            else:
                clock = datetime.strptime(at, "%H:%M").time()
                if clock.strftime("%H:%M") != at:
                    raise ValueError
        except ValueError:
            typer.echo("--at 格式必须是 HH:MM。", err=True)
            raise typer.Exit(2) from None
        cutoff = datetime.combine(day, clock, zone)
        if cutoff > now:
            typer.echo("截止时点不能在未来。", err=True)
            raise typer.Exit(2)
        store = HealthStore(settings.storage.database_path, settings.storage.data_dir)
        result = analyze_readiness(store, settings, day, cutoff)
        path = write_readiness_report(result, settings.storage.reports_dir)
        typer.echo(f"{result['action_label']}；可信度 {result['confidence']}；模式 {result['mode']}")
        _link("恢复报告", path)


def register_health_commands(app: typer.Typer, report_app: typer.Typer):
    app.add_typer(health_app, name="health")
    app.command("feedback")(feedback_command)
    report_app.command("readiness")(readiness_command)
