from __future__ import annotations

import sqlite3

from job_hub.db import Database


def test_initialize_adds_v03_columns_to_a_v02_jobs_table(tmp_path) -> None:
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
            'legacy', 'legacy', '旧岗位', '旧单位', '自然资源、地调与地勘', 'A',
            '旧来源', 'https://official.example.gov.cn/jobs/legacy',
            '2026-09-18T00:00:00Z', '2026-09-18T00:00:00Z',
            '2026-09-18T00:00:00Z', '2026-09-18T00:00:00Z'
        );
        """
    )
    connection.commit()
    connection.close()

    database = Database(path)
    database.initialize()

    with database.connect() as migrated:
        names = {row["name"] for row in migrated.execute("PRAGMA table_info(jobs)")}
        crawl_run_names = {
            row["name"] for row in migrated.execute("PRAGMA table_info(crawl_runs)")
        }
        row = migrated.execute(
            "SELECT official_evidence_url, verification_status FROM jobs WHERE id = 1"
        ).fetchone()
    assert {
        "canonical_employer_id",
        "province",
        "location_confidence",
        "official_evidence_url",
        "verification_status",
    }.issubset(names)
    assert row["official_evidence_url"] == "https://official.example.gov.cn/jobs/legacy"
    assert row["verification_status"] == "published_official"
    assert "open_matching_count" in crawl_run_names


def test_update_job_normalization_reports_and_persists_a_change(tmp_path) -> None:
    path = tmp_path / "jobs.sqlite3"
    database = Database(path)
    database.initialize()
    database.upsert_source(
        {
            "id": "source",
            "name": "来源",
            "publisher": "单位",
            "homepage_url": "https://example.edu.cn/",
            "source_type": "manual",
            "category": "自然资源、地调与地勘",
            "source_tier": "A",
            "enabled": True,
            "config": {},
        }
    )
    job_id, _ = database.save_job(
        {
            "source_id": "source",
            "fingerprint": "fingerprint",
            "content_hash": "hash",
            "title": "地质岗位",
            "employer": "单位",
            "category": "自然资源、地调与地勘",
            "source_tier": "A",
            "source_name": "来源",
            "source_url": "https://example.edu.cn/job",
            "official_evidence_url": "https://example.edu.cn/job",
            "degree_levels": [],
            "major_tags": ["地质工程"],
            "summary": "",
            "description": "工作地点：新疆克拉玛依",
            "relevance_score": 50,
            "relevance_band": "强相关",
        }
    )
    changed = database.update_job_normalization(
        job_id,
        canonical_employer_id=None,
        canonical_employer_name=None,
        parent_employer_name=None,
        location="新疆克拉玛依",
        province="新疆",
        city="克拉玛依",
        country_or_region="中国大陆",
        location_confidence="explicit",
        location_evidence="新疆",
    )
    assert changed is True
    assert database.find_job(job_id)["city"] == "克拉玛依"
