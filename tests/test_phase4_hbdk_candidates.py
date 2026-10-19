from __future__ import annotations

import json
from pathlib import Path

from scripts.register_phase4_hbdk_candidates import build_leads
from scripts.audit_phase4_third_party_funnel import DATABASE, audit_database


def test_hbdk_rows_become_officially_verified_leads_with_resolved_deadline() -> None:
    payload = json.loads(
        Path("docs/current/phase4-third-party-hbdk-official-table-2026-10-19.json").read_text(
            encoding="utf-8"
        )
    )
    leads = build_leads(payload)

    assert len(leads) == 2
    assert all(item["official_source_id"] == "hbdk-official-recruitment" for item in leads)
    assert all(item["official_application_url"] is None for item in leads)
    assert all(item["metadata"]["deadline_policy"] == "asia_shanghai_weekdays_inclusive" for item in leads)
    assert all(item["metadata"]["deadline_date"] == "2026-10-23" for item in leads)
    assert all(item["metadata"]["publication_status"] == "candidate_only" for item in leads)


def test_hbdk_candidates_are_published_only_after_official_row_validation() -> None:
    report = audit_database(DATABASE)
    rows = [
        item
        for item in report["rows"]
        if isinstance(item.get("metadata_json"), str)
        and "huatu-hbdk-2026-10-15" in item["metadata_json"]
    ]

    assert len(rows) == 2
    assert all(item["verification_status"] == "published" for item in rows)
    assert {item["published_job_id"] for item in rows} == {5437, 5438}
    assert report["summary"]["candidate_leads"] == 8
    assert report["summary"]["published_leads"] == 7
