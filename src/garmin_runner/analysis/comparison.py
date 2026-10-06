from __future__ import annotations

import json
import math
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median, pstdev, fmean

from garmin_runner.analysis.single_activity import analyze_activity, training_config_from_settings
from garmin_runner.fit import decode_fit_messages, extract_time_series


TYPES = {"恢复跑", "E 跑", "MAF 跑", "中长有氧 / 稍稳有氧", "稳态跑", "马配桥梁",
         "阈值课", "阈值间歇", "间歇课", "长距离", "比赛", "热身/冷身"}
INTERVALS = {"阈值间歇", "间歇课"}
LIMITS = {"distance_fraction": 0.20, "hr_bpm": 5, "temperature_c": 3,
          "humidity_pct": 10, "ascent_m_per_km": 5, "min_coverage": 0.90,
          "min_records": 60, "pause_seconds": 60, "pace_cv_pct": 10}


class ComparisonError(ValueError):
    """Only fixed, credential-free messages may be shown by the CLI."""


def number(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def load_context(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("activities"), dict):
            raise ValueError
        allowed = {"training_type", "main_range_km", "work_ranges_km", "temperature_c", "humidity_pct",
                   "weather_source", "route", "surface", "hr_source", "note", "session_activity_ids", "variable_effort"}
        for key, item in data["activities"].items():
            if not re.fullmatch(r"[A-Za-z0-9_-]+", key) or not isinstance(item, dict) or set(item) - allowed:
                raise ValueError
            if item.get("training_type") and item["training_type"] not in TYPES:
                raise ValueError
            if "variable_effort" in item and not isinstance(item["variable_effort"], bool):
                raise ValueError
            for field, low, high in (("temperature_c", -50, 60), ("humidity_pct", 0, 100)):
                if field in item and (number(item[field]) is None or not low <= number(item[field]) <= high):
                    raise ValueError
                if field in item:
                    item[field] = float(item[field])
            for field in ("weather_source", "route", "surface", "hr_source", "note"):
                if field in item and not isinstance(item[field], str):
                    raise ValueError
            if any(field in item for field in ("temperature_c", "humidity_pct")) and not item.get("weather_source"):
                raise ValueError
            if "main_range_km" in item and "work_ranges_km" in item:
                raise ValueError
            ranges = item.get("work_ranges_km", [item["main_range_km"]] if "main_range_km" in item else [])
            if not isinstance(ranges, list):
                raise ValueError
            end = -1
            for pair in ranges:
                if not isinstance(pair, list) or len(pair) != 2:
                    raise ValueError
                a, b = map(number, pair)
                if a is None or b is None or not 0 <= a < b or a < end:
                    raise ValueError
                pair[:] = [a, b]
                end = b
            ids = item.get("session_activity_ids", [])
            if not isinstance(ids, list) or len(set(ids)) != len(ids) or any(
                    not isinstance(i, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", i) for i in ids):
                raise ValueError
        return data["activities"]
    except (OSError, ValueError, TypeError):
        raise ComparisonError("对比背景文件无效：请检查版本、字段、天气来源和不重叠的公里范围。") from None


def range_metrics(points, ranges: list[list[float]]) -> dict:
    """Integrate FIT distance increments; clip at exact distance boundaries, never fill gaps."""
    segments = []
    used_records = set()
    pause = 0.0
    previous_end = 0.0
    for a, b in ranges:
        offset = previous_end
        stopped = 0.0
        for index, (p, q) in enumerate(zip(points, points[1:])):
            x, y = number(p.distance_m), number(q.distance_m)
            dt = q.elapsed_s - p.elapsed_s
            if x is None or y is None or not math.isfinite(dt) or dt <= 0:
                continue
            if a <= x < b and dt > 30:
                pause += stopped if stopped >= 10 else 0
                stopped = 0
                pause += dt
            elif a <= x < b and 0 <= (y - x) / dt < 0.5:
                stopped += dt
            else:
                pause += stopped if stopped >= 10 else 0
                stopped = 0
            if y <= x or dt > 30 or not 0.5 <= (y - x) / dt <= 10:
                continue
            lo, hi = max(x, a), min(y, b)
            if hi <= lo:
                continue
            used_records.update((index, index + 1))
            hr = number(p.heart_rate_bpm)
            hr = hr if hr is not None and 30 <= hr <= 240 else None
            segments.append((offset + lo - a, offset + hi - a, dt * (hi - lo) / (y - x), hr))
        pause += stopped if stopped >= 10 else 0
        previous_end += b - a
    total_m = sum(b - a for a, b in ranges)

    def part(lo, hi):
        distance = seconds = weighted_hr = hr_seconds = 0.0
        for a, b, dt, hr in segments:
            overlap = max(0, min(hi, b) - max(lo, a))
            if overlap <= 0:
                continue
            seconds_in = dt * overlap / (b - a)
            distance += overlap
            seconds += seconds_in
            if hr is not None:
                weighted_hr += hr * seconds_in
                hr_seconds += seconds_in
        return {"distance_km": distance / 1000, "duration_s": seconds,
                "pace_s_per_km": seconds * 1000 / distance if distance else None,
                "hr": weighted_hr / hr_seconds if hr_seconds else None,
                "hr_coverage": hr_seconds / seconds if seconds else 0}

    result = part(0, total_m)
    first, second = part(0, total_m / 2), part(total_m / 2, total_m)
    result.update({"requested_km": total_m / 1000,
                   "distance_coverage": result["distance_km"] * 1000 / total_m if total_m else 0,
                   "pause_s": pause, "record_count": len(used_records), "first_half": first, "second_half": second})
    paces = [part(k, k + 1000)["pace_s_per_km"] for k in range(0, int(total_m) - 999, 1000)]
    paces = [p for p in paces if p is not None]
    result["pace_cv_pct"] = pstdev(paces) / fmean(paces) * 100 if len(paces) >= 2 else None
    result["late_slowdown_pct"] = ((second["pace_s_per_km"] / first["pace_s_per_km"] - 1) * 100
                                   if first["pace_s_per_km"] and second["pace_s_per_km"] else None)
    valid_halves = all(p["pace_s_per_km"] and p["hr"] and p["hr_coverage"] >= 0.9 for p in (first, second))
    result["drift_pct"] = ((1 - (first["pace_s_per_km"] * first["hr"]) /
                                (second["pace_s_per_km"] * second["hr"])) * 100 if valid_halves else None)
    return result


def _signature(row):
    keys = ("start_time_local", "start_time_gmt", "distance_m", "duration_s", "average_hr", "max_hr")
    values = tuple(row.get(k) for k in keys)
    return values if all(v is not None for v in values) else (row["activity_id"],)


def lap_work_ranges(laps: list[dict]) -> tuple[list, list]:
    """Join adjacent active laps into repetitions, preserving explicit recovery timers."""
    cursor, groups, recoveries, rest = 0.0, [], [], 0.0
    active = False
    for lap in laps:
        distance, seconds = number(lap.get("total_distance")), number(lap.get("total_timer_time"))
        if distance is None or seconds is None or distance < 0 or seconds < 0:
            return [], []
        role = lap.get("intensity")
        if role == "active" and distance > 20:
            if active:
                groups[-1][1] = cursor + distance
            else:
                if groups:
                    recoveries.append({"duration_s": rest, "pause_s": 0, "source": "FIT lap total_timer_time"})
                groups.append([cursor, cursor + distance])
            active, rest = True, 0.0
        else:
            active = False
            if groups:
                if role not in {"recovery", "rest", "cooldown"} and distance > 20:
                    return [], []
                rest += seconds
        cursor += distance
    if not groups or any(b - a < 200 for a, b in groups):
        return [], []
    return groups, recoveries


def build_sample(row: dict, settings, context: dict) -> dict:
    item = context.get(row["activity_id"], {})
    try:
        summary_path, fit_path = Path(row["summary_path"]), Path(row["fit_path"])
        if not summary_path.exists() or not fit_path.exists():
            raise ComparisonError("summary 或 FIT 缺失，请先检查该活动。")
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        if str(summary.get("activityId")) != row["activity_id"]:
            raise ComparisonError("summary 的 activity_id 与 SQLite 不一致。")
        messages, errors = decode_fit_messages(fit_path)
        if errors:
            raise ComparisonError("FIT 解析失败，无法建立可靠对比。")
        points = extract_time_series(messages)
        analysis = analyze_activity(summary, points, training_config_from_settings(settings), messages.get("lap_mesgs"))
        kind = item.get("training_type", analysis.training_type)
        total = number(row.get("distance_m"))
        if not total or total <= 0:
            raise ComparisonError("活动距离缺失或无效。")
        breakdown = analysis.workout_breakdown
        selection = "整条活动"
        ranges = [[0, total]]
        explicit_work = item.get("work_ranges_km")
        lap_ranges, lap_recoveries = lap_work_ranges(messages.get("lap_mesgs") or [])
        work_selected = bool(explicit_work)
        lap_selected = False
        if explicit_work:
            ranges = [[a * 1000, b * 1000] for a, b in explicit_work]
            selection = "明确快段（不含组间恢复）"
        elif item.get("main_range_km"):
            ranges = [[v * 1000 for v in item["main_range_km"]]]
            selection = "明确主训练段"
        elif kind in INTERVALS and len(lap_ranges) >= 2:
            ranges = lap_ranges
            selection = "FIT 标记快段（不含组间恢复）"
            work_selected = lap_selected = True
        elif kind in {"稳态跑", "马配桥梁", "阈值课", "MAF 跑"} and len(lap_ranges) == 1:
            ranges = lap_ranges
            selection = "FIT 标记主训练段"
        elif breakdown is not None and kind not in {"长距离", "比赛", "恢复跑", "E 跑", "MAF 跑"}:
            start = breakdown.warmup.distance_km * 1000
            ranges = [[start, start + breakdown.main.distance_km * 1000]]
            selection = "自动识别主训练段（待确认）"
        if ranges[-1][1] > total + 50:
            raise ComparisonError("主训练范围超过活动距离，请校准本地对比背景。")
        metrics = range_metrics(points, ranges)
        warnings = []
        if metrics["distance_coverage"] < LIMITS["min_coverage"] or metrics["distance_coverage"] > 1.05:
            warnings.append("FIT 距离覆盖不足或重复，不用 summary 均值补齐")
        if metrics["hr_coverage"] < LIMITS["min_coverage"]:
            warnings.append("FIT 心率覆盖不足")
        if metrics["record_count"] < LIMITS["min_records"]:
            warnings.append("FIT records 太少")
        values = summary.get("summaryDTO") or summary
        if selection == "整条活动":
            elapsed, timer = number(values.get("elapsedDuration")), number(values.get("duration"))
            if elapsed and timer:
                metrics["pause_s"] = max(metrics["pause_s"], elapsed - timer)
        drift_notes = []
        if kind in INTERVALS | {"比赛"}:
            drift_notes.append("间歇/比赛不使用连续跑心率漂移")
        if item.get("variable_effort"):
            drift_notes.append("明确计划渐进或变速，不使用恒定强度漂移判断")
        if metrics["duration_s"] < 1200:
            drift_notes.append("有效段不足 20 分钟，不作持续跑漂移判断")
        if metrics["pause_s"] > LIMITS["pause_seconds"]:
            drift_notes.append("存在明显暂停或采样缺口")
        if (metrics["pace_cv_pct"] or 0) > LIMITS["pace_cv_pct"]:
            drift_notes.append("分公里配速变化较大，不据此判定生理漂移")
        if warnings:
            drift_notes.append("关键字段覆盖不足")
        if drift_notes:
            metrics["drift_pct"] = None
        work = [range_metrics(points, [r]) for r in ranges] if work_selected else []
        recovery = [range_metrics(points, [[left[1], right[0]]]) for left, right in zip(ranges, ranges[1:])
                    if right[0] > left[1]] if work_selected else []
        if lap_selected:
            recovery = lap_recoveries
        return {"activity_id": row["activity_id"], "start": row["start_time_local"],
                "training_type": kind, "type_source": "本地明确校准" if "training_type" in item else "现有规则",
                "total_distance_km": total / 1000, "timer_duration_s": number(row["duration_s"]),
                "elapsed_duration_s": number(values.get("elapsedDuration")),
                "moving_duration_s": number(values.get("movingDuration")),
                "selection": selection, "ranges_km": [[a / 1000, b / 1000] for a, b in ranges],
                "metrics": metrics, "work_intervals": work, "recovery_intervals": recovery,
                "warnings": warnings, "drift_notes": drift_notes,
                "ascent_m_per_km": analysis.basic.elevation_gain_m / (total / 1000)
                    if analysis.basic.elevation_gain_m is not None else None,
                "context": item, "sources": {"summary": str(summary_path), "fit": str(fit_path)}}
    except ComparisonError:
        raise
    except Exception:
        raise ComparisonError("本地活动格式或 FIT 解析异常，无法对比；未输出原始异常。") from None


def compare_pair(target: dict, candidate: dict) -> dict:
    a, b = target["metrics"], candidate["metrics"]
    excluded, caveats = [], []
    if target["training_type"] != candidate["training_type"]:
        excluded.append("训练类型不同")
    if target["training_type"] in {"比赛", "热身/冷身"}:
        excluded.append("比赛或孤立热身/冷身不用于恢复与能力对比")
    if not a["requested_km"] or abs(b["requested_km"] / a["requested_km"] - 1) > LIMITS["distance_fraction"]:
        excluded.append("所选训练段距离差超过 20%")
    if target["warnings"] or candidate["warnings"]:
        excluded.append("关键 FIT 数据质量不足")
    if any(s["context"].get("variable_effort") for s in (target, candidate)):
        caveats.append("包含明确计划变速，不据均值判断状态变化")
    if not a["hr"] or not b["hr"]:
        excluded.append("缺少所选训练段心率")
    elif abs(a["hr"] - b["hr"]) > LIMITS["hr_bpm"]:
        caveats.append("平均心率差超过 5 bpm，不属于近似同心率对比")
    for field, threshold, label in (("temperature_c", 3, "气温"), ("humidity_pct", 10, "湿度")):
        x, y = target["context"].get(field), candidate["context"].get(field)
        if x is None or y is None:
            caveats.append(f"{label}缺失，不作天气校正")
        elif abs(x - y) > threshold:
            caveats.append(f"{label}差异超过阈值")
    for field, label in (("route", "路线"), ("surface", "路面"), ("hr_source", "心率来源")):
        x, y = target["context"].get(field), candidate["context"].get(field)
        if not x or not y:
            caveats.append(f"{label}未知")
        elif x != y:
            caveats.append(f"{label}不同")
    x, y = target["ascent_m_per_km"], candidate["ascent_m_per_km"]
    if x is None or y is None:
        caveats.append("爬升数据缺失")
    elif abs(x - y) > LIMITS["ascent_m_per_km"]:
        caveats.append("每公里爬升差超过 5 m")
    if any("待确认" in s["selection"] for s in (target, candidate)):
        caveats.append("主训练边界为自动估计")
    if max(a["pause_s"], b["pause_s"]) > LIMITS["pause_seconds"]:
        caveats.append("跑停或采样缺口较多")
    if target["training_type"] in INTERVALS:
        aw, bw = target["work_intervals"], candidate["work_intervals"]
        if not aw or not bw:
            caveats.append("快段与恢复结构未明确，不用全程均速评价间歇完成质量")
        elif len(aw) != len(bw) or any(abs(x["requested_km"] - y["requested_km"]) > 0.15 for x, y in zip(aw, bw)):
            excluded.append("间歇组数或单组距离不同")
        else:
            ar, br = target["recovery_intervals"], candidate["recovery_intervals"]
            if len(ar) != len(aw) - 1 or len(br) != len(bw) - 1 or any(
                    abs(x["duration_s"] - y["duration_s"]) > 15 or x["pause_s"] > 30 or y["pause_s"] > 30
                    for x, y in zip(ar, br)):
                caveats.append("组间恢复时长不同或含静止恢复，不强行视为同课表")
    elif any((m["pace_cv_pct"] or 0) > LIMITS["pace_cv_pct"] for m in (a, b)):
        caveats.append("明显变速，不作稳定连续跑对比")
    tier = "excluded" if excluded else "reference" if caveats else "comparable"
    # Intervals without confirmed work ranges must not acquire a whole-session pace delta.
    pace_allowed = tier != "excluded" and not (target["training_type"] in INTERVALS and
                       (not target["work_intervals"] or not candidate["work_intervals"]))
    delta = a["pace_s_per_km"] - b["pace_s_per_km"] if pace_allowed and a["pace_s_per_km"] and b["pace_s_per_km"] else None
    return {"sample": candidate, "tier": tier, "reasons": excluded if excluded else caveats,
            "pace_delta_s_per_km": delta, "hr_delta_bpm": a["hr"] - b["hr"] if a["hr"] and b["hr"] else None}


def build_comparison(settings, activity_id: str, *, days: int = 90, limit: int = 5,
                     context_path: Path | None = None, against: str | None = None) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", activity_id) or (against and not re.fullmatch(r"[A-Za-z0-9_-]+", against)):
        raise ComparisonError("activity_id 格式无效。")
    if not settings.storage.database_path.exists():
        raise ComparisonError("SQLite 数据库不存在，请先运行 sync。")
    context_path = context_path or settings.storage.data_dir / "comparison_context.json"
    context = load_context(context_path)
    with closing(sqlite3.connect(settings.storage.database_path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        rows = [dict(row) for row in conn.execute("SELECT * FROM activities ORDER BY start_time_local DESC, activity_id")]
    by_id = {row["activity_id"]: row for row in rows}
    if activity_id not in by_id:
        raise ComparisonError("SQLite 中找不到该活动，请先运行 sync。")
    target_row = by_id[activity_id]
    target = build_sample(target_row, settings, context)
    end = datetime.fromisoformat(target["start"])
    start = end - timedelta(days=days)
    selected, excluded, duplicates = [], [], []
    seen = {_signature(target_row)}
    if against and against not in by_id:
        raise ComparisonError("SQLite 中找不到指定的历史活动。")
    for row in ([by_id[against]] if against else rows):
        if row["activity_id"] == activity_id:
            if against:
                raise ComparisonError("不能将活动与自身比较。")
            continue
        try:
            when = datetime.fromisoformat(row["start_time_local"])
            in_window = start <= when < end and when.date() < end.date()
        except (TypeError, ValueError):
            in_window = False
        if not in_window:
            if against:
                raise ComparisonError("历史活动必须早于目标当地日期且位于回溯天数内。")
            continue
        signature = _signature(row)
        if signature in seen:
            duplicates.append(row["activity_id"])
            continue
        seen.add(signature)
        try:
            candidate = build_sample(row, settings, context)
            pair = compare_pair(target, candidate)
            if pair["tier"] == "excluded":
                excluded.append({"activity_id": row["activity_id"], "reasons": pair["reasons"]})
            else:
                selected.append(pair)
        except ComparisonError as exc:
            excluded.append({"activity_id": row["activity_id"], "reasons": [str(exc)]})
    selected.sort(key=lambda p: (p["tier"] != "comparable", len(p["reasons"]),
                                 abs(p["sample"]["metrics"]["requested_km"] / target["metrics"]["requested_km"] - 1)))
    matches = selected[:limit]
    strict = [p for p in matches if p["tier"] == "comparable" and p["pace_delta_s_per_km"] is not None]
    conclusion = "没有条件充分相近的样本，不能据此判断能力或恢复状态变化。"
    if len(strict) >= 3:
        delta = median(p["pace_delta_s_per_km"] for p in strict)
        conclusion = (f"与 {len(strict)} 次条件相近的历史训练相比，所选段配速差中位数 {delta:+.1f} 秒/公里"
                      "（负数为更快）。这是表现差异，不直接等同于能力或恢复变化。")
    elif strict:
        conclusion = "已有条件相近样本，但少于 3 次，仅列个案差异，不判断趋势。"
    session = []
    for linked_id in context.get(activity_id, {}).get("session_activity_ids", []):
        if linked_id not in by_id or by_id[linked_id]["start_time_local"][:10] != target["start"][:10]:
            raise ComparisonError("关联热身/冷身活动必须存在且与主训练在同一当地日期。")
        linked = by_id[linked_id]
        session.append({"activity_id": linked_id, "distance_km": linked["distance_m"] / 1000 if linked["distance_m"] else None})
    return {"version": "comparison-v1", "target": target, "window_start": start.isoformat(),
            "window_end_exclusive": end.date().isoformat(), "thresholds": LIMITS,
            "matches": matches, "eligible_count": len(selected), "excluded": excluded,
            "duplicates_excluded": duplicates, "conclusion": conclusion, "linked_session": session,
            "context_path": str(context_path), "strict_count": len(strict),
            "limitations": ["仅比较本地已同步活动，缺失日期不等于休息。", "天气只使用带来源的明确背景，不把腕表温度当作环境温度。",
                            "不会用公式消除天气差异，不从配速变化诊断疲劳或伤病。",
                            "心率漂移采用前后半段速度/心率效率下降比例；不是间歇完成质量指标。",
                            "配速与心率由所选 FIT 距离段积分，暂停不计为移动配速；timer/elapsed/moving 时间单列。",
                            "相邻活动只有明确关联时才共同解释；热身和冷身不混入主训练比较。"]}
