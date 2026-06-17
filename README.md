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

## 安装

需要 Python 3.11+。

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## 配置

复制示例配置：

```bash
cp config/athlete.example.yaml config/athlete.yaml
```

创建 `.env`：

```bash
GARMIN_EMAIL=you@example.com
GARMIN_PASSWORD=your-password
```

`config/athlete.yaml`、`.env`、Garmin token、原始 FIT 文件、SQLite 数据库和本地报告都已加入 `.gitignore`，不要提交到 Git。

默认本地数据位置：

- token: `data/tokens`
- SQLite: `data/garmin-runner.sqlite`
- summary JSON: `data/raw/summary`
- FIT: `data/raw/fit`
- report: `reports/daily`

单次训练分析需要在 `config/athlete.yaml` 中填写个人训练区间：

```yaml
training:
  heart_rate_zones:
    maf_low: 120
    maf_high: 145
    steady_high: 155
    threshold_high: 170
  long_run_min_distance_km: 18
  long_run_min_duration_min: 90
```

## 运行同步

```bash
garmin-runner sync --since 2026-01-01
```

首次登录可能需要 Garmin MFA 验证码。同步过程不会在日志里打印密码、token、cookie 或完整健康数据。

如果登录失败，CLI 会给出清晰提示：

- 本地 token 不可用且 `.env` 没有 Garmin 账号变量。
- Garmin 账号、密码或 MFA 错误。
- Garmin Connect 或网络连接失败。

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
