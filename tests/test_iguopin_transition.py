from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from conftest import make_settings
from job_hub.db import Database
from job_hub.iguopin_transition import (
    transition_iguopin_source_to_production,
)
from job_hub.pipeline import JobPipeline
from job_hub.sources import RawPosting


def _source(*, enabled: bool) -> dict:
    return {
        "id": "chinalco-iguopin-browser",
        "name": "测试国聘来源",
        "publisher": "测试单位",
        "homepage_url": "https://official.example.cn/job",
        "source_type": "iguopin_browser_rows",
        "category": "矿产资源与矿业",
        "source_tier": "A",
        "enabled": enabled,
        "config": {
            "runtime_enabled_control": "chinalco-iguopin-browser-production",
            "capture_path": "captures/current.json",
            "allowed_hosts": ["official.example.cn"],
            "max_age_hours": 30,
            "minimum_relevance": 0,
            "require_complete_scan": True,
            "require_capture_manifest": True,
            "major_filters": [{"parent": "理学", "child": "地质学类"}],
            "external_id_prefix": "chinalco-iguopin",
        },
    }


def _posting() -> RawPosting:
    return RawPosting(
        title="地质勘查技术岗",
        employer="测试单位",
        source_url="https://official.example.cn/job/detail?id=abc12345",
        application_url="https://official.example.cn/job",
        text="资源勘查工程专业，本科及以上，工作地点北京，报名截止2027-03-07。",
        summary="官方岗位",
        published_date="2026-10-03",
        deadline_date="2027-03-07",
        location="北京",
        external_id="chinalco-abc12345",
        field_evidence={
            "evidence_scope": "official_iguopin_browser_detail",
            "岗位": "地质勘查技术岗",
            "专业范围": "资源勘查工程",
            "学历要求": "本科及以上",
            "工作地点": "北京",
            "报名截止": "2027-03-07",
        },
    )


@dataclass
class _Collector:
    def collect(self, _source: object) -> list[RawPosting]:
        return [_posting()]


def _payload(captured_at: str) -> dict:
    return {
        "version": 1,
        "status": "success",
        "captured_at": captured_at,
        "scan": {
            "pages_scanned": 1,
            "pagination_complete": True,
            "rows_discovered": 1,
            "rows_exported": 1,
            "failed_rows": 0,
            "detail_discovered": 1,
            "detail_succeeded": 1,
            "detail_failed": 0,
        },
        "rows": [
            {
                "external_id": "chinalco-abc12345",
                "title": "地质勘查技术岗",
                "employer": "测试单位",
                "major": "资源勘查工程",
                "degree": "本科及以上",
                "location": "北京",
                "deadline": "2027-03-07",
                "detail_url": "https://official.example.cn/job/detail?id=abc12345",
                "evidence_url": "https://official.example.cn/job/detail?id=abc12345",
                "field_evidence": {"岗位": "地质勘查技术岗"},
            }
        ],
        "capture_evidence": {
            "capture_id": "capture-123",
            "source_id": "chinalco-iguopin-browser",
            "adapter_version": "iguopin-browser-v1",
            "payload_sha256": "hash",
            "hash_scope": "canonical_capture_payload_without_manifest",
        },
    }


def test_transition_requires_two_complete_captures(tmp_path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    database.upsert_source(_source(enabled=False))
    calls = iter([_payload("2026-10-03T15:00:00Z"), ValueError("stale")])

    def load(*_args, **_kwargs):
        value = next(calls)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr("job_hub.iguopin_transition.load_iguopin_browser_capture", load)
    result = transition_iguopin_source_to_production(
        database,
        JobPipeline(settings, database, _Collector()),
        source_id="chinalco-iguopin-browser",
        previous_capture_path=Path("captures/previous.json"),
    )

    assert result.status == "blocked"
    assert database.get_source("chinalco-iguopin-browser")["enabled"] is False


def test_transition_activates_after_two_complete_captures(tmp_path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    database.upsert_source(_source(enabled=False))
    monkeypatch.setattr(
        "job_hub.iguopin_transition.load_iguopin_browser_capture",
        lambda *_args, **_kwargs: _payload("2026-10-03T15:00:00Z"),
    )
    result = transition_iguopin_source_to_production(
        database,
        JobPipeline(settings, database, _Collector()),
        source_id="chinalco-iguopin-browser",
        previous_capture_path=Path("captures/previous.json"),
        activate=True,
    )

    assert result.status == "activated"
    assert result.current_eligible == 1
    assert database.get_source("chinalco-iguopin-browser")["enabled"] is True
    jobs, total = database.list_jobs(student_visible=False)
    assert total == 1
    assert jobs[0]["source_id"] == "chinalco-iguopin-browser"
