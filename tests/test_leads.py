from __future__ import annotations

from job_hub.app import create_app

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
            "enabled": False,
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

    verified = client.patch(
        f"/api/admin/leads/{lead_id}",
        headers=headers,
        json={
            "official_url": "https://official.example.gov.cn/jobs/1",
            "verification_status": "official_content_verified",
            "verification_note": "已逐项核对政府官网原文、单位、专业和截止日期。",
            "metadata": {
                "employer": "某省地质局",
                "description": "面向资源勘查工程本科、地质工程硕士毕业生的公开招聘。",
                "location": "山东省东营市",
                "deadline_date": "2026-12-31",
            },
        },
    )
    assert verified.status_code == 200

    published = client.post(
        f"/api/admin/leads/{lead_id}/publish", headers=headers
    )
    assert published.status_code == 201
    jobs = client.get("/api/jobs?province=山东").get_json()
    assert jobs["total"] == 1
    assert jobs["items"][0]["official_evidence_url"] == (
        "https://official.example.gov.cn/jobs/1"
    )
