from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
MATRIX = ROOT / "docs" / "current" / "phase4-three-oil-unit-entry-matrix-2026-10-18.json"


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
