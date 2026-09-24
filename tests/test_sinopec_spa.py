from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from job_hub.cli import main as cli_main
from job_hub.db import Database
from job_hub.pipeline import JobPipeline
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
            "max_items": 500,
            "require_complete_manifest": False,
        },
    }


def test_sinopec_capture_contains_complete_manifest() -> None:
    payload = load_sinopec_capture(SNAPSHOT)
    summary = sinopec_capture_summary(payload)

    assert summary["enterprise_total"] == 132
    assert summary["candidate_enterprise_total"] == 35
    assert summary["enterprise_captured"] == 132
    assert summary["candidate_enterprise_captured"] == 35
    assert summary["job_rows_captured"] == 348
    assert summary["complete_manifest"] is True
    assert summary["enterprise_success"] == 35


def test_sinopec_capture_accepts_complete_manifest_when_promoted() -> None:
    payload = load_sinopec_capture(SNAPSHOT, require_complete_manifest=True)
    assert payload["complete_manifest"] is True


def test_sinopec_spa_rows_preserve_official_field_evidence(tmp_path: Path) -> None:
    collector = OfficialSourceCollector(
        replace(make_settings(tmp_path), max_source_items=10)
    )
    postings = collector.collect(sinopec_source())

    assert len(postings) == 348
    geology = next(posting for posting in postings if posting.title == "油气地质研究岗")
    assert geology.employer == "胜利油田"
    assert geology.location.startswith("山东东营")
    assert geology.deadline_date == "2026-10-28"
    assert "资源勘查工程" in (geology.qualification_text or "")
    assert geology.official_evidence_url == geology.source_url
    assert geology.field_evidence["evidence_scope"] == "official_sinopec_detail_snapshot"
    assert geology.field_evidence["招聘人数"] == "125人"


def test_sinopec_snapshot_batch_gate_is_explicit_and_bounded(tmp_path: Path) -> None:
    """The full capture must be audited before the source can be promoted.

    This locks the distinction between explicit matches, ambiguous related
    wording and rejected majors for the reviewed 2026-09-24 capture.  It also
    prevents a future parser change from silently publishing all 348 rows.
    """
    settings = replace(make_settings(tmp_path), max_source_items=500)
    collector = OfficialSourceCollector(settings)
    pipeline = JobPipeline(settings, database=None, collector=collector)
    rows = [
        pipeline.normalize_posting(posting, sinopec_source())
        for posting in collector.collect(sinopec_source())
    ]

    statuses = {}
    for row in rows:
        status = row["publication_status"]
        statuses[status] = statuses.get(status, 0) + 1
        assert row["official_evidence_url"].startswith("https://job.sinopec.com/")
        assert row["field_evidence"]["evidence_scope"] == (
            "official_sinopec_detail_snapshot"
        )

    assert len(rows) == 348
    assert statuses == {
        "student_eligible": 69,
        "pending_evidence": 98,
        "out_of_scope": 181,
    }
    assert all(
        row["publication_basis"]["matched_profile_ids"]
        for row in rows
        if row["publication_status"] == "student_eligible"
    )
    assert all(
        not row["publication_basis"]["matched_profile_ids"]
        for row in rows
        if row["publication_status"] != "student_eligible"
    )


def test_sinopec_promotion_keeps_pending_rows_private(tmp_path: Path) -> None:
    """A promoted source stores the full audit set but exposes only 69 rows."""
    settings = replace(make_settings(tmp_path), max_source_items=500)
    database = Database(settings.database_path)
    database.initialize()
    source = sinopec_source()
    source["enabled"] = True
    source["config"] = {
        **source["config"],
        "require_complete_manifest": True,
    }
    database.upsert_source(source)
    pipeline = JobPipeline(settings, database=database)

    result = pipeline.sync_source(source)
    assert result.status == "finished"
    assert result.discovered == 348
    assert result.open_matches == 69

    public_rows, public_count = database.list_jobs(page_size=None)
    audit_rows, audit_count = database.list_jobs(
        page_size=None,
        student_visible=False,
        only_open=False,
    )
    source_audit_rows = [
        row for row in audit_rows if row["source_id"] == "sinopec-career"
    ]

    assert public_count == 69
    assert len(public_rows) == 69
    assert audit_count == 348
    assert len(source_audit_rows) == 348
    assert all(
        row["publication_status"] in {"student_eligible", "unrestricted_eligible"}
        for row in public_rows
    )
    assert {row["publication_status"] for row in source_audit_rows} == {
        "student_eligible",
        "pending_evidence",
        "out_of_scope",
    }


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
    assert payload["summary"]["enterprise_captured"] == 132
    assert payload["summary"]["candidate_enterprise_captured"] == 35
    assert payload["summary"]["job_rows_captured"] == 348
    assert payload["summary"]["complete_manifest"] is True
