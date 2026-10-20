from __future__ import annotations

import json
import hashlib
from pathlib import Path

from scripts.audit_phase4_current_state import (
    GENERATED_METADATA_PATHS,
    current_database_facts,
    document_consistency_facts,
)


def test_phase4_current_audit_uses_read_only_database_facts() -> None:
    facts = current_database_facts()

    assert facts["integrity_check"] == "ok"
    assert facts["job_count"] == sum(facts["status_counts"].values())
    assert facts["candidate_lead_count"] >= 1
    assert facts["student_visible_total"] == sum(facts["student_visible_layer_counts"].values())


def test_document_consistency_marks_old_codebase_as_historical() -> None:
    facts = document_consistency_facts()

    old = next(
        item
        for item in facts["historical_documents"]
        if item["path"] == "history/docs/CODEBASE-2026-10-17.md"
    )
    assert old["exists"] is True
    assert old["declared_historical"] is True
    assert facts["historical_documents_are_publish_inputs"] is False
    assert all(item["exists"] for item in facts["current_documents"])
    assert all(item["exists"] for item in facts["metadata_documents"])
    assert all("sha256" not in item for item in facts["metadata_documents"])


def test_current_readme_does_not_duplicate_stale_database_totals() -> None:
    content = Path("docs/current/README.md").read_text(encoding="utf-8")

    # Current counts belong in the hash-bound audit and manifest.  A stale
    # literal here would become a second, conflicting source of truth.
    assert "正式数据库只读审计为 3948 条岗位" not in content
    assert "formal_third_party_job_count" not in content


def test_document_consistency_current_hash_matches_runtime_database() -> None:
    report = json.loads(
        Path("docs/current/phase4-document-consistency-2026-10-20.json").read_text(
            encoding="utf-8"
        )
    )
    digest = hashlib.sha256(Path("runtime/job_hub.sqlite3").read_bytes()).hexdigest()

    assert report["current_database_facts"]["sha256"] == digest
    assert all(
        item.get("current_fact") is False
        for item in report["current_database_facts"].get("historical_snapshot_hashes", [])
    )


def test_current_phase4_record_matches_latest_local_validation_test_count() -> None:
    validation = json.loads(
        Path("docs/current/phase4-local-validation-2026-10-20.json").read_text(
            encoding="utf-8"
        )
    )
    record = Path("docs/current/PHASE4-2026-10-17.md").read_text(encoding="utf-8")

    passed = int(validation["tests"]["passed"])
    skipped = int(validation["tests"]["skipped"])
    failed = int(validation["tests"]["failed"])
    warnings = int(validation["tests"]["warnings"])
    expected = (
        f"`{passed} passed, {skipped} skipped, {failed} failed, {warnings} warning`"
    )
    assert expected in record


def test_current_audit_excludes_all_metadata_outputs_from_input_status() -> None:
    assert ":!docs/current/phase4-completion-audit-2026-10-19.json" in GENERATED_METADATA_PATHS
    assert ":!docs/current/phase4-workspace-layout-audit-2026-10-19.json" in GENERATED_METADATA_PATHS
    assert ":!docs/current/phase4-three-oil-catalog-audit-2026-10-17.json" in GENERATED_METADATA_PATHS
    assert ":!docs/current/phase4-cnooc-portal-live-recheck-2026-10-20.json" in GENERATED_METADATA_PATHS
    assert ":!docs/current/phase4-three-oil-official-homepage-entry-recheck-2026-10-20.json" in GENERATED_METADATA_PATHS
