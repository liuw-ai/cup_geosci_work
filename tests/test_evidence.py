from __future__ import annotations

import sqlite3

import pytest

from job_hub.app import create_app
from job_hub.audit import audit_database
from job_hub.db import Database
from job_hub.pipeline import JobPipeline
from job_hub.sources import RawPosting

from conftest import make_settings, source


def _saved_job(database: Database, settings, *, source_id: str = "official-test-source") -> int:
    pipeline = JobPipeline(settings, database)
    posting = RawPosting(
        title="地质工程师招聘",
        employer="测试能源集团",
        source_url="https://careers.example.edu.cn/jobs/1",
        application_url=None,
        text="面向地质工程硕士的官方招聘，报名截止时间 2026 年 12 月 31 日。",
        summary="官方招聘岗位。",
        published_date="2026-09-20",
        deadline_date="2026-12-31",
        location="北京",
    )
    job_id, _ = database.save_job(
        pipeline.normalize_posting(posting, database.get_source(source_id))
    )
    return job_id


def test_saved_public_job_automatically_receives_verified_official_evidence(tmp_path) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    database.upsert_source(source())

    job_id = _saved_job(database, settings)

    evidence = database.list_job_evidence(job_id)
    assert len(evidence) == 1
    assert evidence[0]["evidence_type"] == "official_page"
    assert evidence[0]["verification_status"] == "verified"
    assert evidence[0]["evidence_url"].endswith("/jobs/1")
    assert database.has_verified_official_evidence(job_id) is True


def test_initialize_backfills_legacy_job_evidence_idempotently(tmp_path) -> None:
    path = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id TEXT,
            external_id TEXT,
            fingerprint TEXT NOT NULL UNIQUE,
            content_hash TEXT NOT NULL,
            title TEXT NOT NULL,
            employer TEXT NOT NULL,
            group_name TEXT,
            category TEXT NOT NULL,
            source_tier TEXT NOT NULL,
            source_name TEXT NOT NULL,
            source_url TEXT NOT NULL,
            application_url TEXT,
            location TEXT,
            published_date TEXT,
            deadline_date TEXT,
            degree_levels_json TEXT NOT NULL DEFAULT '[]',
            major_tags_json TEXT NOT NULL DEFAULT '[]',
            summary TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            relevance_score INTEGER NOT NULL DEFAULT 0,
            relevance_band TEXT NOT NULL DEFAULT '拓展机会',
            status TEXT NOT NULL DEFAULT 'open',
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
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
        INSERT INTO jobs (
            fingerprint, content_hash, title, employer, category, source_tier,
            source_name, source_url, first_seen_at, last_seen_at, created_at, updated_at
        ) VALUES (
            'legacy-evidence', 'legacy-evidence', '旧岗位', '旧单位', '自然资源、地调与地勘', 'A',
            '旧来源', 'https://official.example.gov.cn/jobs/legacy',
            '2026-09-20T00:00:00Z', '2026-09-20T00:00:00Z',
            '2026-09-20T00:00:00Z', '2026-09-20T00:00:00Z'
        );
        """
    )
    connection.commit()
    connection.close()

    database = Database(path)
    database.initialize()
    first = database.list_job_evidence(1)
    database.initialize()
    second = database.list_job_evidence(1)

    assert len(first) == len(second) == 1
    assert first[0]["metadata"]["origin"] == "phase_1_migration"


def test_initialize_backfills_legacy_candidate_lead_status_snapshot_once(tmp_path) -> None:
    path = tmp_path / "legacy-lead.sqlite3"
    database = Database(path)
    database.initialize()
    with database.transaction() as connection:
        cursor = connection.execute(
            """
            INSERT INTO candidate_leads (
                lead_provider, lead_url, title, official_url, verification_status,
                verification_note, metadata_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "旧线索来源",
                "https://clue.example.com/legacy/1",
                "旧地质局招聘线索",
                "https://official.example.gov.cn/jobs/1",
                "official_url_found",
                "已定位官网，待核对岗位字段。",
                "{}",
                "2026-09-20T00:00:00Z",
                "2026-09-20T00:00:00Z",
            ),
        )
        lead_id = int(cursor.lastrowid)

    database.initialize()
    first = database.list_candidate_lead_events(lead_id)
    database.initialize()
    second = database.list_candidate_lead_events(lead_id)

    assert len(first) == len(second) == 1
    assert first[0]["event_type"] == "status_snapshot"
    assert first[0]["to_status"] == "official_url_found"
    assert first[0]["payload"]["origin"] == "phase_1_migration"


def test_attachment_cannot_be_linked_to_a_job_from_another_source(tmp_path) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    database.upsert_source(source())
    second_source = source()
    second_source.update(
        {
            "id": "second-source",
            "homepage_url": "https://second.example.edu.cn/",
        }
    )
    database.upsert_source(second_source)
    job_id = _saved_job(database, settings)
    artifact = database.upsert_source_artifact(
        {
            "source_id": "second-source",
            "parent_url": "https://second.example.edu.cn/notice/1",
            "artifact_url": "https://second.example.edu.cn/files/jobs.xlsx",
            "artifact_kind": "position_table",
            "media_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "metadata": {"display_name": "职位表"},
        }
    )

    with pytest.raises(ValueError, match="registered source"):
        database.add_job_evidence(
            job_id,
            {
                "artifact_id": artifact["id"],
                "evidence_type": "attachment",
                "field_name": "major_tags",
                "evidence_url": artifact["artifact_url"],
                "verification_status": "pending",
            },
        )


def test_candidate_lead_state_machine_records_private_history(tmp_path) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    database.upsert_source(source())
    lead = database.create_candidate_lead(
        {
            "lead_provider": "中公教育",
            "lead_url": "https://clue.example.com/lead/1",
            "title": "某省地质局招聘",
        }
    )

    with pytest.raises(ValueError, match="Illegal candidate lead transition"):
        database.update_candidate_lead(
            lead["id"],
            {
                "official_url": "https://official.example.gov.cn/jobs/1",
                "verification_status": "official_content_verified",
                "verification_note": "已核验。",
            },
        )
    with pytest.raises(ValueError, match="need_review requires"):
        database.update_candidate_lead(
            lead["id"], {"verification_status": "need_review"}
        )

    located = database.update_candidate_lead(
        lead["id"],
        {
            "official_url": "https://official.example.gov.cn/jobs/1",
            "verification_status": "official_url_found",
        },
    )
    assert located and located["verification_status"] == "official_url_found"
    verified = database.update_candidate_lead(
        lead["id"],
        {
            "verification_status": "official_content_verified",
            "official_domain_status": "manual_review_approved",
            "verification_note": "已核对官方公告、专业要求和截止日期。",
        },
    )
    assert verified and verified["verification_status"] == "official_content_verified"
    history = database.list_candidate_lead_events(lead["id"])
    assert [(item["from_status"], item["to_status"]) for item in history] == [
        (None, "candidate"),
        ("candidate", "official_url_found"),
        ("official_url_found", "official_content_verified"),
    ]
    job_id = _saved_job(database, settings)
    published = database.mark_candidate_lead_published(lead["id"], job_id)
    assert published and published["verification_status"] == "published"
    with pytest.raises(ValueError, match="has not passed"):
        database.mark_candidate_lead_published(lead["id"], job_id)


def test_audit_rejects_public_job_without_verified_evidence(tmp_path) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    database.upsert_source(source())
    job_id = _saved_job(database, settings)
    with database.transaction() as connection:
        connection.execute("DELETE FROM job_evidence WHERE job_id = ?", (job_id,))

    audit = audit_database(database, settings)
    assert audit["ok"] is False
    assert any(
        issue["code"] == "missing_verified_official_evidence"
        for issue in audit["issues"]
    )


def test_artifact_and_evidence_admin_apis_are_private(tmp_path) -> None:
    settings = make_settings(tmp_path)
    app = create_app(settings)
    database = app.extensions["database"]
    database.upsert_source(source())
    job_id = _saved_job(database, settings)
    client = app.test_client()
    headers = {"X-Admin-Token": "test-admin-token"}

    assert client.get("/api/admin/artifacts").status_code == 403
    created = client.post(
        "/api/admin/artifacts",
        headers=headers,
        json={
            "source_id": "official-test-source",
            "parent_url": "https://careers.example.edu.cn/jobs/1",
            "artifact_url": "https://careers.example.edu.cn/files/jobs.pdf",
            "artifact_kind": "position_table",
            "media_type": "application/pdf",
        },
    )
    assert created.status_code == 201
    artifact = created.get_json()
    evidence = client.post(
        f"/api/admin/jobs/{job_id}/evidence",
        headers=headers,
        json={
            "artifact_id": artifact["id"],
            "evidence_type": "attachment",
            "field_name": "major_tags",
            "evidence_url": artifact["artifact_url"],
            "locator": "sheet:岗位表",
            "verification_status": "pending",
        },
    )
    assert evidence.status_code == 201
    response = client.get(f"/api/admin/jobs/{job_id}/evidence", headers=headers)
    assert len(response.get_json()["items"]) == 2
    assert client.get("/api/jobs").get_json()["total"] == 1
