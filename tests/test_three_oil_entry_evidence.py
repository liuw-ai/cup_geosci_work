from __future__ import annotations

import json

import pytest

from job_hub.three_oil_entry_evidence import (
    ENTRY_EVIDENCE_STATUSES,
    load_three_oil_entry_evidence,
    three_oil_entry_evidence_summary,
    validate_three_oil_entry_evidence,
)
from scripts.enrich_phase6_three_oil_identity_evidence import build_enriched_payload

from conftest import make_settings
from job_hub.app import create_app


def test_current_entry_evidence_covers_every_formal_three_oil_unit() -> None:
    payload = load_three_oil_entry_evidence()
    summary = three_oil_entry_evidence_summary(payload)

    assert summary["unit_count"] == 64
    assert {item["group_id"] for item in payload["units"]} == {
        "cnpc",
        "sinopec",
        "cnooc",
    }
    assert all(
        item["unit_specific_entry_status"] in ENTRY_EVIDENCE_STATUSES
        for item in payload["units"]
    )
    assert "group_portal_verified" not in summary["status_counts"]
    assert summary["unit_specific_entry_verified_count"] == 3
    assert summary["incomplete_entry_evidence_count"] == 61
    assert summary["official_identity_page_count"] == 10
    assert summary["unit_owned_identity_page_count"] == 7


def test_entry_evidence_rejects_shared_portal_mislabeled_as_unit_entry() -> None:
    payload = load_three_oil_entry_evidence()
    mutated = json.loads(json.dumps(payload, ensure_ascii=False))
    candidate = next(
        row
        for row in mutated["units"]
        if row["unit_specific_entry_status"] == "group_portal_registered"
    )
    candidate["unit_specific_entry_url"] = candidate["group_entry_url"]
    with pytest.raises(ValueError, match="non-unit-specific status"):
        validate_three_oil_entry_evidence(mutated)


def test_identity_backfill_uses_only_unit_owned_official_homepages() -> None:
    payload = build_enriched_payload()
    by_id = {item["catalog_id"]: item for item in payload["units"]}

    assert by_id["cnpc-bgp"]["official_identity_url"] == "https://www.bgp.com.cn/"
    assert by_id["cosl"]["official_identity_url"] == "https://www.cosl.com.cn/"
    assert (
        by_id["sinopec-petroleum-exploration-production-research"][
            "official_identity_url"
        ]
        == "https://pepris.sinopec.com/"
    )
    # These regional CNOOC records only inherit the group site. That is not
    # enough to label it as a unit-owned identity page.
    assert by_id["cnooc-hainan"]["official_identity_url"] is None
    assert by_id["cnooc-shanghai"]["official_identity_url"] is None
    assert by_id["cnooc-tianjin"]["official_identity_url"] is None
    assert by_id["cnpc-bgp"]["unit_specific_entry_status"] == "unit_specific_entry_verified"


def test_three_oil_entry_evidence_api_is_private(tmp_path) -> None:
    app = create_app(make_settings(tmp_path))
    client = app.test_client()
    assert client.get("/api/admin/three-oil-entry-evidence").status_code == 403
    response = client.get(
        "/api/admin/three-oil-entry-evidence",
        headers={"X-Admin-Token": "test-admin-token"},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["summary"]["unit_count"] == 64
    assert len(payload["items"]) == 64
