from __future__ import annotations

from datetime import timedelta

from job_hub.db import Database
from job_hub.pipeline import JobPipeline
from job_hub.reports import build_daily_report, local_today, publish_daily_report
from job_hub.sources import RawPosting

from conftest import make_settings, source


def test_published_daily_report_is_frozen_for_its_calendar_day(tmp_path) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    database.upsert_source(source())
    pipeline = JobPipeline(settings, database)

    first_posting = RawPosting(
        title="地质工程师招聘 A",
        employer="测试能源集团",
        source_url="https://careers.example.edu.cn/jobs/frozen-a",
        application_url=None,
        text="面向地质工程硕士毕业生的官方招聘。",
        summary="第一条岗位。",
        published_date=None,
        deadline_date="2099-12-31",
        location="北京",
    )
    database.save_job(
        pipeline.normalize_posting(
            first_posting,
            database.get_source("official-test-source"),
        )
    )
    first_report = publish_daily_report(database, settings)

    second_posting = RawPosting(
        title="地质工程师招聘 B",
        employer="测试能源集团",
        source_url="https://careers.example.edu.cn/jobs/frozen-b",
        application_url=None,
        text="面向地质工程硕士毕业生的官方招聘。",
        summary="第二条岗位。",
        published_date=None,
        deadline_date="2099-12-31",
        location="北京",
    )
    database.save_job(
        pipeline.normalize_posting(
            second_posting,
            database.get_source("official-test-source"),
        )
    )
    second_report = publish_daily_report(database, settings)

    assert second_report["stats"] == first_report["stats"]
    assert database.get_daily_report(first_report["report_date"])["stats"] == (
        first_report["stats"]
    )


def test_daily_report_can_be_refreshed_after_verified_import(tmp_path) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    database.upsert_source(source())
    pipeline = JobPipeline(settings, database)
    first = RawPosting(
        title="地质工程师招聘",
        employer="测试能源集团",
        source_url="https://careers.example.edu.cn/jobs/refresh-a",
        application_url=None,
        text="面向地质工程硕士毕业生的官方招聘。",
        summary="首条岗位。",
        published_date=None,
        deadline_date="2099-12-31",
        location="北京",
        field_evidence={
            "evidence_scope": "official_html_table_row",
            "岗位": "地质工程师招聘",
            "专业范围": "地质工程",
            "学历要求": "硕士",
        },
    )
    database.save_job(
        pipeline.normalize_posting(first, database.get_source("official-test-source"))
    )
    initial = publish_daily_report(database, settings)

    second = RawPosting(
        **{
            **first.__dict__,
            "title": "地质工程师招聘 B",
            "source_url": "https://careers.example.edu.cn/jobs/refresh-b",
            "field_evidence": {
                "evidence_scope": "official_html_table_row",
                "岗位": "地质工程师招聘 B",
                "专业范围": "地质工程",
                "学历要求": "硕士",
            },
        }
    )
    database.save_job(
        pipeline.normalize_posting(second, database.get_source("official-test-source"))
    )
    refreshed = publish_daily_report(database, settings, force_refresh=True)

    assert refreshed["stats"]["open_total"] == initial["stats"]["open_total"] + 1
    assert len(refreshed["new_jobs"]) == 2


def test_worker_heartbeat_can_be_read_without_touching_job_data(tmp_path) -> None:
    database = Database(make_settings(tmp_path).database_path)
    database.initialize()

    database.record_service_heartbeat("worker", "running", "test")
    heartbeat = database.get_service_heartbeat("worker")

    assert heartbeat is not None
    assert heartbeat["status"] == "running"
    assert heartbeat["detail"] == "test"


def test_expired_jobs_leave_public_views_and_are_counted_once_per_day(tmp_path) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    database.upsert_source(source())
    pipeline = JobPipeline(settings, database)
    today = local_today(settings)
    posting = RawPosting(
        title="地质工程师招聘",
        employer="测试能源集团",
        source_url="https://careers.example.edu.cn/jobs/expiry",
        application_url=None,
        text="面向地质工程硕士毕业生的官方招聘。",
        summary="可验证的测试岗位。",
        published_date=None,
        deadline_date="2099-12-31",
        location="北京",
        field_evidence={
            "evidence_scope": "official_html_table_row",
            "岗位": "地质工程师招聘",
            "专业范围": "地质工程",
            "学历要求": "硕士",
        },
    )
    normalized = pipeline.normalize_posting(posting, database.get_source("official-test-source"))
    job_id, _ = database.save_job(normalized)

    yesterday = (today - timedelta(days=1)).isoformat()
    with database.transaction() as connection:
        connection.execute(
            "UPDATE jobs SET deadline_date = ?, status = 'open' WHERE id = ?",
            (today.isoformat(), job_id),
        )
    assert database.expire_jobs_before(today.isoformat()) == 0
    assert database.find_public_job(job_id) is not None

    with database.transaction() as connection:
        connection.execute(
            "UPDATE jobs SET deadline_date = ?, status = 'open' WHERE id = ?",
            (yesterday, job_id),
        )

    report = build_daily_report(database, settings, today)
    assert report["stats"]["expired"] == 1
    assert report["stats"]["open_total"] == 0
    assert database.list_jobs()[1] == 0
    assert database.find_public_job(job_id) is None
    internal, total = database.list_jobs(only_open=False, student_visible=False)
    assert total == 1
    assert internal[0]["status"] == "expired"
    with database.connect() as connection:
        event = connection.execute(
            "SELECT event_type FROM job_events WHERE job_id = ? ORDER BY id DESC LIMIT 1",
            (job_id,),
        ).fetchone()
    assert event["event_type"] == "expired"

    refreshed = build_daily_report(database, settings, today)
    assert refreshed["stats"]["expired"] == 1
