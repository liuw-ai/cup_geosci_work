from __future__ import annotations

from job_hub.app import create_app
from job_hub.sources import RawPosting

from conftest import make_settings, source


def test_candidate_leads_stay_private_until_official_content_is_verified(tmp_path) -> None:
    settings = make_settings(tmp_path)
    app = create_app(settings)
    database = app.extensions["database"]
    manual_source = source()
    manual_source.update(
        {
            "id": "official-manual-import",
            "source_type": "manual",
            # An administrator-approved manual source must be explicitly
            # enabled before its verified rows can enter student-facing reads.
            "enabled": True,
            "config": {"allow_shared_source_url": True},
        }
    )
    database.upsert_source(manual_source)
    client = app.test_client()
    headers = {"X-Admin-Token": "test-admin-token"}

    forbidden = client.post(
        "/api/admin/leads",
        json={
            "lead_provider": "中公教育",
            "lead_url": "https://example.com/lead/1",
            "title": "某地质局公开招聘",
        },
    )
    assert forbidden.status_code == 403

    created = client.post(
        "/api/admin/leads",
        headers=headers,
        json={
            "lead_provider": "中公教育",
            "lead_url": "https://example.com/lead/1",
            "title": "某地质局公开招聘",
            "employer_hint": "某省地质局",
        },
    )
    assert created.status_code == 201
    lead_id = created.get_json()["id"]
    assert client.get("/api/jobs").get_json()["total"] == 0

    blocked_publish = client.post(
        f"/api/admin/leads/{lead_id}/publish", headers=headers
    )
    assert blocked_publish.status_code == 409

    located = client.patch(
        f"/api/admin/leads/{lead_id}",
        headers=headers,
        json={
            "official_url": "https://official.example.gov.cn/jobs/1",
            "official_application_url": "https://official.example.gov.cn/apply/1",
            "verification_status": "official_url_found",
        },
    )
    assert located.status_code == 200

    verified = client.patch(
        f"/api/admin/leads/{lead_id}",
        headers=headers,
        json={
            "verification_status": "official_content_verified",
            "official_domain_status": "manual_review_approved",
            "verification_note": "已逐项核对政府官网原文、单位、专业和截止日期。",
            "metadata": {
                "employer": "某省地质局",
                "description": "面向资源勘查工程本科、地质工程硕士毕业生的公开招聘。",
                "field_evidence": {
                    "专业范围": "资源勘查工程、地质工程",
                    "学历要求": "本科、硕士研究生",
                },
                "location": "山东省东营市",
                "deadline_date": "2026-12-31",
            },
        },
    )
    assert verified.status_code == 200
    verified_lead = database.get_candidate_lead(lead_id)
    assert verified_lead["conversion_status"] == "official_content_verified"
    assert verified_lead["conversion_checked_at"]

    published = client.post(
        f"/api/admin/leads/{lead_id}/publish", headers=headers
    )
    assert published.status_code == 201
    jobs = client.get("/api/jobs?province=山东").get_json()
    assert jobs["total"] == 1
    assert jobs["items"][0]["official_evidence_url"] == (
        "https://official.example.gov.cn/jobs/1"
    )
    assert jobs["items"][0]["application_url"] == (
        "https://official.example.gov.cn/apply/1"
    )
    published_lead = database.get_candidate_lead(lead_id)
    assert published_lead["conversion_checked_at"]


def test_candidate_lead_rejects_non_http_official_application_url(tmp_path) -> None:
    settings = make_settings(tmp_path)
    app = create_app(settings)
    database = app.extensions["database"]
    database.upsert_source(source())
    lead = database.create_candidate_lead(
        {
            "lead_provider": "中公教育",
            "lead_url": "https://example.com/lead/2",
            "title": "地质岗位",
        }
    )
    with __import__("pytest").raises(ValueError, match="official_application_url"):
        database.update_candidate_lead(
            int(lead["id"]),
            {"official_application_url": "javascript:alert(1)"},
        )


def test_verified_candidate_rejects_third_party_application_url(tmp_path) -> None:
    settings = make_settings(tmp_path)
    app = create_app(settings)
    database = app.extensions["database"]
    database.upsert_source(source())
    lead = database.create_candidate_lead(
        {
            "lead_provider": "中公教育",
            "lead_url": "https://www.offcn.com/lead/official-link-check",
            "title": "地质岗位",
        }
    )
    lead_id = int(lead["id"])
    database.update_candidate_lead(
        lead_id,
        {
            "official_url": "https://careers.example.edu.cn/jobs/1",
            "official_source_id": "official-test-source",
            "verification_status": "official_url_found",
        },
    )
    with __import__("pytest").raises(
        ValueError, match="registered official application URL"
    ):
        database.update_candidate_lead(
            lead_id,
            {
                "official_application_url": "https://www.offcn.com/apply/1",
                "official_source_id": "official-test-source",
                "official_domain_status": "registered_source_match",
                "verification_status": "official_content_verified",
                "verification_note": "已核验官方公告和岗位字段。",
            },
        )


def test_published_lead_requires_student_publication_gate(tmp_path) -> None:
    settings = make_settings(tmp_path)
    app = create_app(settings)
    database = app.extensions["database"]
    manual_source = source()
    manual_source.update(
        {
            "id": "official-manual-import",
            "source_type": "manual",
            "enabled": True,
            "config": {"allow_shared_source_url": True},
        }
    )
    database.upsert_source(manual_source)
    lead = database.create_candidate_lead(
        {
            "lead_provider": "华图教育",
            "lead_url": "https://www.huatu.com/lead/publication-gate",
            "title": "地质研发岗",
            "employer_hint": "某研究院",
            "official_url": "https://official.example.edu.cn/jobs/1",
            "official_source_id": "official-manual-import",
            "metadata": {
                "source_id": "official-manual-import",
                "title": "地质研发岗",
                "employer": "某研究院",
                "description": "官方记录暂未提供岗位级专业与学历字段。",
                "field_evidence": {
                    "岗位": "地质研发岗",
                    "evidence_scope": "admin_verified_official_record",
                },
            },
        }
    )
    lead_id = int(lead["id"])
    database.update_candidate_lead(
        lead_id,
        {
            "verification_status": "official_url_found",
            "official_source_id": "official-manual-import",
        },
    )
    database.update_candidate_lead(
        lead_id,
        {
            "verification_status": "official_content_verified",
            "official_source_id": "official-manual-import",
            "official_domain_status": "manual_review_approved",
            "verification_note": "已核对官方页面，但岗位级专业和学历证据仍不完整。",
        },
    )
    pipeline = app.extensions["pipeline"]
    job_id, _ = database.save_job(
        pipeline.normalize_posting(
            RawPosting(
                title="待补岗位级证据",
                employer="某研究院",
                source_url="https://official.example.edu.cn/jobs/1",
                application_url=None,
                text="官方记录暂未提供岗位级专业与学历字段。",
                summary="",
                published_date="2026-10-18",
                deadline_date="2026-12-31",
                location="北京市",
                external_id="publication-gate-pending-1",
                official_evidence_url="https://official.example.edu.cn/jobs/1",
                field_evidence={
                    "岗位": "待补岗位级证据",
                    "evidence_scope": "admin_verified_official_record",
                },
            ),
            database.get_source("official-manual-import"),
        )
    )
    with __import__("pytest").raises(ValueError, match="student publication gate"):
        database.mark_candidate_lead_published(lead_id, job_id)
