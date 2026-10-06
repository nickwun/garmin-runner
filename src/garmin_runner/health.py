from __future__ import annotations

import logging
import math
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

from garmin_runner.health_storage import HealthStore


SOURCES = {
    "hrv": ("HRV", "get_hrv_data"),
    "sleep": ("睡眠", "get_sleep_data"),
    "rhr": ("静息心率", "get_rhr_day"),
    "stress": ("压力", "get_stress_data"),
    "battery": ("身体电量", "get_body_battery"),
    "readiness": ("训练准备度（可选）", "get_training_readiness"),
}


class HealthSchemaError(ValueError):
    pass


@contextmanager
def quiet_garmin():
    previous = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        yield
    finally:
        logging.disable(previous)


def numeric(value: Any, low: float = 0, high: float = float("inf")) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and low <= number <= high else None


def timestamp(value: Any) -> str | None:
    try:
        if isinstance(value, (int, float)):
            result = datetime.fromtimestamp(value / 1000 if value > 1e11 else value, timezone.utc)
        else:
            result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if result.tzinfo is None:
                result = result.replace(tzinfo=timezone.utc)
        return result.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def _check_day(obj: dict, day: str, key: str = "calendarDate") -> None:
    if obj.get(key) and str(obj[key])[:10] != day:
        raise HealthSchemaError("返回日期与请求日期不一致")


def normalize_health(kind: str, raw: Any, day: str, tz: str) -> dict:
    if raw is None or raw == {} or raw == []:
        return {}
    result: dict[str, Any] = {}
    if kind in {"hrv", "sleep", "rhr", "stress"} and not isinstance(raw, dict):
        raise HealthSchemaError("返回结构变化")
    if kind == "hrv":
        if "hrvSummary" not in raw:
            raise HealthSchemaError("缺少 HRV summary")
        obj = raw.get("hrvSummary") or {}
        _check_day(obj, day)
        baseline = obj.get("baseline") or {}
        result = {
            "last_night_ms": numeric(obj.get("lastNightAvg"), 1),
            "weekly_ms": numeric(obj.get("weeklyAvg"), 1),
            "baseline_low_ms": numeric(baseline.get("balancedLow"), 1),
            "baseline_high_ms": numeric(baseline.get("balancedUpper"), 1),
            "status": obj.get("status") if obj.get("status") in {"BALANCED", "UNBALANCED", "LOW", "POOR"} else None,
            "observed_at": timestamp(raw.get("sleepEndTimestampGMT") or raw.get("endTimestampGMT")),
        }
        low, high = result["baseline_low_ms"], result["baseline_high_ms"]
        if low is not None and high is not None and low > high:
            raise HealthSchemaError("HRV 基线上下界无效")
        if result["last_night_ms"] is None and result["weekly_ms"] is None:
            return {}
    elif kind == "sleep":
        if "dailySleepDTO" not in raw:
            raise HealthSchemaError("缺少 sleep summary")
        obj = raw.get("dailySleepDTO") or {}
        _check_day(obj, day)
        wake = timestamp(obj.get("sleepEndTimestampGMT"))
        if wake and datetime.fromisoformat(wake).astimezone(ZoneInfo(tz)).date().isoformat() != day:
            raise HealthSchemaError("睡眠醒来日期与请求日期不一致")
        result = {"duration_s": numeric(obj.get("sleepTimeSeconds"), 1, 86400),
                  "score": numeric((obj.get("sleepScores") or {}).get("overall", {}).get("value"), 0, 100),
                  "observed_at": wake}
        if result["duration_s"] is None:
            return {}
    elif kind == "rhr":
        if "allMetrics" not in raw:
            raise HealthSchemaError("缺少静息心率 metrics")
        rows = (raw.get("allMetrics") or {}).get("metricsMap", {}).get("WELLNESS_RESTING_HEART_RATE", [])
        values = []
        for obj in rows:
            _check_day(obj, day)
            value = numeric(obj.get("value"), 20, 250)
            if value is not None:
                values.append(value)
        result = {"bpm": values[-1] if values else None}
    elif kind == "stress":
        _check_day(raw, day)
        if "avgStressLevel" not in raw:
            raise HealthSchemaError("缺少压力字段")
        result = {"average": numeric(raw.get("avgStressLevel"), 0, 100)}
    elif kind == "battery":
        if not isinstance(raw, list):
            raise HealthSchemaError("身体电量应为列表")
        samples = []
        for obj in raw:
            _check_day(obj, day, "date")
            if "bodyBatteryValuesArray" not in obj:
                raise HealthSchemaError("缺少身体电量序列")
            descriptors = obj.get("bodyBatteryValueDescriptorDTOList") or []
            indexes = {item.get("bodyBatteryValueDescriptorKey"): item.get("bodyBatteryValueDescriptorIndex")
                       for item in descriptors}
            ti, vi = indexes.get("timestamp", 0), indexes.get("bodyBatteryLevel", 1)
            if not isinstance(ti, int) or not isinstance(vi, int) or min(ti, vi) < 0:
                raise HealthSchemaError("身体电量列描述无效")
            for row in obj.get("bodyBatteryValuesArray") or []:
                if not isinstance(row, list) or len(row) <= max(ti, vi):
                    continue
                at, value = timestamp(row[ti]), numeric(row[vi], 0, 100)
                if at and value is not None:
                    samples.append({"at": at, "value": value})
        result = {"samples": samples} if samples else {}
    elif kind == "readiness":
        if not isinstance(raw, list):
            raise HealthSchemaError("准备度应为列表")
        samples = []
        for obj in raw:
            _check_day(obj, day)
            at, value = timestamp(obj.get("timestamp")), numeric(obj.get("score"), 0, 100)
            if at and value is not None:
                samples.append({"at": at, "value": value})
        result = {"samples": samples} if samples else {}
    else:
        raise ValueError("未知健康数据类型")
    return result if any(value is not None for value in result.values()) else {}


def safe_error(exc: Exception) -> tuple[str, str]:
    """Only allowlisted classifications; exception text can contain credentials."""
    from garminconnect import GarminConnectAuthenticationError, GarminConnectTooManyRequestsError
    code = getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(exc, GarminConnectTooManyRequestsError) or code == 429:
        return "rate_limited", "请求限流；已停止，请稍后重试"
    if isinstance(exc, GarminConnectAuthenticationError) or code == 401:
        return "auth_error", "登录失效或需要 MFA；请重新登录"
    if code == 403:
        return "error", "访问被拒绝（403），不等同于设备不支持"
    if code == 404:
        return "error", "请求未找到（404），可能是接口或数据变化"
    return "error", "请求失败；请检查网络、权限或 Garmin 服务状态"


@dataclass
class HealthResult:
    day: str
    kind: str
    status: str
    detail: str


def sync_health(api: Any, store: HealthStore, since: date, until: date, tz: str,
                refresh: bool = False, delay: float = 0.1,
                progress: Callable[[HealthResult], None] | None = None) -> list[HealthResult]:
    store.initialize()
    old = {(row["day"], row["kind"]): row for row in store.statuses(since.isoformat(), until.isoformat())}
    results = []
    now = datetime.now(timezone.utc)
    today = now.astimezone(ZoneInfo(tz)).date()
    for offset in range((until - since).days + 1):
        day = (since + timedelta(days=offset)).isoformat()
        for kind, (_, method_name) in SOURCES.items():
            previous = old.get((day, kind))
            raw, metrics = None, None
            # Recent days may still be amended by Garmin; never freeze missing days forever.
            cached = (not refresh and previous and previous["status"] == "available"
                      and date.fromisoformat(day) < today - timedelta(days=7))
            if cached:
                status, detail = "cached", "保留已有成功数据"
            elif not callable(getattr(api, method_name, None)):
                status, detail = "unsupported", "当前库缺少方法，未判断设备支持情况"
            else:
                try:
                    with quiet_garmin():
                        raw = getattr(api, method_name)(day)
                    metrics = normalize_health(kind, raw, day, tz)
                    status, detail = ("available", "已获取有效字段") if metrics else ("no_data", "暂无有效数据")
                except (HealthSchemaError, KeyError, TypeError, AttributeError, ValueError):
                    status, detail = "schema_error", "字段、日期或结构变化，未纳入分析"
                except Exception as exc:
                    status, detail = safe_error(exc)
                if delay:
                    time.sleep(delay)
            stamp = datetime.now(timezone.utc).isoformat()
            if status != "cached":
                store.save(day, kind, status, detail, stamp, raw, metrics)
            item = HealthResult(day, kind, status, detail)
            results.append(item)
            if progress:
                progress(item)
            if status in {"auth_error", "rate_limited"}:
                return results
    return results
