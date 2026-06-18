from __future__ import annotations

from pathlib import Path

from garmin_runner.cli import _absolute_display_path


def test_report_quick_link_uses_absolute_path(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    path = _absolute_display_path(Path("reports/daily/example.md"))

    assert path == tmp_path / "reports" / "daily" / "example.md"
