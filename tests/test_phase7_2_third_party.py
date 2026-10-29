from __future__ import annotations

import json
import sys

import job_hub.cli as cli

from job_hub.db import Database
from job_hub.discovery import build_third_party_table_funnel
from job_hub.lead_publication import auto_publish_verified_candidate_leads
from job_hub.pipeline import JobPipeline
from job_hub.third_party_tables import table_candidates

from conftest import make_settings, source


def test_cli_discovery_funnel_includes_persisted_third_party_batches(
    monkeypatch, capsys
) -> None:
    class FakeSettings:
        timezone = "Asia/Shanghai"

    class FakeDatabase:
        @staticmethod
        def list_candidate_leads(*, limit: int):
            assert limit == 5000
            return []

        @staticmethod
        def list_jobs(**_kwargs):
            return [], 0

        @staticmethod
        def list_third_party_batches(*, limit: int):
            assert limit == 1000
            return [{"batch_id": "batch-1", "parsed_row_count": 12}]

    captured: dict[str, object] = {}

    def fake_funnel(leads, registry, *, jobs, third_party_batches):
        captured["leads"] = leads
        captured["registry"] = registry
        captured["jobs"] = jobs
        captured["third_party_batches"] = third_party_batches
        return {"third_party_table_funnel": {"batch_count": len(third_party_batches)}}

    monkeypatch.setattr(cli, "services", lambda: (FakeSettings(), FakeDatabase(), object()))
    monkeypatch.setattr(cli, "load_discovery_source_registry", lambda: {"sources": []})
    monkeypatch.setattr(cli, "discovery_funnel", fake_funnel)
    monkeypatch.setattr(sys, "argv", ["job_hub.cli", "discovery-funnel"])

    cli.main()

    assert captured["third_party_batches"] == [{"batch_id": "batch-1", "parsed_row_count": 12}]
    assert json.loads(capsys.readouterr().out)["third_party_table_funnel"]["batch_count"] == 1


def test_cli_growth_metrics_includes_persisted_third_party_batches(
    monkeypatch, capsys
) -> None:
    class FakeSettings:
        timezone = "Asia/Shanghai"

    class FakeDatabase:
        @staticmethod
        def list_candidate_leads(*, limit: int):
            assert limit == 5000
            return []

        @staticmethod
        def list_jobs(**_kwargs):
            return [], 0

        @staticmethod
        def list_third_party_batches(*, limit: int):
            assert limit == 1000
            return [{"batch_id": "batch-1", "parsed_row_count": 12}]

        @staticmethod
        def list_sources():
            return []

        @staticmethod
        def list_crawl_runs(*, limit: int):
            assert limit == 10_000
            return []

        @staticmethod
        def list_source_health():
            return []

        @staticmethod
        def list_source_tasks():
            return []

        @staticmethod
        def list_source_artifact_status_counts():
            return []

        @staticmethod
        def get_source_capture_freshness(_source_id: str):
            return None

    captured: dict[str, object] = {}

    def fake_funnel(leads, registry, *, jobs, third_party_batches):
        captured["third_party_batches"] = third_party_batches
        return {"growth_metrics": {}, "source_growth_metrics": {}}

    monkeypatch.setattr(cli, "services", lambda: (FakeSettings(), FakeDatabase(), object()))
    monkeypatch.setattr(cli, "coverage_snapshot_date", lambda _settings: "2026-10-28")
    monkeypatch.setattr(cli, "load_discovery_source_registry", lambda: {"sources": []})
    monkeypatch.setattr(cli, "load_effective_job_target_plan", lambda: {"targets": []})
    monkeypatch.setattr(cli, "discovery_funnel", fake_funnel)
    monkeypatch.setattr(cli, "build_source_funnel", lambda **_kwargs: {"summary": {}, "rows": []})
    monkeypatch.setattr(sys, "argv", ["job_hub.cli", "growth-metrics"])

    cli.main()

    assert captured["third_party_batches"] == [{"batch_id": "batch-1", "parsed_row_count": 12}]
    assert json.loads(capsys.readouterr().out)["third_party_leads"] == {}


def test_third_party_table_candidate_keeps_official_source_binding_hint() -> None:
    rows = table_candidates(
        {
            "batch": {
                "id": "huatu-2027",
                "province": "北京",
                "rows": [["GEO-1", "某地质院", "地质调查岗", "地质工程", "硕士", 1, "Sheet1!2", "北京"]],
            },
            "download": {"sha256": "a" * 64, "bytes": 1},
        },
        source_id="huatu-discovery",
        third_party_table_url="https://www.huatu.com/table.xlsx",
        official_notice_url="https://careers.example.edu.cn/notice/1",
        official_table_url="https://careers.example.edu.cn/notice/1/table.xlsx",
        official_source_id="official-test-source",
    )

    assert rows[0]["official_source_id"] == "official-test-source"
    assert rows[0]["metadata"]["official_source_id"] == "official-test-source"


def test_third_party_table_funnel_exposes_each_dropoff() -> None:
    leads = [
        {
            "id": 1,
            "verification_status": "candidate",
            "official_url": None,
            "third_party_table_url": "https://www.huatu.com/table.xlsx",
            "metadata": {"batch_id": "batch-1"},
        },
        {
            "id": 2,
            "verification_status": "official_url_found",
            "official_url": "https://careers.example.edu.cn/notice/1",
            "official_domain_status": "registered_source_match",
            "third_party_table_url": "https://www.huatu.com/table.xlsx",
            "metadata": {"batch_id": "batch-1"},
        },
        {
            "id": 3,
            "verification_status": "published",
            "official_url": "https://careers.example.edu.cn/notice/2",
            "official_domain_status": "registered_source_match",
            "third_party_table_url": "https://www.huatu.com/table.xlsx",
            "metadata": {"batch_id": "batch-1"},
        },
    ]
    report = build_third_party_table_funnel(
        [
            {
                "batch_id": "batch-1",
                "discovery_source_id": "huatu-discovery",
                "status": "imported",
                "parsed_row_count": 10,
                "candidate_row_count": 3,
                "published_links": 1,
                "third_party_table_url": "https://www.huatu.com/table.xlsx",
            }
        ],
        leads,
    )

    assert report["parsed_rows"] == 10
    assert report["candidate_rows"] == 3
    assert report["linked_leads"] == 3
    assert report["official_url_bound"] == 2
    assert report["published"] == 1
    assert report["blockers"]["missing_official_url"] == 1
    assert report["by_discovery_source"]["huatu-discovery"]["published_rows"] == 1


def test_worker_publication_handles_only_already_verified_leads(tmp_path) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    database.upsert_source(source())
    lead = database.create_candidate_lead(
        {
            "lead_provider": "华图教育",
            "discovery_source_id": "huatu-discovery",
            "lead_url": "https://www.huatu.com/lead/phase7-2",
            "title": "地质工程技术岗",
            "employer_hint": "测试地质调查院",
            "official_url": "https://careers.example.edu.cn/jobs/phase7-2",
            "official_source_id": "official-test-source",
            "metadata": {
                "source_id": "official-test-source",
                "title": "地质工程技术岗",
                "employer": "测试地质调查院",
                "description": "面向地质工程硕士毕业生的官方招聘。",
                "location": "北京市",
                "deadline_date": "2099-12-31",
                "field_evidence": {
                    "evidence_scope": "admin_verified_official_record",
                    "岗位": "地质工程技术岗",
                    "专业范围": "地质工程",
                    "学历要求": "硕士研究生",
                },
            },
        }
    )
    database.update_candidate_lead(
        int(lead["id"]),
        {
            "official_source_id": "official-test-source",
            "official_domain_status": "registered_source_match",
            "verification_status": "official_url_found",
            "verification_note": "已找到官方原文。",
        },
    )
    database.update_candidate_lead(
        int(lead["id"]),
        {
            "official_source_id": "official-test-source",
            "official_domain_status": "registered_source_match",
            "verification_status": "official_content_verified",
            "verification_note": "已核验官方原文和岗位级字段。",
        },
    )

    result = auto_publish_verified_candidate_leads(
        database, JobPipeline(settings, database)
    )

    assert result["published"] == 1
    assert result["blocked"] == 0
    assert database.get_candidate_lead(int(lead["id"]))["verification_status"] == "published"
    jobs, _ = database.list_jobs(page_size=None)
    assert jobs[0]["publication_status"] == "student_eligible"

