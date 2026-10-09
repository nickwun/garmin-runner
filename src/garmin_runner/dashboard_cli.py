from datetime import datetime, date
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo

import typer

from garmin_runner.config import load_settings
from garmin_runner.dashboard import build_dashboard, write_dashboard


def register_dashboard_command(report_app: typer.Typer):
    @report_app.command("dashboard")
    def dashboard(
        days: Annotated[int, typer.Option(min=7, max=366, help="看板覆盖的当地日期天数。") ] = 56,
        until: Annotated[str, typer.Option(help="today 或 YYYY-MM-DD。") ] = "today",
        config: Annotated[Path, typer.Option(help="本地运动员配置。") ] = Path("config/athlete.yaml"),
    ):
        """生成离线 HTML 训练看板；只读本地数据，不同步、不联网。"""
        if not config.exists():
            typer.echo("缺少本地运动员配置，请先初始化 config/athlete.yaml。", err=True)
            raise typer.Exit(1)
        try:
            settings = load_settings(config)
            today = datetime.now(ZoneInfo(settings.recovery.timezone)).date()
            try:
                end = today if until == "today" else date.fromisoformat(until)
                if until != "today" and end.isoformat() != until:
                    raise ValueError
            except ValueError:
                typer.echo("日期格式必须是 YYYY-MM-DD 或 today。", err=True)
                raise typer.Exit(2) from None
            if end > today:
                typer.echo("不能生成未来日期的训练看板。", err=True)
                raise typer.Exit(2)
            result = build_dashboard(settings, end, days, project_root=Path.cwd())
            path = write_dashboard(result, settings.storage.reports_dir)
        except typer.Exit:
            raise
        except Exception:
            typer.echo("生成失败：请检查本地 SQLite、配置及目录权限；未输出原始异常。", err=True)
            raise typer.Exit(1) from None
        typer.echo(f"看板已生成：{len(result['activities'])} 条活动，{len(result['reports'])} 份报告；数据仅保存在本地。")
        typer.echo(f"[打开训练看板]({path.resolve()})")
