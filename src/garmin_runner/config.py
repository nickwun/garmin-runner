from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


@dataclass(frozen=True)
class GarminSettings:
    email_env: str = "GARMIN_EMAIL"
    password_env: str = "GARMIN_PASSWORD"
    token_store: Path = Path("data/tokens")
    is_cn: bool = False


@dataclass(frozen=True)
class StorageSettings:
    data_dir: Path = Path("data")
    database_path: Path = Path("data/garmin-runner.sqlite")
    summary_dir: Path = Path("data/raw/summary")
    fit_dir: Path = Path("data/raw/fit")
    reports_dir: Path = Path("reports")


@dataclass(frozen=True)
class TrainingSettings:
    recovery_low: int = 120
    recovery_high: int = 135
    easy_low: int = 133
    easy_high: int = 145
    aerobic_high: int = 155
    steady_high: int = 165
    mp_bridge_high: int = 170
    threshold_high: int = 178
    vo2_high: int = 188
    sprint_high: int = 194
    long_run_min_distance_km: float = 18.0
    long_run_min_duration_min: float = 90.0


@dataclass(frozen=True)
class AppSettings:
    garmin: GarminSettings
    storage: StorageSettings
    training: TrainingSettings


def _path(value: str | Path) -> Path:
    return Path(value).expanduser()


def load_settings(config_path: Path = Path("config/athlete.yaml")) -> AppSettings:
    load_dotenv()
    data: dict[str, Any] = {}
    if config_path.exists():
        with config_path.open("r", encoding="utf-8") as file:
            loaded = yaml.safe_load(file) or {}
            if not isinstance(loaded, dict):
                raise ValueError(f"配置文件格式无效: {config_path}")
            data = loaded

    garmin_data = data.get("garmin", {})
    storage_data = data.get("storage", {})
    training_data = data.get("training", {})
    hr_zones_data = training_data.get("heart_rate_zones", {})
    new_zone_keys = {
        "recovery_low",
        "recovery_high",
        "easy_low",
        "easy_high",
        "aerobic_high",
        "mp_bridge_high",
        "vo2_high",
        "sprint_high",
    }
    uses_new_zone_schema = any(key in hr_zones_data for key in new_zone_keys)

    return AppSettings(
        garmin=GarminSettings(
            email_env=str(garmin_data.get("email_env", "GARMIN_EMAIL")),
            password_env=str(garmin_data.get("password_env", "GARMIN_PASSWORD")),
            token_store=_path(
                os.getenv("GARMIN_TOKEN_STORE")
                or garmin_data.get("token_store", "data/tokens")
            ),
            is_cn=_bool(garmin_data.get("is_cn", False)),
        ),
        storage=StorageSettings(
            data_dir=_path(storage_data.get("data_dir", "data")),
            database_path=_path(
                os.getenv("GARMIN_RUNNER_DB")
                or storage_data.get("database_path", "data/garmin-runner.sqlite")
            ),
            summary_dir=_path(storage_data.get("summary_dir", "data/raw/summary")),
            fit_dir=_path(storage_data.get("fit_dir", "data/raw/fit")),
            reports_dir=_path(storage_data.get("reports_dir", "reports")),
        ),
        training=TrainingSettings(
            recovery_low=_int_with_default(
                hr_zones_data.get("recovery_low", hr_zones_data.get("maf_low")),
                120,
            ),
            recovery_high=_int_with_default(hr_zones_data.get("recovery_high"), 135),
            easy_low=_int_with_default(hr_zones_data.get("easy_low"), 133),
            easy_high=_int_with_default(
                hr_zones_data.get("easy_high", hr_zones_data.get("maf_high")),
                145,
            ),
            aerobic_high=_int_with_default(
                hr_zones_data.get("aerobic_high", hr_zones_data.get("steady_high")),
                155,
            ),
            steady_high=_int_with_default(
                hr_zones_data.get("steady_high") if uses_new_zone_schema else None,
                165,
            ),
            mp_bridge_high=_int_with_default(hr_zones_data.get("mp_bridge_high"), 170),
            threshold_high=_int_with_default(hr_zones_data.get("threshold_high"), 178),
            vo2_high=_int_with_default(hr_zones_data.get("vo2_high"), 188),
            sprint_high=_int_with_default(hr_zones_data.get("sprint_high"), 194),
            long_run_min_distance_km=float(
                training_data.get("long_run_min_distance_km", 18.0)
            ),
            long_run_min_duration_min=float(
                training_data.get("long_run_min_duration_min", 90.0)
            ),
        ),
    )


def get_credentials(settings: GarminSettings) -> tuple[str | None, str | None]:
    return os.getenv(settings.email_env), os.getenv(settings.password_env)


def _int_with_default(value: Any, default: int) -> int:
    if value is None:
        return default
    return int(value)


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)
