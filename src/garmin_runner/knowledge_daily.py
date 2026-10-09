from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import typer

from garmin_runner.config import load_settings
from garmin_runner.health_storage import private_json
from garmin_runner.knowledge import private_knowledge_path, write_index
from garmin_runner.knowledge_coach import iso_day, write_context


def run_daily(settings, root: Path, day: date, days: int = 14) -> dict:
    """Refresh derived local views, without syncing or approving a coach decision."""
    from garmin_runner.dashboard import build_dashboard, write_dashboard

    root = root.resolve()
    latest = private_knowledge_path(root, "coach_context/daily/latest.json")
    result = {"schema_version": 1, "as_of": day.isoformat(),
              "generated_at": datetime.now(ZoneInfo(settings.recovery.timezone)).isoformat(),
              "sync_status": "not_checked", "coach_status": "awaiting_review",
              "stages": {}, "context": None, "warnings": []}
    for stage, action in (("index", lambda: write_index(settings, root, day)),
                          ("context", lambda: write_context(settings, root, day, days))):
        try:
            path = action()
            result["stages"][stage] = "success"
            if stage == "context":
                payload = path.with_name("context.json")
                result["context"] = {"path": str(payload.relative_to(root)),
                                     "sha256": hashlib.sha256(payload.read_bytes()).hexdigest()}
                result["warnings"].extend(json.loads(payload.read_text())["warnings"])
        except Exception:
            result["stages"][stage] = "failed"
            result["warnings"].append(f"{stage} 更新失败；未用旧产物冒充本轮结果，请检查本地资料及隐私规则。")
    result["status"] = "ready" if not result["warnings"] and all(v == "success" for v in result["stages"].values()) else "partial"
    # Publish the current attempt, including failure, before refreshing the dashboard.
    private_json(latest, result)
    try:
        payload = build_dashboard(settings, day, max(56, days), project_root=root)
        if payload["knowledge"]["status"] != "ready":
            result["status"] = "partial"
            result["warnings"].append("看板知识库区存在缺失、过期或校验失败，请查看看板提示。")
        dashboard = write_dashboard(payload, settings.storage.reports_dir)
        result["dashboard"] = str(dashboard.resolve())
        result["stages"]["dashboard"] = "success"
    except Exception:
        result["stages"]["dashboard"] = "failed"
        result["status"] = "partial"
        result["warnings"].append("看板更新失败；已有看板保留旧生成时间，不代表本轮结果。")
    private_json(latest, result)
    return result


def register_knowledge_daily(app: typer.Typer):
    @app.command("daily")
    def daily(as_of: str = typer.Option("today", help="同步后的资料截止日期 YYYY-MM-DD 或 today。"),
              days: int = typer.Option(14, min=1, max=84, help="教练阅读包回顾天数。"),
              config: Path = typer.Option(Path("config/athlete.yaml"))):
        """同步后的本地整理：索引、教练阅读包、看板；不联网、不自动开课表。"""
        try:
            if not config.is_file():
                raise ValueError
            settings = load_settings(config)
            today = datetime.now(ZoneInfo(settings.recovery.timezone)).date()
            day = today if as_of == "today" else iso_day(as_of)
            if day > today:
                raise ValueError
        except Exception:
            typer.echo("配置或日期无效；请检查本地配置，日期使用 YYYY-MM-DD，不能为未来日期。", err=True)
            raise typer.Exit(2) from None
        try:
            result = run_daily(settings, Path.cwd(), day, days)
        except Exception:
            typer.echo("每日整理未完成：请检查 knowledge/ 隐私规则和目录权限；未输出原始异常。", err=True)
            raise typer.Exit(1) from None
        typer.echo(f"本地资料截至 {day}；未检查 Garmin 同步是否完整，教练分析待完成。")
        for stage, status in result["stages"].items():
            typer.echo(f"{'PASS' if status == 'success' else 'FAIL'} {stage}")
        for warning in result["warnings"]:
            typer.echo(warning)
        if result["context"]:
            path = (Path.cwd() / result["context"]["path"]).with_name("README.md")
            typer.echo(f"[打开教练阅读包]({path})")
        if result.get("dashboard"):
            typer.echo(f"[打开训练看板]({result['dashboard']})")
        if result["status"] != "ready":
            raise typer.Exit(1)
