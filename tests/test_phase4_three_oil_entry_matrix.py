from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
MATRIX = ROOT / "docs" / "current" / "phase4-three-oil-unit-entry-matrix-2026-10-18.json"
FULL_CNOOC_EVIDENCE = ROOT / "docs" / "current" / "phase4-cnooc-unit-job-evidence-2026-10-19.json"
SINOPEC_PAGE_ROSTER = ROOT / "docs" / "current" / "phase4-sinopec-page-roster-2026-10-19.json"


def test_phase4_three_oil_entry_matrix_covers_every_formal_catalog_unit() -> None:
    payload = json.loads(MATRIX.read_text(encoding="utf-8"))
    units = payload["units"]
    assert len(units) == 64
    assert len({item["catalog_id"] for item in units}) == 64
    assert payload["summary"]["units_by_group"] == {"cnpc": 27, "sinopec": 17, "cnooc": 20}
    assert payload["summary"]["unit_specific_entry_evidence_complete"] is False
    assert sum(item["unit_specific_entry_status"] == "unit_specific_entry_verified" for item in units) >= 3
    assert any(item["unit_specific_entry_url"] for item in units)
    assert any(item["unit_specific_entry_status"] == "unit_specific_entry_pending" for item in units)
    cnooc_observed = [item for item in units if item["group_id"] == "cnooc" and item["observed_official_job_count"]]
    assert len(cnooc_observed) == 16
    assert sum(int(item["observed_official_job_count"]) for item in cnooc_observed) == 350
    assert all(item["observed_official_application_url"] for item in cnooc_observed)
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
