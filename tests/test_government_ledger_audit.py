from __future__ import annotations

from copy import deepcopy

from job_hub.domestic_expansion import load_domestic_expansion_queue
from job_hub.government_artifacts import load_government_artifact_manifest
from job_hub.government_ledger_audit import (
    build_government_ledger_consistency_audit,
)
from job_hub.government_positions import load_position_registry


def _ledgers() -> tuple[dict, dict, dict]:
    return (
        load_domestic_expansion_queue(),
        load_government_artifact_manifest(),
        load_position_registry(),
    )


def test_default_government_ledgers_are_consistent_but_evidence_is_stale() -> None:
    queue, manifest, registry = _ledgers()

    report = build_government_ledger_consistency_audit(
        queue=queue,
        manifest=manifest,
        registry=registry,
        today="2026-09-30",
    )

    assert report["ok"] is True
    assert report["error_count"] == 0
    assert report["registry_evidence"]["state"] == "verified_positions_stale"
    assert report["registry_evidence"]["verified_position_records"] == 157
    assert report["registry_evidence"]["verified_open_position_records"] == 143


def test_audit_rejects_no_match_status_when_verified_rows_exist() -> None:
    queue, manifest, registry = _ledgers()
    queue = deepcopy(queue)
    row = next(item for item in queue["records"] if item["id"] == "cgs-recruitment")
    row["status"] = "scan_success_no_match"
    row["scan_conclusion"] = "scan_success_no_match"

    report = build_government_ledger_consistency_audit(
        queue=queue,
        manifest=manifest,
        registry=registry,
        today="2026-09-30",
    )

    assert report["ok"] is False
    assert any(
        issue["code"] == "queue_status_conflicts_with_verified_registry_records"
        and issue["queue_record_id"] == "cgs-recruitment"
        for issue in report["errors"]
    )


def test_audit_rejects_unresolvable_government_position_sample() -> None:
    queue, manifest, registry = _ledgers()
    queue = deepcopy(queue)
    row = next(item for item in queue["records"] if item["id"] == "cgs-recruitment")
    row["sample_job_ids"] = ["government-position:missing:404"]

    report = build_government_ledger_consistency_audit(
        queue=queue,
        manifest=manifest,
        registry=registry,
        today="2026-09-30",
    )

    assert report["ok"] is False
    assert any(
        issue["code"] == "queue_government_position_sample_missing"
        for issue in report["errors"]
    )


def test_audit_rejects_excluded_artifact_link_mismatch() -> None:
    queue, manifest, registry = _ledgers()
    queue = deepcopy(queue)
    row = next(item for item in queue["records"] if item["id"] == "ccgc-mature-talent-2026")
    row["related_artifact_id"] = "missing-artifact"

    report = build_government_ledger_consistency_audit(
        queue=queue,
        manifest=manifest,
        registry=registry,
        today="2026-09-30",
    )

    assert report["ok"] is False
    assert any(
        issue["code"] == "non_student_queue_artifact_reference_missing"
        for issue in report["errors"]
    )
    assert any(
        issue["code"] == "excluded_artifact_has_no_queue_link"
        for issue in report["errors"]
    )
