from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Protocol


COACH_API_KEY_ENV = "GARMIN_RUNNER_COACH_API_KEY"


@dataclass(frozen=True)
class CoachRequest:
    scope: str
    title: str
    structured_data: dict[str, Any]
    athlete_background: str
    training_context: dict[str, Any]


@dataclass(frozen=True)
class CoachResult:
    output_text: str
    prompt_path: Path
    output_path: Path
    data_path: Path


class CoachClient(Protocol):
    def complete(self, prompt: str) -> str:
        """Return the coach summary for a fully built prompt."""


class CodexCoachClient:
    def __init__(
        self,
        command: str | None = None,
        cwd: Path | None = None,
        timeout_s: int = 300,
    ) -> None:
        self.command = command or os.getenv("GARMIN_RUNNER_CODEX_COMMAND", "codex")
        self.cwd = cwd or Path.cwd()
        self.timeout_s = timeout_s

    def complete(self, prompt: str) -> str:
        with tempfile.NamedTemporaryFile("r+", encoding="utf-8", suffix=".md") as output:
            try:
                completed = subprocess.run(
                    [
                        self.command,
                        "exec",
                        "--ephemeral",
                        "--sandbox",
                        "read-only",
                        "--cd",
                        str(self.cwd),
                        "--output-last-message",
                        output.name,
                        "-",
                    ],
                    input=prompt,
                    text=True,
                    capture_output=True,
                    timeout=self.timeout_s,
                    check=False,
                )
            except FileNotFoundError as exc:
                raise RuntimeError(
                    "未找到 Codex CLI。请先安装或登录 Codex，再运行 coach 命令。"
                ) from exc
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError("Codex 教练生成超时，请稍后重试。") from exc
            if completed.returncode != 0:
                stderr = (completed.stderr or completed.stdout or "").strip()
                hint = stderr.splitlines()[-1] if stderr else "Codex CLI 返回失败"
                raise RuntimeError(f"Codex 教练生成失败：{hint}")
            output.seek(0)
            response = output.read().strip()
        if not response:
            raise RuntimeError("Codex 教练没有返回内容。")
        return response


def require_coach_api_key() -> str:
    value = os.getenv(COACH_API_KEY_ENV)
    if not value:
        raise RuntimeError(
            f"缺少 {COACH_API_KEY_ENV}。请把它写入本地 .env；该值只用于启用本地 Codex 教练层，不要提交到 Git。"
        )
    return value


def build_coach_prompt(request: CoachRequest) -> str:
    structured_data = _json_dumps(request.structured_data)
    missing_notice = (
        "结构化数据缺失，无法判断。请在输出中明确说明无法判断，不要补数据。"
        if not request.structured_data
        else "结构化数据已提供。"
    )
    context = _json_dumps(request.training_context)
    return f"""你是一个中文跑步教练总结层。你只能把结构化训练数据转成教练建议，不负责也不得重新计算距离、配速、心率、强度比例。

硬性规则：
- 只读取下方结构化 JSON 和训练背景。
- 不得编造数据；JSON 没有的数据必须写“无法判断”。
- 不要输出原始 JSON，不要暴露 token、cookie、账号或隐私路径。
- 输出必须包含四个小节：训练判断、风险提醒、下一步建议、禁止事项。
- 语气要像认真负责的跑步教练，中文简洁、具体、可执行。

训练背景：
{request.athlete_background or "未提供个人训练背景。"}

当前目标与训练结构：
{context}

必须纳入判断的固定约束：
- 周一全休
- 周二强度
- 周五稳态
- 周末长距离
- 常态周跑量 100-120km
- 福州马拉松 2:45 目标
- 东营 B 赛测试，不全力

本次任务：{request.title}

结构化数据状态：
{missing_notice}

结构化训练数据 JSON：
```json
{structured_data}
```

请输出 Markdown，且只输出教练总结正文。
"""


def run_coach(
    request: CoachRequest,
    reports_dir: Path,
    client: CoachClient,
) -> CoachResult:
    prompt = build_coach_prompt(request)
    output = client.complete(prompt)
    base = _artifact_base(Path(reports_dir), request.scope, request.title)
    base.parent.mkdir(parents=True, exist_ok=True)
    prompt_path = base.with_name(base.name + "_prompt.md")
    output_path = base.with_name(base.name + "_output.md")
    data_path = base.with_name(base.name + "_data.json")
    prompt_path.write_text(prompt, encoding="utf-8")
    output_path.write_text(output, encoding="utf-8")
    data_path.write_text(_json_dumps(request.structured_data), encoding="utf-8")
    return CoachResult(
        output_text=output,
        prompt_path=prompt_path,
        output_path=output_path,
        data_path=data_path,
    )


def to_jsonable(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return {key: to_jsonable(item) for key, item in dataclasses.asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    return value


def load_athlete_background(config_path: Path | None = None) -> str:
    candidates: list[Path] = []
    env_path = os.getenv("GARMIN_RUNNER_BACKGROUND_PATH")
    if env_path:
        candidates.append(Path(env_path).expanduser())
    if config_path is not None:
        candidates.append(config_path.parent / "running_background.md")
    candidates.append(Path("config/running_background.md"))
    candidates.append(Path.home() / "Downloads" / "跑步背景信息.md")
    for path in candidates:
        if path.exists():
            return path.read_text(encoding="utf-8")
    return "未提供个人跑步背景信息。"


def training_context_from_settings(settings: Any) -> dict[str, Any]:
    training = settings.training
    return {
        "rest_day": training.weekly_rest_day,
        "tuesday_quality": training.tuesday_quality,
        "friday_steady": training.friday_steady,
        "weekend_long_run": training.weekend_long_run,
        "normal_weekly_volume": f"{training.normal_weekly_volume_min_km:.0f}-{training.normal_weekly_volume_max_km:.0f}km",
        "marathon_goal": training.marathon_goal,
        "b_race_note": training.b_race_note,
    }


def _artifact_base(reports_dir: Path, scope: str, title: str) -> Path:
    safe_title = (
        title.replace("/", "-")
        .replace(" ", "_")
        .replace("：", "_")
        .replace(":", "_")
    )
    return reports_dir / "coach" / scope / safe_title


def _json_dumps(value: Any) -> str:
    return json.dumps(to_jsonable(value), ensure_ascii=False, indent=2, sort_keys=True)
