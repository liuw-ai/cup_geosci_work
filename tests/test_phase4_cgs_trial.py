from __future__ import annotations

import json
from pathlib import Path

from scripts.register_phase4_cgs_trial import build_leads, _load_evidence
from job_hub.profiles import evaluate_student_publication


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_cgs_trial_keeps_four_official_rows_as_unique_audited_leads() -> None:
    evidence_path = PROJECT_ROOT / "docs/current/phase4-third-party-cgs-2026-10-18.json"
    payload = _load_evidence(evidence_path)
    leads = build_leads(payload)

    assert len(leads) == 4
    assert len({lead["metadata"]["locator"] for lead in leads}) == 4
    assert all(lead["discovery_source_id"] == "huatu-discovery" for lead in leads)
    assert all(lead["official_source_id"] == "cgs-notices" for lead in leads)
    assert all(lead["official_application_url"] is None for lead in leads)
    assert all(lead["metadata"]["student_publication"] == "broad_geoscience_reviewed" for lead in leads)
    assert all(lead["metadata"]["field_evidence"]["evidence_scope"] == "official_attachment_row" for lead in leads)


def test_cgs_rows_pass_only_the_broad_geoscience_layer() -> None:
    evidence_path = PROJECT_ROOT / "docs/current/phase4-third-party-cgs-2026-10-18.json"
    payload = _load_evidence(evidence_path)
    decisions = []
    for lead in build_leads(payload):
        row = lead["metadata"]["row"]
        evidence = lead["metadata"]["field_evidence"]
        decision = evaluate_student_publication(
            {
                "title": row["title"],
                "employer": lead["employer_hint"],
                "category": "博士后与科研助理",
                "location": lead["location_hint"],
                "country_or_region": "中国大陆",
                "field_evidence": evidence,
            }
        )
        decisions.append(decision)
    assert len(decisions) == 4
    assert all(item.status == "pending_evidence" for item in decisions)
    assert all("博士后" in item.reason for item in decisions)


def test_cgs_trial_evidence_hash_matches_retained_attachment() -> None:
    evidence_path = PROJECT_ROOT / "docs/current/phase4-third-party-cgs-2026-10-18.json"
    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    retained = PROJECT_ROOT / payload["official_recovery"]["official_table_capture_path"]
    import hashlib

    assert hashlib.sha256(retained.read_bytes()).hexdigest().upper() == payload["official_recovery"]["official_table_sha256"]
