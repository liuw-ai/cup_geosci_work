from __future__ import annotations

import json

from job_hub.app import create_app
from job_hub.db import Database
from job_hub.pipeline import JobPipeline
from job_hub.reports import publish_daily_report
from job_hub.sources import RawPosting
from job_hub.source_validation import load_source_validation_registry

from conftest import make_settings, source


def test_public_pages_and_verified_import_api(tmp_path) -> None:
    settings = make_settings(tmp_path)
    app = create_app(settings)
    database = app.extensions["database"]
    pipeline = app.extensions["pipeline"]
    database.upsert_source(source())
    manual_source = source()
    manual_source["id"] = "official-manual-import"
    manual_source["source_type"] = "manual"
    database.upsert_source(manual_source)
    posting = RawPosting(
        title="地质工程师招聘",
        employer="测试能源集团",
        source_url="https://careers.example.edu.cn/jobs/1",
        application_url=None,
        text="石油勘探岗位，面向地质工程硕士，报名截止时间2026年12月20日。",
        summary="测试官方岗位。",
        published_date="2026-09-17",
        deadline_date="2026-12-20",
        location="北京",
    )
    job_id, _ = database.save_job(
        pipeline.normalize_posting(posting, database.get_source("official-test-source"))
    )
    report = publish_daily_report(database, settings)

    client = app.test_client()
    assert client.get("/").status_code == 200
    assert client.get("/jobs").status_code == 200
    assert client.get(f"/jobs/{job_id}").status_code == 200
    assert client.get(f"/daily/{report['report_date']}").status_code == 200
    assert client.get("/api/jobs").get_json()["total"] == 1
    assert client.get("/jobs?province=北京").status_code == 200
    assert client.get("/api/jobs?province=北京").get_json()["total"] == 1
    assert client.get("/api/jobs?province=山东").get_json()["total"] == 0
    assert client.get("/api/coverage").status_code == 200
    coverage_payload = client.get("/api/coverage").get_json()
    assert coverage_payload["organization_registry"]["organization_count"] > 0
    assert "source_bindings" not in coverage_payload["organization_registry"]
    landscape_response = client.get("/landscape")
    assert landscape_response.status_code == 200
    assert "油气工程技术服务" in landscape_response.get_data(as_text=True)
    detail_response = client.get(f"/jobs/{job_id}")
    assert "就业路径" in detail_response.get_data(as_text=True)

    profile_response = client.get(
        "/jobs?profile=master-geological-engineering"
    )
    assert profile_response.status_code == 200
    assert "明确匹配" in profile_response.get_data(as_text=True)
    profile_api = client.get(
        "/api/jobs?profile=master-geological-engineering"
    ).get_json()
    assert profile_api["items"][0]["profile_match"]["level"] == "explicit"

    response = client.post(
        "/api/admin/jobs",
        headers={"X-Admin-Token": "test-admin-token"},
        json={
            "title": "官方地学实习岗位",
            "employer": "测试能源集团",
            "source_url": "https://careers.example.edu.cn/jobs/2",
            "description": "面向资源勘查工程本科生的官方实习招聘。",
            "deadline_date": "2026-12-31",
        },
    )
    assert response.status_code == 201
    assert response.get_json()["outcome"] == "created"


def test_organization_matrix_is_admin_only_and_supports_role_filter(tmp_path) -> None:
    app = create_app(make_settings(tmp_path))
    client = app.test_client()

    assert client.get("/api/organization-matrix").status_code == 404
    assert client.get("/api/admin/organization-matrix").status_code == 403
    assert (
        client.get(
            "/api/admin/organization-matrix?organization_role=not-a-role",
            headers={"X-Admin-Token": "test-admin-token"},
        ).status_code
        == 400
    )

    response = client.get(
        "/api/admin/organization-matrix?organization_role=internal_technical_service",
        headers={"X-Admin-Token": "test-admin-token"},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["summary"]["organization_count"] > 0
    assert payload["items"]
    assert all(
        item["organization_role"] == "internal_technical_service"
        for item in payload["items"]
    )
    assert all("channels" in item for item in payload["items"])


def test_source_validation_matrix_is_admin_only_and_keeps_evidence_private(tmp_path) -> None:
    app = create_app(make_settings(tmp_path))
    client = app.test_client()

    assert client.get("/api/source-validation-matrix").status_code == 404
    assert client.get("/api/admin/source-validation-matrix").status_code == 403
    assert (
        client.get(
            "/api/admin/source-validation-matrix?validation_stage=not-a-stage",
            headers={"X-Admin-Token": "test-admin-token"},
        ).status_code
        == 400
    )

    response = client.get(
        "/api/admin/source-validation-matrix?validation_stage=adapter_fixture_verified",
        headers={"X-Admin-Token": "test-admin-token"},
    )
    assert response.status_code == 200
    private_payload = response.get_json()
    assert private_payload["summary"]["record_count"] == 9
    assert len(private_payload["items"]) == 6
    assert all(
        "sample" in item and "fixture_path" in item
        for item in private_payload["items"]
    )

    public_payload = client.get("/api/coverage").get_json()
    public_text = json.dumps(public_payload, ensure_ascii=False)
    for record in load_source_validation_registry()["records"]:
        if record.get("sample"):
            assert record["sample"]["official_url"] not in public_text
        if record.get("fixture_path"):
            assert record["fixture_path"] not in public_text
        for backup_url in record["backup_entry_urls"]:
            assert backup_url not in public_text
    assert "unvalidated_verified_targets" not in public_payload[
        "provincial_source_validation"
    ]


def test_admin_import_rejects_invalid_token(tmp_path) -> None:
    settings = make_settings(tmp_path)
    app = create_app(settings)
    client = app.test_client()

    response = client.post(
        "/api/admin/jobs",
        json={
            "title": "无效请求",
            "employer": "测试单位",
            "source_url": "https://example.edu.cn/job",
        },
    )
    assert response.status_code == 403


def test_utc_timestamps_are_rendered_in_site_timezone(tmp_path) -> None:
    app = create_app(make_settings(tmp_path))
    render_timestamp = app.jinja_env.filters["local_timestamp_date"]

    assert render_timestamp("2026-09-17T17:30:00Z") == "2026年9月18日"
    assert render_timestamp("2026-09-18") == "2026年9月18日"


def test_admin_publish_refuses_data_that_fails_the_same_audit_as_worker(tmp_path) -> None:
    settings = make_settings(tmp_path)
    app = create_app(settings)
    database = app.extensions["database"]
    pipeline = app.extensions["pipeline"]
    official_source = source()
    official_source["source_type"] = "html_notice"
    database.upsert_source(official_source)
    posting = RawPosting(
        title="地质工程师招聘",
        employer="测试能源集团",
        source_url="https://careers.example.edu.cn/jobs/audit-guard",
        application_url=None,
        text="面向地质工程硕士的官方招聘。",
        summary="官方招聘岗位。",
        published_date="2026-09-17",
        deadline_date="2026-12-20",
        location="北京",
    )
    job_id, _ = database.save_job(
        pipeline.normalize_posting(posting, database.get_source("official-test-source"))
    )
    with database.transaction() as connection:
        connection.execute(
            "UPDATE jobs SET source_url = ? WHERE id = ?",
            ("https://unverified.example.org/jobs/1", job_id),
        )

    response = app.test_client().post(
        "/api/admin/publish",
        headers={"X-Admin-Token": "test-admin-token"},
    )

    assert response.status_code == 409
    assert response.get_json()["audit"]["ok"] is False
