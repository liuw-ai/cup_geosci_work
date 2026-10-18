from __future__ import annotations

import json
from pathlib import Path

from scripts.register_phase4_cgs_trial import build_leads, _load_evidence


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_cgs_trial_keeps_four_official_rows_as_unique_private_leads() -> None:
    evidence_path = PROJECT_ROOT / "docs/current/phase4-third-party-cgs-2026-10-18.json"
    payload = _load_evidence(evidence_path)
    leads = build_leads(payload)

    assert len(leads) == 4
    assert len({lead["metadata"]["locator"] for lead in leads}) == 4
    assert all(lead["discovery_source_id"] == "huatu-discovery" for lead in leads)
    assert all(lead["official_source_id"] == "cgs-notices" for lead in leads)
    assert all(lead["official_application_url"] is None for lead in leads)
    assert all(lead["metadata"]["student_publication"] == "blocked_until_major_review" for lead in leads)


def test_cgs_trial_evidence_hash_matches_retained_attachment() -> None:
    evidence_path = PROJECT_ROOT / "docs/current/phase4-third-party-cgs-2026-10-18.json"
    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    retained = PROJECT_ROOT / payload["official_recovery"]["official_table_capture_path"]
    import hashlib

    assert hashlib.sha256(retained.read_bytes()).hexdigest().upper() == payload["official_recovery"]["official_table_sha256"]
