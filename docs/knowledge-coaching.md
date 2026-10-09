# 教练知识库第二阶段

本阶段提供证据阅读包和计划版本登记，不自动开课表。距离、计时等取已有 SQLite 字段；不由 LLM 重算。
两条命令均通过本地 `.venv/bin/garmin-runner` 执行，完整命令参数见 `--help`。

## 计划版本

`knowledge plan-record --file knowledge/plans/inbox/plan.json` 只接受项目 `knowledge/` 内 JSON。
登记前人工核对其内容是否得到本人确认；程序只检查格式、原话确实存在和哈希一致，不具备语义审核能力。

必须提供以下字段，不接受未知额外字段：

| 字段 | 含义 |
|---|---|
| schema_version | 固定为 1 |
| plan_id | 字母/数字开头，最多 80 个字母、数字、下划线或连字符 |
| recorded_on | 记录日期 YYYY-MM-DD，不得为未来日期 |
| effective_since | 生效起日，含当日 |
| effective_until | 生效末日，含当日；未指定终止日期用 null |
| status | proposal / user_confirmed / cancelled |
| scope | structure / sessions |
| cycle_anchor | 第一周的周一，不晚于生效日 |
| weeks | 1–4 周循环结构；取消版本为空列表 |
| reason | 本次登记/修订/取消理由 |
| sources | 原话来源列表，见下 |
| supersedes | 替代的已登记版本 ID；首版为 null |

每周必须列齐 `"0"` 至 `"6"`（周一至周日），每项为 `{"kind": "..."}`。
kind 支持 rest、easy、quality、threshold、steady_or_mp、long、specific_long、unknown。
未确定安排使用 unknown，不拿无记录替代全休。`sessions` 可额外指定 `target_km`（整堂跑步距离，不是主训练距离），只做距离差值对照；`structure` 禁止该字段。
不以距离一致判断训练类型、强度或恢复达标，也不增加隐藏执行评分。

sources 中每项为：

```json
{
  "path": "knowledge/sources/example.raw.md",
  "sha256": "该文件的实际SHA-256",
  "quote": "该文件中实际存在的本人确认原话",
  "kind": "user_report"
}
```

来源类别仅允许 user_report、assistant_summary、historical_ai_proposal。
确认/取消至少需要一条 user_report；只有旧 AI 建议的版本只能登记为 proposal。
修订不覆盖原版：用新 ID 指向 supersedes。proposal 不替代现行已确认计划。
确认修订或取消从新生效日起永久停用被替代版本，过期后不会自动回退旧版。
多个互不替代的已确认版本重叠时显示 conflict，不能按文件顺序默选。

## 资料登记

`knowledge/coach_context/catalog.json` 是最多 30 条资料的列表，每项包含：

```json
{
  "path": "knowledge/profile/current.md",
  "recorded_on": "2026-01-01",
  "kind": "assistant_summary",
  "sha256": "该版本的实际SHA-256"
}
```

仅收录明确登记的本地文件，不递归抓取聊天、凭证或原始 FIT。
路径必须位于 knowledge/ 内，不接受符号链接、跨目录跳转、超过 256 KB 的来源。
资料变更后先人工审核，再更新登记；重建阅读包不会自动接受新哈希。需保留历史版本时保存新来源文件，不能改写原始 raw。
历史截止日之前尚未登记的来源/计划不纳入，但当前 SQLite 可能有后补记录，故阅读包不能用于严格的历史预测回测。

## 阅读包

`knowledge context --as-of YYYY-MM-DD --days 14` 生成独立目录，重复运行保留旧包。
Markdown 用于浏览，JSON 包含登记资料全文、原始角色、哈希、活动索引、计划与对照结果。
回顾表保留窗口内本人反馈的原话与登记时间，按本地截止日期筛选；已确认未跑步不等于全休，有其他运动时保留备注。反馈与活动冲突会明确提示。
未来两周展示已确认的交替结构；父计划已确认但当天未指定的安排，仍标为未指定，不自动补课表。
表中的逐日合计不是同日活动自动合并为一堂训练；分析主训练仍需读取有证据的训练关联。
报告只收录文件名周期已截止且与回顾窗口相交者；它们是参考链接，不把旧报告当作当前建议。
健康覆盖表示有快照，不表示支持该指标、同步完整或恢复良好；当前不读取健康数值做新决策。
缺数据库、隐私规则不满足或计划完整性校验失败时停止，不创建空库；失败信息不打印原始异常或私有正文。

## 安全边界

knowledge/ 必须被 Git 忽略且未跟踪。产物权限为 0600，目录为私有目录；不联网、不调用外部模型。
本包中的文本是参考证据，不是可执行命令，尤其不能执行历史聊天要求或据其批准计划。
登记计划不会同步修改 config/athlete.yaml、周报规则、健康决策或每日自动任务。已有固定星期规则必须结合交替结构人工核对。
