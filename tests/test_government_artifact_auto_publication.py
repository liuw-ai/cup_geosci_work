from __future__ import annotations

from job_hub.artifact_publication import auto_publish_verified_artifact_candidates
from job_hub.db import Database
from job_hub.government_artifact_verification import verify_government_artifacts
from job_hub.pipeline import JobPipeline

from conftest import make_settings, source


def test_current_fixed_artifact_candidate_is_published_without_admin_action(tmp_path) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    database.upsert_source(source())
    parent_url = "https://careers.example.edu.cn/notice.html"
    artifact_url = "https://careers.example.edu.cn/table.xlsx"
    artifact = database.upsert_source_artifact(
        {
            "source_id": "official-test-source",
            "parent_url": parent_url,
            "artifact_url": artifact_url,
            "artifact_kind": "position_table",
            "extraction_status": "extracted",
            "content_sha256": "a" * 64,
            "storage_path": "sha256/aa/" + "a" * 64 + ".xlsx",
            "parser_version": "attachments-test",
            "metadata": {"government_artifact_id": "test-artifact"},
        }
    )
    row = database.upsert_source_artifact_rows(
        int(artifact["id"]),
        [
            {
                "sheet_name": "岗位表",
                "row_number": 2,
                "cells": {
                    "岗位代码": "A-001",
                    "招聘单位": "测试能源集团",
                    "专业": "地质工程",
                    "学历": "硕士研究生",
                    "工作地点": "北京市",
                    "招聘人数": "1",
                },
                "row_text": "岗位代码：A-001；招聘单位：测试能源集团；专业：地质工程；学历：硕士研究生；工作地点：北京市；招聘人数：1",
            }
        ],
    )[0]
    candidate = database.upsert_artifact_job_candidate(
        {
            "artifact_row_id": row["id"],
            "source_id": "official-test-source",
            "official_page_url": parent_url,
            "title": "地质工程技术岗",
            "employer": "测试能源集团",
            "location": "北京市",
            "deadline_date": "2099-12-31",
            "degree_levels": ["硕士"],
            "major_tags": ["地质工程"],
            "summary": "官方岗位表第2行",
            "description": "官方岗位表第2行",
            "extraction_confidence": "high",
            "field_evidence": {
                "evidence_scope": "official_attachment_row",
                "岗位": "地质工程技术岗",
                "artifact_url": artifact_url,
                "职位代码": "A-001",
                "专业范围": "地质工程",
                "学历要求": "硕士研究生",
                "工作地点": "北京市",
                "招聘人数": "1",
                "table_row": "岗位表!2",
            },
            "review_status": "needs_review",
        }
    )
    stale_row = database.upsert_source_artifact_rows(
        int(artifact["id"]),
        [{
            "sheet_name": "岗位表",
            "row_number": 3,
            "cells": {"岗位代码": "A-999", "专业": "地质工程", "学历": "硕士研究生"},
            "row_text": "岗位代码：A-999；专业：地质工程；学历：硕士研究生",
        }],
    )[0]
    stale_candidate = database.upsert_artifact_job_candidate(
        {
            "artifact_row_id": stale_row["id"],
            "source_id": "official-test-source",
            "official_page_url": parent_url,
            "title": "未在本轮台账核验的岗位",
            "employer": "测试能源集团",
            "location": "北京市",
            "deadline_date": "2099-12-31",
            "degree_levels": ["硕士"],
            "major_tags": ["地质工程"],
            "summary": "旧核验状态",
            "description": "旧核验状态",
            "extraction_confidence": "high",
            "field_evidence": {
                "artifact_url": artifact_url,
                "职位代码": "A-999",
                "专业范围": "地质工程",
                "学历要求": "硕士研究生",
                "工作地点": "北京市",
                "招聘人数": "1",
                "table_row": "岗位表!3",
            },
            "review_status": "official_content_verified",
        }
    )
    record = {
        "id": "test-record",
        "source_id": "official-test-source",
        "position_type": "public_institution",
        "province": "北京",
        "employer": "测试能源集团",
        "position_code": "A-001",
        "title": "地质工程技术岗",
        "major_requirement": "地质工程",
        "degree_requirement": "硕士研究生",
        "location": "北京市",
        "headcount": 1,
        "deadline_date": "2099-12-31",
        "deadline_policy": "fixed_date",
        "official_notice_url": parent_url,
        "official_attachment_url": artifact_url,
        "evidence_locator": "岗位表!2",
        "record_status": "verified_open",
        "match_status": "explicit_match",
    }
    declared = {
        "id": "test-artifact",
        "source_id": "official-test-source",
        "province": "北京",
        "position_type": "public_institution",
        "notice_url": parent_url,
        "attachment_url": artifact_url,
        "artifact_kind": "position_table",
        "deadline_date": "2099-12-31",
        "deadline_policy": "fixed_date",
        "observed_on": "2026-10-07",
        "status": "server_download_pending",
    }
    registry = {"records": [record]}
    verification = verify_government_artifacts(
        database,
        {"artifacts": [declared]},
        registry,
        today="2026-10-07",
    )
    assert verification["publication_ready"] == 1
    pipeline = JobPipeline(settings, database)
    publication = auto_publish_verified_artifact_candidates(
        database, pipeline, verification
    )
    assert publication["published"] == 1, publication
    assert database.get_artifact_job_candidate(int(candidate["id"]))["review_status"] == "published"
    assert database.get_artifact_job_candidate(int(stale_candidate["id"]))["review_status"] == (
        "official_content_verified"
    )
    job = database.find_public_job(publication["items"][0]["job_id"])
    assert job is not None
    assert job["publication_status"] == "student_eligible"
    unrelated_cycle = auto_publish_verified_artifact_candidates(
        database, pipeline, {"items": []}
    )
    assert unrelated_cycle["withdrawn"] == 0
    assert database.find_public_job(publication["items"][0]["job_id"]) is not None


def test_rolling_artifact_without_availability_confirmation_stays_private(tmp_path) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    database.upsert_source(source())
    # The verifier report is the only authorization input.  An empty report
    # must not publish an already content-verified candidate on its own.
    publication = auto_publish_verified_artifact_candidates(
        database,
        JobPipeline(settings, database),
        {
            "items": [
                {
                    "source_id": "official-test-source",
                    "artifact_url": "https://careers.example.edu.cn/table.xlsx",
                    "publication_ready": False,
                }
            ]
        },
    )
    assert publication["authorized_artifacts"] == 0
    assert publication["published"] == 0


def test_newly_discovered_official_artifact_is_verified_without_static_manifest(tmp_path) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    discovered_source = source()
    discovered_source["source_type"] = "html_notice"
    discovered_source["category"] = "事业单位与人才引进"
    discovered_source["config"] = {
        "allowed_hosts": ["careers.example.edu.cn"],
        "attachment_discovery_enabled": True,
        "listing_urls": ["https://careers.example.edu.cn/notices"],
        "source_role": "human_resources_or_exam",
    }
    database.upsert_source(discovered_source)
    parent_url = "https://careers.example.edu.cn/notice.html"
    artifact_url = "https://careers.example.edu.cn/table.xlsx"
    artifact = database.upsert_source_artifact(
        {
            "source_id": "official-test-source",
            "parent_url": parent_url,
            "artifact_url": artifact_url,
            "artifact_kind": "position_table",
            "extraction_status": "extracted",
            "content_sha256": "b" * 64,
            "storage_path": "sha256/bb/" + "b" * 64 + ".xlsx",
            "parser_version": "attachments-test",
            "metadata": {"discovery_only": True},
        }
    )
    row = database.upsert_source_artifact_rows(
        int(artifact["id"]),
        [
            {
                "sheet_name": "岗位表",
                "row_number": 2,
                "cells": {
                    "岗位名称": "地质工程技术岗",
                    "岗位代码": "D-001",
                    "招聘单位": "测试能源集团",
                    "专业": "地质工程",
                    "学历": "硕士研究生",
                    "工作地点": "北京市",
                    "招聘人数": "1",
                    "报名截止": "2099-12-31",
                },
                "row_text": "岗位代码：D-001；招聘单位：测试能源集团；专业：地质工程；学历：硕士研究生；工作地点：北京市；招聘人数：1；报名截止：2099-12-31",
            }
        ],
    )[0]
    candidate = database.upsert_artifact_job_candidate(
        {
            "artifact_row_id": row["id"],
            "source_id": "official-test-source",
            "official_page_url": parent_url,
            "title": "地质工程技术岗",
            "employer": "测试能源集团",
            "location": "北京市",
            "deadline_date": "2099-12-31",
            "degree_levels": ["硕士"],
            "major_tags": ["地质工程"],
            "summary": "官方岗位表第2行",
            "description": "官方岗位表第2行",
            "extraction_confidence": "high",
                "field_evidence": {
                    "evidence_scope": "official_attachment_row",
                    "岗位": "地质工程技术岗",
                    "artifact_url": artifact_url,
                "专业范围": "地质工程",
                "学历要求": "硕士研究生",
                "工作地点": "北京市",
                "招聘人数": "1",
                "table_row": "岗位表!2",
            },
            "review_status": "needs_review",
        }
    )

    verification = verify_government_artifacts(
        database,
        {"artifacts": []},
        {"records": []},
        today="2026-10-07",
    )

    assert verification["publication_ready"] == 1, verification["items"]
    assert verification["items"][0]["dynamic_discovery"] is True
    assert database.get_artifact_job_candidate(int(candidate["id"]))["review_status"] == (
        "official_content_verified"
    )
    publication = auto_publish_verified_artifact_candidates(
        database, JobPipeline(settings, database), verification
    )
    assert publication["published"] == 1, publication

    renewed = verify_government_artifacts(
        database, {"artifacts": []}, {"records": []}, today="2026-10-07"
    )
    assert renewed["publication_ready"] == 1
    second_cycle = auto_publish_verified_artifact_candidates(
        database, JobPipeline(settings, database), renewed
    )
    assert second_cycle["published"] == 0
    assert second_cycle["refreshed"] == 1
    assert database.find_public_job(publication["items"][0]["job_id"]) is not None


def test_dynamic_discovery_does_not_auto_publish_non_government_source(tmp_path) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    non_government = source()
    non_government["source_type"] = "html_notice"
    non_government["config"] = {
        "allowed_hosts": ["careers.example.edu.cn"],
        "attachment_discovery_enabled": True,
        "listing_urls": ["https://careers.example.edu.cn/notices"],
    }
    database.upsert_source(non_government)
    artifact = database.upsert_source_artifact(
        {
            "source_id": "official-test-source",
            "parent_url": "https://careers.example.edu.cn/notice.html",
            "artifact_url": "https://careers.example.edu.cn/table.xlsx",
            "artifact_kind": "position_table",
            "extraction_status": "extracted",
            "content_sha256": "c" * 64,
            "storage_path": "sha256/cc/" + "c" * 64 + ".xlsx",
            "parser_version": "attachments-test",
            "metadata": {"discovery_only": True},
        }
    )
    verification = verify_government_artifacts(
        database, {"artifacts": []}, {"records": []}, today="2026-10-07"
    )

    assert verification["checked"] == 0
    assert verification["publication_ready"] == 0
    assert database.get_source_artifact(int(artifact["id"]))["metadata"].get(
        "government_auto_verification"
    ) is None
