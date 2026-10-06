from __future__ import annotations

from pathlib import Path

from garmin_runner.health_storage import private_json, private_text


def _safe(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def write_readiness_report(result: dict, reports_dir: Path) -> Path:
    path = reports_dir / "readiness" / f"{result['date']}.md"
    modes = {"as_of": "截至指定时点的决策", "retrospective": "事后回放（非当时的跑前预测）",
             "post_activity": "当日训练后的恢复复盘"}
    plans = {"easy": "轻松/慢跑", "quality": "质量课", "steady": "稳态", "long": "长距离",
             "rest": "休息", "unknown": "未明确"}
    lines = [f"# {result['date']} 恢复决策", "", f"**建议：{result['action_label']}**", "",
             result["reason"], "", f"- 数据可信度：{result['confidence']}",
             f"- 模式：{modes[result['mode']]}", f"- 数据截止：{result['cutoff']}",
             f"- 时点前已经跑步：{'是' if result['trained_before_cutoff'] else '未发现本地记录（不等于未跑）'}",
             f"- 计划：{plans.get(result['plan'], result['plan'])}", f"- 规则版本：{result['rules_version']}", "", "## 数据面", "",
             "| 指标 | 值 |", "| --- | --- |"]
    fields = [("hrv", "last_night_ms", "夜间 HRV（ms）"), ("hrv", "weekly_ms", "7 日 HRV（ms）"),
              ("hrv", "baseline_low_ms", "Garmin 基线下界（ms）"),
              ("hrv", "baseline_high_ms", "Garmin 基线上界（ms）"),
              ("sleep", "duration_s", "夜间睡眠（时:分）"), ("rhr", "bpm", "当日静息心率（bpm）"),
              ("stress", "average", "当日压力汇总"), ("battery", "value", "截止前身体电量"),
              ("readiness", "value", "截止前 Garmin 准备度（仅参考）")]
    for kind, key, label in fields:
        value = result["metrics"].get(kind, {}).get(key)
        if value is not None and key == "duration_s":
            minutes = round(value / 60)
            value = f"{minutes // 60}:{minutes % 60:02d}"
        lines.append(f"| {label} | {value if value is not None else '缺失或不适用'} |")
    distance = result["recent_training"]["distance_km"]
    volume = f"{distance:.2f} km" if distance is not None else "未知（没有本地活动数据）"
    lines += ["", f"含报告当天的近 7 个当地日期、截至指定时点的本地跑量：{volume}；"
              f"排除重复副本 {result['recent_training']['duplicates_excluded']} 条。无记录不推断为休息。"]
    for title, key, empty in (("判断依据", "evidence", "未触发客观变化规则，不等于已确认恢复。"),
                              ("冲突证据", "conflicts", "未发现可确认的冲突。"),
                              ("缺失与可信度限制", "missing", "核心字段齐备。"),
                              ("不适用数据", "excluded", "没有因时间边界排除的记录。"),
                              ("禁止事项", "prohibited", ""), ("使用边界", "limitations", "")):
        lines += ["", f"## {title}", ""] + [f"- {_safe(item)}" for item in result[key] or [empty]]
    lines += ["", "## 体感反馈", ""]
    if result["feedback"]:
        labels = {"fatigue": "疲劳分", "legs": "腿沉分", "pain": "其他疼痛",
                  "sleep_feeling": "睡眠感受", "plan": "训练计划", "note": "用户原话",
                  "effort": "跑步体感", "leg_feeling": "腿部感觉", "soreness": "酸痛",
                  "completed": "今日训练已完成", "recorded_at": "反馈记录时间", "source": "来源"}
        values = {"unknown": "未明确", "easy": "轻松/慢跑", "normal": "不沉", "heavy": "沉重",
                  "mild": "轻微", "marked": "明显", "none": "无", "explicit_user_feedback": "用户明确反馈"}
        for key, value in result["feedback"].items():
            if value is not None:
                display = values.get(value, value) if isinstance(value, str) and key != "note" else value
                if key == "completed":
                    display = "是" if value else "否"
                lines.append(f"- {_safe(labels.get(key, key))}：{_safe(display)}")
    else:
        lines += ["未提供截止时点前的反馈，没有自动补填。"]
    lines += ["", "## 审计", "", f"结构化结果：[JSON]({path.with_suffix('.json').name})。"
              "包含阈值、个人参考样本数、同步状态和原始数据来源；仅保存在本地。", ""]
    private_json(path.with_suffix(".json"), result)
    private_text(path, "\n".join(lines))
    return path
