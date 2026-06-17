from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Any

from garmin_runner.analysis.single_activity import TimeSeriesPoint


def extract_fit_bytes(downloaded: bytes) -> bytes:
    """Return the FIT payload from Garmin's original activity download."""
    if not zipfile.is_zipfile(io.BytesIO(downloaded)):
        return downloaded

    with zipfile.ZipFile(io.BytesIO(downloaded)) as archive:
        fit_names = [name for name in archive.namelist() if name.lower().endswith(".fit")]
        if not fit_names:
            raise ValueError("Garmin 原始活动下载中没有找到 FIT 文件")
        with archive.open(fit_names[0]) as fit_file:
            return fit_file.read()


def decode_fit_messages(fit_path: Path) -> tuple[dict[str, list[dict[str, Any]]], list[Any]]:
    from garmin_fit_sdk import Decoder, Stream

    stream = Stream.from_file(str(fit_path))
    decoder = Decoder(stream)
    return decoder.read()


def extract_time_series(messages: dict[str, list[dict[str, Any]]]) -> list[TimeSeriesPoint]:
    records = messages.get("record_mesgs") or messages.get("record") or []
    points: list[TimeSeriesPoint] = []
    first_timestamp = None

    for record in records:
        timestamp = record.get("timestamp")
        if timestamp is None:
            continue
        if first_timestamp is None:
            first_timestamp = timestamp
        elapsed_s = _elapsed_seconds(record, timestamp, first_timestamp)
        distance_m = _number(record.get("distance"))
        speed_mps = _number(
            record.get("enhanced_speed")
            or record.get("speed")
            or record.get("speed_1s")
        )
        cadence = _number(
            record.get("cadence")
            or record.get("running_cadence")
            or record.get("step_rate")
        )
        if cadence is not None and cadence < 120:
            cadence *= 2
        points.append(
            TimeSeriesPoint(
                timestamp=timestamp,
                elapsed_s=elapsed_s,
                distance_m=distance_m,
                heart_rate_bpm=_number(record.get("heart_rate")),
                speed_mps=speed_mps,
                cadence_spm=cadence,
                altitude_m=_number(record.get("enhanced_altitude") or record.get("altitude")),
            )
        )

    return points


def record_messages(messages: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    records = messages.get("record_mesgs") or messages.get("record") or []
    return records if isinstance(records, list) else []


def _elapsed_seconds(record: dict[str, Any], timestamp: Any, first_timestamp: Any) -> float:
    timer_time = _number(record.get("timer_time") or record.get("elapsed_time"))
    if timer_time is not None:
        return timer_time
    try:
        return float((timestamp - first_timestamp).total_seconds())
    except AttributeError:
        return 0.0


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
