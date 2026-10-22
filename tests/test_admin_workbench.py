from __future__ import annotations

from flask.testing import FlaskClient

from job_hub.app import create_app

from conftest import make_settings, source


def _csrf_token(client: FlaskClient) -> str:
    with client.session_transaction() as browser_session:
        return str(browser_session["admin_csrf_token"])


def _login(client: FlaskClient, token: str = "test-admin-token") -> None:
    response = client.get("/admin/login?next=/admin")
    assert response.status_code == 200
    response = client.post(
        "/admin/login",
        data={
            "csrf_token": _csrf_token(client),
            "admin_token": token,
            "next": "/admin",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin")


def test_admin_workbench_requires_session_and_prevents_cache(tmp_path) -> None:
    app = create_app(make_settings(tmp_path))
    app.extensions["database"].upsert_third_party_batch(
        {
            "batch_id": "dashboard-batch",
            "discovery_source_id": "offcn-discovery",
            "third_party_table_url": "https://www.offcn.com/2026/table.xlsx",
            "capture_mode": "operator_provided_file",
            "captured_at": "2026-10-23T00:00:00Z",
            "content_sha256": "d" * 64,
        }
    )
    client = app.test_client()

    unauthenticated = client.get("/admin", follow_redirects=False)
    assert unauthenticated.status_code == 302
    assert "/admin/login" in unauthenticated.headers["Location"]

    client.get("/admin/login")
    invalid = client.post(
        "/admin/login",
        data={
            "csrf_token": _csrf_token(client),
            "admin_token": "wrong-token",
            "next": "/admin",
        },
    )
    assert invalid.status_code == 403
    assert "登录凭据无效" in invalid.get_data(as_text=True)
    assert client.get("/admin", follow_redirects=False).status_code == 302

    _login(client)
    dashboard = client.get("/admin")
    assert dashboard.status_code == 200
    assert "审核概览" in dashboard.get_data(as_text=True)
    assert "本批次已发布" in dashboard.get_data(as_text=True)
    assert dashboard.headers["Cache-Control"] == "no-store, max-age=0"
    assert client.get("/api/admin/source-funnel").headers["Cache-Control"] == (
        "no-store, max-age=0"
    )


def test_admin_lead_review_records_reviewer_without_leaking_student_data(tmp_path) -> None:
    settings = make_settings(tmp_path)
    app = create_app(settings)
    database = app.extensions["database"]
    database.upsert_source(source())
    lead = database.create_candidate_lead(
        {
            "lead_provider": "中公教育",
            "discovery_source_id": "offcn-discovery",
            "lead_url": "https://www.offcn.com/example/lead-1.html",
            "title": "地质工程岗位",
            "employer_hint": "某地质调查单位",
            "metadata": {"field_evidence": {"第三方发现页": "private"}},
        }
    )
    lead_id = int(lead["id"])
    client = app.test_client()
    _login(client)

    detail = client.get(f"/admin/leads/{lead_id}")
    assert detail.status_code == 200
    assert "第三方候选" in client.get("/admin/leads").get_data(as_text=True)
    assert "中公教育" in detail.get_data(as_text=True)

    saved = client.post(
        f"/admin/leads/{lead_id}",
        data={
            "csrf_token": _csrf_token(client),
            "action": "save",
            "employer_hint": "某地质调查单位",
            "location_hint": "北京市",
            "province_hint": "北京",
            "published_date": "2026-10-23",
            "official_url": "",
            "official_table_url": "",
            "official_application_url": "",
            "official_source_id": "",
            "verification_status": "candidate",
            "trust_grade": "B",
            "reviewed_by": "phase6-test-reviewer",
            "verification_note": "等待逐岗位回溯官方公告。",
            "metadata_description": "第三方线索摘要。",
            "metadata_location": "北京市",
            "metadata_deadline_date": "",
            "metadata_match_text": "",
            "metadata_qualification_text": "",
            "evidence_major": "地质工程",
            "evidence_degree": "本科",
            "evidence_position_code": "GEO-001",
        },
        follow_redirects=False,
    )
    assert saved.status_code == 302
    updated = database.get_candidate_lead(lead_id)
    assert updated is not None
    assert updated["metadata"]["last_reviewed_by"] == "phase6-test-reviewer"
    assert updated["metadata"]["field_evidence"]["专业要求"] == "地质工程"
    events = database.list_candidate_lead_events(lead_id)
    assert events[-1]["event_type"] == "review_updated"
    assert events[-1]["payload"]["reviewed_by"] == "phase6-test-reviewer"

    public = client.get("/api/jobs").get_json()
    assert public["total"] == 0
    assert "中公教育" not in client.get("/jobs").get_data(as_text=True)


def test_admin_workbench_refuses_unverified_candidate_publication(tmp_path) -> None:
    app = create_app(make_settings(tmp_path))
    database = app.extensions["database"]
    lead = database.create_candidate_lead(
        {
            "lead_provider": "华图教育",
            "lead_url": "https://www.huatu.com/example/lead-2.html",
            "title": "资源勘查工程岗位",
        }
    )
    lead_id = int(lead["id"])
    client = app.test_client()
    _login(client)

    response = client.post(
        f"/admin/leads/{lead_id}",
        data={"csrf_token": _csrf_token(client), "action": "publish"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Only official-content-verified leads can be published" in response.get_data(
        as_text=True
    )
    assert database.get_candidate_lead(lead_id)["verification_status"] == "candidate"
    assert client.get("/api/jobs").get_json()["total"] == 0


def test_candidate_lead_rejects_non_http_table_urls(tmp_path) -> None:
    app = create_app(make_settings(tmp_path))
    database = app.extensions["database"]
    lead = database.create_candidate_lead(
        {
            "lead_provider": "公考雷达",
            "lead_url": "https://www.gongkaoleida.com/example/lead-3.html",
            "title": "地质岗位",
        }
    )
    with __import__("pytest").raises(ValueError, match="official_table_url"):
        database.update_candidate_lead(
            int(lead["id"]), {"official_table_url": "javascript:alert(1)"}
        )


def test_admin_three_oil_entry_ledger_is_private_and_filterable(tmp_path) -> None:
    app = create_app(make_settings(tmp_path))
    client = app.test_client()

    unauthenticated = client.get("/admin/three-oil-entries", follow_redirects=False)
    assert unauthenticated.status_code == 302
    _login(client)
    page = client.get(
        "/admin/three-oil-entries?group_id=cnpc&status=unit_specific_entry_verified"
    )
    body = page.get_data(as_text=True)
    assert page.status_code == 200
    assert "中国石油" in body
    assert "单位独立入口已核验" in body
    assert page.headers["Cache-Control"] == "no-store, max-age=0"
