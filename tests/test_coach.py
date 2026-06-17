from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from garmin_runner.cli import app
from garmin_runner.coach import (
    COACH_API_KEY_ENV,
    CoachClient,
    CoachRequest,
    build_coach_prompt,
    run_coach,
)


runner = CliRunner()


def test_prompt_builder_includes_background_goals_rules_and_json() -> None:
    request = CoachRequest(
        scope="weekly",
        title="2026-W25 周训练",
        structured_data={"total_distance_km": 100, "conclusion": "稳定积累"},
        athlete_background="跑龄 13 年，MAF 训练为核心。",
        training_context={
            "rest_day": "周一全休",
            "tuesday_quality": "周二强度",
            "friday_steady": "周五稳态",
            "weekend_long_run": "周末长距离",
            "normal_weekly_volume": "100-120km",
            "marathon_goal": "福州马拉松 2:45 目标",
            "b_race_note": "东营 B 赛测试，不全力",
        },
    )

    prompt = build_coach_prompt(request)

    assert "跑龄 13 年" in prompt
    assert "周一全休" in prompt
    assert "周二强度" in prompt
    assert "周五稳态" in prompt
    assert "周末长距离" in prompt
    assert "100-120km" in prompt
    assert "福州马拉松 2:45 目标" in prompt
    assert "东营 B 赛测试" in prompt
    assert "不得编造数据" in prompt
    assert '"total_distance_km": 100' in prompt
    assert "训练判断" in prompt
    assert "风险提醒" in prompt
    assert "下一步建议" in prompt
    assert "禁止事项" in prompt


def test_prompt_builder_handles_missing_structured_data() -> None:
    request = CoachRequest(
        scope="daily",
        title="缺失数据",
        structured_data={},
        athlete_background="背景",
        training_context={},
    )

    prompt = build_coach_prompt(request)

    assert "结构化数据缺失，无法判断" in prompt


def test_coach_command_reports_missing_api_key(tmp_path: Path, monkeypatch) -> None:
    config = tmp_path / "athlete.yaml"
    config.write_text("storage:\n  database_path: data/test.sqlite\n", encoding="utf-8")
    monkeypatch.setenv(COACH_API_KEY_ENV, "")

    result = runner.invoke(app, ["coach", "weekly", "--config", str(config)])

    assert result.exit_code == 1
    assert "缺少 GARMIN_RUNNER_COACH_API_KEY" in result.output
    assert "Traceback" not in result.output


def test_run_coach_uses_mock_client_and_writes_artifacts(tmp_path: Path) -> None:
    class FakeClient(CoachClient):
        def complete(self, prompt: str) -> str:
            assert "结构化训练数据" in prompt
            return "## 训练判断\n\n稳定积累\n\n## 风险提醒\n\n无\n\n## 下一步建议\n\n维持\n\n## 禁止事项\n\n不要补课"

    request = CoachRequest(
        scope="monthly",
        title="2026-06 月训练",
        structured_data={"conclusion": "专项推进"},
        athlete_background="背景",
        training_context={"marathon_goal": "福州马拉松 2:45 目标"},
    )

    result = run_coach(request, reports_dir=tmp_path, client=FakeClient())

    assert result.output_path.exists()
    assert result.prompt_path.exists()
    assert result.data_path.exists()
    assert "训练判断" in result.output_path.read_text(encoding="utf-8")
