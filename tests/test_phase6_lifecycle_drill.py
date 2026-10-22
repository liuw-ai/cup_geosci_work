from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from job_hub.backups import BackupError
from job_hub.db import Database
from job_hub.pipeline import JobPipeline
from job_hub.reports import local_today
from job_hub.sources import RawPosting
from job_hub.worker import DailyWorker

from conftest import make_settings, source


def _save_verified_job(database: Database, pipeline: JobPipeline, *, suffix: str) -> int:
    posting = RawPosting(
        title=f"地质工程技术岗 {suffix}",
        employer="测试地质调查院",
        source_url=f"https://careers.example.edu.cn/jobs/{suffix}",
        application_url=None,
        text="面向地质工程硕士毕业生的官方招聘。",
        summary="官方招聘测试记录。",
        published_date="2026-10-01",
        deadline_date="2099-12-31",
        location="北京市",
        field_evidence={
            "evidence_scope": "official_html_table_row",
            "岗位": "地质工程技术岗",
            "专业范围": "地质工程",
            "学历要求": "硕士研究生",
        },
    )
    job_id, _ = database.save_job(
        pipeline.normalize_posting(posting, database.get_source("official-test-source"))
    )
    return job_id


def test_phase6_lifecycle_drill_uses_only_allowed_withdrawal_paths(tmp_path) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    database.upsert_source(source())
    pipeline = JobPipeline(settings, database)
    job_id = _save_verified_job(database, pipeline, suffix="lifecycle")

    assert database.withdraw_missing_source_jobs(
        "official-test-source", set(), reason="not seen in this scan"
    ) == 0
    assert database.withdraw_stale_source_jobs(
        "official-test-source", reason="30 hour freshness expired"
    ) == 0
    assert pipeline.expire_stale_source_jobs() == {}
    assert database.find_job(job_id)["status"] == "open"

    with pytest.raises(ValueError, match="allowed lifecycle reason"):
        database.withdraw_official_source_jobs(
            "official-test-source",
            reason="network_error",
            evidence_url="https://careers.example.edu.cn/notices/status",
        )
    assert database.find_job(job_id)["status"] == "open"

    withdrawn = database.withdraw_official_source_jobs(
        "official-test-source",
        reason="official_closed",
        evidence_url="https://careers.example.edu.cn/notices/official-closure",
    )
    assert withdrawn == 1
    closed = database.find_job(job_id)
    assert closed is not None
    assert closed["status"] == "withdrawn"
    assert closed["withdrawal_reason"] == "official_closed"


def test_phase6_lifecycle_drill_expires_only_after_explicit_deadline(tmp_path) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    database.upsert_source(source())
    pipeline = JobPipeline(settings, database)
    job_id = _save_verified_job(database, pipeline, suffix="deadline")
    today = local_today(settings)

    with database.transaction() as connection:
        connection.execute(
            "UPDATE jobs SET deadline_date = ? WHERE id = ?",
            ((today - timedelta(days=1)).isoformat(), job_id),
        )
    assert database.expire_jobs_before(today.isoformat()) == 1
    expired = database.find_job(job_id)
    assert expired is not None
    assert expired["status"] == "expired"


def test_phase6_worker_pauses_without_explicit_automation_enablement(tmp_path, monkeypatch) -> None:
    worker = DailyWorker(replace(make_settings(tmp_path), worker_automation_enabled=False))
    monkeypatch.setattr(
        worker.pipeline,
        "bootstrap_sources",
        lambda: (_ for _ in ()).throw(AssertionError("paused worker must not bootstrap")),
    )
    monkeypatch.setattr(
        worker,
        "_sync_with_alert",
        lambda: (_ for _ in ()).throw(AssertionError("paused worker must not sync")),
    )
    monkeypatch.setattr(
        worker,
        "_run_domestic_entry_health",
        lambda: (_ for _ in ()).throw(AssertionError("paused worker must not probe")),
    )
    monkeypatch.setattr(
        worker,
        "_run_discovery_scan_with_alert",
        lambda: (_ for _ in ()).throw(AssertionError("paused worker must not scan")),
    )

    def stop_on_wait(_: float) -> bool:
        worker.stop_event.set()
        return True

    monkeypatch.setattr(worker.stop_event, "wait", stop_on_wait)
    worker.run_forever()

    heartbeat = worker.database.get_service_heartbeat("worker")
    assert heartbeat is not None
    assert heartbeat["status"] == "stopped"


def test_phase6_worker_backup_failure_blocks_sync_in_isolated_runtime(tmp_path, monkeypatch) -> None:
    worker = DailyWorker(replace(make_settings(tmp_path), worker_automation_enabled=True))
    monkeypatch.setattr(
        worker.backups,
        "backup_if_due",
        lambda: (_ for _ in ()).throw(BackupError("test backup failure")),
    )
    monkeypatch.setattr(
        worker.pipeline,
        "sync_all",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("sync must not run")),
    )
    worker._sync_with_alert()

    heartbeat = worker.database.get_service_heartbeat("worker")
    assert heartbeat is not None
    assert heartbeat["status"] == "degraded"
    assert "backup failed" in heartbeat["detail"]
