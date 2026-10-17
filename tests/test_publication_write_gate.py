from __future__ import annotations

from job_hub.db import Database


def _source() -> dict[str, object]:
    return {
        "id": "enterprise-source",
        "name": "企业官方来源",
        "publisher": "测试能源单位",
        "homepage_url": "https://careers.example.cn/",
        "source_type": "manual",
        "category": "油气工程技术服务",
        "source_tier": "A",
        "enabled": True,
        "config": {},
    }


def _job() -> dict[str, object]:
    return {
        "source_id": "enterprise-source",
        "fingerprint": "unsafe-enterprise-row",
        "content_hash": "unsafe-enterprise-row-v1",
        "title": "地学技术岗",
        "employer": "测试能源单位",
        "category": "油气工程技术服务",
        "source_tier": "A",
        "source_name": "企业官方来源",
        "source_url": "https://careers.example.cn/jobs/1",
        "official_evidence_url": "https://careers.example.cn/jobs/1",
        "location": "北京",
        "province": "北京",
        "country_or_region": "中国大陆",
        "deadline_date": "2099-12-31",
        "field_evidence": {
            "evidence_scope": "official_html_table_row",
            "岗位名称": "地学技术岗",
            "专业范围": "地质类",
            "学历要求": "硕士",
            "工作地点": "北京",
        },
        "publication_status": "student_eligible",
        "publication_basis": {"major_match_mode": "public_named_or_code"},
        "degree_levels": ["硕士"],
        "major_tags": ["地质学"],
        "summary": "官方岗位",
        "description": "官方岗位",
        "relevance_score": 50,
        "relevance_band": "强相关",
        "status": "open",
    }


def test_database_write_gate_downgrades_enterprise_non_exact_publication(tmp_path) -> None:
    database = Database(tmp_path / "jobs.sqlite3")
    database.initialize()
    database.upsert_source(_source())

    job_id, outcome = database.save_job(_job())

    assert outcome == "created"
    stored = database.find_job(job_id, student_visible=False)
    assert stored is not None
    assert stored["publication_status"] == "pending_evidence"
    assert stored["publication_basis"]["major_match_mode"] == ""
    assert database.list_jobs(page_size=None, student_visible=True)[1] == 0


def test_database_write_gate_keeps_enterprise_exact_match_public(tmp_path) -> None:
    database = Database(tmp_path / "jobs.sqlite3")
    database.initialize()
    database.upsert_source(_source())
    job = _job()
    job["fingerprint"] = "safe-enterprise-row"
    job["field_evidence"]["专业范围"] = "地质工程"
    job["publication_basis"] = {
        "major_match_mode": "exact_four_names",
        "matched_profile_ids": ["master-geological-engineering"],
    }

    job_id, _ = database.save_job(job)

    stored = database.find_job(job_id, student_visible=False)
    assert stored is not None
    assert stored["publication_status"] == "student_eligible"
    assert database.list_jobs(page_size=None, student_visible=True)[1] == 1


def test_database_write_gate_keeps_similar_rows_out_of_student_corpus(tmp_path) -> None:
    database = Database(tmp_path / "jobs.sqlite3")
    database.initialize()
    database.upsert_source(_source())
    job = _job()
    job["fingerprint"] = "similar-enterprise-row"
    job["field_evidence"]["专业范围"] = "地球物理学"
    job["publication_status"] = "similar_eligible"
    job["publication_basis"] = {"major_match_mode": "similar_major"}

    job_id, _ = database.save_job(job)

    stored = database.find_job(job_id, student_visible=False)
    assert stored is not None
    assert stored["publication_status"] == "similar_eligible"
    assert database.list_jobs(page_size=None, student_visible=True)[1] == 1
    assert database.list_jobs(page_size=None, student_visible=True)[0][0]["publication_layer"] == "broad_geoscience_match"


def test_database_write_gate_rejects_forged_exact_match_marker(tmp_path) -> None:
    database = Database(tmp_path / "jobs.sqlite3")
    database.initialize()
    database.upsert_source(_source())
    job = _job()
    job["publication_basis"] = {
        "major_match_mode": "exact_four_names",
        "matched_profile_ids": ["master-geological-engineering"],
    }

    job_id, _ = database.save_job(job)

    stored = database.find_job(job_id, student_visible=False)
    assert stored is not None
    assert stored["publication_status"] != "student_eligible"
    assert database.list_jobs(page_size=None, student_visible=True)[1] == 0


def test_database_write_gate_preserves_civil_service_broad_discipline(tmp_path) -> None:
    database = Database(tmp_path / "jobs.sqlite3")
    database.initialize()
    database.upsert_source(_source())
    job = _job()
    job["category"] = "公务员与选调"
    job["field_evidence"]["专业范围"] = "工学类"
    job["field_evidence"]["政府岗位类型"] = "civil_service"
    job["publication_basis"] = {"major_match_mode": "public_named_or_code"}

    job_id, _ = database.save_job(job)

    stored = database.find_job(job_id, student_visible=False)
    assert stored is not None
    assert stored["publication_status"] == "student_eligible"


def test_database_write_gate_does_not_trust_research_category(tmp_path) -> None:
    database = Database(tmp_path / "jobs.sqlite3")
    database.initialize()
    database.upsert_source(_source())
    job = _job()
    job["category"] = "科研院所、高校与博士后"
    job["publication_basis"] = {"major_match_mode": "exact_four_names"}

    job_id, _ = database.save_job(job)

    stored = database.find_job(job_id, student_visible=False)
    assert stored is not None
    assert stored["publication_status"] == "pending_evidence"
    assert database.list_jobs(page_size=None, student_visible=True)[1] == 0
