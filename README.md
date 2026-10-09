# garmin-runner

本地优先的 Garmin 跑步数据同步与训练分析工具。

GitHub: [nickwun/garmin-runner](https://github.com/nickwun/garmin-runner)

第一阶段已经包含：

- 从 Garmin Connect 同步跑步活动列表。
- 保存每条活动的 summary JSON。
- 下载 Garmin 原始活动文件，并提取 FIT 文件保存到本地。
- 用 Garmin 官方 FIT Python SDK 做一次 FIT 解码校验。
- 将标准化后的摘要字段写入 SQLite。
- 使用 `activity_id` 去重，避免重复下载。
- 通过 Typer 提供 `garmin-runner sync --since YYYY-MM-DD` CLI。
- 对已同步的单次训练生成中文 Markdown 分析报告。

后续阶段会在 `analysis` 和 `reporting` 层加入确定性训练指标、规则判断和中文 Markdown 报告生成。

## 从零开始安装

需要 Python 3.11+。

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## 初始化配置

复制示例配置：

```bash
cp config/athlete.example.yaml config/athlete.yaml
```

根据你的训练情况编辑 `config/athlete.yaml`，尤其是心率区间：

```yaml
training:
  heart_rate_zones:
    recovery_low: 120
    recovery_high: 135
    easy_low: 133
    easy_high: 145
    aerobic_high: 155
    steady_high: 165
    mp_bridge_high: 170
    threshold_high: 178
    vo2_high: 188
    sprint_high: 194
  long_run_min_distance_km: 18
  long_run_min_duration_min: 90
  weekly_structure:
    rest_day: "monday"
    normal_volume_min_km: 100
    normal_volume_max_km: 120
    tuesday_quality: true
    friday_steady: true
    weekend_long_run: true
    marathon_goal: "12月27日福州马拉松全力冲击2:45"
    b_race_note: "11月15日南昌马拉松争取刷新全马PB并测试实力"
```

如果你的 Garmin Connect 是中国区账号（`https://connect.garmin.cn/`），请确认：

```yaml
garmin:
  is_cn: true
```

## 设置凭证

推荐使用交互式命令创建本地 `.env`：

```bash
garmin-runner setup-credentials
```

它会询问 Garmin email，并用隐藏输入询问 Garmin password。如果 `.env` 已存在，会要求选择：

- `overwrite`
- `keep`
- `backup and overwrite`

如果你不想保存密码，可以只保存 email：

```bash
garmin-runner setup-credentials --email-only
```

然后通过系统环境变量或手动方式提供 `GARMIN_PASSWORD`。

字段名也可以参考 [.env.example](.env.example)，但不要把真实 `.env` 提交到 Git。

`config/athlete.yaml`、`.env`、Garmin token、原始 FIT 文件、SQLite 数据库和本地报告都已加入 `.gitignore`，不要提交到 Git。

默认本地数据位置：

- token: `data/tokens`
- SQLite: `data/garmin-runner.sqlite`
- summary JSON: `data/raw/summary`
- FIT: `data/raw/fit`
- report: `reports/daily`

## 运行 Doctor

先检查本地环境、配置、目录、SQLite 和 Garmin 登录状态：

```bash
garmin-runner doctor
```

如果只想离线检查本地配置，不登录 Garmin：

```bash
garmin-runner doctor --skip-login
```

## 运行同步

```bash
garmin-runner sync --since 2026-01-01
```

首次登录可能需要 Garmin MFA 验证码。同步过程不会在日志里打印密码、token、cookie 或完整健康数据。

## 查看活动列表

从 SQLite 列出最近活动，方便复制 activity_id：

```bash
garmin-runner list --limit 20
garmin-runner list --since 2026-01-01 --limit 20
```

## 生成单次训练报告

先同步，再用 activity_id 生成报告：

```bash
garmin-runner analyze 123456789
```

输出路径：

```text
reports/daily/YYYY-MM-DD_<activity_id>.md
```

报告包含：

- 数据面
- 生理面
- 执行打分
- 教练指令

第一版教练指令来自规则引擎，不调用 LLM。

## 生成周训练报告

周报会读取 SQLite 中已同步活动，并复用单次训练分析结果生成周级训练量、强度结构、关键训练、风险信号和下周建议。
对 `阈值间歇`、结构化稳态跑等整段 FIT 记录，如果中段配速或心率变化明显，单次报告会拆出热身、主训练、快段和冷身；周报的稳态、阈值/间歇统计优先使用主训练段，不把热身冷身都算作质量训练。

```bash
garmin-runner report weekly --week current
garmin-runner report weekly --week 2026-W25
garmin-runner report weekly --since 2026-06-15 --until 2026-06-21
```

输出路径：

```text
reports/weekly/YYYY-Www.md
```

周训练结构来自 `config/athlete.yaml` 的 `training.weekly_structure`，默认包含周一全休、周二强度、周五稳态、周末长距离、常态周跑量 100-120km、11 月 15 日南昌马拉松争取刷新 PB 并测试实力，以及 12 月 27 日福州马拉松全力冲击 2:45。

## 生成月训练报告

月报会读取当月已同步活动，复用单次训练分析和分段结果，生成月跑量、周跑量分布、强度结构、能力趋势、疲劳趋势、目标意义和下月建议。

```bash
garmin-runner report monthly --month current
garmin-runner report monthly --month 2026-06
```

输出路径：

```text
reports/monthly/YYYY-MM.md
```

月报仍使用确定性规则，不调用 LLM；阈值/间歇和结构化稳态优先按主训练段统计。

## 生成 Codex 教练总结

教练层是可选的 LLM 总结层。它不会读取原始 FIT、SQLite 明细或重新计算训练指标，只读取 daily/weekly/monthly 分析模块已经生成的结构化 JSON，再把这些确定性结果改写成更像教练的中文建议。

```bash
garmin-runner coach daily ACTIVITY_ID
garmin-runner coach weekly --week current
garmin-runner coach monthly --month current
```

输出路径：

```text
reports/coach/<daily|weekly|monthly>/*_prompt.md
reports/coach/<daily|weekly|monthly>/*_data.json
reports/coach/<daily|weekly|monthly>/*_output.md
```

启用前需要在本地 `.env` 设置：

```bash
GARMIN_RUNNER_COACH_API_KEY=your-local-secret
GARMIN_RUNNER_BACKGROUND_PATH=/Users/hui/Downloads/跑步背景信息.md
```

`GARMIN_RUNNER_COACH_API_KEY` 只作为本地私有启用开关，不要提交到 Git。当前 provider 使用本机 Codex CLI；如果缺少 key，命令会给出中文提示并退出。个人跑步背景可以放在 `GARMIN_RUNNER_BACKGROUND_PATH` 指向的文件，或本地 `config/running_background.md`，后者已被 `.gitignore` 忽略。

## 真实数据验收

检查本地环境、配置、目录、SQLite 和 Garmin 登录状态：

```bash
garmin-runner doctor
```

离线只看本地检查：

```bash
garmin-runner doctor --skip-login
```

从 SQLite 列出最近活动，方便复制 activity_id：

```bash
garmin-runner list --limit 20
garmin-runner list --since 2026-01-01 --limit 20
```

检查某条活动的本地数据完整性：

```bash
garmin-runner inspect 123456789
```

`inspect` 只输出 SQLite、summary JSON、FIT 文件状态、summary 关键字段、FIT record 数量和字段列表；不会展开完整原始 JSON 或 FIT records。

## 常见错误

MFA：
首次登录或 Garmin 风控时可能要求 MFA。`doctor`、`sync` 会提示输入验证码，验证码只在本地交互使用，不会写入 Git。

登录失败：
检查 `.env` 是否存在，或运行 `garmin-runner setup-credentials` 重新写入。CLI 不会打印密码、token 或 cookie。

token 失效：
删除或备份本地 `data/tokens` 后重新运行 `garmin-runner doctor` 或 `garmin-runner sync`，让 Garmin 重新登录并刷新 token。

数据库不存在：
先运行 `garmin-runner sync --since YYYY-MM-DD`。`garmin-runner list` 和 `inspect` 会给出中文提示。

FIT 缺失：
运行 `garmin-runner inspect ACTIVITY_ID` 查看 FIT 路径状态。缺失时可重新同步对应日期范围。

Garmin 字段变化：
如果 summary 或 FIT 字段变化，`inspect` 会列出当前可用字段和明显缺失字段，便于定位解析问题。

## 恢复数据与决策（第一阶段）

继续使用现有中国区配置和本地凭证，不需要第二套 Garmin 登录。

```bash
garmin-runner health doctor
garmin-runner health sync --days 56
garmin-runner feedback --date today
garmin-runner report readiness --date today
```

- `health doctor` 实际请求最近七天的 HRV、睡眠、静息心率、压力、身体电量和可选训练准备度，保存本地响应并输出 PASS/WARN/FAIL。空数据不等于设备不支持；403、404、网络及结构变化分别作为请求失败处理。
- `health sync` 支持 `--until YYYY-MM-DD` 和 `--refresh`；最近七天及当日会刷新，较早的成功日期跳过，缺失或失败项可重试。遇到限流或认证失效立即停止，不密集重试。CLI 只打印可用性，不输出原始健康响应或异常正文。
- 首次建立健康表前通过 SQLite backup 备份到本地 `data/backups/`。健康响应按日期、来源及内容哈希保存在 `data/raw/health/`；成功历史版本不删除，失败状态不会覆盖成功数据。文件和数据库使用仅当前用户可读写的权限。
- 体感反馈追加保存，留空仍是未知，不改写原话。可使用 `feedback --non-interactive --date YYYY-MM-DD --fatigue 4 --legs 5 --pain no --plan easy --note "体感原话"`。疲劳与腿沉为 0-10；睡眠感受使用 `--sleep-feeling good/poor/unknown`。补录历史反馈不会被伪装成当时已经知道的信息。
- 也支持自然描述的明确结构化项：`--completed --effort easy --leg-feeling normal --soreness mild` 表示已跑完、体感轻松、腿不沉、轻微酸痛。不会自动换算成数字疲劳分，也不把轻微酸痛等同于其他疼痛；已完成训练的报告只做恢复复盘，不补发跑前许可。
- 报告保存为 `reports/readiness/YYYY-MM-DD.md` 和同名 JSON，CLI 提供绝对路径快捷链接。JSON 包含阈值、参考样本数、来源和同步状态。相同日期重新生成只更新派生报告，原始数据与反馈不受影响。

历史回放示例：

```bash
garmin-runner report readiness --date 2026-09-22 --at 05:00
```

历史日期默认截止当地 07:00；当日默认当前时点，可用 `--at HH:MM` 指定。日期使用 `athlete.timezone`。睡眠按醒来日期关联，晚于截止的观测和反馈不参与计算；事后取得的当日静息心率/压力汇总不用于跑前判断，身体电量与准备度仅取截止前的采样。历史补采明确标为“事后回放”，不能证明当时已同步可见。时点前已有跑步记录时，不补发跑前执行许可。

规则使用 `recovery` 配置（见示例 YAML）：过去 28 天至少 21 天数据形成睡眠/静息心率个人中位数参考；默认连续 2 天偏离才构成持续变化，静息心率偏高阈值 5 bpm、睡眠减少阈值 1 小时，当前观测及反馈新鲜度上限 6 小时。以上均为可校准工程启发式，不是医学标准。

HRV 用 Garmin 的 7 日均值对照其个人基线，不能直接把该区间套在单晚值上；单晚变化不单独取消训练。固定全休日仍全休；明确腿沉或疲劳不会被低心率/平衡 HRV 抵消；证据不足不批准加量。身体电量、压力和准备度仅作为参考，不与 HRV 重复加权生成黑箱总分。不使用 LLM 计算或诊断。

验收报告位于 `reports/validation/health_doctor.md` 与 `health_sync.md`。所有健康数据、备份、反馈及报告都留在本地，不提交 Git。MCP 属于后续阶段，当前没有提供该入口。

## 同类训练对比（第二阶段）

```bash
garmin-runner compare ACTIVITY_ID --days 90 --limit 5
garmin-runner compare ACTIVITY_ID --against HISTORY_ACTIVITY_ID --days 365
```

只读取本地 SQLite、summary 和 FIT，不联网、不改写活动、不覆盖单次报告。输出 `reports/comparison/YYYY-MM-DD_ACTIVITY_ID.md` 和同名 JSON，命令输出可点击绝对路径。缺少数据库、FIT 或无可比样本均给出中文说明；没有历史数据时不会创建空数据库。

- **条件相近**：类型一致、所选段距离差不超过 20%、平均心率差不超过 5 bpm、气温差不超过 3℃、湿度差不超过 10 个百分点，且路线、路面、心率来源一致，每公里爬升差不超过 5m。仍是观察性比较，不是严格对照实验。
- **仅供参考**：天气、路线或心率来源缺失，明显跑停、条件差异较大，或主训练段只是自动估计。缺失不是相同，不使用天气校正公式，不直接下进步/退步结论。
- **排除**：类型/距离不匹配、关键 FIT 字段覆盖不足、比赛或孤立热身冷身。重复记录按精确活动签名排除但保留原始记录。只选择目标当地日期之前的活动，不混入同日热身冷身和未来记录。
- 配速、心率均由所选 FIT 距离段积分，心率按时间加权；关键心率与距离覆盖至少 90%，有效记录至少 60。timer、elapsed、moving 三种时间单列，不混用。
- 优先按 FIT `intensity` 圈标记拆分主训练，将连续 active 公里圈合并为一个工作组；间歇比较组数、单组距离及组间恢复时长（差超过 15 秒降级）。缺少快段结构时，不计算包含恢复段的全程配速差来评价间歇质量。
- 连续跑显示主段配速 CV、前后半掉速与速度/心率效率变化。间歇、比赛、明显变速、计划渐进、明显暂停、关键字段不足及不足 20 分钟的段不输出生理漂移结论。热身冷身不混入明确主段。
- 至少 3 个条件相近样本才汇总配速差中位数，仍不把表现差异直接解释为能力或恢复变化。慢跑更慢不扣分，也不会因为更快给高分。

可选校准保存在私有 `data/comparison_context.json`，不存在时正常运行，缺失条件降级。也可用 `--context PATH` 指定。下例仅为虚构格式示例，不是运动员数据：

```json
{
  "version": 1,
  "activities": {
    "example-activity": {
      "training_type": "阈值间歇",
      "work_ranges_km": [[3, 5], [5.4, 7.4]],
      "temperature_c": 23,
      "humidity_pct": 80,
      "weather_source": "用户现场记录",
      "route": "训练场A",
      "surface": "塑胶",
      "hr_source": "心率带",
      "note": "用户提供的体感原话",
      "session_activity_ids": ["warmup-id", "cooldown-id"]
    }
  }
}
```

连续主段用 `main_range_km: [3, 11]`，与 `work_ranges_km` 互斥；范围是活动累计公里数。`variable_effort: true` 表示明确计划渐进/变速。天气必须带来源，不将 FIT 腕表温度冒充环境温度；不自动从自由文本猜测条件。关联活动必须存在且同日，仅共同解释训练，不改变主段对比。未填写数字疲劳分时不自动换算。

## 本地训练看板（第三阶段）

```bash
garmin-runner report dashboard
garmin-runner report dashboard --days 90 --until 2026-10-03
```

输出 `reports/dashboard/index.html`，点击命令给出的链接即可离线打开，无需服务器或额外前端安装。包含训练概览、可搜索分页的活动表、健康趋势及报告库；日期筛选作用于已导出的快照，不会联网加载其他日期。默认覆盖 56 个当地日期，支持 7-366 天。重新生成会更新该派生看板，不更改原始数据和已有分析报告。

- 只读 SQLite 与已有报告，不触发 Garmin 登录、同步、FIT 重算或恢复决策。不把旧恢复报告冒充当前判断；生成日期、数据最新日期、缺口及报告截止均可见。
- 跑量按精确活动签名去重；缺失距离和无活动记录的日期保持未知，不推断休息。配速使用已有 summary 的活动计时/距离，不冒充主训练段或移动配速。
- 健康图表取本地规范化观测，缺失不补零或跨缺口连线；显示的是日期回顾，不是历史跑前预测。身体电量为当日最后有效采样，不是最高值。HRV 基线只与 7 日均值一起展示。
- 分类优先采用本地校准、已有对比结构化结果、单次报告显式分类；没有结构化类型时明确标注，不再根据平均心率猜测。
- 报告可以在页面中阅读并打开原始 Markdown。仅收录日期与窗口相交的 daily/weekly/monthly/annual/comparison/readiness 报告；周月年报保留原有完整周期，不随筛选重算。单份上限 256KB、总数上限 200，排除数量可见；不收录 coach prompt、原始数据或目录外的符号链接。
- HTML 内嵌本次快照和所收录的报告，使用本地脚本及画布，不加载外部字体、图库或 CDN；CSP 禁止网络连接。文件包含个人健康信息，**不要上传、分享或提交 Git**，默认权限 0600。
- 这是只读看板，不能在页面里同步、修改凭证、写反馈或批准训练；数据变化后重新运行生成命令即可更新。

## 私有跑步知识库（建档阶段）

```bash
garmin-runner knowledge index --as-of YYYY-MM-DD
```

`knowledge/` 保存私有训练档案、比赛记录、来源快照与活动索引，不提交 Git。
命令只读 SQLite，重新生成 `knowledge/index/` 下的年份、最近 12 周、报告及 JSON 索引；
不改写原始数据、已有报告或人工档案，不自动升级课表，不调用 LLM。
导出前检查目录忽略规则及 Git 跟踪状态；缺失文件、日期覆盖和精确重复候选单独标记。
活动条数不等于训练堂数，关联热身、主训练、冷身需有明确证据。
聊天原话和旧教练建议分别保存，建议不自动视为已确认课表。现有定时任务尚不更新知识库索引。

### 教练阅读包与计划版本（知识库第二阶段）

```bash
garmin-runner knowledge plan-record --file knowledge/plans/inbox/plan.json
garmin-runner knowledge context --as-of YYYY-MM-DD --days 14
```

计划 JSON 登记在 `knowledge/plans/versions/`，必须带原话来源、SHA-256、记录日、生效范围与版本 ID。
草案、本人确认、取消分别保存；修订使用新 ID 和 `supersedes`，同 ID 只允许完全相同内容的幂等登记。
结构计划不包含配速或里程剂量；支持按周交替，未指定日期保留未知。文字来源是否真的支持计划内容仍须人工审核，哈希校验不是语义批准。

阅读包在 `knowledge/coach_context/generated/` 每次生成独立 JSON/Markdown，不覆盖旧包。
包含近期设备记录、去重后的逐日跑量、本人反馈、计划对照、后续两周已确认结构、来源原文与历史报告链接。
人工资料由本地 `knowledge/coach_context/catalog.json` 白名单登记，保留作者类别、记录日期及哈希；变更或缺失时警告，不悄悄引用。
健康部分仅列覆盖，不据此判断恢复；没有跑步记录不当作全休，没有主段证据不判断质量课完成。
这是当前本地数据的日期回顾，不是严格的历史跑前回放。工具不联网、不调用 LLM、不改原始数据、不批准新处方，也不修改定时任务。
输入格式和版本边界见 [教练知识库使用说明](docs/knowledge-coaching.md)。全部输入及产物仍在被忽略的 `knowledge/` 内。

### 每日教练资料与看板（知识库第三阶段）

```bash
garmin-runner knowledge daily --as-of today
garmin-runner knowledge daily --as-of YYYY-MM-DD --days 14
```

在现有 sync、health sync 与新增活动 analyze 之后运行：按配置时区解析日期，依次更新派生索引、生成独立教练阅读包、刷新本地看板。
不联网、不重新计算生理指标、不覆盖个性化日报、不自动批准或下发课表。该命令成功只表示资料整理完成，不能证明 Garmin 同步完整，也不代表 Codex 已完成教练分析。

看板新增「教练知识库」，可查看已确认交替结构、完整训练、比赛档案、长期报告索引和待确认问题；档案支持页内预览和本地原文链接。
「课表计划」单独展示未来四周逐日安排与比赛目标，首页预览未来七天。结构确认不代表训练剂量确认；整堂距离与主段距离分开，未排期的建议不自动绑定训练日。
本地 `knowledge/plans/outlook.json` 登记比赛目标及历史教练建议，附来源原话与哈希；建议单列为待确认，不能覆盖 `plans/versions/` 中的已确认计划。
窗口筛选仅作用于训练数据；知识库明确显示自己的资料日期。无阅读包、过期、来源变更和部分失败分别显示，不用旧包冒充本轮成功。
运行状态保存在忽略的 `knowledge/coach_context/daily/latest.json`；全部阅读包保留，原始档案不改写。
现有定时任务需另行授权后更新，不会因安装此版本自动改动。具体说明见 [教练知识库使用说明](docs/knowledge-coaching.md)。

## 开发

运行测试：

```bash
pytest
```

当前测试覆盖：

- SQLite activity 写入。
- 重复 `activity_id` 去重，且不覆盖已有记录。
- 健康数据规范化、SQLite 备份与幂等、错误脱敏、限流停止。
- 恢复判断、反馈保真、缺失/过期数据、历史回放的时间边界与原始活动副本排除。
- 同类对比条件分档、FIT 圈段合并、主段积分、间歇结构匹配、暂停与缺失字段、只读及隐私边界。
- 离线看板数据筛选与去重、数据库只读、缺失/过期标记、报告范围/大小限制、符号链接排除和脚本转义。

## 结构

```text
src/garmin_runner/
  cli.py             # Typer CLI
  config.py          # .env 与 YAML 配置
  garmin_client.py   # python-garminconnect 访问层
  fit.py             # Garmin FIT SDK 解析边界
  normalize.py       # Garmin summary -> 标准化记录
  storage.py         # SQLite 存储
  sync.py            # 同步编排
  health.py          # 健康数据规范化与同步
  health_storage.py  # 健康版本、请求状态与反馈
  health_cli.py      # health/feedback/readiness 命令
  comparison_cli.py  # 本地同类训练对比命令
  dashboard.py       # 只读看板数据与离线导出
  dashboard.html     # 自包含交互页面模板
  dashboard_cli.py   # report dashboard 命令
  analysis/          # 确定性训练分析
  reporting/         # 中文 Markdown 报告
```
