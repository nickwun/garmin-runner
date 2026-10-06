from pathlib import Path
from typing import Annotated

import typer

from garmin_runner.analysis.comparison import ComparisonError, build_comparison
from garmin_runner.config import load_settings
from garmin_runner.reporting.comparison import write_comparison_report


def register_comparison_commands(app: typer.Typer):
    @app.command("compare")
    def compare(
        activity_id: Annotated[str, typer.Argument(help="目标 activity_id；只比较它之前的本地训练。")],
        days: Annotated[int, typer.Option(min=1, max=730, help="回溯天数。") ] = 90,
        limit: Annotated[int, typer.Option(min=1, max=20, help="最多展示的历史训练数。") ] = 5,
        against: Annotated[str | None, typer.Option(help="仅检查指定历史 activity_id；不跳过可比性门槛。") ] = None,
        context: Annotated[Path | None, typer.Option(help="本地校准 JSON；默认 data/comparison_context.json。") ] = None,
        config: Annotated[Path, typer.Option(help="本地运动员配置。") ] = Path("config/athlete.yaml"),
    ):
        """同类训练对比：确定性计算，缺失条件降级，不自动判定能力进退。"""
        try:
            if not config.exists():
                raise ComparisonError("缺少 config/athlete.yaml，请先初始化本地配置。")
            if context is not None and not context.exists():
                raise ComparisonError("指定的对比背景文件不存在。")
            settings = load_settings(config)
            result = build_comparison(settings, activity_id, days=days, limit=limit,
                                      against=against, context_path=context)
            path = write_comparison_report(result, settings.storage.reports_dir)
        except ComparisonError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1) from None
        except Exception:
            typer.echo("对比失败：请检查本地配置、SQLite 结构及文件权限；未输出原始异常。", err=True)
            raise typer.Exit(1) from None
        typer.echo(result["conclusion"])
        typer.echo(f"对比报告：[查看报告]({path.resolve()})")
