from __future__ import annotations

from dataclasses import dataclass

from job_hub.cmgb_transition import transition_cmgb_browser_to_production
from job_hub.db import Database
from job_hub.pipeline import JobPipeline
from job_hub.sources import RawPosting

from conftest import make_settings


def _source(source_id: str, *, enabled: bool, source_type: str = "manual") -> dict:
    return {
        "id": source_id,
        "name": source_id,
        "publisher": "测试官方单位",
        "homepage_url": "https://official.example.cn/jobs",
        "source_type": source_type,
        "category": "自然资源、地调与地勘",
        "source_tier": "A",
        "enabled": enabled,
        "config": {
            "minimum_relevance": 0,
            "runtime_enabled_control": "cmgb-iguopin-browser-production",
            "reconcile_missing_external_ids": True,
        },
    }


def _posting(external_id: str) -> RawPosting:
    return RawPosting(
        title="地质勘查技术岗",
        employer="测试官方单位",
        source_url=f"https://official.example.cn/jobs/{external_id}",
        application_url=None,
        text="资源勘查工程、地质工程专业，本科及以上，工作地点北京，报名截止2026-12-31。",
        summary="官方岗位",
        published_date="2026-09-27",
        deadline_date="2026-12-31",
        location="北京",
        external_id=external_id,
        field_evidence={
            "evidence_scope": "official_html_table_row",
            "岗位": "地质勘查技术岗",
            "专业范围": "资源勘查工程、地质工程",
            "学历要求": "本科及以上",
            "工作地点": "北京",
            "报名截止": "2026-12-31",
        },
    )


@dataclass
class _Collector:
    postings: list[RawPosting] | None = None
    error: Exception | None = None

    def collect(self, _source: object) -> list[RawPosting]:
        if self.error is not None:
            raise self.error
        return list(self.postings or [])


def _database(tmp_path):
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    database.upsert_source(_source("cmgb-iguopin-2027-geoscience-snapshot", enabled=True))
    database.upsert_source(_source("cmgb-iguopin-browser", enabled=False))
    return settings, database


def test_failed_transition_keeps_snapshot_visible(tmp_path) -> None:
    settings, database = _database(tmp_path)
    pipeline = JobPipeline(settings, database, _Collector(error=RuntimeError("blocked")))
    snapshot = database.get_source("cmgb-iguopin-2027-geoscience-snapshot")
    database.save_job(pipeline.normalize_posting(_posting("cmgb-1"), snapshot))

    result = transition_cmgb_browser_to_production(database, pipeline, activate=True)

    assert result.status == "blocked"
    assert database.get_source("cmgb-iguopin-2027-geoscience-snapshot")["enabled"] is True
    assert database.get_source("cmgb-iguopin-browser")["enabled"] is False
    assert database.count_open_jobs() == 1
    pipeline.bootstrap_sources()
    assert database.get_source("cmgb-iguopin-2027-geoscience-snapshot")["enabled"] is True
    assert database.get_source("cmgb-iguopin-browser")["enabled"] is False


def test_successful_transition_is_atomic_and_removes_public_duplicate(tmp_path) -> None:
    settings, database = _database(tmp_path)
    pipeline = JobPipeline(settings, database, _Collector(postings=[_posting("cmgb-1")]))
    snapshot = database.get_source("cmgb-iguopin-2027-geoscience-snapshot")
    database.save_job(pipeline.normalize_posting(_posting("cmgb-1"), snapshot))

    result = transition_cmgb_browser_to_production(database, pipeline, activate=True)

    assert result.status == "activated"
    assert database.get_source("cmgb-iguopin-2027-geoscience-snapshot")["enabled"] is False
    assert database.get_source("cmgb-iguopin-browser")["enabled"] is True
    jobs, total = database.list_jobs()
    assert total == 1
    assert jobs[0]["source_id"] == "cmgb-iguopin-browser"
    internal, _ = database.list_jobs(only_open=False, student_visible=False)
    assert len(internal) == 2
    assert sum(item["status"] == "superseded" for item in internal) == 1
    # Registry bootstrap must preserve the audited runtime switch.
    pipeline.bootstrap_sources()
    assert database.get_source("cmgb-iguopin-2027-geoscience-snapshot")["enabled"] is False
    assert database.get_source("cmgb-iguopin-browser")["enabled"] is True
