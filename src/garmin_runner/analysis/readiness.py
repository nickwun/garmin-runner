from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo

from garmin_runner.config import AppSettings
from garmin_runner.health_storage import HealthStore
from garmin_runner.health import numeric
from garmin_runner.storage import ActivityStore


ACTIONS = {"maintain": "维持原计划", "shorten": "缩短训练", "downgrade": "降级为轻松训练",
           "rest": "休息", "insufficient": "证据不足", "observe": "训练后恢复观察"}
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def _select_snapshots(rows: list[dict], cutoff: datetime, tz: ZoneInfo) -> tuple[dict, list[str], bool]:
    grouped: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        grouped.setdefault((row["day"], row["kind"]), []).append(row)
    selected = {}
    excluded = []
    retrospective = False
    for key, versions in grouped.items():
        known = [row for row in versions if datetime.fromisoformat(row["fetched_at"]) <= cutoff]
        row = max(known or versions, key=lambda item: item["fetched_at"])
        metrics = dict(row["metrics"])
        later_fetch = datetime.fromisoformat(row["fetched_at"]) > cutoff
        retrospective |= later_fetch
        observed = metrics.get("observed_at")
        if observed and datetime.fromisoformat(observed) > cutoff:
            excluded.append(f"{key[0]} {key[1]}：观测结束晚于决策时点")
            continue
        if key[0] == cutoff.astimezone(tz).date().isoformat():
            if key[1] in {"sleep", "hrv"} and not observed:
                excluded.append(f"{key[1]}：缺少结束时间，不能用于当日跑前判断")
                continue
            if key[1] in {"rhr", "stress"} and later_fetch:
                excluded.append(f"{key[1]}：事后取得的当日汇总，可能包含训练后数据")
                continue
        if "samples" in metrics:
            samples = [point for point in metrics["samples"]
                       if datetime.fromisoformat(point["at"]) <= cutoff
                       and datetime.fromisoformat(point["at"]).astimezone(tz).date().isoformat() == key[0]]
            if not samples:
                excluded.append(f"{key[0]} {key[1]}：时点前没有有效采样")
                continue
            sample = max(samples, key=lambda item: item["at"])
            metrics = {"value": sample["value"], "observed_at": sample["at"]}
        selected[key] = {**row, "metrics": metrics}
    return selected, excluded, retrospective


def _recent_training(store: HealthStore, since: date, cutoff: datetime, tz: ZoneInfo) -> dict:
    empty = {"available": False, "activities": [], "distance_km": None, "duplicates_excluded": 0,
             "retrospective": False, "started_today": False, "unfinished_excluded": 0}
    if not store.database.exists():
        return empty
    try:
        rows = ActivityStore(store.database).list_activities_between(since.isoformat(), cutoff.astimezone(tz).date().isoformat())
    except sqlite3.OperationalError:
        return empty
    unique, seen, duplicates = [], set(), 0
    retrospective, started_today, unfinished = False, False, 0
    for row in rows:
        value = row.get("start_time_local")
        try:
            start = datetime.fromisoformat(value)
            if start.tzinfo is None:
                start = start.replace(tzinfo=tz)
        except (TypeError, ValueError):
            continue
        if start >= cutoff:
            continue
        started_today |= start.astimezone(tz).date() == cutoff.astimezone(tz).date()
        fetched = datetime.fromisoformat(row["synced_at"])
        retrospective |= fetched > cutoff
        elapsed = None
        try:
            raw = json.loads(Path(row["summary_path"]).read_text(encoding="utf-8"))
            elapsed = numeric((raw.get("summaryDTO") or raw).get("elapsedDuration"))
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        # A later import with no elapsed duration cannot prove the session ended by cutoff.
        duration = elapsed if elapsed is not None else row.get("duration_s")
        if (fetched > cutoff and elapsed is None) or duration is None or start + timedelta(seconds=duration) > cutoff:
            unfinished += 1
            continue
        keys = ("start_time_local", "start_time_gmt", "distance_m", "duration_s", "average_hr", "max_hr")
        signature = tuple(row.get(key) for key in keys)
        if all(part is not None for part in signature):
            if signature in seen:
                duplicates += 1
                continue
            seen.add(signature)
        unique.append({"activity_id": row["activity_id"], "start": start.isoformat(),
                       "distance_km": (row.get("distance_m") or 0) / 1000,
                       "duration_s": row.get("duration_s")})
    return {"available": True, "activities": unique,
            "distance_km": round(sum(row["distance_km"] for row in unique), 2),
            "duplicates_excluded": duplicates,
            "retrospective": retrospective, "started_today": started_today,
            "unfinished_excluded": unfinished,
            "coverage": "仅本地已同步记录；无记录不等于休息，不推断完整覆盖"}


def analyze_readiness(store: HealthStore, settings: AppSettings, day: date, cutoff: datetime) -> dict:
    cfg, tz = settings.recovery, ZoneInfo(settings.recovery.timezone)
    if cutoff.tzinfo is None or cutoff.astimezone(tz).date() != day:
        raise ValueError("决策时点必须带时区且属于报告当地日期。")
    beginning = day - timedelta(days=cfg.baseline_days)
    selected, excluded, retrospective = _select_snapshots(
        store.snapshots(beginning.isoformat(), day.isoformat()), cutoff, tz)
    feedback = store.feedback(day.isoformat(), cutoff)
    if feedback and cutoff - datetime.fromisoformat(feedback["recorded_at"]) > timedelta(hours=cfg.fresh_hours):
        feedback = None
    evidence, conflicts, missing = [], [], []
    today = {kind: row["metrics"] for (key_day, kind), row in selected.items() if key_day == day.isoformat()}
    current = {}
    for kind, metrics in today.items():
        observed = metrics.get("observed_at") or selected[(day.isoformat(), kind)]["fetched_at"]
        if observed and cutoff - datetime.fromisoformat(observed) > timedelta(hours=cfg.fresh_hours):
            missing.append(f"{kind} 数据距时点已超过 {cfg.fresh_hours:g} 小时，不作为当前状态依据")
        else:
            current[kind] = metrics
    baselines = {}
    for kind, field in (("rhr", "bpm"), ("sleep", "duration_s")):
        values = [row["metrics"][field] for (key_day, key_kind), row in selected.items()
                  if key_kind == kind and key_day < day.isoformat() and row["metrics"].get(field) is not None]
        valid = len(values) >= cfg.min_baseline_days
        baselines[kind] = {"n": len(values), "median": median(values) if valid else None}
        if not valid:
            missing.append(f"{kind} 个人参考仅 {len(values)} 天，需 {cfg.min_baseline_days} 天")
    flags: dict[str, list[str]] = {"hrv_low": [], "rhr_high": [], "sleep_short": []}
    for offset in range(cfg.consecutive_days):
        target = (day - timedelta(days=offset)).isoformat()
        for kind, flag in (("hrv", "hrv_low"), ("rhr", "rhr_high"), ("sleep", "sleep_short")):
            row = selected.get((target, kind))
            if not row or (offset == 0 and kind not in current):
                continue
            obj = row["metrics"]
            if kind == "hrv":
                # Garmin's balanced band applies to its seven-day average, not a single night.
                value, low = obj.get("weekly_ms"), obj.get("baseline_low_ms")
                hit = value is not None and low is not None and value < low
            elif kind == "rhr":
                reference = baselines[kind]["median"]
                hit = reference is not None and obj["bpm"] >= reference + cfg.elevated_rhr_bpm
            else:
                reference = baselines[kind]["median"]
                hit = reference is not None and obj["duration_s"] <= reference - cfg.sleep_deficit_hours * 3600
            if hit:
                flags[flag].append(target)
    sustained = {key for key, dates in flags.items() if len(dates) == cfg.consecutive_days}
    for key, dates in flags.items():
        if dates:
            evidence.append(f"{ {'hrv_low': '7 日 HRV 均值低于 Garmin 个人基线下界', 'rhr_high': '静息心率高于个人参考', 'sleep_short': '睡眠短于个人参考'}[key]}：{', '.join(dates)}")
    if len(flags["hrv_low"]) == 1:
        evidence.append("只有一天的 7 日 HRV 均值触及规则，仅提示观察，不单独决定取消训练。")
    for kind in ("hrv", "sleep"):
        if kind not in current:
            missing.append(f"缺少时点前新鲜的 {kind} 数据")
    if current.get("hrv", {}).get("baseline_low_ms") is None:
        missing.append("缺少 Garmin HRV 个人基线，不使用人群均值替代")
    if current.get("hrv", {}).get("weekly_ms") is None:
        missing.append("缺少 Garmin 7 日 HRV 均值，单晚值不能替代")
    statuses = store.statuses(day.isoformat(), day.isoformat())
    request_issues = [row for row in statuses if row["kind"] in {"hrv", "sleep", "rhr"}
                      and row["status"] != "available"
                      and datetime.fromisoformat(row["checked_at"]) <= cutoff]
    if request_issues:
        missing.append("部分最近请求失败或字段变化；保留旧数据，不能视为本次同步成功")
    if not feedback:
        missing.append("没有决策时点前的体感反馈；不默认感觉良好")
    fatigue, legs = (feedback or {}).get("fatigue"), (feedback or {}).get("legs")
    pain = (feedback or {}).get("pain", "unknown")
    effort = (feedback or {}).get("effort", "unknown")
    leg_feeling = (feedback or {}).get("leg_feeling", "unknown")
    soreness = (feedback or {}).get("soreness", "unknown")
    complete_feedback = fatigue is not None and legs is not None and pain != "unknown"
    if feedback and not complete_feedback:
        if fatigue is None:
            missing.append("未提供数字疲劳分；已保留明确体感，不自动换算")
        if legs is None and leg_feeling == "unknown":
            missing.append("腿部感觉未明确")
        if pain == "unknown":
            missing.append("其他疼痛情况未明确；已有酸痛描述单独保留，不自动判断原因")
    if feedback and feedback.get("sleep_feeling") == "poor":
        evidence.append("主观睡眠感受较差。")
    heavy = max(fatigue or 0, legs or 0) >= 4 or leg_feeling == "heavy"
    severe = max(fatigue or 0, legs or 0) >= 7 or soreness == "marked"
    if effort == "easy":
        evidence.append("用户反馈已跑训练体感轻松；未换算为疲劳分或完全恢复。")
    if leg_feeling == "normal":
        evidence.append("用户明确反馈腿不沉。")
    if soreness == "mild":
        evidence.append("用户反馈仍有轻微酸痛；与其他疼痛分开记录，原因仅保留用户描述。")
        if current.get("hrv", {}).get("status") == "BALANCED":
            conflicts.append("HRV 平衡与酸痛仍在同时存在，不能据前者宣告肌肉已经恢复。")
    if heavy and current.get("hrv", {}).get("status") == "BALANCED":
        conflicts.append("HRV 显示平衡，但反馈腿沉或疲劳；不据此确认恢复。")
    if sustained and complete_feedback and not heavy:
        conflicts.append("主观较轻松，但存在连续客观变化；保留冲突，不作恢复充分结论。")
    training = _recent_training(store, day - timedelta(days=6), cutoff, tz)
    retrospective |= training["retrospective"]
    if training["unfinished_excluded"]:
        missing.append(f"{training['unfinished_excluded']} 条活动未结束或缺少可确认的结束时间，未计入完整跑量")
    loaded = [row for row in training["activities"] if cutoff - datetime.fromisoformat(row["start"]) <= timedelta(hours=48)
              and (row["distance_km"] >= settings.training.long_run_min_distance_km
                   or (row["duration_s"] or 0) >= settings.training.long_run_min_duration_min * 60)]
    if loaded:
        evidence.append("前 48 小时存在较长或长时活动；这不是按全程均心作强度分类，需要结合分段报告。")
    trained_today = training["started_today"] or (feedback or {}).get("completed") is True
    explicit_plan = (feedback or {}).get("plan", "unknown")
    rest_day = WEEKDAYS[day.weekday()] == settings.training.weekly_rest_day.lower()
    plan = explicit_plan
    if plan == "unknown":
        plan = "rest" if rest_day else ("quality" if day.weekday() == 1 and settings.training.tuesday_quality else
               "steady" if day.weekday() == 4 and settings.training.friday_steady else "unknown")
    adequate = ("hrv" in current and "sleep" in current and current["hrv"].get("baseline_low_ms") is not None
                and current["hrv"].get("weekly_ms") is not None
                and all(row["median"] is not None for row in baselines.values())
                and not request_issues and training["available"])
    # HRV, resting HR and Garmin's derived scores are not independent votes.
    autonomic = bool(sustained & {"hrv_low", "rhr_high"})
    if rest_day or explicit_plan == "rest":
        action, reason = "rest", "尊重固定全休日或明确休息计划；不以指标良好追加训练。"
    elif pain == "yes" or severe:
        action, reason = "rest", "有疼痛、明显酸痛或明显疲劳反馈，优先停止加码；本工具不能判断原因。"
    elif trained_today and adequate and feedback and (effort != "unknown" or leg_feeling != "unknown"):
        action, reason = "observe", "今日训练已完成，不追加训练；继续观察体感和酸痛，不据单次轻松表现确认恢复充分。"
    elif (heavy and (sustained or loaded)) or (autonomic and "sleep_short" in sustained):
        action, reason = "downgrade", "体感和负荷/连续变化共同提示保守调整，不追赶配速或补组数。"
    elif heavy:
        action, reason = "shorten", "体感不佳即使心率低也不确认恢复，建议缩短并观察。"
    elif not adequate or not complete_feedback or plan == "unknown":
        action, reason = "insufficient", "缺少足够证据或明确计划，不给出适合加强训练的许可。"
    elif sustained or (feedback or {}).get("sleep_feeling") == "poor":
        action, reason = "shorten", "存在连续变化或主观睡眠不佳，先保守执行并观察。"
    else:
        action, reason = "maintain", "当前证据未触发调整规则；只支持原计划，不自动增加跑量或组数。"
    if trained_today and action == "maintain":
        action, reason = "insufficient", "时点前已跑步，报告仅供恢复复盘，不补发跑前执行许可。"
    if plan == "unknown":
        missing.append("未明确今日计划；周末不自动把两天都安排成长距离")
    confidence = "high" if adequate and complete_feedback and not retrospective and not trained_today else "medium"
    if not adequate or not complete_feedback:
        confidence = "low"
    if action == "observe" and adequate:
        confidence = "medium"
    if not selected:
        missing.append("无本地健康数据，请先运行 garmin-runner health sync")
    sources = [{"day": key[0], "kind": key[1], "fetched_at": row["fetched_at"], "raw_path": row["raw_path"]}
               for key, row in sorted(selected.items())]
    return {
        "schema_version": 1, "rules_version": "readiness-v1", "date": day.isoformat(),
        "cutoff": cutoff.isoformat(), "timezone": cfg.timezone,
        "mode": "retrospective" if retrospective else ("post_activity" if trained_today else "as_of"),
        "trained_before_cutoff": trained_today, "action": action, "action_label": ACTIONS[action],
        "reason": reason, "confidence": confidence, "plan": plan, "evidence": evidence,
        "conflicts": conflicts, "missing": missing, "excluded": excluded, "metrics": current,
        "baselines": baselines, "feedback": feedback, "recent_training": training,
        "sources": sources, "request_status": statuses, "rules": asdict(cfg),
        "prohibited": ["不因低心率、单晚 HRV 或身体电量高而确认恢复充分。",
                       "不把 HRV、压力、身体电量、准备度重复加权成恢复总分。",
                       "不补课、不自动加组；不把本报告当作伤病诊断或参赛能力证明。"],
        "limitations": ["规则阈值是可校准的工程启发式，不是医学标准。",
                        "事后回放仅使用截止前观测，但不能证明这些结果当时已同步可见。",
                        "缺失日期不计作休息；本地活动覆盖不一定完整。"],
    }
