from __future__ import annotations

from scripts.audit_phase4_third_party_funnel import DATABASE, audit_database
from scripts.audit_phase4_third_party_funnel import _same_discovery_domain


def test_current_third_party_funnel_keeps_unresolved_leads_out_of_publication() -> None:
    report = audit_database(DATABASE)

    assert report["database"]["integrity_check"] == "ok"
    assert report["summary"]["candidate_leads"] >= 12
    assert report["summary"]["published_leads"] == 9
    assert report["summary"]["unresolved_or_pending_leads"] >= 3
    assert report["summary"]["by_discovery_source"]["huatu-discovery"]["published"] == 7
    assert report["summary"]["by_discovery_source"]["huatu-discovery"]["unresolved_or_pending"] >= 1
    assert report["summary"]["by_discovery_source"]["geotalent-discovery"]["published"] == 1
    assert report["summary"]["by_discovery_source"]["offcn-discovery"]["candidate_leads"] >= 0
    assert report["summary"]["by_discovery_source"]["fenbi-discovery"]["candidate_leads"] == 0


def test_published_leads_keep_discovery_provenance_without_using_it_as_application() -> None:
    report = audit_database(DATABASE)
    published = [row for row in report["rows"] if row["verification_status"] == "published"]

    assert len(published) == 9
    assert all(row["lead_url"].startswith(("http://", "https://")) for row in published)
    assert all(row["discovery_source_id"] for row in published)
    assert all(
        not row.get("official_application_url")
        or row["official_application_url"] != row["lead_url"]
        for row in published
    )


def test_third_party_sibling_subdomain_is_still_discovery_evidence() -> None:
    assert _same_discovery_domain(
        "https://u3.huatu.com/uploads/soft/table.xlsx",
        "https://bj.huatu.com/2026/notice.html",
    )
    assert _same_discovery_domain(
        "https://www.offcn.com/path/apply.html",
        "https://offcn.com/notice.html",
    )
    assert not _same_discovery_domain(
        "https://campus1.zhaopin.com/Resume/CheckIntoApp?pid=1",
        "https://bj.huatu.com/2026/notice.html",
    )
