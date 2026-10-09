from __future__ import annotations

import hashlib
import sqlite3
import subprocess
from collections import Counter
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import typer

from garmin_runner.analysis.comparison import _signature
from garmin_runner.config import load_settings
from garmin_runner.health_storage import private_json, private_text


knowledge_app = typer.Typer(help="本地私有跑步知识库；不联网、不改写原始活动。")


def _local_file(root: Path, value: str) -> Path | None:
    path = root / value
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        return None
    return path


def _cell(value) -> str:
    return str(value if value is not None else "未知").replace("|", "&#124;").replace("\n", " ").replace("<", "&lt;")


def _link(root: Path, value: str | None, label: str) -> str:
    return f"[{label}]({quote(str((root / value).resolve()))})" if value else "缺失"


def collect_index(settings, root: Path, as_of: date) -> dict:
    """Inventory every running ID; duplicate candidates stay visible, not deleted."""
    db = root / settings.storage.database_path
    if not db.is_file():
        raise ValueError("SQLite 不存在，请先运行 sync。")
    activities, seen = [], {}
    with closing(sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "activities" not in tables:
            raise ValueError("SQLite 缺少活动表，请先运行 sync。")
        rows = conn.execute("SELECT * FROM activities ORDER BY start_time_local, activity_id")
        for row in map(dict, rows):
            if "running" not in str(row["activity_type"]).lower():
                continue
            day = (row["start_time_local"] or "")[:10]
            try:
                parsed = date.fromisoformat(day)
            except ValueError:
                parsed = None
            if parsed and parsed > as_of:
                continue
            signature = _signature(row)
            duplicate_of = seen.get(signature)
            seen.setdefault(signature, row["activity_id"])
            item = {k: row[k] for k in ("activity_id", "start_time_local", "activity_name", "activity_type",
                                       "distance_m", "duration_s", "average_hr", "max_hr")}
            item.update(day=day if parsed else None, duplicate_of=duplicate_of)
            for kind in ("summary", "fit"):
                path = _local_file(root, row[f"{kind}_path"])
                item[f"{kind}_path"] = str(path.relative_to(root)) if path else None
            report = root / settings.storage.reports_dir / "daily" / f"{day}_{row['activity_id']}.md"
            found = _local_file(root, str(report))
            item["daily_report"] = str(found.relative_to(root)) if found else None
            activities.append(item)
        coverage = []
        if "health_snapshots" in tables:
            for kind in sorted({r[0] for r in conn.execute("SELECT DISTINCT kind FROM health_snapshots")}):
                days = [r[0] for r in conn.execute(
                    "SELECT DISTINCT day FROM health_snapshots WHERE kind=? AND day<=? ORDER BY day",
                    (kind, as_of.isoformat()))]
                coverage.append({"kind": kind, "days": days, "count": len(days)})
    report_root = root / settings.storage.reports_dir
    reports = []
    for folder in ("daily", "weekly", "monthly", "annual", "comparison", "readiness"):
        for path in sorted((report_root / folder).glob("*.md")):
            if _local_file(root, str(path)):
                reports.append({"path": str(path.relative_to(root)), "category": folder,
                                "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    return {"schema_version": 1, "as_of": as_of.isoformat(),
            "generated_at": datetime.now(ZoneInfo(settings.recovery.timezone)).isoformat(),
            "database": str(db.relative_to(root)), "activities": activities, "reports": reports,
            "health_coverage": coverage,
            "limitations": ["活动条数不等于训练堂数；精确重复候选保留但不重复统计跑量。",
                            "索引检查文件存在，不代表逐一重新解析 FIT 或审核报告。",
                            "无记录不等于休息；健康覆盖日期不连续时不补零。",
                            "已有报告保留其原始生成日期和判断，不能当成当前建议。"]}


def private_knowledge_path(root: Path, relative: str) -> Path:
    """Check Git privacy and every parent before writing private knowledge."""
    root = root.resolve()
    ignored = subprocess.run(["git", "check-ignore", "-q", "knowledge/"], cwd=root, capture_output=True)
    tracked = subprocess.run(["git", "ls-files", "-z", "--", "knowledge"], cwd=root, capture_output=True)
    if ignored.returncode or tracked.returncode or tracked.stdout:
        raise ValueError("请先忽略 knowledge/ 并确认它未被 Git 跟踪；未导出个人数据。")
    output = root / "knowledge" / relative
    if not output.resolve().is_relative_to(root / "knowledge"):
        raise ValueError("知识库输出目录不安全。")
    if any(p.is_symlink() for p in (output, *output.parents) if p != root and p.is_relative_to(root)):
        raise ValueError("知识库输出目录不安全。")
    return output


def write_index(settings, root: Path, as_of: date) -> Path:
    root = root.resolve()
    output = private_knowledge_path(root, "index")
    payload = collect_index(settings, root, as_of)
    private_json(output / "activities.json", payload)
    rows = payload["activities"]
    by_year = Counter((r["day"] or "unknown")[:4] for r in rows)
    header = ["| 日期 | activity_id | 名称 | km | timer 秒 | 均心 | 原始数据 | 报告 | 重复候选 |",
              "|---|---|---|---:|---:|---:|---|---|---|"]

    def table(items):
        lines = list(header)
        for r in items:
            km = f"{r['distance_m'] / 1000:.2f}" if r["distance_m"] is not None else "未知"
            source = _link(root, r["summary_path"], "JSON") + " / " + _link(root, r["fit_path"], "FIT")
            cells = [r["day"], r["activity_id"], r["activity_name"], km, r["duration_s"], r["average_hr"]]
            lines.append("| " + " | ".join(map(_cell, cells)) + " | " + source + " | " +
                         _link(root, r["daily_report"], "单次报告") + " | " + _cell(r["duplicate_of"] or "无") + " |")
        return "\n".join(lines) + "\n"

    for year in sorted(by_year):
        private_text(output / f"{year}.md", f"# {year} 跑步活动索引\n\n原始活动 ID 清单，不自动合并训练。\n\n" +
                     table(r for r in rows if (r["day"] or "unknown")[:4] == year))
    first = (as_of - timedelta(days=83)).isoformat()
    recent = [r for r in rows if r["day"] and first <= r["day"] <= as_of.isoformat()]
    private_text(output / "recent-12-weeks.md", f"# 最近 12 周索引\n\n{first} 至 {as_of}，共 {len(recent)} 条原始活动。\n\n" + table(recent))
    links = "\n".join(f"- {_link(root, r['path'], Path(r['path']).name)}（{r['category']}）" for r in payload["reports"])
    private_text(output / "reports.md", "# 已有报告目录\n\n历史报告不代表当前判断；本命令不重写正文。\n\n" + links + "\n")
    duplicates = sum(r["duplicate_of"] is not None for r in rows)
    lines = ["# 数据覆盖与活动索引", "", f"截至 {as_of}：{len(rows)} 条原始跑步记录；精确重复候选 {duplicates} 条。", "",
             "| 年份 | 原始条数 | 实际记录范围 |", "|---|---:|---|"]
    for year, count in sorted(by_year.items()):
        dates = [r["day"] for r in rows if r["day"] and r["day"].startswith(year)]
        lines.append(f"| [{year}]({year}.md) | {count} | {min(dates) if dates else '未知'} 至 {max(dates) if dates else '未知'} |")
    lines += ["", "[最近 12 周](recent-12-weeks.md) · [全部报告](reports.md) · [结构化索引](activities.json)", "",
              f"summary 缺失 {sum(r['summary_path'] is None for r in rows)}；FIT 缺失 {sum(r['fit_path'] is None for r in rows)}；无单次报告 {sum(r['daily_report'] is None for r in rows)}。", "",
              "## 健康数据覆盖", "", "统计已有快照的实际日期，不代表每天已完整同步。"]
    for c in payload["health_coverage"]:
        lines.append(f"- {c['kind']}：{c['count']} 天；{c['days'][0] if c['days'] else '未知'} 至 {c['days'][-1] if c['days'] else '未知'}；逐日清单见 JSON。")
    lines += ["", "## 限制", ""] + ["- " + s for s in payload["limitations"]]
    private_text(output / "README.md", "\n".join(lines) + "\n")
    return output / "README.md"


@knowledge_app.command("index")
def index_command(as_of: str = typer.Option("today", help="索引截止日期 YYYY-MM-DD 或 today。"),
                  config: Path = typer.Option(Path("config/athlete.yaml"), help="本地配置文件。")):
    """重建私有索引；保留人工档案、聊天快照和原始数据库。"""
    if not config.is_file():
        typer.echo("缺少本地配置文件。", err=True)
        raise typer.Exit(1)
    try:
        settings = load_settings(config)
        today = datetime.now(ZoneInfo(settings.recovery.timezone)).date()
        day = today if as_of == "today" else date.fromisoformat(as_of)
        if as_of != "today" and day.isoformat() != as_of:
            raise ValueError
    except Exception:
        typer.echo("日期或配置无效；日期使用 YYYY-MM-DD。", err=True)
        raise typer.Exit(2) from None
    try:
        path = write_index(settings, Path.cwd(), day)
    except Exception:
        typer.echo("索引未完成：请检查数据库、knowledge/ 忽略规则及目录权限；未输出原始异常。", err=True)
        raise typer.Exit(1) from None
    typer.echo(f"[打开知识库索引]({path})")
