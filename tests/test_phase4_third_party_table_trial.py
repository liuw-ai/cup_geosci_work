from __future__ import annotations

from scripts.audit_phase4_third_party_table_trial import build_report


def test_retained_third_party_table_is_hash_bound_and_current_robots_block_is_separate() -> None:
    report = build_report()

    assert report["status"] == "historical_capture_retained_current_access_blocked"
    capture = report["historical_capture"]
    assert capture["file_exists"] is True
    assert capture["sha256_matches"] is True
    assert capture["parsed_input_rows"] == 12
    funnel = report["historical_funnel"]
    assert funnel["accepted_rows"] == 11
    assert funnel["rejected_rows"] == 1
    assert funnel["formal_jobs_created"] == 0
    current = report["current_network_recheck"]
    assert current["robots_status"] == 200
    assert current["robots_allowed"] is False
    assert current["downloaded"] is False
    assert report["publication_boundary"]["formal_database_written"] is False
