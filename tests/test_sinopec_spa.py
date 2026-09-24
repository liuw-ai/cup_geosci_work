from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from job_hub.cli import main as cli_main
from job_hub.sinopec import SinopecCaptureError, load_sinopec_capture, sinopec_capture_summary
from job_hub.sources import OfficialSourceCollector

from conftest import make_settings


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = PROJECT_ROOT / "data" / "verified" / "sinopec-geoscience-20260924.json"


def sinopec_source() -> dict[str, object]:
    return {
        "id": "sinopec-career",
        "name": "中国石化人才招聘网站",
        "publisher": "中国石油化工集团有限公司",
        "homepage_url": "https://job.sinopec.com/",
        "source_type": "sinopec_spa_rows",
        "category": "油气上游业主与研究机构",
        "source_tier": "A",
        "enabled": False,
        "config": {
            "snapshot_path": "data/verified/sinopec-geoscience-20260924.json",
            "official_evidence_url": "https://job.sinopec.com/#/school/recruitmentPositions",
            "application_url": "https://job.sinopec.com/",
            "allowed_hosts": ["job.sinopec.com"],
            "enterprise_total": 132,
            "candidate_enterprise_total": 35,
            "require_complete_manifest": False,
        },
    }


def test_sinopec_capture_keeps_partial_manifest_explicit() -> None:
    payload = load_sinopec_capture(SNAPSHOT)
    summary = sinopec_capture_summary(payload)

    assert summary["enterprise_total"] == 132
    assert summary["candidate_enterprise_total"] == 35
    assert summary["enterprise_captured"] == 1
    assert summary["job_rows_captured"] == 3
    assert summary["complete_manifest"] is False
    assert summary["enterprise_success"] == 1


def test_sinopec_capture_rejects_incomplete_manifest_when_promoted() -> None:
    with pytest.raises(SinopecCaptureError, match="complete manifest"):
        load_sinopec_capture(SNAPSHOT, require_complete_manifest=True)


def test_sinopec_spa_rows_preserve_official_field_evidence(tmp_path: Path) -> None:
    collector = OfficialSourceCollector(
        replace(make_settings(tmp_path), max_source_items=10)
    )
    postings = collector.collect(sinopec_source())

    assert len(postings) == 3
    geology = next(posting for posting in postings if posting.title == "油气地质研究岗")
    assert geology.employer == "胜利油田"
    assert geology.location.startswith("山东东营")
    assert geology.deadline_date == "2026-10-28"
    assert "资源勘查工程" in (geology.qualification_text or "")
    assert geology.official_evidence_url == geology.source_url
    assert geology.field_evidence["evidence_scope"] == "official_sinopec_detail_snapshot"
    assert geology.field_evidence["招聘人数"] == "125人"


def test_sinopec_capture_rejects_external_evidence(tmp_path: Path) -> None:
    payload = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    payload["jobs"][0]["evidence_url"] = "https://example.com/not-official"
    mutated = tmp_path / "capture.json"
    mutated.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(SinopecCaptureError, match="not allowlisted"):
        load_sinopec_capture(mutated)


def test_sinopec_capture_cli_reports_partial_manifest(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "sys.argv",
        ["job_hub", "sinopec-capture", "--path", str(SNAPSHOT)],
    )
    cli_main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["enterprise_total"] == 132
    assert payload["summary"]["enterprise_captured"] == 1
    assert payload["summary"]["complete_manifest"] is False
