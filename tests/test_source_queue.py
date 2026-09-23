from __future__ import annotations

from datetime import datetime, timezone

from job_hub.db import Database, utc_now


def _source(source_id: str) -> dict[str, object]:
    return {
        "id": source_id,
        "name": f"来源 {source_id}",
        "publisher": "官方单位",
        "homepage_url": "https://official.example.cn/",
        "source_type": "manual",
        "category": "自然资源、地调与地勘",
        "source_tier": "A",
        "enabled": True,
        "config": {"queue_priority": 10},
    }


def test_source_queue_claims_and_reschedules_completed_work(tmp_path) -> None:
    database = Database(tmp_path / "queue.sqlite3")
    database.initialize()
    source = _source("source-a")
    database.upsert_source(source)
    database.ensure_source_tasks([source])

    claimed = database.claim_source_task("source-a", lease_seconds=60)
    assert claimed is not None
    assert claimed["status"] == "running"
    assert claimed["attempts"] == 1
    assert database.claim_source_task("source-a", lease_seconds=60) is None

    database.complete_source_task("source-a", next_attempt_seconds=0)
    due = database.list_source_tasks(due_only=True)
    assert due[0]["source_id"] == "source-a"


def test_source_queue_keeps_policy_blocked_sources_distinct_from_success(tmp_path) -> None:
    database = Database(tmp_path / "queue.sqlite3")
    database.initialize()
    source = _source("source-b")
    database.upsert_source(source)
    database.ensure_source_tasks([source])
    assert database.claim_source_task("source-b") is not None

    database.fail_source_task(
        "source-b",
        error="robots.txt does not permit collection",
        error_class="access_policy",
        retry_after_seconds=3600,
        blocked=True,
    )

    task = database.get_source_task("source-b")
    assert task is not None
    assert task["status"] == "blocked"
    assert task["last_error_class"] == "access_policy"
    assert database.list_source_tasks(due_only=True) == []


def test_queue_migration_is_additive_for_existing_database(tmp_path) -> None:
    database = Database(tmp_path / "queue.sqlite3")
    database.initialize()
    with database.connect() as connection:
        names = {
            row["name"] for row in connection.execute("PRAGMA table_info(source_tasks)")
        }
    assert {"source_id", "status", "attempts", "next_attempt_at"}.issubset(names)
