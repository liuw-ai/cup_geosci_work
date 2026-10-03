from __future__ import annotations

from job_hub.source_funnel import build_source_funnel


def _source(source_id: str, *, enabled: bool = True) -> dict[str, object]:
    return {"id": source_id, "name": source_id, "enabled": enabled}


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
