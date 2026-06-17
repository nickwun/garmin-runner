from pathlib import Path

from garmin_runner.storage import ActivityRecord, ActivityStore


def test_activity_store_inserts_activity(tmp_path: Path) -> None:
    db_path = tmp_path / "garmin-runner.sqlite"
    store = ActivityStore(db_path)
    store.initialize()

    created = store.upsert_activity(
        ActivityRecord(
            activity_id="12345",
            activity_type="running",
            start_time_local="2026-06-01T06:30:00",
            activity_name="Morning Run",
            distance_m=5000.0,
            duration_s=1800.0,
            summary_path="raw/summary/12345.json",
            fit_path="raw/fit/12345.fit",
        )
    )

    assert created is True
    row = store.get_activity("12345")
    assert row is not None
    assert row["activity_id"] == "12345"
    assert row["activity_type"] == "running"
    assert row["distance_m"] == 5000.0
    assert row["summary_path"] == "raw/summary/12345.json"


def test_activity_store_deduplicates_by_activity_id(tmp_path: Path) -> None:
    db_path = tmp_path / "garmin-runner.sqlite"
    store = ActivityStore(db_path)
    store.initialize()

    first = ActivityRecord(
        activity_id="12345",
        activity_type="running",
        start_time_local="2026-06-01T06:30:00",
        activity_name="Morning Run",
        distance_m=5000.0,
        duration_s=1800.0,
        summary_path="raw/summary/12345.json",
        fit_path="raw/fit/12345.fit",
    )
    duplicate = ActivityRecord(
        activity_id="12345",
        activity_type="running",
        start_time_local="2026-06-01T06:30:00",
        activity_name="Edited Run",
        distance_m=5100.0,
        duration_s=1810.0,
        summary_path="raw/summary/12345-new.json",
        fit_path="raw/fit/12345-new.fit",
    )

    assert store.upsert_activity(first) is True
    assert store.upsert_activity(duplicate) is False

    rows = store.list_activities()
    assert len(rows) == 1
    assert rows[0]["activity_name"] == "Morning Run"
    assert rows[0]["fit_path"] == "raw/fit/12345.fit"


def test_activity_store_refreshes_existing_activity_metadata(tmp_path: Path) -> None:
    db_path = tmp_path / "garmin-runner.sqlite"
    store = ActivityStore(db_path)
    store.initialize()
    store.upsert_activity(
        ActivityRecord(
            activity_id="12345",
            activity_type="running",
            start_time_local=None,
            activity_name="Morning Run",
            distance_m=None,
            duration_s=None,
            summary_path="raw/summary/12345.json",
            fit_path="raw/fit/12345.fit",
        )
    )

    store.refresh_activity(
        ActivityRecord(
            activity_id="12345",
            activity_type="running",
            start_time_local="2026-06-01T06:30:00",
            activity_name="Morning Run",
            distance_m=5000.0,
            duration_s=1800.0,
            summary_path="raw/summary/12345.json",
            fit_path="raw/fit/12345.fit",
        )
    )

    row = store.get_activity("12345")
    assert row is not None
    assert row["start_time_local"] == "2026-06-01T06:30:00"
    assert row["distance_m"] == 5000.0
