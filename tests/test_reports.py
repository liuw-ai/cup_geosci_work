from __future__ import annotations

from job_hub.db import Database
from job_hub.pipeline import JobPipeline
from job_hub.reports import publish_daily_report
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


def test_worker_heartbeat_can_be_read_without_touching_job_data(tmp_path) -> None:
    database = Database(make_settings(tmp_path).database_path)
    database.initialize()

    database.record_service_heartbeat("worker", "running", "test")
    heartbeat = database.get_service_heartbeat("worker")

    assert heartbeat is not None
    assert heartbeat["status"] == "running"
    assert heartbeat["detail"] == "test"
