from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

from conftest import make_settings, source, write_registry
from job_hub.db import Database
from job_hub.government_positions import (
    current_publishable_position_records,
    position_record_to_posting,
)
from job_hub.pipeline import JobPipeline
from job_hub.worker import DailyWorker


def test_current_publishable_rows_require_open_explicit_match() -> None:
    registry = {
        "records": [
            {
                "id": "open",
                "record_status": "verified_open",
                "match_status": "explicit_match",
                "deadline_date": "2026-10-01",
                "deadline_policy": "fixed_date",
            },
            {
                "id": "review",
                "record_status": "verified_open",
                "match_status": "needs_review",
                "deadline_date": "2026-10-01",
            },
            {
                "id": "closed",
                "record_status": "verified_closed",
                "match_status": "explicit_match",
                "deadline_date": "2026-10-01",
            },
        ]
    }

    assert [item["id"] for item in current_publishable_position_records(registry, today="2026-09-26")] == ["open"]


def test_stale_review_snapshot_is_not_publishable() -> None:
    registry = {
        "as_of": "2026-09-25",
        "records": [
            {
                "id": "open",
                "record_status": "verified_open",
                "match_status": "explicit_match",
                "deadline_date": "2026-10-01",
                "deadline_policy": "fixed_date",
            }
        ],
    }

    assert current_publishable_position_records(
        registry,
        today="2026-09-28",
        max_age_hours=48,
    ) == []


def test_future_official_application_window_is_not_published_early() -> None:
    registry = {
        "as_of": "2026-09-28",
        "source_opening_dates": {"official-test-source": "2026-10-10"},
        "records": [
            {
                "id": "scheduled",
                "source_id": "official-test-source",
                "record_status": "verified_open",
                "match_status": "explicit_match",
                "deadline_date": "2026-10-26",
                "deadline_policy": "fixed_date",
            }
        ],
    }

    assert current_publishable_position_records(registry, today="2026-09-28") == []
    assert [item["id"] for item in current_publishable_position_records(registry, today="2026-10-10")] == [
        "scheduled"
    ]


def test_position_record_preserves_notice_attachment_and_row_evidence() -> None:
    record = {
        "id": "anhui-2026-2026113",
        "position_type": "public_institution",
        "province": "安徽",
        "position_code": "2026113",
        "title": "专业技术岗位（地质资源与地质工程等）",
        "employer": "安徽工业经济职业技术学院",
        "major_requirement": "地质学、地质资源与地质工程",
        "degree_requirement": "博士研究生",
        "location": "合肥市",
        "headcount": 1,
        "deadline_date": "2026-10-31",
        "deadline_policy": "fixed_date",
        "official_notice_url": "https://example.gov.cn/notice.html",
        "official_attachment_url": "https://example.gov.cn/table.xls",
        "evidence_locator": "岗位表!17",
    }

    posting = position_record_to_posting(record)
    assert posting.source_url == record["official_notice_url"]
    assert posting.official_evidence_url == record["official_attachment_url"]
    assert posting.field_evidence["表格定位"] == "岗位表!17"
    assert posting.field_evidence["官方职位表省份"] == "安徽"
    assert posting.field_evidence["evidence_scope"] == "official_attachment_row"
    assert posting.field_evidence["岗位"] == record["title"]
    assert "地质资源与地质工程" in (posting.qualification_text or "")


def test_government_equivalence_ignores_transient_normalized_rows() -> None:
    record = {
        "source_id": "hunan-geology-institute",
        "employer": "湖南省地质调查所",
        "position_code": "A02",
        "major_requirement": "地质学",
        "deadline_date": "",
    }
    transient = {
        "source_id": record["source_id"],
        "employer": record["employer"],
        "deadline_date": "",
        "external_id": "government-position:hunan-2026-a02:A02",
        "field_evidence": {"专业范围": "地质学", "职位代码": "A02"},
    }

    assert DailyWorker._find_equivalent_government_job(
        [transient], record, "https://example.gov.cn/table.xlsx"
    ) is None


def test_government_sync_keeps_equivalent_attachment_candidate_current(tmp_path) -> None:
    record = {
        "id": "current-geology-row",
        "source_id": "official-test-source",
        "position_type": "public_institution",
        "province": "河南",
        "position_code": "A-001",
        "title": "地质工程技术岗",
        "employer": "测试地质调查院",
        "major_requirement": "地质工程",
        "degree_requirement": "硕士研究生",
        "location": "测试市",
        "headcount": 1,
        "deadline_date": "2099-12-31",
        "deadline_policy": "fixed_date",
        "official_notice_url": "https://example.gov.cn/notice.html",
        "official_attachment_url": "https://example.gov.cn/table.xlsx",
        "evidence_locator": "岗位表!2",
        "record_status": "verified_open",
        "match_status": "explicit_match",
    }
    registry_path = tmp_path / "government-positions.json"
    registry_path.write_text(
        json.dumps({"version": 1, "as_of": "2026-09-28", "records": [record]}),
        encoding="utf-8",
    )
    settings = replace(
        make_settings(tmp_path),
        government_position_registry_path=registry_path,
        government_position_max_age_hours=None,
    )
    write_registry(settings.source_registry_path, [source()])
    worker = DailyWorker(settings)
    worker.pipeline.bootstrap_sources()
    official_source = worker.database.get_source("official-test-source")
    assert official_source is not None

    legacy = replace(
        position_record_to_posting(record), external_id="artifact-candidate-legacy-row"
    )
    worker.database.save_job(worker.pipeline.normalize_posting(legacy, official_source))

    result = worker._sync_verified_government_positions()
    jobs, _ = worker.database.list_jobs(
        page_size=None, only_open=False, student_visible=False
    )

    assert result["withdrawn"] == 0
    assert len(jobs) == 1
    assert jobs[0]["status"] == "open"
    assert jobs[0]["external_id"] == "artifact-candidate-legacy-row"


def test_government_sync_marks_missing_rows_for_review_without_withdrawing(tmp_path) -> None:
    record = {
        "id": "current-geology-row",
        "source_id": "official-test-source",
        "position_type": "public_institution",
        "province": "河南",
        "position_code": "A-001",
        "title": "地质工程技术岗",
        "employer": "测试地质调查院",
        "major_requirement": "地质工程",
        "degree_requirement": "硕士研究生",
        "location": "测试市",
        "headcount": 1,
        "deadline_date": "2099-12-31",
        "deadline_policy": "fixed_date",
        "official_notice_url": "https://example.gov.cn/notice.html",
        "official_attachment_url": "https://example.gov.cn/table.xlsx",
        "evidence_locator": "岗位表!2",
        "record_status": "verified_open",
        "match_status": "explicit_match",
    }
    registry_path = tmp_path / "government-positions.json"
    registry_path.write_text(
        json.dumps({"version": 1, "as_of": "2026-09-28", "records": [record]}),
        encoding="utf-8",
    )
    settings = replace(
        make_settings(tmp_path),
        government_position_registry_path=registry_path,
        government_position_max_age_hours=None,
    )
    write_registry(settings.source_registry_path, [source()])
    worker = DailyWorker(settings)
    worker.pipeline.bootstrap_sources()
    official_source = worker.database.get_source("official-test-source")
    assert official_source is not None

    missing = replace(
        position_record_to_posting(
            {
                **record,
                "position_code": "A-OLD",
                "title": "历史地质岗位",
                "employer": "旧台账单位",
            }
        ),
        external_id="government-position:old-row",
    )
    missing_id, _ = worker.database.save_job(
        worker.pipeline.normalize_posting(missing, official_source)
    )

    result = worker._sync_verified_government_positions()

    assert result["withdrawn"] == 0
    assert result["review_pending"] == 1
    retained = worker.database.find_job(missing_id, student_visible=False)
    assert retained is not None
    assert retained["status"] == "open"


def test_disabled_government_source_cannot_be_resurrected_by_reindex(tmp_path) -> None:
    record = {
        "id": "disabled-source-row",
        "source_id": "official-test-source",
        "position_type": "public_institution",
        "province": "河南",
        "position_code": "A-001",
        "title": "地质工程技术岗",
        "employer": "测试地质调查院",
        "major_requirement": "地质工程",
        "degree_requirement": "硕士研究生",
        "location": "测试市",
        "headcount": 1,
        "deadline_date": "2099-12-31",
        "deadline_policy": "fixed_date",
        "official_notice_url": "https://example.gov.cn/notice.html",
        "official_attachment_url": "https://example.gov.cn/table.xlsx",
        "evidence_locator": "岗位表!2",
        "record_status": "verified_open",
        "match_status": "explicit_match",
    }
    registry_path = tmp_path / "government-positions.json"
    registry_path.write_text(
        json.dumps({"version": 1, "as_of": date.today().isoformat(), "records": [record]}),
        encoding="utf-8",
    )
    settings = replace(
        make_settings(tmp_path),
        government_position_registry_path=registry_path,
        government_position_max_age_hours=None,
    )
    write_registry(settings.source_registry_path, [source()])
    database = Database(settings.database_path)
    database.initialize()
    database.upsert_source(source())
    database.set_source_enabled("official-test-source", False)
    pipeline = JobPipeline(settings, database)
    posting = position_record_to_posting(record)
    job_id, _ = database.save_job(
        pipeline.normalize_posting(posting, database.get_source("official-test-source"))
    )

    pipeline.reindex_jobs()

    job = database.find_job(job_id)
    assert job is not None
    assert job["publication_status"] == "pending_evidence"
    assert "来源当前已停用" in job["publication_basis"]["reason"]


def test_disabled_government_source_is_withdrawn_during_worker_sync(tmp_path) -> None:
    record = {
        "id": "disabled-source-row",
        "source_id": "official-test-source",
        "position_type": "public_institution",
        "province": "河南",
        "position_code": "A-001",
        "title": "地质工程技术岗",
        "employer": "测试地质调查院",
        "major_requirement": "地质工程",
        "degree_requirement": "硕士研究生",
        "location": "测试市",
        "headcount": 1,
        "deadline_date": "2099-12-31",
        "deadline_policy": "fixed_date",
        "official_notice_url": "https://example.gov.cn/notice.html",
        "official_attachment_url": "https://example.gov.cn/table.xlsx",
        "evidence_locator": "岗位表!2",
        "record_status": "verified_open",
        "match_status": "explicit_match",
    }
    registry_path = tmp_path / "government-positions.json"
    registry_path.write_text(
        json.dumps({"version": 1, "as_of": date.today().isoformat(), "records": [record]}),
        encoding="utf-8",
    )
    settings = replace(
        make_settings(tmp_path),
        government_position_registry_path=registry_path,
        government_position_max_age_hours=None,
    )
    write_registry(settings.source_registry_path, [source()])
    worker = DailyWorker(settings)
    worker.pipeline.bootstrap_sources()
    worker.database.set_source_enabled("official-test-source", False)
    official_source = worker.database.get_source("official-test-source")
    assert official_source is not None
    posting = position_record_to_posting(record)
    job_id, _ = worker.database.save_job(
        worker.pipeline.normalize_posting(posting, official_source)
    )

    result = worker._sync_verified_government_positions()

    job = worker.database.find_job(job_id)
    assert result["withdrawn"] == 0
    assert job is not None
    assert job["status"] == "open"
    assert job["publication_layer"] == "source_unavailable"


def test_reindex_keeps_future_government_window_private(tmp_path) -> None:
    record = {
        "id": "future-geology-row",
        "source_id": "official-test-source",
        "position_type": "public_institution",
        "province": "河南",
        "position_code": "A-001",
        "title": "地质工程技术岗",
        "employer": "测试地质调查院",
        "major_requirement": "地质工程",
        "degree_requirement": "硕士研究生",
        "location": "测试市",
        "headcount": 1,
        "deadline_date": "2099-12-31",
        "deadline_policy": "fixed_date",
        "official_notice_url": "https://example.gov.cn/notice.html",
        "official_attachment_url": "https://example.gov.cn/table.xlsx",
        "evidence_locator": "岗位表!2",
        "record_status": "verified_open",
        "match_status": "explicit_match",
    }
    registry_path = tmp_path / "government-positions.json"
    registry_path.write_text(
        json.dumps(
            {
                "version": 1,
                "as_of": date.today().isoformat(),
                "source_opening_dates": {"official-test-source": "2099-01-01"},
                "source_opening_at": {
                    "official-test-source": "2099-01-01T08:00:00+08:00"
                },
                "records": [record],
            }
        ),
        encoding="utf-8",
    )
    settings = replace(
        make_settings(tmp_path),
        government_position_registry_path=registry_path,
        government_position_max_age_hours=None,
    )
    write_registry(settings.source_registry_path, [source()])
    database = Database(settings.database_path)
    database.initialize()
    database.upsert_source(source())
    pipeline = JobPipeline(settings, database)
    posting = position_record_to_posting(record)
    job_id, _ = database.save_job(
        pipeline.normalize_posting(posting, database.get_source("official-test-source"))
    )
    assert database.find_job(job_id)["publication_status"] == "student_eligible"

    pipeline.reindex_jobs()

    job = database.find_job(job_id)
    assert job is not None
    assert job["publication_status"] == "pending_evidence"
    assert "尚未开始" in job["publication_basis"]["reason"]
    assert "2099-01-01T08:00:00+08:00" in job["publication_basis"]["reason"]


def test_reindex_keeps_current_attachment_candidate_public(tmp_path) -> None:
    record = {
        "id": "current-geology-row",
        "source_id": "official-test-source",
        "position_type": "public_institution",
        "province": "河南",
        "position_code": "A-001",
        "title": "地质工程技术岗",
        "employer": "测试地质调查院",
        "major_requirement": "地质工程",
        "degree_requirement": "硕士研究生",
        "location": "测试市",
        "headcount": 1,
        "deadline_date": "2099-12-31",
        "deadline_policy": "fixed_date",
        "official_notice_url": "https://example.gov.cn/notice.html",
        "official_attachment_url": "https://example.gov.cn/table.xlsx",
        "evidence_locator": "岗位表!2",
        "record_status": "verified_open",
        "match_status": "explicit_match",
    }
    registry_path = tmp_path / "government-positions.json"
    registry_path.write_text(
        json.dumps(
            {"version": 1, "as_of": date.today().isoformat(), "records": [record]}),
        encoding="utf-8",
    )
    settings = replace(
        make_settings(tmp_path),
        government_position_registry_path=registry_path,
        government_position_max_age_hours=None,
    )
    write_registry(settings.source_registry_path, [source()])
    database = Database(settings.database_path)
    database.initialize()
    database.upsert_source(source())
    pipeline = JobPipeline(settings, database)
    legacy = replace(
        position_record_to_posting(record), external_id="artifact-candidate:legacy-row"
    )
    job_id, _ = database.save_job(
        pipeline.normalize_posting(legacy, database.get_source("official-test-source"))
    )

    pipeline.reindex_jobs()

    job = database.find_job(job_id)
    assert job is not None
    assert job["publication_status"] == "student_eligible"
    assert job["category"] == "事业单位与人才引进"


def test_activated_government_ledger_rows_survive_next_full_worker_cycle(
    tmp_path, monkeypatch
) -> None:
    """A guarded manual source is published by its ledger, never by an empty scan."""
    record = {
        "id": "automatic-ledger-row",
        "source_id": "official-test-source",
        "position_type": "public_institution",
        "province": "北京",
        "employer": "测试地质调查院",
        "position_code": "A-001",
        "title": "地质工程技术岗",
        "major_requirement": "地质工程",
        "degree_requirement": "硕士研究生",
        "location": "北京市",
        "headcount": 1,
        "deadline_date": "2099-12-31",
        "deadline_policy": "fixed_date",
        "official_notice_url": "https://example.gov.cn/notice.html",
        "official_attachment_url": "https://example.gov.cn/table.xlsx",
        "evidence_locator": "岗位表!2",
        "record_status": "verified_open",
        "match_status": "explicit_match",
    }
    registry_path = tmp_path / "government-positions.json"
    registry_path.write_text(
        json.dumps(
            {
                "version": 1,
                "as_of": date.today().isoformat(),
                "source_opening_at": {
                    "official-test-source": (
                        datetime.now(timezone.utc) - timedelta(days=1)
                    ).isoformat()
                },
                "records": [record],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    settings = replace(
        make_settings(tmp_path),
        government_position_registry_path=registry_path,
        government_position_max_age_hours=48,
    )
    worker = DailyWorker(settings)
    source_record = source()
    source_record["enabled"] = False
    source_record["config"] = {
        "government_evidence_recheck": True,
        "government_evidence_recheck_mode": "automatic_after_opening",
        "runtime_enabled_control": True,
        "activation_required_successes": 2,
        "expected_attachment_sha256": "a" * 64,
    }
    worker.database.upsert_source(source_record)
    first_checked_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    second_checked_at = datetime.now(timezone.utc)
    worker.database.record_government_source_verification(
        "official-test-source",
        status="verified",
        checked_at=first_checked_at.isoformat().replace("+00:00", "Z"),
        detail="first complete official revalidation",
    )
    monkeypatch.setattr(
        "job_hub.worker.revalidate_government_sources",
        lambda *_args, **_kwargs: [
            {
                "source_id": "official-test-source",
                "status": "verified",
                "checked_at": second_checked_at.isoformat().replace("+00:00", "Z"),
                "detail": "second complete official revalidation",
                "evidence_fingerprint": "fixture-fingerprint",
            }
        ],
    )

    activated = worker._revalidate_government_position_sources()
    first_publication = worker._sync_verified_government_positions()

    assert activated["activated"] == 1
    assert first_publication["created"] == 1
    assert worker.database.count_jobs_for_source("official-test-source") == 1
    before = worker.database.find_public_job(1)
    assert before is not None
    assert before["external_id"] == "government-position:automatic-ledger-row:A-001"

    worker._sync_with_alert()

    jobs, _ = worker.database.list_jobs(
        page_size=None, only_open=False, student_visible=False
    )
    source_jobs = [job for job in jobs if job["source_id"] == "official-test-source"]
    assert len(source_jobs) == 1
    assert source_jobs[0]["status"] == "open"
    assert source_jobs[0]["publication_status"] == "student_eligible"
    assert not [
        run
        for run in worker.database.list_latest_crawl_runs()
        if run["source_id"] == "official-test-source"
    ]
