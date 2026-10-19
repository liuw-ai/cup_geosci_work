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


def test_published_leads_keep_discovery_provenance_without_using_it_as_application() -> None:
    report = audit_database(DATABASE)
    published = [row for row in report["rows"] if row["verification_status"] == "published"]

    assert len(published) == 7
    assert all(row["lead_url"].startswith(("http://", "https://")) for row in published)
    assert all(row["discovery_source_id"] for row in published)
    assert all(
        not row.get("official_application_url")
        or row["official_application_url"] != row["lead_url"]
        for row in published
    )
