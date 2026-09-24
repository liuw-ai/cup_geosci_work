from __future__ import annotations

import json
from pathlib import Path

import pytest

from job_hub.cli import import_verified_jobs
from job_hub.db import Database
from job_hub.domestic_expansion import (
    domestic_expansion_rows,
    domestic_expansion_summary,
    load_domestic_expansion_queue,
)
from job_hub.pipeline import JobPipeline

from conftest import make_settings


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_domestic_expansion_queue_is_explicit_and_non_public() -> None:
    queue = load_domestic_expansion_queue()
    summary = domestic_expansion_summary(queue)

    assert summary["record_count"] >= 10
    assert summary["backup_rate"] == 1.0
    assert summary["official_job_sample_verified"] >= 3
    assert "中国石油" in summary["by_system"]
    assert "公务员" in summary["by_system"]
    assert summary["scan_success_no_match"] >= 1

    cnpc_rows = [
        item
        for item in queue["records"]
        if item.get("system") == "中国石油"
        and item.get("organization_role") == "upstream_operator"
    ]
    assert len(cnpc_rows) == 10
    assert {item["status"] for item in cnpc_rows} == {
        "official_job_sample_verified"
    }
    assert all(item.get("source_id") == "cnpc-career" for item in cnpc_rows)
    assert all(item.get("sample_job_ids") for item in cnpc_rows)
    assert all(item.get("field_validation") for item in cnpc_rows)

    limited = domestic_expansion_rows(queue, status="access_limited")
    assert limited
    assert all(item["status"] == "access_limited" for item in limited)
    # Queue rows are planning records; none may be mistaken for a job id.
    assert all("job_id" not in item for item in queue["records"])


def test_domestic_verified_snapshot_has_job_level_gate(tmp_path) -> None:
    settings = make_settings(tmp_path)
    source_registry = json.loads(
        (PROJECT_ROOT / "data" / "sources.json").read_text(encoding="utf-8")
    )
    settings.source_registry_path.write_text(
        json.dumps(
            [item for item in source_registry if item["id"] == "cupb-career"],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    database = Database(settings.database_path)
    database.initialize()
    pipeline = JobPipeline(settings, database)
    pipeline.bootstrap_sources()

    path = PROJECT_ROOT / "data" / "verified" / "domestic-geoscience-20260924.json"
    result = import_verified_jobs(database, pipeline, path)
    assert result == {"created": 2, "updated": 0, "unchanged": 0}

    jobs, _ = database.list_jobs(page_size=None, only_open=True, student_visible=True)
    titles = {str(job["title"]) for job in jobs}
    assert "油气田勘探开发技术研究" in titles
    assert "岩土勘察工程师" in titles
    for job in jobs:
        if job["title"] not in {"油气田勘探开发技术研究", "岩土勘察工程师"}:
            continue
        assert job["publication_status"] == "student_eligible"
        assert job["field_evidence"]["evidence_scope"] == "official_html_table_row"
        assert job["field_evidence"]["岗位"] == job["title"]


def test_cnpc_snapshot_covers_ten_oilfields_and_all_rows_are_publishable(tmp_path) -> None:
    snapshot_path = PROJECT_ROOT / "data" / "verified" / "cnpc-geoscience-20260924.json"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert len(snapshot) == 20
    assert len({item["employer"] for item in snapshot}) == 10

    for item in snapshot:
        assert item["source_id"] == "cnpc-career"
        assert item["official_evidence_url"].startswith("https://zhaopin.cnpc.com.cn/")
        assert item["field_evidence"]["岗位"] == item["title"]
        assert item["qualification_text"]
        assert item["location"]
        assert item["deadline_date"] == "2026-10-15"

    settings = make_settings(tmp_path)
    source_registry = json.loads(
        (PROJECT_ROOT / "data" / "sources.json").read_text(encoding="utf-8")
    )
    settings.source_registry_path.write_text(
        json.dumps(
            [item for item in source_registry if item["id"] == "cnpc-career"],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    database = Database(settings.database_path)
    database.initialize()
    pipeline = JobPipeline(settings, database)
    pipeline.bootstrap_sources()

    result = import_verified_jobs(database, pipeline, snapshot_path)
    assert result == {"created": 20, "updated": 0, "unchanged": 0}
    jobs, _ = database.list_jobs(page_size=None, only_open=True, student_visible=True)
    assert len(jobs) == 20
    assert all(job["publication_status"] == "student_eligible" for job in jobs)


def test_domestic_queue_rejects_unknown_status(tmp_path) -> None:
    path = tmp_path / "queue.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "records": [
                    {
                        "id": "bad",
                        "system": "中国石油",
                        "official_url": "https://example.cn/",
                        "backup_urls": ["https://example.cn/backup"],
                        "status": "published_without_evidence",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unsupported queue status"):
        load_domestic_expansion_queue(path)


def test_verified_queue_row_requires_auditable_sample(tmp_path) -> None:
    path = tmp_path / "queue.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "records": [
                    {
                        "id": "verified-without-evidence",
                        "system": "中国石油",
                        "official_url": "https://example.cn/",
                        "backup_urls": ["https://example.cn/backup"],
                        "source_id": "cnpc-career",
                        "status": "official_job_sample_verified",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="sample_announcement_url"):
        load_domestic_expansion_queue(path)
