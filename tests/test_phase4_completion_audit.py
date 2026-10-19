from __future__ import annotations

from scripts.audit_phase4_completion import build_report


def test_phase4_completion_audit_is_requirement_scoped_and_not_complete() -> None:
    report = build_report()
    statuses = {item["id"]: item["status"] for item in report["requirements"]}

    assert report["status"] == "not_complete"
    assert statuses["phase3_lifecycle_and_withdrawal_policy"] == "pass"
    assert statuses["third_party_table_to_official_recovery_trial"] == "pass"
    assert statuses["third_party_batch_expansion"] == "blocked"
    assert statuses["third_party_hbdk_geoscience_recovery"] == "partial"
    assert statuses["three_oil_catalog_and_entry_evidence"] == "partial"
    three_oil = next(
        item
        for item in report["requirements"]
        if item["id"] == "three_oil_catalog_and_entry_evidence"
    )
    assert three_oil["facts"]["external_identity_evidence_review"]["verified_unit_count"] == 21
    assert three_oil["facts"]["external_identity_evidence_review"]["pending_unit_count"] == 43
    assert statuses["isolated_local_regression"] == "pass"
    assert statuses["student_site_acceptance"] == "pass"
    regression = next(
        item for item in report["requirements"] if item["id"] == "isolated_local_regression"
    )
    assert regression["facts"]["formal_database_stable"] is True
