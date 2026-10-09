from __future__ import annotations

import hashlib
import json
import os
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote


def dashboard_knowledge(root: Path, reports_dir: Path, until: date) -> dict:
    # Lazy imports avoid a dependency cycle with the context report-period reader.
    from garmin_runner.knowledge import private_knowledge_path
    from garmin_runner.knowledge_coach import KINDS, evidence_file, iso_day, plan_for

    root = root.resolve()
    output = {"status": "missing", "as_of": None, "documents": [], "plan": [], "outlook": [],
              "warnings": [], "coach_status": "awaiting_review"}
    if not (root / "knowledge").exists():
        return output
    try:
        private_knowledge_path(root, "coach_context/daily/latest.json")
    except Exception:
        output["status"] = "unavailable"
        output["warnings"].append("知识库隐私检查未通过，未读取个人档案。")
        return output

    def document(relative, title, category):
        try:
            p = evidence_file(root, relative)
            content = p.read_text(encoding="utf-8")
            item = {"key": relative, "title": title, "category": category, "content": content,
                    "href": quote(os.path.relpath(p, reports_dir.resolve() / "dashboard")),
                    "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
            if not any(d["key"] == relative for d in output["documents"]):
                output["documents"].append(item)
        except Exception:
            output["warnings"].append(f"{title} 缺失、过大或路径不安全，未收录。")

    latest = root / "knowledge/coach_context/daily/latest.json"
    if latest.exists():
        try:
            run = json.loads(evidence_file(root, str(latest.relative_to(root))).read_text())
            cutoff = iso_day(run["as_of"])
            if cutoff > until:
                output["warnings"].append("最新阅读包晚于当前看板截止日，未作为该日期的依据。")
                return output
            output.update(status="stale" if cutoff < until else run["status"], as_of=run["as_of"])
            if output["status"] not in {"ready", "partial", "stale"}:
                raise ValueError
            if output["status"] != "ready":
                output["warnings"].append("本轮知识库整理不完整或已过期，请勿当作最新教练判断。")
            if run["context"] is not None:
                relative = run["context"]["path"]
                if not relative.startswith("knowledge/coach_context/generated/") or Path(relative).name != "context.json":
                    raise ValueError
                p = evidence_file(root, relative)
                raw = p.read_bytes()
                if hashlib.sha256(raw).hexdigest() != run["context"]["sha256"]:
                    raise ValueError
                context = json.loads(raw)
                if context["as_of"] != run["as_of"]:
                    raise ValueError
                if context["warnings"]:
                    output["warnings"].append("阅读包包含缺失资料或来源警告，详见本轮教练阅读包。")
                for offset in range(29):
                    day = cutoff + timedelta(days=offset)
                    plan = plan_for(day, cutoff, context["plans"])
                    label = KINDS[plan["kind"]]
                    state = {"confirmed": "已确认结构", "unknown": "未确定", "conflict": "计划冲突"}[plan["state"]]
                    if plan["state"] == "confirmed" and plan["kind"] == "unknown":
                        state = "当日未指定"
                    dose = "未确认"
                    if plan["kind"] == "rest":
                        dose = "全休"
                    elif plan.get("scope") == "sessions" and plan.get("target_km") is not None:
                        dose = f"整堂 {plan['target_km']:g} km；配速/分段未指定"
                    key = f"knowledge/plans/versions/{plan['plan_id']}.json" if plan.get("plan_id") else None
                    output["plan"].append({"day": day.isoformat(), "weekday": "一二三四五六日"[day.weekday()],
                                           "kind": label, "state": state, "dose": dose, "source_key": key})
                    if key and not any(d["key"] == key for d in output["documents"]):
                        document(key, "课表版本 " + plan["plan_id"], "课表来源")
                document(str(p.with_name("README.md").relative_to(root)), "本轮教练阅读包", "每日教练")
                for source in context["sources"]:
                    file = evidence_file(root, source["path"])
                    if hashlib.sha256(file.read_bytes()).hexdigest() != source["sha256"]:
                        output["warnings"].append("阅读包的一份来源已变更，需重建阅读包，旧版本不代表当前状态。")
                        output["status"] = "stale"
        except Exception:
            output.update(status="invalid", plan=[], documents=[])
            output["warnings"].append("本轮阅读包校验失败，未展示其中的计划或替换成旧包。")

    for path, title, category in (
        ("knowledge/profile/current.md", "当前目标与约束", "个人档案"),
        ("knowledge/plans/README.md", "当前课表与版本", "当前课表"),
        ("knowledge/races/README.md", "比赛档案", "比赛档案"),
        ("knowledge/index/recent-12-weeks.md", "最近 12 周记录", "长期趋势"),
        ("knowledge/index/reports.md", "长期报告索引", "长期趋势"),
        ("knowledge/insights/open_questions.md", "待确认问题", "待确认问题"),
    ):
        document(path, title, category)
    outlook = root / "knowledge/plans/outlook.json"
    if outlook.exists():
        try:
            data = json.loads(evidence_file(root, str(outlook.relative_to(root))).read_text())
            if data["schema_version"] != 1 or iso_day(data["recorded_on"]) > until:
                raise ValueError
            if not isinstance(data["items"], list) or len(data["items"]) > 30:
                raise ValueError
            for item in data["items"]:
                if item["kind"] not in {"goal", "historical_proposal"}:
                    raise ValueError
                if any(not isinstance(item[k], str) or len(item[k]) > 2000 for k in ("title", "detail", "conditions")):
                    raise ValueError
                if item["date"] is not None and iso_day(item["date"]) < until:
                    continue
                if item["kind"] == "goal" and item["date"] is None:
                    raise ValueError
                source = item["source"]
                if source["kind"] not in {"user_report", "assistant_summary", "historical_ai_proposal"}:
                    raise ValueError
                p = evidence_file(root, source["path"])
                raw = p.read_bytes()
                if hashlib.sha256(raw).hexdigest() != source["sha256"] or not source["quote"] or source["quote"] not in raw.decode("utf-8"):
                    raise ValueError
                document(source["path"], "计划依据：" + Path(source["path"]).stem, "计划来源")
                output["outlook"].append({k: item[k] for k in ("kind", "date", "title", "detail", "conditions")})
                output["outlook"][-1].update(source_key=source["path"],
                    status="训练目标，非能力结论" if item["kind"] == "goal" else "历史建议，待确认",
                    days_away=(iso_day(item["date"]) - until).days if item["date"] else None)
        except Exception:
            output["outlook"] = []
            output["warnings"].append("未来计划资料或来源校验失败，未将其作为有效课表展示。")
    catalog = root / "knowledge/sessions/catalog.json"
    if catalog.exists():
        try:
            rows = json.loads(evidence_file(root, str(catalog.relative_to(root))).read_text())["sessions"]
            rows = [r for r in rows if iso_day(r["day"]) <= until and iso_day(r["recorded_at"][:10]) <= until]
            for row in sorted(rows, key=lambda r: r["day"], reverse=True)[:20]:
                relative = "knowledge/sessions/" + row["document"]
                document(relative, f"{row['day']} 完整训练", "完整训练")
        except Exception:
            output["warnings"].append("完整训练关联目录无效，未自动按同日合并活动。")
    return output
