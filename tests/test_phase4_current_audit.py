from __future__ import annotations

from scripts.audit_phase4_current_state import current_database_facts, document_consistency_facts


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
        if item["path"] == "docs/current/CODEBASE-2026-10-17.md"
    )
    assert old["exists"] is True
    assert old["declared_historical"] is True
    assert facts["historical_documents_are_publish_inputs"] is False
    assert all(item["exists"] for item in facts["current_documents"])
