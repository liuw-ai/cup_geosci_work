from __future__ import annotations

from datetime import datetime, timedelta, timezone

from job_hub.db import Database
from job_hub.pipeline import JobPipeline
from job_hub.sources import RawPosting

from conftest import make_settings, source


class Collector:
    def __init__(self, posting: RawPosting) -> None:
        self.posting = posting

    def collect(self, _source: object) -> list[RawPosting]:
        return [self.posting]


def test_stale_snapshot_rows_are_withdrawn_and_auditable(tmp_path) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    official_source = source()
    official_source["config"] = {
        **official_source["config"],
        "max_age_hours": 1,
        "snapshot_captured_at": datetime.now(timezone.utc).isoformat(),
    }
    database.upsert_source(official_source)
    posting = RawPosting(
        title="地质工程勘查技术岗",
        employer="测试地质单位",
        source_url="https://careers.example.edu.cn/jobs/freshness-1",
        application_url="https://careers.example.edu.cn/apply/freshness-1",
        text="地质工程硕士可报，工作地点北京，报名截止时间为2026年12月31日。",
        summary="官方岗位级证据。",
        published_date="2026-09-30",
        deadline_date="2026-12-31",
        location="北京",
        external_id="freshness-1",
        field_evidence={
            "evidence_scope": "official_html_table_row",
            "岗位": "地质工程勘查技术岗",
            "专业范围": "地质工程",
            "学历要求": "硕士",
            "官方详情链接": "https://careers.example.edu.cn/jobs/freshness-1",
        },
    )
    pipeline = JobPipeline(settings, database, Collector(posting))
    result = pipeline.sync_source(database.get_source("official-test-source"))
    assert result.created == 1
    _jobs, total = database.list_jobs(page_size=None)
    assert total == 1
    # The normal sync path does not enable list reconciliation, but a complete
    # successful capture must still record public-read freshness evidence.
    capture = database.get_source_capture_freshness("official-test-source")
    assert capture is not None
    assert capture["crawl_run_id"] == result.run_id
    assert database.get_source("official-test-source")["last_synced_at"] is not None

    old = (datetime.now(timezone.utc) - timedelta(hours=3)).replace(microsecond=0)
    database.upsert_source(
        {
            **official_source,
            "config": {
                **official_source["config"],
                "snapshot_captured_at": old.isoformat().replace("+00:00", "Z"),
            },
        }
    )

    withdrawn = pipeline.expire_stale_source_jobs()
    assert withdrawn == {"official-test-source": 1}
    _public_jobs, public_total = database.list_jobs(page_size=None)
    assert public_total == 0
    history, history_total = database.list_jobs(
        page_size=None, only_open=False, student_visible=False
    )
    assert history_total == 1
    assert history[0]["status"] == "withdrawn"
    with database.connect() as connection:
        event = connection.execute(
            "SELECT payload_json FROM job_events WHERE job_id = ? ORDER BY id DESC LIMIT 1",
            (history[0]["id"],),
        ).fetchone()
    assert event is not None
    assert "新鲜度窗口" in event["payload_json"]


def test_read_side_hides_stale_capture_when_worker_has_not_withdrawn_row(tmp_path) -> None:
    """A worker outage must not leave an old snapshot visible to students."""

    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    official_source = source()
    official_source["config"] = {
        **official_source["config"],
        "max_age_hours": 1,
        "snapshot_captured_at": datetime.now(timezone.utc).isoformat(),
    }
    database.upsert_source(official_source)
    posting = RawPosting(
        title="地质调查技术岗",
        employer="测试地勘单位",
        source_url="https://careers.example.edu.cn/jobs/read-side-freshness",
        application_url=None,
        text="地质学硕士可报，工作地点北京，报名截止时间为2026年12月31日。",
        summary="官方岗位级证据。",
        published_date="2026-09-30",
        deadline_date="2026-12-31",
        location="北京",
        external_id="read-side-freshness",
        field_evidence={
            "evidence_scope": "official_html_table_row",
            "岗位": "地质调查技术岗",
            "专业范围": "地质学",
            "学历要求": "硕士",
            "官方详情链接": "https://careers.example.edu.cn/jobs/read-side-freshness",
        },
    )
    pipeline = JobPipeline(settings, database, Collector(posting))
    pipeline.sync_source(database.get_source("official-test-source"))
    assert database.list_jobs(page_size=None)[1] == 1

    # Simulate time passing while the worker is stopped. The job row remains
    # open for audit, but the public read path must reject it without writing.
    stale = datetime.now(timezone.utc) - timedelta(hours=2)
    database.record_source_capture_freshness(
        "official-test-source",
        captured_at=stale.isoformat(),
    )

    assert database.list_jobs(page_size=None)[1] == 0
    private_rows, private_total = database.list_jobs(
        page_size=None, only_open=False, student_visible=False
    )
    assert private_total == 1
    assert private_rows[0]["status"] == "open"


def test_current_dated_read_uses_current_clock_for_freshness(tmp_path) -> None:
    """A current-day read must not age a fresh capture to end-of-day."""

    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    official_source = source()
    official_source["config"] = {
        **official_source["config"],
        "max_age_hours": 30,
    }
    database.upsert_source(official_source)
    posting = RawPosting(
        title="当天仍有效的地质岗位",
        employer="测试地质单位",
        source_url="https://careers.example.edu.cn/jobs/current-freshness",
        application_url=None,
        text="地质工程硕士可报，工作地点北京，报名截止时间为2026年12月31日。",
        summary="官方岗位级证据。",
        published_date="2026-09-30",
        deadline_date="2026-12-31",
        location="北京",
        external_id="current-freshness",
        field_evidence={
            "evidence_scope": "official_html_table_row",
            "岗位": "当天仍有效的地质岗位",
            "专业范围": "地质工程",
            "学历要求": "硕士",
        },
    )
    pipeline = JobPipeline(settings, database)
    database.save_job(
        pipeline.normalize_posting(posting, database.get_source("official-test-source"))
    )

    # The capture is inside the 30-hour contract now, but would be outside it
    # if today's 23:59 were used as the reference at the current clock time.
    captured_at = datetime.now(timezone.utc) - timedelta(hours=28)
    database.record_source_capture_freshness(
        "official-test-source",
        captured_at=captured_at.isoformat().replace("+00:00", "Z"),
    )

    today = datetime.now(timezone.utc).date().isoformat()
    assert database.list_jobs(page_size=None, as_of_date=today)[1] == 1


def test_read_side_requires_capture_evidence_for_configured_snapshot_source(tmp_path) -> None:
    """Existing rows without a successful capture projection fail closed."""

    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    official_source = source()
    official_source["config"] = {
        **official_source["config"],
        "max_age_hours": 24,
        "snapshot_captured_at": datetime.now(timezone.utc).isoformat(),
    }
    database.upsert_source(official_source)
    posting = RawPosting(
        title="地质工程技术岗",
        employer="测试地勘单位",
        source_url="https://careers.example.edu.cn/jobs/missing-capture-proof",
        application_url=None,
        text="地质工程硕士可报，工作地点北京，报名截止时间为2026年12月31日。",
        summary="官方岗位级证据。",
        published_date="2026-09-30",
        deadline_date="2026-12-31",
        location="北京",
        field_evidence={
            "evidence_scope": "official_html_table_row",
            "岗位": "地质工程技术岗",
            "专业范围": "地质工程",
            "学历要求": "硕士",
        },
    )
    pipeline = JobPipeline(settings, database)
    database.save_job(
        pipeline.normalize_posting(posting, database.get_source("official-test-source"))
    )

    assert database.list_jobs(page_size=None)[1] == 0
    assert database.list_jobs(
        page_size=None, only_open=False, student_visible=False
    )[1] == 1


def test_sources_without_freshness_contract_are_not_withdrawn(tmp_path) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    official_source = source()
    database.upsert_source(official_source)
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO source_health (source_id, status, checked_at, last_success_at, detail) VALUES (?, ?, ?, ?, ?)",
            (
                "official-test-source",
                "source_error",
                "2026-09-01T00:00:00Z",
                "2026-09-01T00:00:00Z",
                "test",
            ),
        )
    assert JobPipeline(settings, database).expire_stale_source_jobs() == {}


def test_disabled_non_manual_source_rows_are_withdrawn(tmp_path) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    disabled_source = {
        **source(),
        "source_type": "official_snapshot_rows",
        "enabled": False,
        "config": {
            "minimum_relevance": 0,
            "snapshot_path": "unused.json",
            "official_evidence_url": "https://careers.example.edu.cn/notice",
            "application_url": "https://careers.example.edu.cn/apply",
            "allowed_hosts": ["careers.example.edu.cn"],
        },
    }
    database.upsert_source(disabled_source)
    posting = RawPosting(
        title="停用来源地质工程岗",
        employer="测试地质单位",
        source_url="https://careers.example.edu.cn/jobs/disabled-1",
        application_url="https://careers.example.edu.cn/apply/disabled-1",
        text="地质工程硕士可报，工作地点北京，报名截止时间为2026年12月31日。",
        summary="官方岗位级证据。",
        published_date="2026-09-30",
        deadline_date="2026-12-31",
        location="北京",
        external_id="disabled-1",
        field_evidence={
            "evidence_scope": "official_html_table_row",
            "岗位": "停用来源地质工程岗",
            "专业范围": "地质工程",
            "学历要求": "硕士",
            "官方详情链接": "https://careers.example.edu.cn/jobs/disabled-1",
        },
    )
    # Sync while the source is temporarily enabled so the row is a genuine
    # public record, then disable it before the lifecycle pass.
    database.set_source_enabled(disabled_source["id"], True)
    pipeline = JobPipeline(settings, database, Collector(posting))
    result = pipeline.sync_source(database.get_source(disabled_source["id"]))
    assert result.created == 1
    database.set_source_enabled(disabled_source["id"], False)

    withdrawn = pipeline.expire_stale_source_jobs()
    assert withdrawn == {disabled_source["id"]: 1}
    public_jobs, public_total = database.list_jobs(page_size=None)
    assert public_total == 0
    assert public_jobs == []
