from __future__ import annotations

from job_hub.db import Database
from job_hub.pipeline import JobPipeline
from job_hub.sources import RawPosting

from conftest import make_settings, source


def test_jobs_persist_internal_third_party_provenance_as_independent_fields(tmp_path):
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    registered_source = source()
    database.upsert_source(registered_source)
    pipeline = JobPipeline(settings, database)
    job = pipeline.normalize_posting(
        RawPosting(
            title="地质工程师",
            employer="测试单位",
            source_url="https://official.example/jobs/1",
            application_url=None,
            text="地质工程岗位，硕士，北京，报名截止时间2026年12月20日。",
            summary="官方岗位",
            published_date="2026-10-01",
            deadline_date="2026-12-20",
            location="北京",
            field_evidence={"专业要求": "地质工程", "学历要求": "硕士"},
        ),
        registered_source,
    )
    job.update(
        {
            "third_party_source_id": "huatu-discovery",
            "third_party_discovery_url": "https://www.huatu.com/discovery/1",
            "third_party_batch_id": "huatu-test-2026",
            "third_party_discovery_sha256": "b" * 64,
        }
    )
    job_id, _ = database.save_job(job)
    stored = database.find_job(job_id, student_visible=False)
    assert stored["third_party_source_id"] == "huatu-discovery"
    assert stored["third_party_discovery_url"].endswith("discovery/1")
    assert stored["third_party_batch_id"] == "huatu-test-2026"
    assert stored["third_party_discovery_sha256"] == "b" * 64
