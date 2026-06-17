from garmin_runner.normalize import normalize_activity


def test_normalize_activity_supports_china_summary_dto_shape() -> None:
    record = normalize_activity(
        {
            "activityId": 607025766,
            "activityName": "福州市 跑步",
            "activityTypeDTO": {"typeKey": "running"},
            "summaryDTO": {
                "startTimeLocal": "2026-06-01 06:30:00",
                "startTimeGMT": "2026-05-31 22:30:00",
                "distance": 5000.0,
                "duration": 1500.0,
                "averageHR": 142,
                "maxHR": 170,
                "calories": 300,
            },
        },
        summary_path="data/raw/summary/607025766.json",
        fit_path="data/raw/fit/607025766.fit",
    )

    assert record.activity_id == "607025766"
    assert record.activity_type == "running"
    assert record.start_time_local == "2026-06-01 06:30:00"
    assert record.distance_m == 5000.0
    assert record.duration_s == 1500.0
    assert record.average_hr == 142
