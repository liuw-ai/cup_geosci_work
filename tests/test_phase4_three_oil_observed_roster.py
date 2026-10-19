from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "docs" / "current" / "phase4-three-oil-observed-roster-2026-10-20.json"


def test_observed_display_roster_is_separate_from_formal_catalog() -> None:
    payload = json.loads(REPORT.read_text(encoding="utf-8"))
    assert payload["status"] == "observed_roster_not_formal_catalog"
    assert payload["summary"] == {
        "observed_candidate_count": 239,
        "observed_candidate_count_by_group": {"cnpc": 108, "sinopec": 131},
        "formal_catalog_unit_count_excluded": 64,
        "legal_identity_verified_count": 0,
        "unit_specific_entry_verified_count": 0,
        "formal_catalog_additions": 0,
    }
    assert all(
        unit["evidence_boundary"]["legal_name_verified"] is False
        and unit["evidence_boundary"]["unit_specific_recruitment_entry_verified"] is False
        and unit["evidence_boundary"]["eligible_for_formal_catalog"] is False
        for unit in payload["units"]
    )
