from __future__ import annotations

from scripts.audit_phase4_third_party_funnel import DATABASE, audit_database


def test_current_third_party_funnel_has_no_published_evidence_gaps() -> None:
    report = audit_database(DATABASE)

    assert report["database"]["integrity_check"] == "ok"
    assert report["summary"]["candidate_leads"] == 8
    assert report["summary"]["published_leads"] == 7
    assert report["summary"]["failed_leads"] == 0
    assert report["summary"]["audit_status"] == "pass"
    assert report["summary"]["by_discovery_source"]["huatu-discovery"]["published"] == 6
    assert report["summary"]["by_discovery_source"]["geotalent-discovery"]["published"] == 1
    assert report["summary"]["by_discovery_source"]["offcn-discovery"]["candidate_leads"] == 0
    assert report["summary"]["by_discovery_source"]["fenbi-discovery"]["candidate_leads"] == 0
