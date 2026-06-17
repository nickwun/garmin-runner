from __future__ import annotations

from pathlib import Path

from garmin_runner.config import load_settings


def test_load_settings_maps_legacy_heart_rate_zones(tmp_path: Path) -> None:
    config_path = tmp_path / "athlete.yaml"
    config_path.write_text(
        """training:
  heart_rate_zones:
    maf_low: 120
    maf_high: 145
    steady_high: 155
    threshold_high: 170
""",
        encoding="utf-8",
    )

    settings = load_settings(config_path)

    assert settings.training.recovery_low == 120
    assert settings.training.easy_high == 145
    assert settings.training.aerobic_high == 155
    assert settings.training.steady_high == 165
    assert settings.training.threshold_high == 170
