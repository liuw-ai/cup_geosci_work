import json

import pytest

from job_hub.recruitment_batches import (
    build_recruitment_batch_report,
    load_recruitment_batch_registry,
)


def _batch(**overrides):
    value = {
        "id": "batch-a",
        "label": "测试批次",
        "family": "three_oil",
        "priority": "critical",
        "status": "in_progress",
        "target_jobs": 2,
        "source_ids": ["source-a"],
        "official_entry_urls": ["https://official.example.cn/jobs"],
        "required_fields": ["official_evidence_url", "major", "degree", "location", "deadline"],
    }
    value.update(overrides)
    return value


def _job(external_id: str, *, source_id: str = "source-a", complete: bool = True):
    job = {
        "external_id": external_id,
        "source_id": source_id,
        "status": "open",
        "publication_status": "student_eligible",
        "canonical_employer_name": f"单位-{external_id}",
        "province": "北京",
        "official_evidence_url": "https://official.example.cn/jobs/1",
        "major": "地质学",
        "degree": "本科",
        "location": "北京",
        "deadline": "2027-03-01",
        "field_evidence": {"evidence_scope": "official_detail_block"},
    }
    if not complete:
        job["deadline"] = ""
    return job


def test_registry_is_versioned_and_all_entries_have_official_entry():
    registry = load_recruitment_batch_registry()
    assert registry["version"] == 1
    assert sum(item["target_jobs"] for item in registry["batches"]) == 500
    assert sum(item["target_jobs"] for item in registry["batches"] if item["family"] == "three_oil") == 360
    assert sum(item["target_jobs"] for item in registry["batches"] if item["family"] != "three_oil") == 140
    assert {item["id"] for item in registry["batches"]} >= {
        "cnooc-campus-2027",
        "cnpc-campus-2027",
        "sinopec-campus-2027",
        "provincial-public-institutions-2026",
    }


def test_registry_rejects_duplicate_source_ids(tmp_path):
    path = tmp_path / "batches.json"
    path.write_text(json.dumps({"version": 1, "batches": [_batch(source_ids=["source-a", "source-a"])]}), encoding="utf-8")
    with pytest.raises(ValueError, match="source_ids must be unique"):
        load_recruitment_batch_registry(path)


def test_report_keeps_incomplete_evidence_out_of_completion_rate():
    result = build_recruitment_batch_report(
        batches=[_batch()],
        sources=[{"id": "source-a", "enabled": True, "config": {}}],
        jobs=[_job("1"), _job("2", complete=False)],
        crawl_runs=[
            {"id": 2, "source_id": "source-a", "status": "finished"},
            {"id": 1, "source_id": "source-a", "status": "finished"},
        ],
    )
    row = result["batches"][0]
    assert row["current_public_domestic_jobs"] == 2
    assert row["evidence_complete_jobs"] == 1
    assert row["evidence_rate"] == 0.5
    assert "evidence_rate_at_least_95_percent" in row["failed_gates"]
    assert row["two_consecutive_refresh_rate"] == 1.0


def test_report_maps_production_job_field_names_to_batch_contract():
    job = {
        "external_id": "canonical-1",
        "source_id": "source-a",
        "status": "open",
        "publication_status": "student_eligible",
        "canonical_employer_name": "单位-canonical-1",
        "province": "北京",
        "official_evidence_url": "https://official.example.cn/jobs/1",
        "source_url": "https://official.example.cn/jobs/1",
        "major_tags": ["地质学"],
        "degree_levels": ["硕士"],
        "location": "北京",
        "deadline_date": "2027-03-01",
        "field_evidence": {"evidence_scope": "official_html_table_row"},
    }
    result = build_recruitment_batch_report(
        batches=[_batch()],
        sources=[{"id": "source-a", "enabled": True, "config": {}}],
        jobs=[job],
        crawl_runs=[],
    )
    row = result["batches"][0]
    assert row["evidence_complete_jobs"] == 1
    assert row["evidence_rate"] == 1.0
    assert "evidence_rate_at_least_95_percent" not in row["failed_gates"]


def test_normalized_province_does_not_replace_job_location_evidence():
    job = _job("province-only")
    job.pop("location")
    job["province"] = "北京"
    result = build_recruitment_batch_report(
        batches=[_batch()],
        sources=[{"id": "source-a", "enabled": True, "config": {}}],
        jobs=[job],
        crawl_runs=[],
    )
    assert result["batches"][0]["evidence_complete_jobs"] == 0


def test_report_marks_unregistered_sources_instead_of_guessing_success():
    result = build_recruitment_batch_report(
        batches=[_batch(source_ids=["missing-source"])],
        sources=[],
        jobs=[],
        crawl_runs=[],
    )
    row = result["batches"][0]
    assert row["missing_source_ids"] == ["missing-source"]
    assert "all_sources_registered" in row["failed_gates"]


def test_report_surfaces_access_limited_health_without_calling_it_empty():
    result = build_recruitment_batch_report(
        batches=[_batch()],
        sources=[{"id": "source-a", "enabled": True, "config": {}}],
        jobs=[],
        crawl_runs=[],
        health_by_id={"source-a": {"status": "robots_blocked"}},
    )
    row = result["batches"][0]
    assert row["access_limited_source_count"] == 1
    assert row["current_public_domestic_jobs"] == 0
    assert "has_current_public_jobs" in row["failed_gates"]


def test_report_deduplicates_live_snapshot_overlap():
    batch = _batch(source_ids=["source-a", "source-b"], target_jobs=1)
    first = _job("1", source_id="source-a")
    second = _job("1", source_id="source-b")
    first["official_detail_key"] = "detail-1"
    second["official_detail_key"] = "detail-1"
    result = build_recruitment_batch_report(
        batches=[batch],
        sources=[
            {"id": "source-a", "enabled": True, "config": {}},
            {"id": "source-b", "enabled": False, "config": {}},
        ],
        jobs=[first, second],
        crawl_runs=[],
    )
    assert result["batches"][0]["current_public_domestic_jobs"] == 1


def test_report_explains_current_jobs_outside_registered_batches():
    in_batch = _job("in-batch", source_id="source-a")
    outside_batch = _job("outside-batch", source_id="source-b")
    result = build_recruitment_batch_report(
        batches=[_batch()],
        sources=[
            {"id": "source-a", "enabled": True, "config": {}},
            {"id": "source-b", "enabled": True, "config": {}},
        ],
        jobs=[in_batch, outside_batch],
        crawl_runs=[],
    )

    assert result["all_current_public_domestic_jobs"] == 2
    assert result["assigned_current_public_domestic_jobs"] == 1
    assert result["unassigned_current_public_domestic_jobs"] == 1
