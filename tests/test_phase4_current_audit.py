from __future__ import annotations

from scripts.audit_phase4_current_state import current_database_facts


def test_phase4_current_audit_uses_read_only_database_facts() -> None:
    facts = current_database_facts()

    assert facts["integrity_check"] == "ok"
    assert facts["job_count"] == sum(facts["status_counts"].values())
    assert facts["candidate_lead_count"] == 0
    assert facts["student_visible_total"] == sum(facts["student_visible_layer_counts"].values())
