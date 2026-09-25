from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from job_hub.sinopec import load_sinopec_capture, sinopec_capture_summary
from job_hub.sources import OfficialSourceCollector

from conftest import make_settings


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = PROJECT_ROOT / "data" / "verified" / "sinopec-geoscience-20260925.json"


def _source() -> dict[str, object]:
    return {
        "id": "sinopec-career",
        "name": "中国石化人才招聘网站",
        "publisher": "中国石油化工集团有限公司",
        "homepage_url": "https://job.sinopec.com/",
        "source_type": "sinopec_spa_rows",
        "category": "油气上游业主与研究机构",
        "source_tier": "A",
        "enabled": True,
        "config": {
            "snapshot_path": "data/verified/sinopec-geoscience-20260925.json",
            "allowed_hosts": ["job.sinopec.com"],
            "max_items": 1000,
            "require_complete_manifest": True,
        },
    }


def test_live_sinopec_capture_has_complete_candidate_scan() -> None:
    payload = load_sinopec_capture(SNAPSHOT, require_complete_manifest=True)
    summary = sinopec_capture_summary(payload)

    assert summary["enterprise_total"] == 132
    assert summary["enterprise_captured"] == 132
    assert summary["candidate_enterprise_total"] == 35
    assert summary["candidate_enterprise_captured"] == 35
    assert summary["candidate_scan_complete_all"] is True
    assert summary["job_rows_captured"] == 397
    assert summary["jobs_exported"] == 397
    assert summary["failed_jobs"] == 0


def test_live_sinopec_rows_keep_raw_deadline_and_iso_deadline_field(tmp_path: Path) -> None:
    collector = OfficialSourceCollector(replace(make_settings(tmp_path), max_source_items=1000))
    postings = collector.collect(_source())

    assert len(postings) == 397
    geology = next(posting for posting in postings if posting.title == "油气地质研究岗")
    assert geology.deadline_date == "2026-10-28"
    assert geology.field_evidence["报名截止"] == "截止时间：2026-10-28 17:00:00"
    assert geology.field_evidence["招聘人数"] == "125"
