from job_hub.source_lifecycle import lifecycle_report, project_source_lifecycle


def test_lifecycle_distinguishes_partial_and_complete_empty_scan() -> None:
    source = {"id": "s", "enabled": True, "config": {}}
    assert project_source_lifecycle(
        source,
        health={"status": "source_active"},
        latest_run={"status": "finished", "outcome": "partial"},
    ) == "partial"
    assert project_source_lifecycle(
        source,
        health={"status": "source_active"},
        latest_run={"status": "finished", "outcome": "complete", "open_matching_count": 0},
    ) == "complete"


def test_lifecycle_never_treats_access_failure_as_no_jobs() -> None:
    source = {"id": "s", "enabled": True, "config": {}}
    assert project_source_lifecycle(
        source,
        health={"status": "source_blocked", "detail": "robots blocked"},
        latest_run={"status": "failed"},
    ) == "access_limited"


def test_recovered_active_source_is_not_classified_from_old_detail_text() -> None:
    source = {"id": "s", "enabled": True, "config": {}}
    assert project_source_lifecycle(
        source,
        health={"status": "source_active", "detail": "maintenance window completed"},
        latest_run={"status": "finished", "outcome": "complete", "open_matching_count": 0},
    ) == "complete"


def test_lifecycle_report_keeps_retired_sources_auditable() -> None:
    report = lifecycle_report(
        [
            {"id": "old", "enabled": False, "config": {"automation_status": "retired_legacy"}},
            {"id": "new", "enabled": True, "config": {}},
        ],
        health_by_id={},
        latest_runs_by_id={},
    )
    assert report["states"] == {"registered": 1, "withdrawn": 1}
