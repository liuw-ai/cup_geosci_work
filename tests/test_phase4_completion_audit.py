from __future__ import annotations

from scripts.audit_phase4_completion import build_report


def test_phase4_completion_audit_is_requirement_scoped_and_not_complete() -> None:
    report = build_report()
    statuses = {item["id"]: item["status"] for item in report["requirements"]}

    assert report["status"] == "not_complete"
    assert statuses["phase3_lifecycle_and_withdrawal_policy"] == "pass"
    assert statuses["third_party_table_to_official_recovery_trial"] == "pass"
    assert statuses["third_party_batch_expansion"] == "blocked"
    assert statuses["three_oil_catalog_and_entry_evidence"] == "partial"
    assert statuses["isolated_local_regression"] == "pass"
    assert statuses["student_site_acceptance"] == "pass"
