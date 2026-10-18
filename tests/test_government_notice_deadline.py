from datetime import date, datetime, timedelta, timezone

from job_hub.attachments import notice_deadline_evidence
from job_hub.artifact_publication import auto_publish_verified_artifact_candidates
from job_hub.db import Database
from job_hub.government_artifact_verification import _dynamic_candidate_reasons
from job_hub.government_artifact_verification import verify_government_artifacts
from job_hub.pipeline import JobPipeline

from conftest import make_settings, source


NOW = datetime(2026, 10, 13, 2, 0, tzinfo=timezone.utc)
NOTICE_URL = "https://careers.example.edu.cn/notice.html"
TABLE_URL = "https://careers.example.edu.cn/positions.xlsx"


def _case():
    evidence = notice_deadline_evidence(
        "2026年事业单位公开招聘。报名截止时间：2026年10月18日18:00。详见附件职位表。",
        NOTICE_URL,
        captured_at=NOW.isoformat(),
    )
    assert evidence is not None
    artifact = {
        "parent_url": NOTICE_URL,
        "artifact_url": TABLE_URL,
        "metadata": {"notice_deadline_evidence": evidence},
    }
    candidate = {
        "source_id": "official-test-source",
        "official_page_url": NOTICE_URL,
        "title": "地质工程技术岗",
        "employer": "某省地质调查院",
        "location": "北京市",
        "deadline_date": "2026-10-18",
        "major_tags": ["地质工程"],
        "row_sheet_name": "岗位表",
        "row_number": 2,
        "extraction_confidence": "high",
        "field_evidence": {
            "evidence_scope": "official_attachment_row",
            "artifact_url": TABLE_URL,
            "table_row": "岗位表!2",
            "岗位": "地质工程技术岗",
            "专业范围": "地质工程",
            "学历要求": "硕士研究生",
            "招聘人数": "1",
            "工作地点": "北京市",
        },
        "row_cells": {
            "岗位名称": "地质工程技术岗",
            "招聘单位": "某省地质调查院",
            "专业要求": "地质工程",
            "学历要求": "硕士研究生",
            "工作地点": "北京市",
            "招聘人数": "1",
        },
    }
    source = {"category": "事业单位与人才引进"}
    return candidate, artifact, source


def test_unique_current_official_notice_deadline_supports_code_free_table_row():
    candidate, artifact, source = _case()

    assert _dynamic_candidate_reasons(candidate, artifact, source, date(2026, 10, 13), NOW) == []


def test_notice_deadline_must_be_unqualified_and_unambiguous():
    assert notice_deadline_evidence(
        "第一批报名截止时间：2026年10月18日。第二批报名截止时间：2026年10月25日。",
        NOTICE_URL,
        captured_at=NOW.isoformat(),
    ) is None
    assert notice_deadline_evidence(
        "第一批报名截止时间：2026年10月18日。",
        NOTICE_URL,
        captured_at=NOW.isoformat(),
    ) is None


def test_stale_or_mismatched_notice_deadline_does_not_verify_row():
    candidate, artifact, source = _case()
    artifact["metadata"]["notice_deadline_evidence"]["captured_at"] = (
        NOW - timedelta(hours=49)
    ).isoformat()
    assert "deadline_not_bound_to_current_official_notice_or_row" in _dynamic_candidate_reasons(
        candidate, artifact, source, date(2026, 10, 13), NOW
    )

    artifact["metadata"]["notice_deadline_evidence"]["captured_at"] = NOW.isoformat()
    artifact["metadata"]["notice_deadline_evidence"]["notice_url"] = (
        "https://unrelated.example.cn/notice.html"
    )
    assert "deadline_not_bound_to_current_official_notice_or_row" in _dynamic_candidate_reasons(
        candidate, artifact, source, date(2026, 10, 13), NOW
    )


def test_conflicting_row_deadline_cannot_be_overridden_by_notice():
    candidate, artifact, source = _case()
    candidate["row_cells"]["报名截止"] = "2026年10月16日"

    assert "deadline_row_conflicts_with_candidate" in _dynamic_candidate_reasons(
        candidate, artifact, source, date(2026, 10, 13), NOW
    )


def test_row_locator_is_required_even_when_notice_deadline_is_current():
    candidate, artifact, source = _case()
    candidate["field_evidence"].pop("table_row")

    assert "row_locator_missing" in _dynamic_candidate_reasons(
        candidate, artifact, source, date(2026, 10, 13), NOW
    )


def test_current_notice_and_code_free_official_table_pass_full_publication_gate(tmp_path):
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    registered = source()
    registered["source_type"] = "html_notice"
    registered["category"] = "事业单位与人才引进"
    registered["config"] = {
        "allowed_hosts": ["careers.example.edu.cn"],
        "attachment_discovery_enabled": True,
        "listing_urls": ["https://careers.example.edu.cn/notices"],
        "source_role": "human_resources_or_exam",
    }
    database.upsert_source(registered)
    candidate_data, _, _ = _case()
    artifact = database.upsert_source_artifact(
        {
            "source_id": registered["id"],
            "parent_url": NOTICE_URL,
            "artifact_url": TABLE_URL,
            "artifact_kind": "position_table",
            "extraction_status": "extracted",
            "content_sha256": "a" * 64,
            "storage_path": "sha256/aa/" + "a" * 64 + ".xlsx",
            "parser_version": "attachments-test",
            "metadata": {
                "discovery_only": True,
                "notice_deadline_evidence": notice_deadline_evidence(
                    "报名截止时间：2026年10月18日。",
                    NOTICE_URL,
                    captured_at=NOW.isoformat(),
                ),
            },
        }
    )
    row = database.upsert_source_artifact_rows(
        int(artifact["id"]),
        [{
            "sheet_name": "岗位表",
            "row_number": 2,
            "cells": candidate_data["row_cells"],
            "row_text": "岗位名称：地质工程技术岗；招聘单位：某省地质调查院；专业要求：地质工程；学历要求：硕士研究生；工作地点：北京市；招聘人数：1",
        }],
    )[0]
    candidate = database.upsert_artifact_job_candidate(
        {
            "artifact_row_id": row["id"],
            "source_id": registered["id"],
            "official_page_url": NOTICE_URL,
            "title": candidate_data["title"],
            "employer": candidate_data["employer"],
            "location": candidate_data["location"],
            "deadline_date": candidate_data["deadline_date"],
            "degree_levels": ["硕士"],
            "major_tags": candidate_data["major_tags"],
            "summary": "官方职位表第2行",
            "description": "官方职位表第2行",
            "extraction_confidence": "high",
            "field_evidence": candidate_data["field_evidence"],
            "review_status": "needs_review",
        }
    )

    verification = verify_government_artifacts(
        database, {"artifacts": []}, {"records": []}, now=NOW
    )
    assert verification["publication_ready"] == 1, verification["items"]
    promoted = database.get_artifact_job_candidate(int(candidate["id"]))
    assert promoted["review_status"] == "official_content_verified"
    assert promoted["field_evidence"]["报名截止公告链接"] == NOTICE_URL
    published = auto_publish_verified_artifact_candidates(
        database, JobPipeline(settings, database), verification
    )
    assert published["published"] == 1, published
    assert database.find_public_job(
        published["items"][0]["job_id"], as_of_date=NOW.date().isoformat()
    ) is not None

    job_id = published["items"][0]["job_id"]
    failed_cycle = auto_publish_verified_artifact_candidates(
        database,
        JobPipeline(settings, database),
        {"status": "failed", "items": []},
    )
    assert failed_cycle["withdrawn"] == 0
    assert database.find_public_job(job_id, as_of_date=NOW.date().isoformat()) is not None

    stale = verify_government_artifacts(
        database, {"artifacts": []}, {"records": []}, now=NOW + timedelta(hours=49)
    )
    assert stale["publication_ready"] == 0
    withdrawn = auto_publish_verified_artifact_candidates(
        database, JobPipeline(settings, database), stale
    )
    assert withdrawn["withdrawn"] == 0, withdrawn
    assert database.find_public_job(job_id, as_of_date=NOW.date().isoformat()) is not None
    assert auto_publish_verified_artifact_candidates(
        database, JobPipeline(settings, database), stale
    )["withdrawn"] == 0

    database.update_source_artifact_processing(
        int(artifact["id"]),
        extraction_status="extracted",
        metadata_updates={
            "notice_deadline_evidence": notice_deadline_evidence(
                "报名截止时间：2026年10月18日。",
                NOTICE_URL,
                captured_at=(NOW + timedelta(hours=50)).isoformat(),
            )
        },
    )
    recovered = verify_government_artifacts(
        database, {"artifacts": []}, {"records": []}, now=NOW + timedelta(hours=50)
    )
    assert recovered["publication_ready"] == 1, recovered["items"]
    restored = auto_publish_verified_artifact_candidates(
        database, JobPipeline(settings, database), recovered
    )
    assert restored["restored"] == 0, restored
    assert database.find_public_job(job_id, as_of_date=NOW.date().isoformat()) is not None

    database.withdraw_missing_source_jobs(
        registered["id"], set(), reason="独立来源核验撤回"
    )
    still_verified = verify_government_artifacts(
        database, {"artifacts": []}, {"records": []}, now=NOW + timedelta(hours=51)
    )
    unrelated = auto_publish_verified_artifact_candidates(
        database, JobPipeline(settings, database), still_verified
    )
    assert unrelated["restored"] == 0
    assert database.find_public_job(job_id, as_of_date=NOW.date().isoformat()) is not None
