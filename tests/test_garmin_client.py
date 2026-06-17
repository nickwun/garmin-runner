from __future__ import annotations

import sys
import types
from pathlib import Path

from garmin_runner.config import load_settings
from garmin_runner.garmin_client import create_garmin_client


def test_load_settings_supports_china_garmin_region(tmp_path: Path) -> None:
    config = tmp_path / "athlete.yaml"
    config.write_text(
        """
garmin:
  is_cn: true
  token_store: "data/tokens-cn"
""",
        encoding="utf-8",
    )

    settings = load_settings(config)

    assert settings.garmin.is_cn is True
    assert settings.garmin.token_store == Path("data/tokens-cn")


def test_create_garmin_client_passes_china_region_to_library(
    tmp_path: Path,
    monkeypatch,
) -> None:
    seen_is_cn: list[bool] = []

    class FakeGarmin:
        def __init__(self, *args, **kwargs):
            seen_is_cn.append(kwargs.get("is_cn", False))

        def login(self, tokenstore):
            return None, None

    fake_module = types.SimpleNamespace(
        Garmin=FakeGarmin,
        GarminConnectAuthenticationError=RuntimeError,
        GarminConnectConnectionError=ConnectionError,
        GarminConnectTooManyRequestsError=RuntimeError,
    )
    monkeypatch.setitem(sys.modules, "garminconnect", fake_module)
    config = tmp_path / "athlete.yaml"
    config.write_text(
        f"""
garmin:
  is_cn: true
  token_store: "{tmp_path / "tokens-cn"}"
""",
        encoding="utf-8",
    )
    settings = load_settings(config)

    create_garmin_client(settings.garmin)

    assert seen_is_cn == [True]
