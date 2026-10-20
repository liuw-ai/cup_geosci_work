from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import job_hub.app as app_module
from job_hub.app import create_app
from job_hub.cli import import_verified_jobs
from job_hub.db import Database
from job_hub.pipeline import JobPipeline
from job_hub.reports import publish_daily_report
from job_hub.sources import RawPosting, load_source_registries
from job_hub.source_validation import load_source_validation_registry

from conftest import make_settings, source


def test_version_and_health_report_release_identity(tmp_path) -> None:
    settings = replace(
        make_settings(tmp_path),
        release_version="release-test",
        release_git_sha="abc1234",
        release_image_sha="sha256:image",
        release_built_at="2026-10-04T00:00:00Z",
    )
    app = create_app(settings)
    client = app.test_client()

    version = client.get("/version")
    health = client.get("/healthz")

    assert version.status_code == 200
    version_payload = version.get_json()
    assert version_payload == {
        "service": "cupb-geoscience-job-hub",
        "version": "release-test",
        "git_sha": "abc1234",
        "image_sha": "sha256:image",
        "built_at": "2026-10-04T00:00:00Z",
    }
    assert health.status_code == 200
    assert health.get_json()["release"] == {
        key: value for key, value in version_payload.items() if key != "service"
    }


def test_readiness_endpoint_separates_liveness_from_publication_gate(tmp_path) -> None:
    app = create_app(make_settings(tmp_path))
    client = app.test_client()

    health = client.get("/healthz")
    readiness = client.get("/readinessz")

    assert health.status_code == 200
    assert readiness.status_code == 503
    assert readiness.get_json() == {
        "status": "not_ready",
        "internal_ready": False,
    }
    assert readiness.headers["Cache-Control"] == "no-store"


def test_readiness_endpoint_returns_200_only_for_internal_ready_report(
    tmp_path, monkeypatch
) -> None:
    app = create_app(make_settings(tmp_path))
    monkeypatch.setattr(
        app_module,
        "build_production_readiness",
        lambda *_args, **_kwargs: {"internal_ready": True},
    )

    response = app.test_client().get("/readinessz")

    assert response.status_code == 200
    assert response.get_json() == {
        "status": "ready",
        "internal_ready": True,
    }


def test_home_uses_today_preview_when_latest_frozen_report_is_stale(
    tmp_path, monkeypatch
) -> None:
    settings = make_settings(tmp_path)
    app = create_app(settings)
    database = app.extensions["database"]
    database.upsert_source(source())
    monkeypatch.setattr(app_module, "local_today", lambda _settings: date(2026, 9, 23))
    publish_daily_report(database, settings, date(2026, 9, 18))

    response = app.test_client().get("/")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "2026年9月23日就业日报" in body
    assert "今天的正式日报将在 20:00 固化发布" in body


def test_manual_only_government_positions_stay_off_public_upcoming_page_until_confirmed(
    tmp_path, monkeypatch
) -> None:
    registry_payload = json.loads(
        Path("data/government_position_registry.json").read_text(encoding="utf-8")
    )
    # Keep this test about manual evidence confirmation, not the live CEA
    # opening instant.  The production registry may already be past that
    # instant when the suite runs.
    registry_payload["source_opening_dates"] = {"cea-2027-recruitment": "2026-10-01"}
    registry_payload.pop("source_opening_at", None)
    registry_path = tmp_path / "government_position_registry.json"
    registry_path.write_text(
        json.dumps(registry_payload, ensure_ascii=False), encoding="utf-8"
    )
    settings = replace(
        make_settings(tmp_path),
        government_position_registry_path=registry_path,
        government_position_max_age_hours=None,
    )
    app = create_app(settings)
    database = app.extensions["database"]
    cea_source = next(
        item
        for item in load_source_registries(["data/sources.json"])
        if item["id"] == "cea-2027-recruitment"
    )
    database.upsert_source(cea_source)
    monkeypatch.setattr(app_module, "local_today", lambda _settings: date(2026, 9, 30))

    client = app.test_client()
    unpublished = client.get("/government/upcoming").get_data(as_text=True)
    assert "中国地震局" not in unpublished

    database.record_government_source_verification(
        "cea-2027-recruitment",
        status="verified",
        checked_at="2026-09-30T01:00:00Z",
        detail="Administrator manual confirmation: official notice and workbook checked",
    )
    confirmed = client.get("/government/upcoming").get_data(as_text=True)
    assert "中国地震局" in confirmed


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
        field_evidence={
            "evidence_scope": "official_html_table_row",
            "岗位": "地质工程师招聘",
            "专业范围": "地质工程",
            "学历要求": "硕士",
        },
    )
    job_id, _ = database.save_job(
        pipeline.normalize_posting(posting, database.get_source("official-test-source"))
    )
    report = publish_daily_report(database, settings)

    client = app.test_client()
    assert client.get("/").status_code == 200
    assert client.get("/jobs").status_code == 200
    assert client.get("/jobs?page=not-a-number").status_code == 200
    detail_response = client.get(f"/jobs/{job_id}")
    assert detail_response.status_code == 200
    assert "专业要求原文" in detail_response.get_data(as_text=True)
    assert client.get(f"/daily/{report['report_date']}").status_code == 200
    assert client.get("/api/jobs").get_json()["total"] == 1
    malformed_page = client.get("/api/jobs?page=not-a-number")
    assert malformed_page.status_code == 200
    assert malformed_page.get_json()["page"] == 1
    assert client.get("/jobs?province=北京").status_code == 200
    assert client.get("/api/jobs?province=北京").get_json()["total"] == 1
    assert client.get("/api/jobs?province=山东").get_json()["total"] == 0
    # The search field must cover the verified structured major requirement,
    # not only title/body text.  Some official table adapters keep the
    # qualification in field_evidence while their public summary is generic.
    structured_only_posting = RawPosting(
        title="勘探技术岗位",
        employer="测试能源集团",
        source_url="https://careers.example.edu.cn/jobs/structured-major",
        application_url=None,
        text="2027届校园招聘，报名截止时间2026年12月20日。",
        summary="官方岗位。",
        published_date="2026-09-17",
        deadline_date="2026-12-20",
        location="北京",
        field_evidence={
            "evidence_scope": "official_html_table_row",
            "岗位": "勘探技术岗位",
            "专业范围": "地质资源与地质工程",
            "学历要求": "硕士",
        },
    )
    database.save_job(
        pipeline.normalize_posting(
            structured_only_posting,
            database.get_source("official-test-source"),
        )
    )
    search_payload = client.get("/api/jobs?q=地质资源与地质工程").get_json()
    assert search_payload["total"] == 1
    assert search_payload["items"][0]["title"] == "勘探技术岗位"
    with database.transaction() as connection:
        connection.execute(
            """
            UPDATE jobs
            SET third_party_source_id = ?,
                third_party_discovery_url = ?,
                third_party_batch_id = ?,
                third_party_discovery_sha256 = ?,
                third_party_table_url = ?,
                field_evidence_json = ?
            WHERE id = ?
            """,
            (
                "huatu-discovery",
                "https://www.huatu.com/discovery/1",
                "huatu-test-2026",
                "a" * 64,
                "https://www.huatu.com/table.xlsx",
                json.dumps(
                    {
                        "专业范围": "地质资源与地质工程",
                        "第三方发现页": "https://www.huatu.com/discovery/1",
                        "官方岗位表链接": "https://official.example/table.xlsx",
                    },
                    ensure_ascii=False,
                ),
                job_id + 1,
            ),
        )
    public_payload = client.get("/api/jobs?q=地质资源与地质工程").get_json()
    public_item = public_payload["items"][0]
    assert "third_party_discovery_url" not in public_item
    assert "third_party_table_url" not in public_item
    assert "third_party_source_id" not in public_item
    assert "第三方发现页" not in public_item["field_evidence"]
    assert public_item["official_table_url"] is None
    assert client.get("/api/coverage").status_code == 200
    assert client.get("/about").status_code == 200
    coverage_payload = client.get("/api/coverage").get_json()
    assert coverage_payload["organization_registry"]["organization_count"] > 0
    assert "source_bindings" not in coverage_payload["organization_registry"]
    assert coverage_payload["domestic_open_jobs"] == 2
    assert coverage_payload["dual_axis"]["coverage_axis"]["domestic_effective_jobs"] == 2
    assert coverage_payload["open_jobs"] == client.get("/api/jobs").get_json()["total"]
    assert "rows" not in coverage_payload["source_funnel"]
    assert client.get("/api/admin/source-funnel").status_code == 403
    funnel_payload = client.get(
        "/api/admin/source-funnel",
        headers={"X-Admin-Token": "test-admin-token"},
    ).get_json()
    assert funnel_payload["summary"]["registered_sources"] >= 1
    assert funnel_payload["rows"]
    assert funnel_payload["summary"]["current_public_jobs"] == client.get(
        "/api/jobs"
    ).get_json()["total"]
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


def test_public_api_layer_filter_is_forwarded_without_profile(tmp_path) -> None:
    settings = make_settings(tmp_path)
    app = create_app(settings)
    database = app.extensions["database"]
    database.upsert_source(source())
    pipeline = app.extensions["pipeline"]

    def save(title: str, url: str) -> int:
        posting = RawPosting(
            title=title,
            employer="测试能源集团",
            source_url=url,
            application_url=url,
            text="官方招聘，面向地质工程硕士。",
            summary="官方岗位。",
            published_date="2026-10-01",
            deadline_date="2026-12-31",
            location="北京",
            field_evidence={
                "岗位": title,
                "专业范围": "地质工程",
                "学历要求": "硕士",
                "工作地点": "北京",
            },
        )
        job_id, _ = database.save_job(
            pipeline.normalize_posting(posting, database.get_source("official-test-source"))
        )
        return job_id

    exact_id = save("精确岗位", "https://careers.example.edu.cn/jobs/exact")
    broad_id = save("宽泛岗位", "https://careers.example.edu.cn/jobs/broad")
    with database.transaction() as connection:
        connection.execute(
            "UPDATE jobs SET publication_layer = 'exact_match' WHERE id = ?",
            (exact_id,),
        )
        connection.execute(
            "UPDATE jobs SET publication_layer = 'broad_geoscience_match' WHERE id = ?",
            (broad_id,),
        )

    payload = app.test_client().get("/api/jobs?layer=exact_match").get_json()
    assert payload["total"] == 1
    assert payload["items"][0]["id"] == exact_id
    assert payload["items"][0]["publication_layer"] == "exact_match"

    broad_payload = app.test_client().get(
        "/api/jobs?layer=broad_geoscience_match"
    ).get_json()
    assert broad_payload["total"] == 1
    assert broad_payload["items"][0]["id"] == broad_id


def test_official_pending_scope_is_separate_from_current_public_jobs(tmp_path) -> None:
    settings = make_settings(tmp_path)
    app = create_app(settings)
    database = app.extensions["database"]
    pipeline = app.extensions["pipeline"]
    database.upsert_source(source())
    pending = RawPosting(
        title="勘探技术岗位",
        employer="测试能源集团",
        source_url="https://careers.example.edu.cn/jobs/pending",
        application_url="https://careers.example.edu.cn/apply/pending",
        text="2027届校园招聘，岗位详情请以官方公告为准。",
        summary="已登记官方来源的待核记录。",
        published_date="2026-09-17",
        deadline_date="2026-12-20",
        location="北京",
        external_id="pending-scope-1",
        official_evidence_url="https://careers.example.edu.cn/jobs/pending",
        field_evidence={
            "evidence_scope": "official_detail_block",
            "岗位": "勘探技术岗位",
            "学历要求": "硕士",
        },
    )
    pending_id, _ = database.save_job(
        pipeline.normalize_posting(pending, database.get_source("official-test-source"))
    )

    client = app.test_client()
    assert client.get("/api/jobs").get_json()["total"] == 0
    browse = client.get("/jobs?scope=official_pending")
    assert browse.status_code == 200
    body = browse.get_data(as_text=True)
    assert "官方待核岗位" in body
    assert "不计入当前可投递岗位" in body
    assert "待补字段：专业要求" in body

    payload = client.get("/api/jobs?scope=official_pending").get_json()
    assert payload["scope"] == "official_pending"
    assert payload["counts_as_current"] is False
    assert payload["total"] == 1
    assert payload["items"][0]["id"] == pending_id
    assert payload["items"][0]["pending_missing_fields"] == ["专业要求"]

    assert client.get(f"/jobs/{pending_id}").status_code == 404
    detail = client.get(f"/jobs/{pending_id}?scope=official_pending")
    assert detail.status_code == 200
    detail_body = detail.get_data(as_text=True)
    assert "官方待核记录" in detail_body
    assert "打开官方原文核验" in detail_body
    assert "待补字段" in detail_body


def test_admin_source_fallback_audit_is_private_and_candidate_only(tmp_path) -> None:
    settings = make_settings(tmp_path)
    app = create_app(settings)
    client = app.test_client()

    assert client.get("/api/admin/source-fallback-audit").status_code == 403
    response = client.get(
        "/api/admin/source-fallback-audit",
        headers={"X-Admin-Token": settings.admin_token},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["entry_count"] == 6
    # CNPC's two fallback entries were reclassified as access_limited by the
    # read-only probe; that is a source state, not a disappearance of jobs.
    assert payload["candidate_count"] == 4
    assert payload["access_limited_count"] == 2
    assert payload["verified_count"] == 0
    assert payload["enabled_source_count"] == 0
    assert payload["automation_ready_count"] == 0


def test_admin_three_oil_catalog_audit_is_private_and_reports_job_binding(tmp_path) -> None:
    settings = make_settings(tmp_path)
    app = create_app(settings)
    client = app.test_client()

    assert client.get("/api/admin/three-oil-catalog-audit").status_code == 403
    response = client.get(
        "/api/admin/three-oil-catalog-audit",
        headers={"X-Admin-Token": settings.admin_token},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["catalog_summary"]["group_count"] == 3
    assert payload["job_detail_binding_summary"]["provided"] is True
    assert "job_detail_employer_binding_count" in payload["items"][0]


def test_admin_source_funnel_includes_capture_freshness_projection(tmp_path) -> None:
    settings = make_settings(tmp_path)
    app = create_app(settings)
    database = app.extensions["database"]
    source_record = source()
    source_record.update(
        {
            "id": "stale-admin-source",
            "source_type": "html_notice",
            "config": {"max_age_hours": 1},
        }
    )
    database.upsert_source(source_record)
    run_id = database.record_crawl_start(source_record["id"])
    database.record_crawl_finish(
        run_id,
        "finished",
        discovered_count=1,
        evidence_complete_count=1,
    )
    database.record_source_capture_freshness(
        source_record["id"],
        captured_at="2026-10-01T00:00:00Z",
        evidence_kind="capture_manifest",
    )

    response = app.test_client().get(
        "/api/admin/source-funnel",
        headers={"X-Admin-Token": settings.admin_token},
    )

    assert response.status_code == 200
    payload = response.get_json()
    row = next(item for item in payload["rows"] if item["source_id"] == source_record["id"])
    assert row["capture_freshness"]["status"] == "stale"
    assert payload["summary"]["stale_capture_sources"] >= 1


def test_public_pages_hide_legacy_non_mainland_rows_even_if_marked_eligible(tmp_path) -> None:
    settings = make_settings(tmp_path)
    app = create_app(settings)
    database = app.extensions["database"]
    pipeline = app.extensions["pipeline"]
    database.upsert_source(source())
    posting = RawPosting(
        title="境外地质工程师",
        employer="测试能源集团",
        source_url="https://careers.example.edu.cn/jobs/overseas",
        application_url=None,
        text="专业要求：地质工程。学历要求：硕士。",
        summary="海外岗位。",
        published_date="2026-09-17",
        deadline_date="2026-12-20",
        location="北京",
        field_evidence={
            "evidence_scope": "official_html_table_row",
            "岗位": "境外地质工程师",
            "专业范围": "地质工程",
            "学历要求": "硕士",
        },
    )
    normalized = pipeline.normalize_posting(
        posting, database.get_source("official-test-source")
    )
    # This represents old rows created before the domestic public-read guard.
    normalized["country_or_region"] = "美国"
    normalized["province"] = None
    normalized["publication_status"] = "student_eligible"
    normalized["publication_basis"] = {
        "public_scope": "international",
        "reason": "legacy international source opt-in",
    }
    job_id, _ = database.save_job(normalized)

    client = app.test_client()
    assert client.get("/api/jobs").get_json()["total"] == 0
    assert database.count_open_jobs() == 0
    assert client.get(f"/jobs/{job_id}").status_code == 404


def test_government_position_activation_tasks_are_admin_only(tmp_path) -> None:
    registry_payload = json.loads(
        Path("data/government_position_registry.json").read_text(encoding="utf-8")
    )
    # Use a future opening date so this reporting test remains deterministic
    # after the real CEA window has opened.
    registry_payload["source_opening_dates"] = {"cea-2027-recruitment": "2099-01-01"}
    registry_payload["source_opening_at"] = {
        "cea-2027-recruitment": "2099-01-01T00:00:00+08:00"
    }
    registry_path = tmp_path / "government_position_registry.json"
    registry_path.write_text(
        json.dumps(registry_payload, ensure_ascii=False), encoding="utf-8"
    )
    settings = replace(
        make_settings(tmp_path),
        government_position_registry_path=registry_path,
        government_position_max_age_hours=None,
    )
    app = create_app(settings)
    client = app.test_client()

    assert client.get("/api/admin/government-position-tasks").status_code == 403
    response = client.get(
        "/api/admin/government-position-tasks",
        headers={"X-Admin-Token": "test-admin-token"},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["summary"]["total"] >= 1
    cea = next(item for item in payload["items"] if item["source_id"] == "cea-2027-recruitment")
    assert cea["status"] == "scheduled"
    assert cea["matching_row_count"] == 91


def test_sinopec_capture_admin_endpoint_is_private_and_reports_complete_manifest(tmp_path) -> None:
    settings = make_settings(tmp_path)
    app = create_app(settings)
    database = app.extensions["database"]
    sinopec = next(
        item
        for item in load_source_registries(["data/sources.json"])
        if item["id"] == "sinopec-career"
    )
    database.upsert_source(sinopec)
    client = app.test_client()

    assert client.get("/api/admin/sinopec-capture").status_code == 403
    response = client.get(
        "/api/admin/sinopec-capture",
        headers={"X-Admin-Token": settings.admin_token},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["enabled"] is False
    assert payload["summary"]["enterprise_total"] == 132
    assert payload["summary"]["enterprise_captured"] == 132
    assert payload["summary"]["candidate_enterprise_captured"] == 35
    assert payload["summary"]["job_rows_captured"] == 363
    assert payload["summary"]["complete_manifest"] is True


def test_cnpc_detail_page_discloses_degraded_official_detail_endpoint(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(app_module, "local_today", lambda _settings: date(2026, 10, 10))
    settings = make_settings(tmp_path)
    app = create_app(settings)
    database = app.extensions["database"]
    source = next(
        item
        for item in load_source_registries(["data/sources.json"])
        if item["id"] == "cnpc-career"
    )
    # This test exercises the detail-page disclosure contract, not production
    # snapshot freshness. Keep the fixture inside its freshness window so the
    # read-side stale-capture gate does not intentionally hide the old sample.
    source["config"] = {**source["config"], "max_age_hours": 100_000}
    database.upsert_source(source)
    pipeline = JobPipeline(settings, database)
    snapshot_path = Path("data/verified/cnpc-geoscience-20260924.json")
    import_verified_jobs(database, pipeline, snapshot_path)
    job = database.list_jobs(page_size=None, as_of_date="2026-10-10")[0][0]

    response = app.test_client().get(f"/jobs/{job['id']}")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "中国石油详情接口当前返回空字段" in body
    assert "打开中国石油官方招聘入口" in body
    assert "官方详情编号" in body


def test_non_cnpc_detail_page_does_not_use_cnpc_application_fallback(tmp_path) -> None:
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
    pipeline = JobPipeline(settings, database)
    posting = RawPosting(
        title="地质工程师",
        employer="测试官方研究院",
        source_url="https://official.example.gov.cn/notices/1",
        application_url=None,
        text="官方公告要求将材料发送至 hr@example.gov.cn。",
        summary="官方公告岗位。",
        published_date="2026-10-20",
        deadline_date="2026-12-31",
        location="北京",
        field_evidence={
            "evidence_scope": "official_html_table_row",
            "岗位": "地质工程师",
            "专业范围": "地质工程",
            "学历要求": "硕士",
            "官方报名方式": "将材料发送至 hr@example.gov.cn",
        },
    )
    job_id, _ = database.save_job(
        pipeline.normalize_posting(posting, database.get_source("official-manual-import"))
    )

    body = app.test_client().get(f"/jobs/{job_id}").get_data(as_text=True)

    assert "打开中国石油官方招聘入口" not in body
    assert "官方报名方式" in body
    assert "hr@example.gov.cn" in body


def test_cnpc_matrix_is_admin_only_and_binds_snapshot_rows(tmp_path) -> None:
    settings = make_settings(tmp_path)
    app = create_app(settings)
    client = app.test_client()

    assert client.get("/api/admin/cnpc-matrix").status_code == 403
    response = client.get(
        "/api/admin/cnpc-matrix",
        headers={"X-Admin-Token": settings.admin_token},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["summary"]["unit_count"] == 13
    assert payload["summary"]["snapshot_rows_bound"] == 20
    assert payload["summary"]["snapshot_contract_passed"] is True


def test_cnpc_browser_capture_is_admin_only_and_never_publishes_index_rows(tmp_path) -> None:
    app = create_app(make_settings(tmp_path))
    client = app.test_client()

    assert client.get("/api/admin/cnpc-browser-capture").status_code == 403
    response = client.get(
        "/api/admin/cnpc-browser-capture",
        headers={"X-Admin-Token": "test-admin-token"},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["summary"]["pages_scanned"] == 13
    assert payload["summary"]["announcements_discovered"] == 122
    assert payload["summary"]["publishable_job_rows"] == 0
    assert payload["source_policy"]["index_is_job"] is False


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
    assert private_payload["summary"]["record_count"] == 13
    assert len(private_payload["items"]) == 4
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


def test_source_task_queue_is_admin_only(tmp_path) -> None:
    app = create_app(make_settings(tmp_path))
    client = app.test_client()

    assert client.get("/api/admin/source-tasks").status_code == 403
    response = client.get(
        "/api/admin/source-tasks?due_only=true",
        headers={"X-Admin-Token": "test-admin-token"},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["summary"]["due_only"] is True
    assert isinstance(payload["summary"]["by_status"], dict)


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
        field_evidence={
            "evidence_scope": "official_html_table_row",
            "岗位": "地质工程师招聘",
            "专业范围": "地质工程",
            "学历要求": "硕士",
        },
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


def test_student_endpoints_never_expose_out_of_scope_official_job(tmp_path) -> None:
    settings = make_settings(tmp_path)
    app = create_app(settings)
    database = app.extensions["database"]
    pipeline = app.extensions["pipeline"]
    database.upsert_source(source())
    posting = RawPosting(
        title="炼化设备技术",
        employer="中国石油某炼化分公司",
        source_url="https://careers.example.edu.cn/jobs/refinery-equipment",
        application_url=None,
        text="官方招聘岗位。",
        summary="炼化设备岗位。",
        published_date="2026-09-20",
        deadline_date="2026-12-31",
        location="呼和浩特",
        field_evidence={
            "evidence_scope": "official_html_table_row",
            "岗位": "炼化设备技术",
            "专业范围": "过程装备与控制工程、机械工程、电气工程及其自动化",
            "学历要求": "本科、硕士研究生",
        },
    )
    job_id, _ = database.save_job(
        pipeline.normalize_posting(posting, database.get_source("official-test-source"))
    )

    client = app.test_client()
    assert client.get("/api/jobs").get_json()["total"] == 0
    assert "炼化设备技术" not in client.get("/jobs").get_data(as_text=True)
    assert client.get(f"/jobs/{job_id}").status_code == 404
    assert database.find_job(job_id)["publication_status"] == "out_of_scope"
