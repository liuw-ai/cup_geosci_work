from __future__ import annotations

import json
from dataclasses import replace
from datetime import date

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
        "province": "测试省",
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


def test_reindex_keeps_future_government_window_private(tmp_path) -> None:
    record = {
        "id": "future-geology-row",
        "source_id": "official-test-source",
        "position_type": "public_institution",
        "province": "测试省",
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


def test_reindex_keeps_current_attachment_candidate_public(tmp_path) -> None:
    record = {
        "id": "current-geology-row",
        "source_id": "official-test-source",
        "position_type": "public_institution",
        "province": "测试省",
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
