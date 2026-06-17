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
    marathon_goal: "年底 2:45 全马目标"
    b_race_note: "东营作为 B 赛测试，不全力"
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

周训练结构来自 `config/athlete.yaml` 的 `training.weekly_structure`，默认包含周一全休、周二强度、周五稳态、周末长距离、常态周跑量 100-120km、年底 2:45 全马目标，以及东营 B 赛测试说明。

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

## 开发

运行测试：

```bash
pytest
```

当前测试覆盖：

- SQLite activity 写入。
- 重复 `activity_id` 去重，且不覆盖已有记录。

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
  analysis/          # 确定性训练分析
  reporting/         # 中文 Markdown 报告
```
