from __future__ import annotations

from pathlib import Path

from garmin_runner.health_storage import private_json, private_text
from garmin_runner.reporting.daily import _format_pace, _format_duration


def _text(value):
    return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def _value(value, suffix=""):
    return f"{value:.1f}{suffix}" if value is not None else "未知"


def write_comparison_report(result: dict, reports_dir: Path) -> Path:
    target = result["target"]
    path = reports_dir / "comparison" / f"{target['start'][:10]}_{target['activity_id']}.md"
    lines = [f"# {target['start'][:10]} 同类训练对比", "", result["conclusion"], "",
             f"- 目标 activity_id：{target['activity_id']}",
             f"- 类型：{target['training_type']}（{target['type_source']}）",
             f"- 比较口径：{target['selection']}；范围 " + "、".join(f"{a:.3f}-{b:.3f}" for a, b in target['ranges_km']) + " km",
             f"- 回溯起点：{result['window_start']}；不纳入目标当天及之后的训练。",
             f"- 展示 {len(result['matches'])} / {result['eligible_count']} 个候选；条件相近 {result['strict_count']} 个。",
             f"- 排除 {len(result['excluded'])} 条；重复副本 {len(result['duplicates_excluded'])} 条（不删除）。",
             "", "## 所选段表现", "",
             "配速由有效 FIT 距离增量与对应时间计算。配速差 = 本次减历史，负数更快，不代表评分更高。", "",
             "| activity_id | 日期 | 档位 | 类型 | 比较段 | 有效距离 km | 配速 | 均心 | 配速差 秒/km | 心率差 |",
             "| --- | --- | --- | --- | --- | ---: | --- | ---: | ---: | ---: |"]
    for entry in [{"sample": target, "tier": "target"}] + result["matches"]:
        s, m = entry["sample"], entry["sample"]["metrics"]
        label = {"target": "本次", "comparable": "条件相近", "reference": "仅供参考"}[entry["tier"]]
        fields = [s["activity_id"], s["start"][:10], label, s["training_type"], s["selection"],
                  _value(m["distance_km"]), _format_pace(m["pace_s_per_km"]), _value(m["hr"]),
                  _value(entry.get("pace_delta_s_per_km")), _value(entry.get("hr_delta_bpm"))]
        lines.append("| " + " | ".join(_text(f) for f in fields) + " |")
    lines += ["", "## 时间与数据口径", "",
              "timer 为 Garmin 活动计时，elapsed 为包含暂停的总历时，moving 为 Garmin 移动时间；缺失时不相互填充。", "",
              "| activity_id | 全活动 km | timer | elapsed | moving | 所选段距离覆盖 | 心率覆盖 | 暂停/缺口 秒 |",
              "| --- | ---: | --- | --- | --- | ---: | ---: | ---: |"]
    samples = [target] + [p["sample"] for p in result["matches"]]
    for s in samples:
        m = s["metrics"]
        fields = [s["activity_id"], _value(s["total_distance_km"]), _format_duration(s["timer_duration_s"]),
                  _format_duration(s["elapsed_duration_s"]), _format_duration(s["moving_duration_s"]),
                  _value(m["distance_coverage"] * 100, "%"), _value(m["hr_coverage"] * 100, "%"), _value(m["pause_s"])]
        lines.append("| " + " | ".join(_text(f) for f in fields) + " |")
    lines += ["", "## 条件与主观反馈", ""]
    for s in samples:
        c = s["context"]
        lines += [f"### {s['activity_id']}", "",
                  f"- 气温：{_value(c.get('temperature_c'), '℃')}；湿度：{_value(c.get('humidity_pct'), '%')}；来源：{_text(c.get('weather_source', '未知'))}。",
                  f"- 路线：{_text(c.get('route', '未知'))}；路面：{_text(c.get('surface', '未知'))}；心率来源：{_text(c.get('hr_source', '未知'))}。",
                  f"- 原始体感说明：{_text(c.get('note', '未提供，不自动补填'))}",
                  f"- 所选段配速 CV：{_value(s['metrics']['pace_cv_pct'], '%')}；后半程掉速：{_value(s['metrics']['late_slowdown_pct'], '%')}。",
                  f"- 心率漂移：{_value(s['metrics']['drift_pct'], '%')}。" + "；".join(s["drift_notes"]), ""]
        lines += [f"- 数据限制：{_text(w)}" for w in s["warnings"]]
        if s["work_intervals"]:
            lines += ["| 快段 | km | 配速 | 均心 |", "| --- | ---: | --- | ---: |"]
            for i, work in enumerate(s["work_intervals"], 1):
                lines.append(f"| {i} | {_value(work['distance_km'])} | {_format_pace(work['pace_s_per_km'])} | {_value(work['hr'])} |")
            lines += ["", "组间恢复计时（秒）：" + " / ".join(_value(r["duration_s"]) for r in s["recovery_intervals"]) + "。", ""]
    lines += ["", "## 可比性说明", ""]
    for entry in result["matches"]:
        lines.append(f"- {entry['sample']['activity_id']}：" + "；".join(entry["reasons"] or ["已记录的比较条件在阈值内，仍不是严格实验对照"]))
    if not result["matches"]:
        lines.append("没有符合类型、距离及关键数据质量门槛的历史样本。")
    if result["linked_session"]:
        lines += ["", "## 同一次训练的关联记录", "", "以下为明确关联的热身、主训练或冷身，仅共同解释训练，不混入主段指标："]
        lines += [f"- {s['activity_id']}：{_value(s['distance_km'], ' km')}" for s in result["linked_session"]]
    lines += ["", "## 排除记录", ""]
    lines += [f"- {s['activity_id']}：" + "；".join(s["reasons"]) for s in result["excluded"]] or ["无。"]
    lines += ["", "## 使用边界", ""] + [f"- {s}" for s in result["limitations"]]
    lines += ["", f"规则版本：{result['version']}。阈值、分段与来源见 [结构化 JSON]({path.with_suffix('.json').name})。", ""]
    private_json(path.with_suffix(".json"), result)
    private_text(path, "\n".join(lines))
    return path
