from datetime import datetime, timezone

from job_hub.db import Database
from job_hub.operations import continuous_validation

from conftest import make_settings


def test_continuous_validation_fails_closed_without_daily_artifacts(tmp_path) -> None:
    database = Database(make_settings(tmp_path).database_path)
    database.initialize()
    result = continuous_validation(database)
    assert result["ok"] is False
    assert "不能把进程存活当成每日更新成功" in result["message"]


def test_continuous_validation_accepts_recent_snapshot_and_report(tmp_path) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    database.save_coverage_snapshot(
        "2026-10-02",
        {
            "open_jobs": 1,
            "profile_match_quality": {"cohort_summary": {}},
            "scan_quality": {},
            "job_distribution": {},
        },
    )
    database.save_daily_report(
        "2026-10-02",
        {"stats": {}},
        delivery_status="skipped",
    )
    # SQLite timestamps use current UTC time; the check is intentionally based
    # on those persisted values rather than a fabricated historical date.
    result = continuous_validation(database)
    assert result["ok"] is True
    assert result["daily_report"]["delivery_status"] == "skipped"
