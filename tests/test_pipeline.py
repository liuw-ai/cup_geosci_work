from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from job_hub.audit import audit_database
from job_hub.db import Database
from job_hub.pipeline import JobPipeline
from job_hub.reports import build_daily_report
from job_hub.sources import RawPosting

from conftest import make_settings, source


class FakeCollector:
    def __init__(self, posting: RawPosting) -> None:
        self.posting = posting

    def collect(self, _source: object) -> list[RawPosting]:
        return [self.posting]


class FailingCollector:
    def collect(self, _source: object) -> list[RawPosting]:
        raise RuntimeError("unexpected source parser failure")


def test_pipeline_deduplicates_and_creates_daily_change(tmp_path) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    official_source = source()
    database.upsert_source(official_source)
    posting = RawPosting(
        title="油田勘探开发研究院 2027 届校园招聘",
        employer="测试能源集团",
        source_url="https://careers.example.edu.cn/jobs/1001",
        application_url=None,
        text=(
            "面向资源勘查工程、地质工程、地质学、地球物理专业本科和硕士毕业生，"
            "报名截止时间为2026年12月10日。"
        ),
        summary="面向地学相关专业的官方校园招聘。",
        published_date="2026-09-17",
        deadline_date="2026-12-10",
        location="北京 / 新疆",
    )
    pipeline = JobPipeline(settings, database, FakeCollector(posting))

    first = pipeline.sync_source(database.get_source("official-test-source"))
    second = pipeline.sync_source(database.get_source("official-test-source"))

    assert first.created == 1
    assert first.updated == 0
    assert second.created == 0
    assert second.updated == 0
    jobs, total = database.list_jobs()
    assert total == 0
    internal_jobs, internal_total = database.list_jobs(student_visible=False)
    assert internal_total == 1
    # Announcement prose is a private discovery hint, never a student-facing
    # recommendation without row-level professional and degree evidence.
    assert internal_jobs[0]["publication_status"] == "pending_evidence"

    report = build_daily_report(database, settings)
    assert report["stats"]["new"] == 0
    assert report["new_jobs"] == []

    assert database.count_jobs_for_source("official-test-source") == 1
    assert database.delete_jobs_for_source("official-test-source") == 1
    assert database.count_jobs_for_source("official-test-source") == 0
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM job_events").fetchone()[0] == 0


def test_sync_all_reports_progress_without_changing_results(tmp_path) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    official_source = source()
    database.upsert_source(official_source)
    pipeline = JobPipeline(settings, database, FakeCollector(RawPosting(
        title="地质勘探技术岗",
        employer="测试能源集团",
        source_url="https://careers.example.edu.cn/jobs/progress",
        application_url=None,
        text="资源勘查工程本科可报，工作地点北京。",
        summary="地学岗位",
        published_date="2026-09-20",
        deadline_date="2026-12-20",
        location="北京",
    )))
    events: list[tuple[int, int, str]] = []

    result = pipeline.sync_all(progress_callback=lambda completed, total, source_id: events.append(
        (completed, total, source_id)
    ))

    assert len(result.source_results) == 1
    assert result.source_results[0].status == "finished"
    assert events[0] == (0, 1, "official-test-source")
    assert events[-1] == (1, 1, "official-test-source")


def test_successful_scan_records_zero_open_matches_without_marking_source_unavailable(
    tmp_path,
) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    official_source = source()
    official_source["config"] = {"minimum_relevance": 100}
    database.upsert_source(official_source)
    posting = RawPosting(
        title="普通行政岗位",
        employer="测试单位",
        source_url="https://careers.example.edu.cn/jobs/no-match",
        application_url=None,
        text="面向不限专业毕业生的公开招聘。",
        summary="不属于地学专业的测试公告。",
        published_date="2026-09-17",
        deadline_date="2026-12-31",
        location="北京",
    )

    result = JobPipeline(settings, database, FakeCollector(posting)).sync_source(
        database.get_source("official-test-source")
    )

    assert result.status == "finished"
    assert result.discovered == 1
    assert result.open_matches == 0
    assert result.skipped == 1
    assert database.get_source_health("official-test-source")["status"] == "source_active"
    latest_run = database.list_latest_crawl_runs()[0]
    assert latest_run["status"] == "finished"
    assert latest_run["open_matching_count"] == 0


def test_delete_job_removes_only_the_exact_invalid_record(tmp_path) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    official_source = source()
    database.upsert_source(official_source)
    pipeline = JobPipeline(settings, database)
    postings = [
        RawPosting(
            title="Title-only false positive",
            employer="Test employer",
            source_url="https://careers.example.edu.cn/jobs/invalid",
            application_url=None,
            text="Title-only false positive.",
            summary="",
            published_date=None,
            deadline_date=None,
            location=None,
        ),
        RawPosting(
            title="Geological engineering role",
            employer="Test employer",
            source_url="https://careers.example.edu.cn/jobs/valid",
            application_url=None,
            text="Official role for geological engineering master graduates.",
            summary="A valid official vacancy.",
            published_date=None,
            deadline_date="2099-12-31",
            location="Beijing",
        ),
    ]
    job_ids = [
        database.save_job(pipeline.normalize_posting(posting, official_source))[0]
        for posting in postings
    ]

    assert database.delete_job(job_ids[0]) is True
    assert database.find_job(job_ids[0]) is None
    assert database.find_job(job_ids[1]) is not None
    assert database.delete_job(job_ids[0]) is False


def test_stale_crawl_run_blocks_audit_then_is_safely_recovered_before_sync(tmp_path) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    database.upsert_source(source())
    stale_run_id = database.record_crawl_start("official-test-source")
    stale_started_at = (
        datetime.now(timezone.utc) - timedelta(seconds=1900)
    ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    with database.transaction() as connection:
        connection.execute(
            "UPDATE crawl_runs SET started_at = ? WHERE id = ?",
            (stale_started_at, stale_run_id),
        )

    audit = audit_database(database, settings)
    assert audit["ok"] is False
    assert audit["issues"][0]["code"] == "stale_crawl_run"

    posting = RawPosting(
        title="地质工程科研助理招聘",
        employer="测试研究院",
        source_url="https://careers.example.edu.cn/jobs/stale-run",
        application_url=None,
        text="面向地质工程硕士毕业生的官方科研助理招聘。",
        summary="测试中断采集记录恢复。",
        published_date="2026-09-17",
        deadline_date=None,
        location="北京",
        field_evidence={
            "evidence_scope": "official_html_table_row",
            "岗位": "地质工程师招聘",
            "专业范围": "地质工程",
            "学历要求": "硕士",
        },
    )
    pipeline = JobPipeline(settings, database, FakeCollector(posting))
    result = pipeline.sync_source(database.get_source("official-test-source"))

    assert result.status == "finished"
    with database.connect() as connection:
        stale_run = connection.execute(
            "SELECT status, finished_at, error_message FROM crawl_runs WHERE id = ?",
            (stale_run_id,),
        ).fetchone()
    assert stale_run["status"] == "interrupted"
    assert stale_run["finished_at"] is not None
    assert "timeout" in stale_run["error_message"]
    assert audit_database(database, settings)["ok"] is True


def test_daily_changes_use_the_configured_local_calendar_day(tmp_path) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    database.upsert_source(source())
    pipeline = JobPipeline(settings, database)
    posting = RawPosting(
        title="地质工程科研助理招聘",
        employer="测试研究院",
        source_url="https://careers.example.edu.cn/jobs/utc-boundary",
        application_url=None,
        text="面向地质工程硕士毕业生的官方科研助理招聘。",
        summary="测试北京时间日报边界。",
        published_date="2026-09-17",
        deadline_date=None,
        location="北京",
        field_evidence={
            "evidence_scope": "official_html_table_row",
            "岗位": "地质工程科研助理招聘",
            "专业范围": "地质工程",
            "学历要求": "硕士",
        },
    )
    database.save_job(
        pipeline.normalize_posting(posting, database.get_source("official-test-source"))
    )
    with database.transaction() as connection:
        connection.execute(
            "UPDATE job_events SET occurred_at = ?",
            ("2026-09-17T16:30:00Z",),
        )

    report = build_daily_report(database, settings, date(2026, 9, 18))

    assert report["stats"]["new"] == 1
    assert report["new_jobs"][0]["title"] == "地质工程科研助理招聘"


def test_explicit_match_text_prevents_institute_name_from_overriding_requirements(tmp_path) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    research_source = source()
    research_source.update(
        {
            "id": "cas-test-source",
            "category": "科研院所与高校",
            "source_tier": "A",
        }
    )
    database.upsert_source(research_source)
    pipeline = JobPipeline(settings, database)
    posting = RawPosting(
        title="财务工作人员",
        employer="烟台海岸带研究所",
        source_url="https://careers.example.edu.cn/jobs/finance",
        application_url=None,
        text="烟台海岸带研究所公开招聘，专业要求为会计、财务管理、审计。",
        summary="非地学专业岗位。",
        published_date="2026-09-17",
        deadline_date=None,
        location="烟台",
        match_text="财务工作人员 会计、财务管理、审计相关专业",
    )

    normalized = pipeline.normalize_posting(posting, research_source)

    assert normalized["major_tags"] == []
    assert normalized["relevance_score"] == 30


def test_unchanged_content_refreshes_derived_matching_fields(tmp_path) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    official_source = source()
    database.upsert_source(official_source)
    pipeline = JobPipeline(settings, database)
    first = RawPosting(
        title="官方岗位",
        employer="测试能源集团",
        source_url="https://careers.example.edu.cn/jobs/derived",
        application_url=None,
        text="岗位正文不变。",
        summary="官方岗位。",
        published_date=None,
        deadline_date=None,
        location="北京",
        match_text="普通岗位",
    )
    second = RawPosting(
        **{
            **first.__dict__,
            "match_text": "地质工程 资源勘查工程",
        }
    )
    first_job = pipeline.normalize_posting(first, official_source)
    second_job = pipeline.normalize_posting(second, official_source)
    job_id, first_outcome = database.save_job(first_job)
    _, second_outcome = database.save_job(second_job)

    assert first_outcome == "created"
    assert second_outcome == "unchanged"
    refreshed = database.find_job(job_id)
    assert refreshed is not None
    assert "地质工程" in refreshed["major_tags"]


def test_audit_accepts_official_host_and_rejects_no_integrity_issues(tmp_path) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    official_source = source()
    database.upsert_source(official_source)
    pipeline = JobPipeline(settings, database)
    posting = RawPosting(
        title="地质工程师招聘",
        employer="测试能源集团",
        source_url="https://careers.example.edu.cn/jobs/audit",
        application_url=None,
        text="面向地质工程硕士的官方招聘岗位。",
        summary="官方招聘岗位。",
        published_date=None,
        deadline_date="2099-12-31",
        location="北京",
    )
    database.save_job(
        pipeline.normalize_posting(posting, official_source)
    )

    result = audit_database(database, settings, date(2026, 9, 18))

    assert result["ok"] is True
    assert result["checked_jobs"] == 1
    assert result["issues"] == []


def test_audit_allows_explicit_multi_role_notice_but_not_implicit_duplicates(tmp_path) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    multi_role_source = source()
    multi_role_source["config"] = {
        **multi_role_source["config"],
        "allow_shared_source_url": True,
    }
    database.upsert_source(multi_role_source)
    pipeline = JobPipeline(settings, database)

    for index, title in enumerate(("地质工程师 A", "地质工程师 B"), start=1):
        posting = RawPosting(
            title=title,
            employer="测试能源集团",
            source_url="https://careers.example.edu.cn/jobs/one-notice",
            application_url=None,
            text="面向地质工程硕士的官方招聘岗位。",
            summary="同一公告中的两个独立岗位。",
            published_date=None,
            deadline_date="2099-12-31",
            location="北京",
            external_id=f"notice-role-{index}",
        )
        database.save_job(pipeline.normalize_posting(posting, multi_role_source))

    result = audit_database(database, settings, date(2026, 9, 18))

    assert result["ok"] is True
    assert result["issues"] == []


def test_unexpected_source_error_is_recorded_as_a_failed_run(tmp_path) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    official_source = source()
    database.upsert_source(official_source)
    pipeline = JobPipeline(settings, database, FailingCollector())

    result = pipeline.sync_source(database.get_source("official-test-source"))

    assert result.status == "failed"
    assert result.error == "unexpected source parser failure"
    with database.connect() as connection:
        run = connection.execute(
            "SELECT status, error_message, finished_at FROM crawl_runs"
        ).fetchone()
    assert run["status"] == "failed"
    assert run["error_message"] == "unexpected source parser failure"
    assert run["finished_at"] is not None


def test_audit_allows_administrator_verified_manual_original_link(tmp_path) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    manual_source = source()
    manual_source.update(
        {
            "id": "official-manual-import",
            "source_type": "manual",
            "homepage_url": "https://example.invalid/manual-import",
            "enabled": False,
        }
    )
    database.upsert_source(manual_source)
    pipeline = JobPipeline(settings, database)
    posting = RawPosting(
        title="地质工程师招聘",
        employer="官方单位",
        source_url="https://official-employer.example.cn/jobs/1",
        application_url=None,
        text="面向地质工程专业的官方招聘。",
        summary="已人工核验的官网原文。",
        published_date=None,
        deadline_date="2099-12-31",
        location="北京",
    )
    database.save_job(pipeline.normalize_posting(posting, manual_source))

    result = audit_database(database, settings, date(2026, 9, 18))

    assert result["ok"] is True
