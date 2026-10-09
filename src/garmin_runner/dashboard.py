from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

from garmin_runner.analysis.comparison import _signature, load_context
from garmin_runner.health import numeric
from garmin_runner.health_storage import private_text


REPORT_FOLDERS = {"daily": "单次", "weekly": "周报", "monthly": "月报", "annual": "年报",
                  "comparison": "同类对比", "readiness": "恢复决策"}
MAX_REPORT_BYTES = 256_000
MAX_REPORTS = 200


def _period(name: str):
    """Only index reports with an explicit, parseable period in their filename."""
    try:
        if match := re.match(r"^(\d{4}-\d{2}-\d{2})(?:_|\.)", name):
            start = end = date.fromisoformat(match[1])
        elif match := re.match(r"^(\d{4})-W(\d{2})\.md$", name):
            start = date.fromisocalendar(int(match[1]), int(match[2]), 1)
            end = start + timedelta(days=6)
        elif match := re.match(r"^(\d{4})-(\d{2})\.md$", name):
            start = date(int(match[1]), int(match[2]), 1)
            end = (start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        elif match := re.match(r"^(\d{4})\.md$", name):
            start, end = date(int(match[1]), 1, 1), date(int(match[1]), 12, 31)
        else:
            return None
        return start, end
    except ValueError:
        return None


def _safe_file(path: Path, root: Path) -> bool:
    return not path.is_symlink() and path.is_file() and path.resolve().is_relative_to(root.resolve())


def _reports(root: Path, start: date, end: date) -> tuple[list, int]:
    candidates, skipped = [], 0
    for folder, label in REPORT_FOLDERS.items():
        directory = root / folder
        if directory.is_symlink():
            skipped += 1
            continue
        for path in directory.glob("*.md"):
            period = _period(path.name)
            if not period or not _safe_file(path, root):
                skipped += 1
                continue
            if period[0] > end or period[1] < start:
                continue
            candidates.append((period[0], path, folder, label, period[1]))
    candidates.sort(key=lambda item: (item[0], item[1].name), reverse=True)
    output = []
    for first, path, folder, label, last in candidates:
        if len(output) >= MAX_REPORTS or path.stat().st_size > MAX_REPORT_BYTES:
            skipped += 1
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            skipped += 1
            continue
        title = next((line[2:].strip() for line in content.splitlines() if line.startswith("# ")), path.stem)
        output.append({"key": f"{folder}/{path.name}", "category": label, "folder": folder,
                       "title": title, "start": first.isoformat(), "end": last.isoformat(),
                       "content": content, "href": "../" + quote(f"{folder}/{path.name}")})
    return output, skipped


def _readiness(root: Path, reports: list, until: date, tz: ZoneInfo, fresh_hours: float):
    for report in reports:
        if report["folder"] != "readiness":
            continue
        path = root / Path(report["key"]).with_suffix(".json")
        if not _safe_file(path, root) or path.stat().st_size > MAX_REPORT_BYTES:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("date") != report["start"]:
                continue
            cutoff = datetime.fromisoformat(data["cutoff"])
            now = datetime.now(tz)
            fresh = cutoff.tzinfo is not None and 0 <= (now - cutoff).total_seconds() <= fresh_hours * 3600
            return {"date": data["date"], "label": str(data.get("action_label", "未明确")),
                    "reason": str(data.get("reason", "")), "confidence": str(data.get("confidence", "未知")),
                    "cutoff": str(data.get("cutoff", "未知")), "key": report["key"],
                    "stale": not fresh or data["date"] != until.isoformat()}
        except (OSError, ValueError, AttributeError, TypeError, KeyError):
            continue
    return None


def _metric_rows(conn, tables: set, start: date, end: date, tz: ZoneInfo):
    days = {(start + timedelta(days=i)).isoformat(): {"day": (start + timedelta(days=i)).isoformat(),
            "hrv": None, "weekly_hrv": None, "baseline_low": None, "baseline_high": None,
            "sleep_hours": None, "rhr": None, "stress": None, "battery": None, "status": "无健康记录"}
            for i in range((end - start).days + 1)}
    if "health_snapshots" not in tables:
        return list(days.values())
    fields = {"hrv": {"last_night_ms": "hrv", "weekly_ms": "weekly_hrv", "baseline_low_ms": "baseline_low",
                      "baseline_high_ms": "baseline_high"}, "sleep": {"duration_s": "sleep_hours"},
              "rhr": {"bpm": "rhr"}, "stress": {"average": "stress"}}
    for row in conn.execute("SELECT day,kind,metrics_json,fetched_at FROM health_snapshots WHERE day BETWEEN ? AND ? ORDER BY fetched_at",
                            (start.isoformat(), end.isoformat())):
        try:
            data = json.loads(row["metrics_json"])
            if not isinstance(data, dict):
                continue
            item = days[row["day"]]
            for field, name in fields.get(row["kind"], {}).items():
                value = numeric(data.get(field))
                item[name] = value / 3600 if value is not None and name == "sleep_hours" else value
            if row["kind"] == "battery":
                samples = []
                for sample in data.get("samples", []):
                    at = datetime.fromisoformat(sample["at"])
                    if at.tzinfo is not None and at.astimezone(tz).date().isoformat() == row["day"] and numeric(sample.get("value"), 0, 100) is not None:
                        samples.append((at, float(sample["value"])))
                item["battery"] = max(samples)[1] if samples else None
        except (ValueError, TypeError, KeyError, AttributeError):
            continue
    for item in days.values():
        core = [item[key] is not None for key in ("hrv", "sleep_hours", "rhr")]
        item["status"] = "核心字段齐备" if all(core) else "部分字段缺失" if any(core) else "无健康记录"
    if "health_sync_status" in tables:
        for row in conn.execute("SELECT day,kind,status FROM health_sync_status WHERE day BETWEEN ? AND ?",
                                (start.isoformat(), end.isoformat())):
            if row["kind"] in {"hrv", "sleep", "rhr", "stress", "battery"} and row["status"] not in {"available", "cached"}:
                days[row["day"]]["status"] = "存在缺失或同步失败，已有值仅供回顾"
    return list(days.values())


def build_dashboard(settings, until: date, days: int = 56) -> dict:
    start = until - timedelta(days=days - 1)
    root = settings.storage.reports_dir
    reports, skipped = _reports(root, start, until)
    warnings = []
    if skipped:
        warnings.append(f"{skipped} 个报告文件因日期无法识别、大小/数量上限或安全限制未收录。")
    try:
        context = load_context(settings.storage.data_dir / "comparison_context.json")
    except ValueError:
        context = {}
        warnings.append("本地分类校准文件无效，未据其补填分类。")
    daily = {}
    for report in reports:
        if report["folder"] == "daily" and "_" in Path(report["key"]).stem:
            id = Path(report["key"]).stem.split("_", 1)[1]
            match = re.search(r"^\s*-\s*训练类型[：:]\s*(.+)$", report["content"], re.MULTILINE)
            daily[id] = {"key": report["key"], "type": match[1].strip() if match else None}
    comparison, comparison_types = {}, {}
    for report in reports:
        if report["folder"] == "comparison" and "_" in Path(report["key"]).stem:
            id = Path(report["key"]).stem.split("_", 1)[1]
            comparison[id] = report["key"]
            path = root / Path(report["key"]).with_suffix(".json")
            if _safe_file(path, root) and path.stat().st_size <= MAX_REPORT_BYTES:
                try:
                    target = json.loads(path.read_text(encoding="utf-8")).get("target", {})
                    if target.get("activity_id") == id and isinstance(target.get("training_type"), str):
                        comparison_types[id] = target["training_type"]
                except (OSError, ValueError, AttributeError):
                    pass
    activities, duplicates = [], 0
    feedback = {}
    metrics = [{"day": (start + timedelta(days=i)).isoformat(), "status": "无健康记录"} for i in range(days)]
    database = settings.storage.database_path
    if not database.exists():
        warnings.append("SQLite 不存在，请先同步；看板未创建空数据库。")
    else:
        with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "activities" in tables:
                rows = conn.execute("SELECT * FROM activities WHERE substr(start_time_local,1,10) BETWEEN ? AND ? ORDER BY start_time_local DESC, activity_id",
                                    (start.isoformat(), until.isoformat()))
                seen = set()
                for row in map(dict, rows):
                    if "running" not in str(row["activity_type"]).lower():
                        continue
                    signature = _signature(row)
                    if signature in seen:
                        duplicates += 1
                        continue
                    seen.add(signature)
                    km = numeric(row["distance_m"])
                    km = km / 1000 if km is not None else None
                    seconds = numeric(row["duration_s"])
                    id = row["activity_id"]
                    kind = context.get(id, {}).get("training_type") or comparison_types.get(id) or daily.get(id, {}).get("type") or "未结构化分类"
                    activities.append({"id": id, "start": row["start_time_local"], "day": row["start_time_local"][:10],
                        "name": row["activity_name"] or "跑步", "km": km, "duration_s": seconds,
                        "pace": seconds / km if seconds and km else None, "hr": numeric(row["average_hr"]),
                        "type": kind, "daily": daily.get(id, {}).get("key"), "comparison": comparison.get(id)})
            else:
                warnings.append("SQLite 没有活动表，训练记录为空。")
            metrics = _metric_rows(conn, tables, start, until, ZoneInfo(settings.recovery.timezone))
            if "recovery_feedback" in tables:
                for row in conn.execute(
                    "SELECT day, values_json FROM recovery_feedback WHERE day BETWEEN ? AND ? ORDER BY recorded_at, id",
                    (start.isoformat(), until.isoformat()),
                ):
                    try:
                        values = json.loads(row["values_json"])
                        if isinstance(values, dict) and isinstance(values.get("ran"), bool):
                            feedback[row["day"]] = values
                    except (ValueError, TypeError):
                        continue
    by_day = {}
    for activity in activities:
        by_day.setdefault(activity["day"], []).append(activity)
    for item in metrics:
        entries = by_day.get(item["day"], [])
        distances = [a["km"] for a in entries if a["km"] is not None]
        item["km"] = sum(distances) if distances else None
        item["activities"] = len(entries)
        note = feedback.get(item["day"], {})
        item["confirmed_no_run"] = note.get("ran") is False and not entries
        item["run_note"] = str(note.get("note") or "用户确认未跑步") if note.get("ran") is False else None
        if item["confirmed_no_run"]:
            item["km"] = 0
        elif note.get("ran") is False:
            warnings.append(f"{item['day']} 的未跑步反馈与活动记录冲突，保留活动数据，请核对。")
    readiness = _readiness(root, reports, until, ZoneInfo(settings.recovery.timezone), settings.recovery.fresh_hours)
    newest_run = max((a["day"] for a in activities), default=None)
    newest_health = max((m["day"] for m in metrics if any(m.get(k) is not None for k in ("hrv", "sleep_hours", "rhr"))), default=None)
    if newest_run != until.isoformat():
        warnings.append(f"跑步记录最新至 {newest_run or '未知'}；之后无记录不能视为休息。")
    if newest_health != until.isoformat():
        warnings.append(f"核心健康数据最新至 {newest_health or '未知'}，不是今天的恢复状态。")
    return {"version": "dashboard-v1", "generated_at": datetime.now(ZoneInfo(settings.recovery.timezone)).isoformat(),
            "start": start.isoformat(), "until": until.isoformat(), "timezone": settings.recovery.timezone,
            "activities": activities, "days": metrics, "reports": reports, "readiness": readiness,
            "duplicates_excluded": duplicates, "warnings": warnings,
            "latest_run": newest_run, "latest_health": newest_health}


def write_dashboard(payload: dict, reports_dir: Path) -> Path:
    template = Path(__file__).with_name("dashboard.html").read_text(encoding="utf-8")
    data = json.dumps(payload, ensure_ascii=False, allow_nan=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    output = template.replace("__DASHBOARD_DATA__", data)
    path = reports_dir / "dashboard" / "index.html"
    private_text(path, output)
    return path
