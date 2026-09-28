from __future__ import annotations

from job_hub.government_discovery import discover_configured_government_artifacts
from job_hub.db import Database
from job_hub.sources import SourceCollectionError, SourceSkipped

from conftest import make_settings, source


def _discovery_source(source_id: str, *, enabled: bool = True, opted_in: bool = True):
    record = source()
    record["id"] = source_id
    record["name"] = source_id
    record["enabled"] = enabled
    record["source_type"] = "html_notice"
    record["config"] = {
        "allowed_hosts": ["careers.example.edu.cn"],
        "listing_urls": ["https://careers.example.edu.cn/notices"],
        "attachment_discovery_enabled": opted_in,
        "attachment_discovery_max_notices": 3,
    }
    return record


class FakeNoticeCollector:
    def __init__(self, outcomes: dict[str, object]) -> None:
        self.outcomes = outcomes
        self.calls: list[str] = []

    def discover_notice_pages(self, record):
        source_id = str(record["id"])
        self.calls.append(source_id)
        outcome = self.outcomes[source_id]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeAttachmentProcessor:
    def __init__(self, database: Database) -> None:
        self.database = database
        self.calls: list[tuple[str, str]] = []

    def discover_from_page(self, source_id: str, parent_url: str):
        self.calls.append((source_id, parent_url))
        return [
            self.database.upsert_source_artifact(
                {
                    "source_id": source_id,
                    "parent_url": parent_url,
                    "artifact_url": "https://careers.example.edu.cn/files/positions.xlsx",
                    "artifact_kind": "position_table",
                    "media_type": (
                        "application/vnd.openxmlformats-officedocument."
                        "spreadsheetml.sheet"
                    ),
                    "metadata": {"discovered_by": "test"},
                }
            )
        ]


def test_discovery_scans_only_opted_in_enabled_sources_and_keeps_jobs_private(tmp_path) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    enabled = _discovery_source("enabled")
    disabled = _discovery_source("disabled", opted_in=False)
    inactive = _discovery_source("inactive", enabled=False)
    for record in (enabled, disabled, inactive):
        database.upsert_source(record)
    collector = FakeNoticeCollector(
        {"enabled": [("2026年公开招聘", "https://careers.example.edu.cn/notices/1")]}
    )
    processor = FakeAttachmentProcessor(database)

    result = discover_configured_government_artifacts(
        settings, database, collector=collector, processor=processor
    )

    assert collector.calls == ["enabled"]
    assert processor.calls == [
        ("enabled", "https://careers.example.edu.cn/notices/1")
    ]
    assert result["configured_sources"] == 1
    assert result["notices_found"] == 1
    assert result["attachments_registered"] == 1
    assert result["position_table_attachments"] == 1
    assert database.list_source_artifacts("enabled")
    jobs, total = database.list_jobs(student_visible=False)
    assert jobs == []
    assert total == 0


def test_discovery_reports_blocked_and_failed_sources_without_false_zero_result(tmp_path) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    for source_id in ("available", "blocked", "failed"):
        database.upsert_source(_discovery_source(source_id))
    collector = FakeNoticeCollector(
        {
            "available": [],
            "blocked": SourceSkipped("robots.txt does not permit collection"),
            "failed": SourceCollectionError("official portal timed out"),
        }
    )

    result = discover_configured_government_artifacts(
        settings,
        database,
        collector=collector,
        processor=FakeAttachmentProcessor(database),
    )

    by_source = {item["source_id"]: item for item in result["source_results"]}
    assert result["configured_sources"] == 3
    assert result["no_notice_sources"] == 1
    assert result["blocked"] == 1
    assert result["failed"] == 1
    assert by_source["available"]["status"] == "ok"
    assert by_source["blocked"]["status"] == "blocked"
    assert by_source["failed"]["status"] == "failed"
