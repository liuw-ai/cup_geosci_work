from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from job_hub.db import Database
from job_hub.run_ledger import build_run_ledger, classify_run_outcome


def test_completed_empty_scan_is_distinct_from_unavailable_source() -> None:
    assert (
        classify_run_outcome("finished", discovered_count=0, open_matching_count=0)
        == "success_without_matches"
    )
    assert (
        classify_run_outcome("failed", error="TLS EOF while connecting")
        == "source_unavailable"
    )
    assert (
        classify_run_outcome("skipped", error="robots.txt disallows the listing")
        == "access_limited"
    )
    assert (
        classify_run_outcome("failed", error="parser could not decode JSON")
        == "parse_failed"
    )
    assert (
        classify_run_outcome(
            "finished", discovered_count=4, open_matching_count=0, manual_review_count=4
        )
        == "manual_review_required"
    )


def test_legacy_unknown_outcome_is_inferred_from_finished_run() -> None:
    ledger = build_run_ledger(
        [
            {
                "id": 1,
                "source_id": "source",
                "started_at": "2026-09-29T01:00:00Z",
                "finished_at": "2026-09-29T01:00:01Z",
                "status": "finished",
                "outcome": "unknown",
                "discovered_count": 4,
                "open_matching_count": 2,
            }
        ],
        report_date=date(2026, 9, 29),
        timezone="Asia/Shanghai",
    )
    assert ledger["summary"]["success_with_matches"] == 1
    assert ledger["summary"]["unknown"] == 0


def test_run_ledger_counts_only_local_day_and_preserves_metrics(tmp_path) -> None:
    database = Database(tmp_path / "jobs.sqlite3")
    database.initialize()
    database.upsert_source(
        {
            "id": "official-test-source",
            "name": "测试官方来源",
            "publisher": "测试单位",
            "homepage_url": "https://example.gov.cn/jobs",
            "source_type": "manual",
            "category": "事业单位",
            "source_tier": "A",
            "enabled": True,
            "config": {},
        }
    )
    database.upsert_source(
        {
            "id": "second-source",
            "name": "当天未运行来源",
            "publisher": "测试单位二",
            "homepage_url": "https://example.gov.cn/other",
            "source_type": "manual",
            "category": "公务员",
            "source_tier": "A",
            "enabled": True,
            "config": {},
        }
    )
    run_id = database.record_crawl_start("official-test-source", transport_mode="direct")
    database.record_crawl_finish(
        run_id,
        "finished",
        discovered_count=3,
        open_matching_count=2,
        inserted_count=2,
        outcome="success_with_matches",
        attempts=4,
        retryable_failures=1,
        evidence_complete_count=2,
        manual_review_count=1,
        metadata={"request_count": 3},
    )

    ledger = build_run_ledger(
        database.list_crawl_runs(),
        sources=database.list_sources(),
        report_date=datetime.now(ZoneInfo("Asia/Shanghai")).date(),
        timezone="Asia/Shanghai",
    )

    assert ledger["summary"]["run_count"] == 2
    assert ledger["summary"]["success_with_matches"] == 1
    assert ledger["summary"]["not_run"] == 1
    row = ledger["runs"][0]
    assert row["source_name"] == "测试官方来源"
    assert row["transport_mode"] == "direct"
    assert row["attempts"] == 4
    assert row["retryable_failures"] == 1
    assert row["evidence_complete_count"] == 2
    assert row["manual_review_count"] == 1


def test_legacy_crawl_table_gets_run_ledger_columns(tmp_path) -> None:
    path = tmp_path / "legacy.sqlite3"
    import sqlite3

    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE crawl_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id TEXT,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            status TEXT NOT NULL,
            discovered_count INTEGER NOT NULL DEFAULT 0,
            inserted_count INTEGER NOT NULL DEFAULT 0,
            updated_count INTEGER NOT NULL DEFAULT 0,
            error_message TEXT
        );
        """
    )
    connection.commit()
    connection.close()

    database = Database(path)
    database.initialize()
    with database.connect() as connection:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(crawl_runs)")}
    assert {
        "outcome",
        "attempts",
        "retryable_failures",
        "transport_mode",
        "evidence_complete_count",
        "manual_review_count",
        "attachment_success_count",
        "metadata_json",
    }.issubset(columns)
