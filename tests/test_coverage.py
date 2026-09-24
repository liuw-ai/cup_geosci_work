from __future__ import annotations

import job_hub.coverage as coverage_module
from job_hub.coverage import build_coverage_report
from job_hub.db import Database
from job_hub.pipeline import JobPipeline
from job_hub.sources import RawPosting

from conftest import make_settings, source


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
