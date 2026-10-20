from __future__ import annotations

import json
from pathlib import Path

from scripts.trial_phase4_hbdk_water import build_leads, load_evidence


REPORT = Path("docs/current/phase4-third-party-hbdk-water-official-evidence-2026-10-20.json")


def test_hbdk_water_report_binds_official_notice_and_both_attachments() -> None:
    payload = load_evidence(REPORT)
    assert payload["source"]["official_notice_url"].endswith("/382.html")
    assert payload["source"]["official_table_url"].startswith("https://docs.qq.com/doc/")
    assert payload["source"]["official_registration_form_url"].startswith("https://docs.qq.com/doc/")
    assert len(payload["rows"]) == 3
    assert payload["publication_decision"]["eligible_rows_for_isolated_trial"] == [3]
    assert payload["publication_decision"]["rows_rejected_by_major_gate"] == [1, 2]


def test_hbdk_water_candidate_rows_keep_row_level_major_decisions() -> None:
    payload = json.loads(REPORT.read_text(encoding="utf-8"))
    leads = build_leads(payload)
    assert len(leads) == 3
    assert all(item["official_source_id"] == "hbdk-water-official-recruitment" for item in leads)
    assert all(item["official_application_url"] is None for item in leads)
    assert leads[0]["metadata"]["row"]["student_layer_assessment"] == "not_public"
    assert leads[1]["metadata"]["row"]["student_layer_assessment"] == "not_public"
    assert leads[2]["metadata"]["row"]["student_layer_assessment"] == "broad_geoscience_match"
