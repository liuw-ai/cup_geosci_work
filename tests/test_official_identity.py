from __future__ import annotations

import sqlite3

from job_hub.db import Database
from job_hub.official_identity import iguopin_detail_key
from job_hub.pipeline import JobPipeline
from job_hub.sources import RawPosting

from conftest import make_settings


def _source(source_id: str, *, precedence: int, enabled: bool = True) -> dict[str, object]:
    return {
        "id": source_id,
        "name": source_id,
        "publisher": "测试官方单位",
        "homepage_url": "https://official.example.cn/jobs",
        "source_type": "landing_page",
        "category": "自然资源、地调与地勘",
        "source_tier": "A",
        "enabled": enabled,
        "config": {
            "minimum_relevance": 0,
            "official_detail_precedence": precedence,
        },
    }


def _posting(
    external_id: str,
    detail_id: str,
    *,
    title: str = "地质工程师",
) -> RawPosting:
    detail_url = f"https://www.iguopin.com/job/detail?id={detail_id}"
    return RawPosting(
        title=title,
        employer="测试能源集团",
        source_url=detail_url,
        application_url=detail_url,
        text="地质工程专业本科及以上，工作地点北京，报名截止2026-12-31。",
        summary="官方岗位详情。",
        published_date="2026-10-01",
        deadline_date="2026-12-31",
        location="北京",
        external_id=external_id,
        field_evidence={
            "evidence_scope": "official_iguopin_browser_detail",
            "岗位": title,
            "专业范围": "地质工程",
            "学历要求": "本科及以上",
            "工作地点": "北京",
            "招聘人数": "1",
            "报名截止": "2026-12-31",
            "官方详情链接": detail_url,
        },
    )


def test_iguopin_identity_accepts_only_a_concrete_global_detail_url() -> None:
    assert (
        iguopin_detail_key("https://www.iguopin.com/job/detail?id=217600756180583891")
        == "iguopin-job:217600756180583891"
    )
    assert iguopin_detail_key("https://cmgb.iguopin.com/jobCampus?id=217600756180583891") is None
    assert iguopin_detail_key("https://www.iguopin.com/job/detail") is None
    assert iguopin_detail_key("https://untrusted.example/job/detail?id=217600756180583891") is None


def test_public_reads_fold_same_official_detail_and_fallback_when_preferred_source_stops(
    tmp_path,
) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    specialist = _source("specialist-source", precedence=10)
    general = _source("general-iguopin-source", precedence=900)
    database.upsert_source(specialist)
    database.upsert_source(general)
    pipeline = JobPipeline(settings, database)

    specialist_id, _ = database.save_job(
        pipeline.normalize_posting(_posting("specialist-1", "217600756180583891"), specialist)
    )
    general_id, _ = database.save_job(
        pipeline.normalize_posting(_posting("general-1", "217600756180583891"), general)
    )

    audit_rows, audit_total = database.list_jobs(
        page_size=None, only_open=False, student_visible=False
    )
    assert audit_total == 2
    assert {row["official_detail_key"] for row in audit_rows} == {
        "iguopin-job:217600756180583891"
    }

    public_rows, public_total = database.list_jobs(
        page_size=None, as_of_date="2026-10-03"
    )
    assert public_total == 1
    assert public_rows[0]["id"] == specialist_id
    assert database.count_open_jobs(as_of_date="2026-10-03") == 1
    assert database.list_categories(as_of_date="2026-10-03") == [
        {"category": public_rows[0]["category"], "count": 1}
    ]
    assert [row["id"] for row in database.upcoming_deadlines("2026-10-03", "2026-12-31")] == [
        specialist_id
    ]

    database.set_source_enabled("specialist-source", False)

    fallback_rows, fallback_total = database.list_jobs(
        page_size=None, as_of_date="2026-10-03"
    )
    assert fallback_total == 1
    assert fallback_rows[0]["id"] == general_id
    # A bookmarked public page for the former primary record resolves to the
    # active same-detail source instead of presenting a dead vacancy.
    assert database.find_public_job(specialist_id, as_of_date="2026-10-03")["id"] == general_id


def test_same_title_with_different_official_detail_ids_remains_two_jobs(tmp_path) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    source = _source("specialist-source", precedence=10)
    database.upsert_source(source)
    pipeline = JobPipeline(settings, database)
    database.save_job(pipeline.normalize_posting(_posting("one", "217600756180583891"), source))
    database.save_job(pipeline.normalize_posting(_posting("two", "217600756180583892"), source))

    rows, total = database.list_jobs(page_size=None, as_of_date="2026-10-03")
    assert total == 2
    assert {row["official_detail_key"] for row in rows} == {
        "iguopin-job:217600756180583891",
        "iguopin-job:217600756180583892",
    }


def test_migration_backfills_existing_concrete_iguopin_detail_identity(tmp_path) -> None:
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
        INSERT INTO jobs (
            fingerprint, content_hash, title, employer, category, source_tier,
            source_name, source_url, first_seen_at, last_seen_at, created_at, updated_at
        ) VALUES (
            'legacy-detail', 'legacy-detail', '地质工程师', '测试单位', '自然资源、地调与地勘', 'A',
            '旧来源', 'https://www.iguopin.com/job/detail?id=217600756180583891',
            '2026-10-01T00:00:00Z', '2026-10-01T00:00:00Z',
            '2026-10-01T00:00:00Z', '2026-10-01T00:00:00Z'
        );
        """
    )
    connection.commit()
    connection.close()

    database = Database(path)
    database.initialize()

    with database.connect() as migrated:
        row = migrated.execute(
            "SELECT official_detail_key, official_detail_precedence FROM jobs WHERE id = 1"
        ).fetchone()
    assert row["official_detail_key"] == "iguopin-job:217600756180583891"
    assert row["official_detail_precedence"] == 100
