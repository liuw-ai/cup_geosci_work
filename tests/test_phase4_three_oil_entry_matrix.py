from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
MATRIX = ROOT / "docs" / "current" / "phase4-three-oil-unit-entry-matrix-2026-10-18.json"
FULL_CNOOC_EVIDENCE = ROOT / "docs" / "current" / "phase4-cnooc-unit-job-evidence-2026-10-19.json"
SINOPEC_PAGE_ROSTER = ROOT / "docs" / "current" / "phase4-sinopec-page-roster-2026-10-19.json"
COOEC_NEGATIVE_EVIDENCE = ROOT / "docs" / "current" / "phase4-three-oil-cooec-entry-negative-evidence-2026-10-19.json"
UNIT_ENTRY_RECHECK = ROOT / "docs" / "current" / "phase4-three-oil-unit-entry-recheck-2026-10-19.json"
SINOPEC_OFFICIAL_JOB_EVIDENCE = ROOT / "docs" / "current" / "phase4-sinopec-official-job-evidence-2026-10-19.json"


def test_phase4_three_oil_entry_matrix_covers_every_formal_catalog_unit() -> None:
    payload = json.loads(MATRIX.read_text(encoding="utf-8"))
    units = payload["units"]
    assert len(units) == 64
    assert len({item["catalog_id"] for item in units}) == 64
    assert payload["summary"]["units_by_group"] == {"cnpc": 27, "sinopec": 17, "cnooc": 20}
    assert payload["summary"]["unit_specific_entry_evidence_complete"] is False
    assert sum(item["unit_specific_entry_status"] == "unit_specific_entry_verified" for item in units) >= 3
    assert any(item["unit_specific_entry_url"] for item in units)
    assert payload["summary"]["unit_specific_entry_status_counts"]["group_portal_registered"] == 34
    assert not any(item["unit_specific_entry_status"] == "unit_specific_entry_pending" for item in units)
    assert all(
        item["unit_specific_entry_url"] is None
        for item in units
        if item["unit_specific_entry_status"] == "group_portal_registered"
    )
    cnooc_observed = [item for item in units if item["group_id"] == "cnooc" and item["observed_official_job_count"]]
    assert len(cnooc_observed) == 16
    assert sum(int(item["observed_official_job_count"]) for item in cnooc_observed) == 350
    assert all(item["observed_official_application_url"] for item in cnooc_observed)
    cooec = next(item for item in units if item["catalog_id"] == "cnooc-engineering")
    assert cooec["unit_specific_entry_status"] == "official_site_checked_no_unit_entry_found"
    assert cooec["unit_specific_entry_url"] is None
    negative = json.loads(COOEC_NEGATIVE_EVIDENCE.read_text(encoding="utf-8"))
    assert negative["evidence_boundary"]["official_identity_verified"] is True
    assert negative["evidence_boundary"]["unit_specific_recruitment_entry_verified"] is False
    recheck = json.loads(UNIT_ENTRY_RECHECK.read_text(encoding="utf-8"))
    assert recheck["summary"] == {
        "entries_checked": 4,
        "unit_specific_entry_verified": 2,
        "official_homepage_checked_no_unit_entry_found": 2,
        "current_open_position_claims": 0,
        "student_application_urls_created": 0,
    }
    assert {
        item["catalog_id"] for item in recheck["entries"]
    } == {"sinopec-petroleum-exploration-production-research", "cosl"}
    assert {
        item["catalog_id"] for item in recheck["negative_entries"]
    } == {"sinopec-jianghan", "sinopec-shengli"}
    for catalog_id in ("sinopec-jianghan", "sinopec-shengli"):
        row = next(item for item in units if item["catalog_id"] == catalog_id)
        assert row["unit_specific_entry_status"] == "official_site_checked_no_unit_entry_found"
        assert row["unit_specific_entry_url"] is None
    sinopec_displayed_ids = {
        item["catalog_id"]
        for item in units
        if item["group_id"] == "sinopec"
        and item["unit_specific_entry_status"] == "group_portal_unit_display_observed"
    }
    assert {
        "sinopec-zhongyuan",
        "sinopec-henan",
        "sinopec-jiangsu",
        "sinopec-petroleum-engineering-research",
        "sinopec-petroleum-machinery",
        "sinopec-engineering-construction",
        "sinopec-international-exploration",
    } <= sinopec_displayed_ids
    full_evidence = json.loads(FULL_CNOOC_EVIDENCE.read_text(encoding="utf-8"))
    assert full_evidence["unit_count"] == 16
    assert full_evidence["total_job_cards"] == 350
    assert sum(len(unit["jobs"]) for unit in full_evidence["units"]) == 350


def test_phase4_sinopec_page_roster_is_complete_and_not_unit_entry_evidence() -> None:
    payload = json.loads(SINOPEC_PAGE_ROSTER.read_text(encoding="utf-8"))
    records = payload["records"]
    assert payload["source"]["observed_display_total"] == 132
    assert len(records) == 132
    assert payload["verification"]["page_counts"] == {
        "1": 20,
        "2": 20,
        "3": 20,
        "4": 20,
        "5": 20,
        "6": 20,
        "7": 12,
    }
    assert sum(payload["verification"]["page_counts"].values()) == len(records)
    assert sum(item.get("record_type") == "non_unit_program" for item in records) == 1
    assert payload["verification"]["unit_specific_entry_verified_count"] == 0
    assert payload["verification"]["source_is_not_application_link"] is True


def test_phase4_sinopec_display_job_keeps_legal_identity_gate() -> None:
    payload = json.loads(SINOPEC_OFFICIAL_JOB_EVIDENCE.read_text(encoding="utf-8"))
    assert payload["official_source"]["detail_page_is_official"] is True
    assert payload["position"]["employer_displayed"] == "国际石油工程公司"
    assert payload["position"]["deadline"] == "2026-10-28 17:00:00"
    assert payload["identity_boundary"]["legal_name_verified"] is False
    assert payload["publication_decision"]["formal_job_created"] is False
    assert payload["publication_decision"]["student_visible"] is False
