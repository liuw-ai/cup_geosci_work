from __future__ import annotations

import json
from pathlib import Path


EVIDENCE = Path(
    "docs/current/phase4-third-party-huatu-hbdk-geophysics-2026-10-19.json"
)


def test_hbdk_evidence_keeps_official_chain_and_blocks_unproven_publication() -> None:
    payload = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    official = payload["official_recovery"]
    capture = payload["table_capture"]
    decision = payload["publication_decision"]

    assert payload["source"]["trust_grade"] == "B"
    assert official["official_notice_url"].startswith("https://www.hbdkjt.cn/")
    assert official["official_table_url"] == "https://docs.qq.com/doc/DTFFuZmpRQ1FxR3JU"
    assert official["official_application_url"] is None
    assert official["deadline_policy"] == "relative_working_days_unresolved"
    assert len(payload["rows"]) == 2
    assert all(row["student_layer_assessment"] == "broad_geoscience_match" for row in payload["rows"])
    assert capture["original_attachment_downloaded"] is False
    assert capture["original_attachment_sha256"] is None
    assert len(capture["screenshot_sha256"]) == 2
    assert decision["status"] == "candidate_only"
    assert decision["formal_jobs_created"] == 0
    assert decision["student_visible"] is False
