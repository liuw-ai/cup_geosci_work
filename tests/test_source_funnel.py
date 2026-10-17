from __future__ import annotations

from datetime import datetime, timezone

from job_hub.source_funnel import build_source_funnel


def _source(
    source_id: str,
    *,
    enabled: bool = True,
    config: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "id": source_id,
        "name": source_id,
        "enabled": enabled,
        "config": config or {},
    }


def _job(source_id: str, *, domestic: bool = True) -> dict[str, object]:
    return {
        "source_id": source_id,
        "status": "open",
        "publication_status": "student_eligible",
        "province": "北京" if domestic else None,
        "country_or_region": "中国大陆" if domestic else "加拿大",
    }


def test_funnel_keeps_access_limited_distinct_from_no_match() -> None:
    report = build_source_funnel(
        sources=[_source("active"), _source("limited"), _source("unseen")],
        jobs=[_job("active"), _job("limited"), {**_job("unseen"), "publication_status": "pending_evidence"}],
        crawl_runs=[
            {
                "id": 2,
                "source_id": "active",
                "status": "finished",
                "discovered_count": 12,
                "evidence_complete_count": 8,
                "manual_review_count": 3,
                "open_matching_count": 2,
            }
        ],
        health_by_id={"limited": {"status": "source_blocked"}},
        source_tasks=[{"source_id": "limited", "status": "blocked", "attempts": 2}],
        artifact_status_counts=[],
        target_plan={"segments": [{"id": "energy", "source_ids": ["active", "limited"]}]},
    )

    rows = {row["source_id"]: row for row in report["rows"]}
    assert rows["active"]["stage"] == "scanned_with_matches"
    assert rows["active"]["published_domestic_jobs"] == 1
    assert rows["limited"]["stage"] == "access_limited"
    assert rows["limited"]["recommended_next_action"] == "monitor_policy_or_use_registered_official_alternate"
    assert rows["unseen"]["stage"] == "not_scanned"
    assert report["summary"]["latest_discovered_candidates"] == 12
    assert report["summary"]["current_domestic_jobs"] == 2


def test_funnel_reports_growth_stages_without_turning_missing_metrics_into_zero() -> None:
    report = build_source_funnel(
        sources=[_source("instrumented"), _source("legacy")],
        jobs=[_job("instrumented")],
        crawl_runs=[
            {
                "id": 1,
                "source_id": "instrumented",
                "status": "finished",
                "discovered_count": 4,
                "open_matching_count": 1,
                "metadata": {
                    "growth_counts": {
                        "version": 1,
                        "official_url_count": 3,
                        "field_complete_count": 2,
                        "precise_major_pass_count": 1,
                        "public_broad_pass_count": 0,
                        "unrestricted_major_pass_count": 0,
                    }
                },
            },
            {
                "id": 2,
                "source_id": "legacy",
                "status": "finished",
                "discovered_count": 4,
                "open_matching_count": 1,
            },
        ],
        health_by_id={},
        source_tasks=[],
        artifact_status_counts=[],
    )
    rows = {row["source_id"]: row for row in report["rows"]}
    assert rows["instrumented"]["growth_metrics"]["precise_major_pass_count"] == 1
    assert rows["legacy"]["growth_metrics"]["instrumented"] is False
    assert rows["legacy"]["growth_metrics"]["precise_major_pass_count"] is None
    assert report["summary"]["latest_precise_major_pass_candidates"] == 1
    assert report["summary"]["metric_contract_incomplete_source_ids"] == [
        "instrumented",
        "legacy",
    ]
    assert report["summary"]["growth_uninstrumented_source_ids"] == ["legacy"]


def test_funnel_uses_only_the_latest_run_and_keeps_disabled_sources_out_of_work_queue() -> None:
    report = build_source_funnel(
        sources=[_source("source-a", enabled=False), _source("source-b")],
        jobs=[_job("source-b", domestic=False)],
        crawl_runs=[
            {"id": 1, "source_id": "source-b", "status": "finished", "discovered_count": 100},
            {"id": 2, "source_id": "source-b", "status": "finished", "discovered_count": 3},
        ],
        health_by_id={},
        source_tasks=[],
        artifact_status_counts=[{"source_id": "source-b", "total_count": 2, "extracted_count": 1, "failed_count": 1}],
    )

    rows = {row["source_id"]: row for row in report["rows"]}
    assert rows["source-a"]["stage"] == "disabled"
    assert rows["source-b"]["latest_run"]["discovered_count"] == 3
    assert rows["source-b"]["published_domestic_jobs"] == 0
    assert rows["source-b"]["artifacts"] == {
        "total_count": 2,
        "extracted_count": 1,
        "failed_count": 1,
    }


def test_funnel_keeps_a_current_run_out_of_failure_counts() -> None:
    report = build_source_funnel(
        sources=[_source("slow-browser")],
        jobs=[],
        crawl_runs=[{"id": 3, "source_id": "slow-browser", "status": "running"}],
        health_by_id={},
        source_tasks=[{"source_id": "slow-browser", "status": "running"}],
        artifact_status_counts=[],
    )

    assert report["rows"][0]["stage"] == "running"
    assert report["summary"]["running_sources"] == 1
    assert report["summary"]["failed_sources"] == 0


def test_funnel_distinguishes_background_capture_from_a_retired_source() -> None:
    report = build_source_funnel(
        sources=[
            _source(
                "background",
                enabled=False,
                config={"background_capture_enabled": True},
            ),
            _source("retired", enabled=False),
        ],
        jobs=[],
        crawl_runs=[],
        health_by_id={},
        source_tasks=[],
        artifact_status_counts=[],
    )

    rows = {row["source_id"]: row for row in report["rows"]}
    assert rows["background"]["stage"] == "background_capture"
    assert (
        rows["background"]["recommended_next_action"]
        == "wait_for_complete_background_capture_before_publication"
    )
    assert rows["retired"]["stage"] == "disabled"
    assert report["summary"]["background_capture_sources"] == 1


def test_funnel_reports_a_policy_limited_background_capture() -> None:
    report = build_source_funnel(
        sources=[
            _source(
                "background",
                enabled=False,
                config={"background_capture_enabled": True},
            )
        ],
        jobs=[],
        crawl_runs=[],
        health_by_id={
            "background": {
                "status": "source_degraded",
                "detail": "robots.txt returned HTTP 403",
            }
        },
        source_tasks=[],
        artifact_status_counts=[],
    )

    assert report["rows"][0]["stage"] == "access_limited"


def test_funnel_marks_non_comparable_open_matches_for_metric_repair() -> None:
    report = build_source_funnel(
        sources=[_source("legacy-adapter")],
        jobs=[_job("legacy-adapter")],
        crawl_runs=[
            {
                "id": 4,
                "source_id": "legacy-adapter",
                "status": "finished",
                "discovered_count": 4,
                "evidence_complete_count": 1,
                "manual_review_count": 0,
                "open_matching_count": 2,
            }
        ],
        health_by_id={},
        source_tasks=[],
        artifact_status_counts=[],
    )

    row = report["rows"][0]
    assert row["metric_contract"]["status"] == "incomplete"
    assert row["recommended_next_action"] == "normalize_adapter_funnel_metrics_before_expansion"
    assert report["summary"]["metric_contract_incomplete_sources"] == 1


def test_funnel_does_not_call_a_stale_capture_a_current_match() -> None:
    source_id = "stale-browser"
    report = build_source_funnel(
        sources=[_source(source_id, config={"max_age_hours": 30})],
        jobs=[],
        crawl_runs=[
            {
                "id": 8,
                "source_id": source_id,
                "status": "finished",
                "discovered_count": 181,
                "evidence_complete_count": 56,
                "manual_review_count": 34,
                "open_matching_count": 56,
            }
        ],
        health_by_id={},
        source_tasks=[],
        artifact_status_counts=[],
        capture_freshness_by_id={
            source_id: {
                "captured_at": "2026-10-06T01:55:30Z",
                "recorded_at": "2026-10-07T07:55:16Z",
            }
        },
        now=datetime(2026, 10, 7, 8, 0, tzinfo=timezone.utc),
    )

    row = report["rows"][0]
    assert row["capture_freshness"]["status"] == "stale"
    assert row["stage"] == "stale_capture"
    assert row["latest_run"]["open_matching_count"] == 56
    assert row["published_domestic_jobs"] == 0
    assert row["recommended_next_action"] == "refresh_complete_capture_before_counting_matches"
    assert report["summary"]["stale_capture_sources"] == 1


def test_funnel_keeps_capture_staleness_visible_when_access_limit_masks_stage() -> None:
    source_id = "limited-stale-source"
    report = build_source_funnel(
        sources=[_source(source_id, config={"max_age_hours": 30})],
        jobs=[],
        crawl_runs=[
            {
                "id": 11,
                "source_id": source_id,
                "status": "finished",
                "discovered_count": 2,
                "evidence_complete_count": 1,
                "manual_review_count": 0,
                "open_matching_count": 0,
            }
        ],
        health_by_id={
            source_id: {
                "status": "source_degraded",
                "detail": "robots.txt returned HTTP 403",
            }
        },
        source_tasks=[],
        artifact_status_counts=[],
        capture_freshness_by_id={
            source_id: {"captured_at": "2026-10-06T01:55:30Z"}
        },
        now=datetime(2026, 10, 7, 8, 0, tzinfo=timezone.utc),
    )

    assert report["rows"][0]["stage"] == "access_limited"
    assert report["summary"]["stale_capture_sources"] == 1
    assert report["summary"]["stage_counts"]["access_limited"] == 1
    assert report["summary"]["capture_freshness_status_counts"]["stale"] == 1


def test_funnel_exposes_enabled_only_freshness_counts_for_readiness_reconciliation() -> None:
    report = build_source_funnel(
        sources=[
            _source("enabled-stale", config={"max_age_hours": 1}),
            _source("disabled-stale", enabled=False, config={"max_age_hours": 1}),
        ],
        jobs=[],
        crawl_runs=[],
        health_by_id={},
        source_tasks=[],
        artifact_status_counts=[],
        capture_freshness_by_id={
            "enabled-stale": {"captured_at": "2026-10-01T00:00:00Z"},
            "disabled-stale": {"captured_at": "2026-10-01T00:00:00Z"},
        },
        now=datetime(2026, 10, 7, 8, 0, tzinfo=timezone.utc),
    )

    summary = report["summary"]
    assert summary["stale_capture_sources"] == 2
    assert summary["enabled_stale_capture_sources"] == 1
    assert summary["enabled_capture_freshness_status_counts"]["stale"] == 1


def test_funnel_treats_freshness_boundary_as_inclusive() -> None:
    source_id = "boundary-browser"
    source_record = _source(source_id, config={"max_age_hours": 30})
    run = {
        "id": 9,
        "source_id": source_id,
        "status": "finished",
        "discovered_count": 1,
        "evidence_complete_count": 1,
        "manual_review_count": 0,
        "open_matching_count": 1,
    }
    capture = {source_id: {"captured_at": "2026-10-06T02:00:00Z"}}
    fresh = build_source_funnel(
        sources=[source_record],
        jobs=[_job(source_id)],
        crawl_runs=[run],
        health_by_id={},
        source_tasks=[],
        artifact_status_counts=[],
        capture_freshness_by_id=capture,
        now=datetime(2026, 10, 7, 8, 0, tzinfo=timezone.utc),
    )
    stale = build_source_funnel(
        sources=[source_record],
        jobs=[_job(source_id)],
        crawl_runs=[run],
        health_by_id={},
        source_tasks=[],
        artifact_status_counts=[],
        capture_freshness_by_id=capture,
        now=datetime(2026, 10, 7, 8, 0, 1, tzinfo=timezone.utc),
    )
    assert fresh["rows"][0]["capture_freshness"]["status"] == "fresh"
    assert fresh["rows"][0]["stage"] == "scanned_with_matches"
    assert stale["rows"][0]["capture_freshness"]["status"] == "stale"
    assert stale["rows"][0]["stage"] == "stale_capture"


def test_funnel_marks_missing_capture_proof_without_claiming_no_jobs() -> None:
    source_id = "unproven-browser"
    report = build_source_funnel(
        sources=[_source(source_id, config={"max_age_hours": 30})],
        jobs=[],
        crawl_runs=[
            {
                "id": 10,
                "source_id": source_id,
                "status": "finished",
                "discovered_count": 1,
                "evidence_complete_count": 1,
                "manual_review_count": 0,
                "open_matching_count": 1,
            }
        ],
        health_by_id={},
        source_tasks=[],
        artifact_status_counts=[],
        now=datetime(2026, 10, 7, 8, 0, tzinfo=timezone.utc),
    )
    row = report["rows"][0]
    assert row["capture_freshness"]["status"] == "unproven"
    assert row["stage"] == "freshness_unproven"
    assert row["recommended_next_action"] == "record_complete_capture_timestamp_before_publication"
