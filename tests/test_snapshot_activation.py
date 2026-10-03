from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from job_hub.config import Settings
from job_hub.db import Database
from job_hub.pipeline import JobPipeline
from job_hub.snapshot_activation import activate_sinopec_snapshot

from conftest import make_settings


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = PROJECT_ROOT / "data" / "verified" / "sinopec-geoscience-20260929.json"


def _source() -> dict[str, object]:
    return {
        "id": "sinopec-2027-geoscience-snapshot",
        "name": "中国石化2027届地学岗位官方快照（人工核验过渡源）",
        "publisher": "中国石油化工集团有限公司",
        "homepage_url": "https://job.sinopec.com/#/school/recruitmentPositions",
        "source_type": "manual",
        "category": "油气上游业主与研究机构",
        "source_tier": "A",
        "enabled": False,
        "config": {
            "allowed_hosts": ["job.sinopec.com"],
            "snapshot_path": "data/verified/sinopec-geoscience-20260929.json",
            "snapshot_captured_at": "2026-09-29T07:08:24.607107Z",
            "official_evidence_url": "https://job.sinopec.com/#/school/recruitmentPositions",
            "application_url": "https://job.sinopec.com/",
        },
    }


def test_activation_keeps_manual_source_public_without_enabling_live_sync(
    tmp_path: Path,
) -> None:
    # Production uses MAX_SOURCE_ITEMS=80 for ordinary live pages.  Snapshot
    # activation must still read the complete reviewed capture.
    settings = replace(make_settings(tmp_path), max_source_items=80)
    database = Database(settings.database_path)
    database.initialize()
    database.upsert_source(_source())
    result = activate_sinopec_snapshot(settings, database)

    assert result["captured_rows"] == 363
    assert result["publication_status"] == {
        "student_eligible": 64,
        "pending_evidence": 98,
        "out_of_scope": 201,
    }
    assert result["visible_rows"] == 64
    assert result["live_replacement_source_id"] is None
    assert database.get_source("sinopec-2027-geoscience-snapshot")["enabled"] is False


def test_activation_does_not_count_pending_or_out_of_scope_rows(tmp_path: Path) -> None:
    settings = replace(make_settings(tmp_path), max_source_items=80)
    database = Database(settings.database_path)
    database.initialize()
    database.upsert_source(_source())
    activate_sinopec_snapshot(settings, database)
    public_rows, public_count = database.list_jobs(page_size=None)
    audit_rows, audit_count = database.list_jobs(
        page_size=None, student_visible=False, only_open=False
    )

    assert public_count == 64
    assert len(public_rows) == 64
    assert audit_count == 363
    assert len(audit_rows) == 363
    assert all(row["publication_status"] == "student_eligible" for row in public_rows)
