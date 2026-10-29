from __future__ import annotations

import json
from dataclasses import replace

from job_hub.audit import audit_database
from job_hub.db import Database
from job_hub.pipeline import JobPipeline
from job_hub.sources import RawPosting

from conftest import make_settings, source


def test_database_audit_detects_legacy_enterprise_publication_without_exact_mode(
    tmp_path,
) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    official_source = source()
    official_source["category"] = "油气上游业主与研究机构"
    database.upsert_source(official_source)
    pipeline = JobPipeline(settings, database)
    job_id, _ = database.save_job(
        pipeline.normalize_posting(
            RawPosting(
                title="地质工程技术岗",
                employer="测试能源集团",
                source_url="https://careers.example.edu.cn/jobs/audit",
                application_url=None,
                text="专业要求：地质工程；学历要求：硕士研究生。",
                summary="官方岗位。",
                published_date="2026-10-01",
                deadline_date="2099-12-31",
                location="北京",
                external_id="audit-enterprise-row",
                field_evidence={
                    "evidence_scope": "official_html_table_row",
                    "岗位": "地质工程技术岗",
                    "专业范围": "地质工程",
                    "学历要求": "硕士研究生",
                    "工作地点": "北京",
                },
            ),
            official_source,
        )
    )
    database.add_job_evidence(
        job_id,
        {
            "evidence_type": "official_page",
            "field_name": "job_record",
            "evidence_url": "https://careers.example.edu.cn/jobs/audit",
            "locator": "official_html_table_row",
            "verification_status": "verified",
        },
    )

    # Simulate a legacy public row written before the persisted match mode.
    with database.connect() as connection:
        connection.execute(
            "UPDATE jobs SET publication_basis_json = ? WHERE id = ?",
            (json.dumps({"matched_profile_ids": ["master-geological-engineering"]}), job_id),
        )
        connection.commit()

    result = audit_database(database, settings)
    codes = {item["code"] for item in result["issues"]}
    assert result["ok"] is False
    assert "enterprise_job_without_exact_four_major_mode" in codes

    pipeline.reindex_jobs()
    repaired = audit_database(database, settings)
    assert repaired["ok"] is True


def test_database_audit_does_not_trust_research_category_for_legacy_public_row(tmp_path) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    source_record = source()
    source_record["category"] = "科研院所、高校与博士后"
    database.upsert_source(source_record)
    pipeline = JobPipeline(settings, database)
    posting = RawPosting(
        title="地学技术岗",
        employer="测试研究院",
        source_url="https://careers.example.edu.cn/jobs/research-audit",
        application_url=None,
        text="专业要求：地质类；学历要求：硕士研究生。",
        summary="官方岗位。",
        published_date="2026-10-01",
        deadline_date="2099-12-31",
        location="北京",
        external_id="audit-research-row",
        field_evidence={
            "evidence_scope": "official_html_table_row",
            "岗位名称": "地学技术岗",
            "专业范围": "地质类",
            "学历要求": "硕士研究生",
            "工作地点": "北京",
        },
    )
    job_id, _ = database.save_job(pipeline.normalize_posting(posting, source_record))
    with database.connect() as connection:
        connection.execute(
            "UPDATE jobs SET publication_status = 'student_eligible', publication_basis_json = ? WHERE id = ?",
            (json.dumps({"major_match_mode": "exact_four_names", "matched_profile_ids": ["master-geology"]}), job_id),
        )
        connection.commit()

    result = audit_database(database, settings)
    codes = {item["code"] for item in result["issues"]}
    assert result["ok"] is False
    assert "public_job_publication_decision_mismatch" in codes


def test_database_audit_does_not_use_relevance_threshold_against_incomplete_layer(
    tmp_path,
) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    source_record = source()
    source_record["config"] = {"minimum_relevance": 99}
    database.upsert_source(source_record)
    pipeline = JobPipeline(settings, database)
    job_id, _ = database.save_job(
        pipeline.normalize_posting(
            RawPosting(
                title="地质工程岗位",
                employer="测试能源集团",
                source_url="https://careers.example.edu.cn/jobs/incomplete",
                application_url=None,
                text="专业要求：地质工程；学历要求：硕士研究生。",
                summary="官方岗位。",
                published_date="2026-10-01",
                deadline_date="2099-12-31",
                location="北京",
                external_id="audit-incomplete-layer",
                field_evidence={
                    "evidence_scope": "official_html_table_row",
                    "岗位名称": "地质工程岗位",
                    "专业范围": "地质工程",
                    "学历要求": "硕士研究生",
                    "工作地点": "北京",
                },
            ),
            source_record,
        )
    )
    with database.connect() as connection:
        connection.execute(
            """
            UPDATE jobs
            SET relevance_score = 0,
                publication_status = 'pending_evidence',
                publication_layer = 'incomplete_but_basic_match'
            WHERE id = ?
            """,
            (job_id,),
        )
        connection.commit()

    result = audit_database(database, settings)
    assert not any(
        item["code"] == "below_source_threshold" for item in result["issues"]
    )


def test_database_audit_replays_extended_opportunity_policy(tmp_path) -> None:
    settings = replace(make_settings(tmp_path), include_extended_opportunities=True)
    database = Database(settings.database_path)
    database.initialize()
    source_record = source()
    database.upsert_source(source_record)
    pipeline = JobPipeline(settings, database)
    job_id, _ = database.save_job(
        pipeline.normalize_posting(
            RawPosting(
                title="地球物理解释工程师",
                employer="测试能源集团",
                source_url="https://careers.example.edu.cn/jobs/social-audit",
                application_url=None,
                text="社会招聘。专业要求：地球物理学、地质学或相关专业；学历要求：硕士。",
                summary="官方社会招聘岗位。",
                published_date="2026-10-01",
                deadline_date="2099-12-31",
                location="北京",
                external_id="audit-social-extended",
                field_evidence={
                    "evidence_scope": "official_html_table_row",
                    "岗位名称": "地球物理解释工程师",
                    "专业范围": "地球物理学、地质学或相关专业",
                    "学历要求": "硕士",
                    "工作地点": "北京",
                    "招聘性质": "社会招聘",
                },
            ),
            source_record,
        )
    )
    stored = database.find_job(job_id, student_visible=False)
    assert stored is not None
    assert stored["publication_basis"]["extended_opportunity"] is True
    assert stored["publication_basis"]["major_match_mode"] == "social_exact_four_names"

    result = audit_database(database, settings)
    assert result["ok"] is True
