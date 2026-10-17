from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

import job_hub.coverage as coverage_module
from job_hub.coverage import build_coverage_report
from job_hub.db import Database
from job_hub.pipeline import JobPipeline
from job_hub.sources import RawPosting

from conftest import make_settings, source


def test_industry_mix_uses_current_five_hundred_target_plan() -> None:
    report = coverage_module._industry_mix(
        Counter({"three_oil": 67, "other_domestic": 106}),
        173,
        three_oil_group_counts=Counter({"cnooc": 50, "cnpc": 17}),
    )

    assert report["target_counts"]["total_jobs"] == 500
    assert report["target_counts"]["planned_three_oil_jobs"] == 360
    assert report["target_counts"]["three_oil_min"] == 350
    assert report["target_counts"]["three_oil_max"] == 375
    assert report["three_oil_group_counts"] == {
        "cnooc": 50,
        "cnpc": 17,
        "sinopec": 0,
    }


def test_three_oil_group_counts_exposes_unresolved_rows() -> None:
    jobs = [
        {
            "employer": "中国海洋石油集团有限公司",
            "source_id": "unknown-source",
            "affiliation": "中国海油体系",
            "country_or_region": "中国大陆",
        },
        {
            "employer": "简称岗位单位",
            "source_id": "cnpc-career",
            "country_or_region": "中国大陆",
        },
    ]
    counts = coverage_module._three_oil_group_counts(jobs)
    assert counts == {"cnpc": 1, "sinopec": 0, "cnooc": 1}


def test_three_oil_group_counts_rejects_conflicting_dedicated_source() -> None:
    jobs = [
        {
            "employer": "国家石油天然气管网集团有限公司",
            "source_id": "cnpc-career",
            "country_or_region": "中国大陆",
        },
        {
            "employer": "中国石化胜利油田分公司",
            "source_id": "cnpc-career",
            "country_or_region": "中国大陆",
        },
    ]
    assert coverage_module._three_oil_group_counts(jobs) == {
        "cnpc": 0,
        "sinopec": 1,
        "cnooc": 0,
    }


def test_current_job_metrics_separates_public_counts_from_pending_rows() -> None:
    exact = {
        "source_id": "cnpc-career",
        "employer": "\u4e2d\u56fd\u77f3\u6cb9\u4e1c\u65b9\u7269\u63a2",
        "affiliation": "\u4e2d\u56fd\u77f3\u6cb9\u4f53\u7cfb",
        "category": "\u6cb9\u6c14\u5de5\u7a0b\u6280\u672f\u670d\u52a1",
        "country_or_region": "\u4e2d\u56fd\u5927\u9646",
        "publication_status": "student_eligible",
        "deadline_date": "2026-12-31",
        "title": "\u5730\u8d28\u5de5\u7a0b\u5e08",
        "location": "\u5317\u4eac",
        "field_evidence": {
            "evidence_scope": "official_html_table_row",
            "\u5c97\u4f4d": "\u5730\u8d28\u5de5\u7a0b\u5e08",
            "\u4e13\u4e1a\u8303\u56f4": "\u5730\u8d28\u5de5\u7a0b",
            "\u5b66\u5386\u8981\u6c42": "\u7855\u58eb\u7814\u7a76\u751f",
        },
    }
    pending = {
        **exact,
        "source_id": "candidate-source",
        "publication_status": "pending_evidence",
        "category": "\u80fd\u6e90\u3001\u5de5\u7a0b\u4e0e\u5730\u5b66\u62d3\u5c55",
        "field_evidence": {
            "evidence_scope": "official_html_table_row",
            "\u5c97\u4f4d": "\u77ff\u4ea7\u52d8\u67e5\u5c97",
            "\u4e13\u4e1a\u8303\u56f4": "\u77ff\u4ea7\u52d8\u67e5",
            "\u5b66\u5386\u8981\u6c42": "\u7855\u58eb\u7814\u7a76\u751f",
        },
        "title": "\u77ff\u4ea7\u52d8\u67e5\u5c97",
    }
    broad = {
        **exact,
        "source_id": "government-source",
        "employer": "\u67d0\u81ea\u7136\u8d44\u6e90\u5c40",
        "affiliation": "\u653f\u5e9c\u62db\u5f55\u4f53\u7cfb",
        "category": "\u516c\u52a1\u5458\u4e0e\u9009\u8c03",
        "title": "\u81ea\u7136\u8d44\u6e90\u7ba1\u7406\u5c97",
        "field_evidence": {
            "evidence_scope": "official_html_table_row",
            "\u5c97\u4f4d": "\u81ea\u7136\u8d44\u6e90\u7ba1\u7406\u5c97",
            "\u4e13\u4e1a\u8303\u56f4": "\u5de5\u5b66\u5927\u7c7b",
            "\u5b66\u5386\u8981\u6c42": "\u7855\u58eb\u7814\u7a76\u751f",
            "\u5de5\u4f5c\u5730\u70b9": "\u5317\u4eac",
        },
    }
    report = coverage_module._current_job_metrics(
        [exact, broad], [exact, pending, broad], as_of_date="2026-10-12"
    )
    assert report["current_valid_jobs"] == 2
    assert report["three_oil_jobs"] == 1
    assert report["exact_match_jobs"] == 1
    assert report["broad_match_jobs"] == 1
    assert report["government_jobs"] == 1
    assert report["domestic_enterprise_jobs"] == 1
    assert report["pending_evidence_jobs"] == 1
    assert report["pending_evidence_by_match_level"]["similar"] == 1


def snapshot_report(
    *,
    open_jobs: int,
    explicit: int,
    review: int,
    matched_students: int,
    successful_sources: int,
    sources_with_open_matches: int,
    source_top_share: float,
    category_top_share: float,
) -> dict[str, object]:
    """Minimal payload accepted by the compact snapshot persistence API."""
    return {
        "open_jobs": open_jobs,
        "profile_match_quality": {
            "cohort_summary": {
                "explicit_job_profile_matches": explicit,
                "review_job_profile_matches": review,
                "students_with_explicit_match": matched_students,
            }
        },
        "scan_quality": {
            "successful_scan_sources": successful_sources,
            "sources_with_open_matches": sources_with_open_matches,
        },
        "job_distribution": {
            "source_concentration": {"top_share": source_top_share},
            "category_concentration": {"top_share": category_top_share},
        },
    }


def test_coverage_distinguishes_successful_no_match_from_source_failure(tmp_path) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    shandong_source = source()
    shandong_source["config"] = {
        "province": "山东",
        "source_role": "natural_resources",
        "fallback_source_ids": ["mnr-public-recruitment"],
        "target_active_sources": 3,
    }
    database.upsert_source(shandong_source)
    database.upsert_source(
        {
            **source(),
            "id": "mnr-public-recruitment",
            "name": "全国自然资源公开招聘",
            "enabled": False,
        }
    )
    database.record_source_health(
        "official-test-source",
        status="source_active",
        detail="公开入口可访问。",
        successful=True,
    )
    database.mark_source_synced("official-test-source")
    run_id = database.record_crawl_start("official-test-source")
    database.record_crawl_finish(
        run_id,
        "finished",
        discovered_count=1,
        open_matching_count=1,
        inserted_count=1,
    )
    pipeline = JobPipeline(settings, database)
    posting = RawPosting(
        title="东营地质工程师招聘",
        employer="测试能源集团",
        source_url="https://careers.example.edu.cn/jobs/shandong",
        application_url=None,
        text="面向资源勘查工程本科、地质工程硕士毕业生，报名截止时间为2026年12月31日。",
        summary="山东地学岗位。",
            published_date="2026-09-18",
            deadline_date="2026-12-31",
            location="山东省东营市",
            field_evidence={
                "evidence_scope": "official_html_table_row",
                "岗位": "东营地质工程师招聘",
                "专业范围": "资源勘查工程本科、地质工程硕士",
                "学历要求": "本科、硕士",
            },
        )
    database.save_job(
        pipeline.normalize_posting(posting, database.get_source("official-test-source"))
    )

    report = build_coverage_report(database)
    shandong = next(
        item for item in report["province_coverage"] if item["province"] == "山东"
    )

    assert shandong["active_official_sources"] == 1
    assert shandong["completed_scan_sources"] == 1
    assert shandong["fallback_sources_registered"] == 1
    assert shandong["published_open_jobs"] == 1
    assert shandong["missing_source_roles"]
    assert report["field_completeness"]["official_evidence_url"]["rate"] == 1.0
    assert "location_quality" in report
    assert "deadline_quality" in report
    assert report["source_target_matrix"]["province_count"] == 31
    assert report["quality_gate"]["status"] in {"pass", "needs_attention"}
    assert "employer_concentration" in report["job_distribution"]
    assert report["job_distribution"]["source_concentration"]["risk"] == "high"
    assert (
        report["job_distribution"]["category_source_concentration"]
        ["油气上游业主与研究机构"]["risk"]
        == "no_data"
    )
    assert report["source_resilience"]["sources_with_backup"] == 1
    assert (
        report["profile_match_quality"]["cohort_summary"]
        ["students_with_explicit_match"]
        > 0
    )


def test_coverage_never_interprets_a_failed_latest_scan_as_no_opening(tmp_path) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    provincial_source = source()
    provincial_source["config"] = {
        "province": "山东",
        "source_role": "natural_resources",
        "fallback_source_ids": ["mnr-public-recruitment"],
        "target_active_sources": 3,
    }
    database.upsert_source(provincial_source)
    database.record_source_health(
        "official-test-source",
        status="source_error",
        detail="测试网络异常。",
    )
    run_id = database.record_crawl_start("official-test-source")
    database.record_crawl_finish(run_id, "failed", error_message="测试网络异常")

    report = build_coverage_report(database)
    shandong = next(
        item for item in report["province_coverage"] if item["province"] == "山东"
    )

    assert shandong["completed_scan_sources"] == 0
    assert shandong["no_matching_opening_sources"] == 0
    assert "不能把零岗位解释为无招聘" in shandong["scan_interpretation"]


def test_coverage_separates_retired_source_errors_from_active_health(tmp_path) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    database.upsert_source(source())
    database.upsert_source(
        {
            **source(),
            "id": "retired-source",
            "name": "已停用旧适配器",
            "enabled": False,
            "config": {
                "automation_status": "retired_legacy_api",
                "replacement_source_id": "official-test-source",
                "retired_reason": "已由详情浏览器采集接管。",
            },
        }
    )
    database.record_source_health(
        "official-test-source", status="source_active", detail="当前可用。", successful=True
    )
    database.record_source_health(
        "retired-source", status="source_error", detail="历史接口失败。"
    )

    report = build_coverage_report(database)
    health = report["source_health"]

    assert health["enabled_distribution"] == {"source_active": 1}
    assert [item["id"] for item in health["unhealthy_sources"]] == []
    assert health["disabled_sources"] == [
        {
            "id": "retired-source",
            "name": "已停用旧适配器",
            "status": "source_error",
            "replacement_source_id": "official-test-source",
            "retired_reason": "已由详情浏览器采集接管。",
        }
    ]


def test_coverage_reports_degraded_browser_worker_and_downgrades_lifecycle(tmp_path) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    browser_source = source()
    browser_source.update(
        {
            "id": "cnooc-career-browser",
            "name": "测试海油浏览器来源",
            "source_type": "cnooc_browser_rows",
        }
    )
    database.upsert_source(browser_source)
    database.record_source_health(
        "cnooc-career-browser", status="source_active", successful=True
    )
    run_id = database.record_crawl_start("cnooc-career-browser")
    database.record_crawl_finish(
        run_id,
        "finished",
        outcome="complete",
        open_matching_count=4,
    )
    database.record_service_heartbeat(
        "cnooc-browser", "degraded", "browser capture timeout"
    )

    report = build_coverage_report(database)

    assert any(
        item["id"] == "cnooc-career-browser"
        for item in report["source_health"]["runtime_unhealthy_sources"]
    )
    assert any(
        item["id"] == "cnooc-career-browser"
        for item in report["source_health"]["unhealthy_sources"]
    )
    assert report["source_lifecycle"]["states"]["source_error"] == 1


def test_concentration_risk_flags_top_five_source_monopoly() -> None:
    metric = coverage_module._concentration(
        coverage_module.Counter({"a": 3, "b": 2, "c": 2, "d": 2, "e": 2, "f": 1}),
        12,
    )

    assert metric["top_share"] == 0.25
    assert metric["top_5_share"] == 0.9167
    assert metric["risk"] == "high"


def test_public_job_reads_apply_deadline_gate_before_worker_expiry(tmp_path) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    registered_source = source()
    database.upsert_source(registered_source)
    pipeline = JobPipeline(settings, database)
    posting = RawPosting(
        title="地质工程师",
        employer="测试地质单位",
        source_url="https://careers.example.edu.cn/jobs/deadline-read-gate",
        application_url=None,
        text="资源勘查工程本科，报名截止时间为2026年10月5日。",
        summary="官方岗位公告。",
        published_date="2026-09-01",
        deadline_date="2026-10-05",
        location="北京市",
        field_evidence={
            "evidence_scope": "official_html_table_row",
            "岗位": "地质工程师",
            "专业范围": "资源勘查工程",
            "学历要求": "本科",
        },
    )
    job_id, _ = database.save_job(
        pipeline.normalize_posting(posting, database.get_source("official-test-source"))
    )

    assert database.list_jobs(
        page_size=None, as_of_date="2026-10-04"
    )[1] == 1
    assert database.list_jobs(
        page_size=None, as_of_date="2026-10-06"
    )[1] == 0
    assert database.find_public_job(job_id, as_of_date="2026-10-06") is None
    assert database.count_open_jobs(as_of_date="2026-10-06") == 0


def test_coverage_report_uses_same_deadline_gate_as_student_reads(tmp_path) -> None:
    """Coverage totals must never include rows hidden from the student API."""
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    database.upsert_source(source())
    pipeline = JobPipeline(settings, database)
    posting = RawPosting(
        title="已截止地质岗位",
        employer="测试地质单位",
        source_url="https://careers.example.edu.cn/jobs/expired-coverage",
        application_url=None,
        text="资源勘查工程本科，报名截止时间为2026年10月5日。",
        summary="官方岗位公告。",
        published_date="2026-09-01",
        deadline_date="2026-10-05",
        location="北京市",
        field_evidence={
            "evidence_scope": "official_html_table_row",
            "岗位": "已截止地质岗位",
            "专业范围": "资源勘查工程",
            "学历要求": "本科",
        },
    )
    database.save_job(
        pipeline.normalize_posting(posting, database.get_source("official-test-source"))
    )

    report = build_coverage_report(database, snapshot_date="2026-10-06")

    assert database.count_open_jobs(as_of_date="2026-10-06") == 0
    assert report["open_jobs"] == 0
    assert report["domestic_open_jobs"] == 0


def test_coverage_report_passes_capture_freshness_to_nested_source_funnel(tmp_path) -> None:
    """The coverage report and standalone funnel must share freshness state."""
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    configured_source = source()
    configured_source["config"] = {
        **configured_source["config"],
        "max_age_hours": 24,
    }
    database.upsert_source(configured_source)
    database.record_source_capture_freshness(
        "official-test-source",
        captured_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    )

    report = build_coverage_report(database)

    nested_funnel = report["source_funnel"]
    assert nested_funnel["summary"]["freshness_unproven_sources"] == 0


def test_public_job_reads_apply_undated_source_window_before_worker_expiry(tmp_path) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    configured_source = source()
    configured_source["config"] = {
        "minimum_relevance": 0,
        "undated_open_window_days": 10,
    }
    database.upsert_source(configured_source)
    pipeline = JobPipeline(settings, database)
    posting = RawPosting(
        title="地质调查技术岗",
        employer="测试地质调查单位",
        source_url="https://careers.example.edu.cn/jobs/undated-read-gate",
        application_url=None,
        text="地质学硕士，公告未注明截止日期。",
        summary="官方岗位公告。",
        published_date="2026-09-01",
        deadline_date=None,
        location="北京市",
        field_evidence={
            "evidence_scope": "official_html_table_row",
            "岗位": "地质调查技术岗",
            "专业范围": "地质学",
            "学历要求": "硕士",
        },
    )
    job_id, _ = database.save_job(
        pipeline.normalize_posting(posting, database.get_source("official-test-source"))
    )
    # Simulate a worker outage after a row was published: the lifecycle column
    # is still open even though the source-specific undated window has passed.
    with database.transaction() as connection:
        connection.execute("UPDATE jobs SET status = 'open' WHERE id = ?", (job_id,))

    assert database.list_jobs(page_size=None, as_of_date="2026-09-05")[1] == 1
    assert database.list_jobs(page_size=None, as_of_date="2026-10-02")[1] == 1
    assert database.find_public_job(job_id, as_of_date="2026-10-02") is not None
    assert database.count_open_jobs(as_of_date="2026-10-02") == 1


def test_public_job_reads_treat_empty_deadline_as_undated(tmp_path) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    configured_source = source()
    configured_source["config"] = {
        "minimum_relevance": 0,
        "undated_open_window_days": 10,
    }
    database.upsert_source(configured_source)
    pipeline = JobPipeline(settings, database)
    posting = RawPosting(
        title="地质调查技术岗",
        employer="测试地质调查单位",
        source_url="https://careers.example.edu.cn/jobs/empty-deadline",
        application_url=None,
        text="地质学硕士，公告未注明截止日期。",
        summary="官方岗位公告。",
        published_date="2026-09-01",
        deadline_date=None,
        location="北京市",
        field_evidence={
            "evidence_scope": "official_html_table_row",
            "岗位": "地质调查技术岗",
            "专业范围": "地质学",
            "学历要求": "硕士",
        },
    )
    job_id, _ = database.save_job(
        pipeline.normalize_posting(posting, database.get_source("official-test-source"))
    )
    # Exercise the legacy representation produced by older imports.
    with database.transaction() as connection:
        connection.execute(
            "UPDATE jobs SET deadline_date = '', status = 'open' WHERE id = ?",
            (job_id,),
        )

    assert database.find_public_job(job_id, as_of_date="2026-09-05") is not None
    assert database.find_public_job(job_id, as_of_date="2026-10-02") is not None


def test_snapshot_trend_skips_a_replaceable_current_day_snapshot(tmp_path) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    database.upsert_source(source())

    database.save_coverage_snapshot(
        "2026-09-17",
        snapshot_report(
            open_jobs=2,
            explicit=3,
            review=5,
            matched_students=2,
            successful_sources=1,
            sources_with_open_matches=1,
            source_top_share=0.9,
            category_top_share=0.8,
        ),
    )
    # A same-day record is replaceable after a later source sync. It is not a
    # valid baseline for the current-day view, which must use the prior day.
    database.save_coverage_snapshot(
        "2026-09-18",
        snapshot_report(
            open_jobs=99,
            explicit=99,
            review=99,
            matched_students=99,
            successful_sources=99,
            sources_with_open_matches=99,
            source_top_share=0.1,
            category_top_share=0.1,
        ),
    )

    report = build_coverage_report(database, snapshot_date="2026-09-18")

    trend = report["trend_since_last_snapshot"]
    assert trend["available"] is True
    assert trend["baseline_snapshot_date"] == "2026-09-17"
    assert trend["open_jobs_change"] == -2
    assert (
        report["profile_match_quality"]["trend"]
        ["baseline_snapshot_date"]
        == "2026-09-17"
    )


def test_verified_target_role_without_usable_scan_cannot_prove_no_opening(
    tmp_path,
    monkeypatch,
) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    provincial_source = source()
    provincial_source["config"] = {
        "province": "山东",
        "source_role": "natural_resources",
    }
    database.upsert_source(provincial_source)
    database.upsert_source(
        {
            **source(),
            "id": "disabled-civil-service-source",
            "name": "测试公务员官方入口",
            "enabled": False,
            "config": {},
        }
    )
    database.record_source_health(
        "official-test-source",
        status="source_active",
        detail="公开入口可访问。",
        successful=True,
    )
    run_id = database.record_crawl_start("official-test-source")
    database.record_crawl_finish(
        run_id,
        "finished",
        discovered_count=0,
        open_matching_count=0,
    )
    role_map = {
        role: {"state": "verified", "source_id": "official-test-source"}
        for role in coverage_module.REQUIRED_PROVINCIAL_SOURCE_ROLES
    }
    role_map["civil_service"] = {
        "state": "verified",
        "source_id": "disabled-civil-service-source",
    }
    monkeypatch.setattr(
        coverage_module,
        "load_source_targets",
        lambda: {
            "required_roles": list(coverage_module.REQUIRED_PROVINCIAL_SOURCE_ROLES),
            "province_targets": {"山东": role_map},
        },
    )

    report = build_coverage_report(database)
    shandong = next(
        item for item in report["province_coverage"] if item["province"] == "山东"
    )

    assert shandong["all_enabled_sources_scanned"] is True
    assert shandong["all_target_roles_verified"] is True
    assert shandong["all_verified_target_roles_usable"] is False
    assert shandong["target_roles_without_usable_scan"] == ["civil_service"]
    assert shandong["no_match_is_verified"] is False
    assert "不能把零岗位解释为无招聘" in shandong["scan_interpretation"]
