from __future__ import annotations

from scripts.audit_phase4_third_party_funnel import DATABASE, audit_database


def test_current_third_party_funnel_has_no_published_evidence_gaps() -> None:
    report = audit_database(DATABASE)

    assert report["database"]["integrity_check"] == "ok"
    assert report["summary"]["candidate_leads"] == 6
    assert report["summary"]["published_leads"] == 5
    assert report["summary"]["failed_leads"] == 0
    assert report["summary"]["audit_status"] == "pass"
