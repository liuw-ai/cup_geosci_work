from __future__ import annotations

import json
from pathlib import Path

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
    assert three_oil["facts"]["external_identity_evidence_review"]["verified_unit_count"] == 24
    assert three_oil["facts"]["external_identity_evidence_review"]["pending_unit_count"] == 40
    assert statuses["isolated_local_regression"] == "pass"
    assert statuses["student_site_acceptance"] == "pass"
    regression = next(
        item for item in report["requirements"] if item["id"] == "isolated_local_regression"
    )
    validation = json.loads(
        Path("docs/current/phase4-local-validation-2026-10-21.json").read_text(
            encoding="utf-8"
        )
    )["tests"]
    assert regression["facts"]["passed"] == validation["passed"]
    assert regression["facts"]["skipped"] == validation["skipped"]
    assert regression["facts"]["failed"] == validation["failed"]
    assert regression["facts"]["formal_database_stable"] is True
    site = next(item for item in report["requirements"] if item["id"] == "student_site_acceptance")
    assert site["facts"]["healthz"] == 200
    assert site["facts"]["home"] == 200
    assert all(value == 200 for value in site["facts"]["layer_api_statuses"].values())
    live_cnooc = three_oil["facts"]["latest_cnooc_portal_live_recheck"]
    assert live_cnooc["observed_unit_count"] == 16
    assert live_cnooc["visible_job_card_count"] == 14
    assert live_cnooc["independent_unit_recruitment_portals_proven"] is False
    assert live_cnooc["jobs_created"] is False
    homepage = three_oil["facts"]["latest_official_homepage_entry_recheck"]
    assert homepage["observations"] == 3
    assert homepage["recruitment_entries_proven"] == 0
    assert homepage["no_jobs_created"] is True
    assert homepage["catalog_changed"] is False
    homepage_probe = three_oil["facts"]["latest_registered_homepage_probe"]
    assert homepage_probe["registered_homepage_records"] == 20
    assert homepage_probe["unique_homepage_urls"] == 17
    assert homepage_probe["units_with_recruitment_links"] == 4
    assert homepage_probe["jobs_collected"] == 0
    assert homepage_probe["formal_catalog_changed"] is False
