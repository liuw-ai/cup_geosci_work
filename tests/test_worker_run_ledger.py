from __future__ import annotations

from job_hub.db import Database
from job_hub.worker import DailyWorker

from conftest import make_settings


def test_worker_run_ledger_is_idempotent_and_records_failure(tmp_path):
    database = Database(tmp_path / "runs.sqlite3")
    database.initialize()
    assert database.claim_worker_run("daily-cycle:2026-10-23", "daily_cycle") is True
    assert database.claim_worker_run("daily-cycle:2026-10-23", "daily_cycle") is False
    database.finish_worker_run("daily-cycle:2026-10-23", "failed", "backup unavailable")
    assert database.claim_worker_run("daily-cycle:2026-10-23", "daily_cycle") is True
    database.finish_worker_run("daily-cycle:2026-10-23", "succeeded", "recovered")
    assert database.claim_worker_run("daily-cycle:2026-10-23", "daily_cycle") is False
    row = database.recent_worker_runs(run_kind="daily_cycle", limit=1)[0]
    assert row["status"] == "succeeded"
    assert row["detail"] == "recovered"


def test_daily_publish_blocks_when_required_sync_fails(tmp_path, monkeypatch):
    worker = DailyWorker(make_settings(tmp_path))
    monkeypatch.setattr(worker, "_sync_with_alert", lambda: False)
    monkeypatch.setattr(
        "job_hub.worker.publish_daily_report",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not publish")),
    )

    worker._publish_with_alert("2026-10-23")

    row = worker.database.recent_worker_runs(run_kind="daily_cycle", limit=1)[0]
    assert row["status"] == "failed"
    assert "阻断日报发布" in row["detail"]
