from __future__ import annotations

from pathlib import Path

from scripts.audit_phase4_third_party_formal_jobs import build_report


def test_formal_third_party_jobs_have_official_evidence_and_clean_application_links() -> None:
    report = build_report(Path("runtime/job_hub.sqlite3"))

    assert report["status"] == "pass", report["violations"]
    assert report["database"]["formal_third_party_job_count"] == 8
    assert report["source_counts"] == {
        "geotalent-discovery": 1,
        "huatu-discovery": 7,
    }
    assert report["violations"] == []
