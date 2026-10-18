from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from dataclasses import replace

from job_hub.db import Database
from job_hub.pipeline import JobPipeline
from job_hub.sources import RawPosting

from conftest import make_settings, source


class Collector:
    def __init__(self, posting: RawPosting) -> None:
        self.posting = posting

    def collect(self, _source: object) -> list[RawPosting]:
        return [self.posting]


def test_production_default_freshness_contract_is_materialized_on_bootstrap(tmp_path) -> None:
    settings = replace(make_settings(tmp_path), default_source_max_age_hours=72)
    live_source = {**source(), "source_type": "html_notice"}
    settings.source_registry_path.write_text(
        json.dumps([live_source], ensure_ascii=False),
        encoding="utf-8",
    )
    database = Database(settings.database_path)
    database.initialize()

    JobPipeline(settings, database).bootstrap_sources()

    persisted = database.get_source(live_source["id"])
    assert persisted is not None
    assert persisted["config"]["max_age_hours"] == 72


def test_successful_live_sync_records_capture_timestamp_for_default_contract(tmp_path) -> None:
    settings = replace(make_settings(tmp_path), default_source_max_age_hours=72)
    live_source = {**source(), "source_type": "html_notice"}
    settings.source_registry_path.write_text(
        json.dumps([live_source], ensure_ascii=False),
        encoding="utf-8",
    )
    database = Database(settings.database_path)
    database.initialize()
    pipeline = JobPipeline(settings, database)
    pipeline.bootstrap_sources()
    posting = RawPosting(
        title="勘探地质技术岗",
        employer="中国海油测试单位",
        source_url="https://careers.example.edu.cn/jobs/live-capture",
        application_url=None,
        text="地质工程硕士可报，工作地点北京，报名截止时间为2026年12月31日。",
        summary="官方岗位级证据。",
        published_date="2026-10-01",
        deadline_date="2026-12-31",
        location="北京",
        external_id="live-capture",
        field_evidence={
            "evidence_scope": "official_detail_block",
            "岗位": "勘探地质技术岗",
            "专业范围": "地质工程",
            "学历要求": "硕士",
            "工作地点": "北京",
        },
        qualification_text="学历要求：硕士；专业要求：地质工程",
    )
    result = JobPipeline(settings, database, Collector(posting)).sync_source(
        database.get_source(live_source["id"])
    )

    assert result.created == 1
    capture = database.get_source_capture_freshness(live_source["id"])
    assert capture is not None
    assert capture["evidence_kind"] == "live_sync"
    assert pipeline.expire_stale_source_jobs(
        now=datetime.now(timezone.utc) + timedelta(hours=73)
    ) == {}
    assert database.list_jobs(page_size=None)[1] == 1


def test_repeated_live_sync_refreshes_capture_timestamp(tmp_path) -> None:
    settings = replace(make_settings(tmp_path), default_source_max_age_hours=72)
    live_source = {**source(), "source_type": "html_notice"}
    database = Database(settings.database_path)
    database.initialize()
    pipeline = JobPipeline(settings, database, Collector(
        RawPosting(
            title="重复抓取地质岗",
            employer="测试地质单位",
            source_url="https://careers.example.edu.cn/jobs/repeated-live",
            application_url=None,
            text="地质工程硕士可报，工作地点北京，报名截止时间为2026年12月31日。",
            summary="官方岗位级证据。",
            published_date="2026-10-01",
            deadline_date="2026-12-31",
            location="北京",
            external_id="repeated-live",
            field_evidence={
                "evidence_scope": "official_detail_block",
                "岗位": "重复抓取地质岗",
                "专业范围": "地质工程",
                "学历要求": "硕士",
                "工作地点": "北京",
            },
            qualification_text="学历要求：硕士；专业要求：地质工程",
        )
    ))
    settings.source_registry_path.write_text(
        json.dumps([live_source], ensure_ascii=False), encoding="utf-8"
    )
    pipeline.bootstrap_sources()
    first = pipeline.sync_source(database.get_source(live_source["id"]))
    assert first.created == 1
    old_capture = (
        datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=100)
    ).isoformat().replace("+00:00", "Z")
    database.record_source_capture_freshness(
        live_source["id"], captured_at=old_capture, evidence_kind="live_sync"
    )

    second = pipeline.sync_source(database.get_source(live_source["id"]))

    assert second.created == 0
    assert second.updated == 0
    capture = database.get_source_capture_freshness(live_source["id"])
    assert capture is not None
    assert capture["evidence_kind"] == "live_sync"
    assert capture["captured_at"] != old_capture
    assert datetime.fromisoformat(capture["captured_at"].replace("Z", "+00:00")) > datetime.fromisoformat(
        old_capture.replace("Z", "+00:00")
    )


def test_browser_worker_plain_list_capture_uses_durable_timestamp(tmp_path) -> None:
    """CUPB browser rows are a plain list, not a manifest-shaped payload."""

    settings = make_settings(tmp_path)
    capture_path = tmp_path / "captures" / "cupb.json"
    capture_path.parent.mkdir(parents=True)
    capture_path.write_text("[]", encoding="utf-8")
    browser_source = {
        **source(),
        "id": "cupb-career-browser-20261007",
        "source_type": "official_snapshot_rows",
        "config": {
            "minimum_relevance": 0,
            "max_age_hours": 30,
            "runtime_mode": "browser_worker_only",
            "snapshot_path": "captures/cupb.json",
            "capture_path": "captures/cupb.json",
            "official_evidence_url": "https://career.cup.edu.cn/campus",
            "application_url": "https://career.cup.edu.cn/campus",
            "allowed_hosts": ["career.cup.edu.cn"],
        },
    }
    database = Database(settings.database_path)
    database.initialize()
    database.upsert_source(browser_source)
    pipeline = JobPipeline(settings, database)
    captured_at = (
        datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=1)
    ).isoformat().replace("+00:00", "Z")
    database.record_source_capture_freshness(
        browser_source["id"], captured_at=captured_at, evidence_kind="browser_capture_result"
    )

    assert pipeline._source_capture_timestamp(database.get_source(browser_source["id"])) == captured_at
    assert pipeline.expire_stale_source_jobs(
        now=datetime.now(timezone.utc)
    ) == {}


def test_public_reads_fail_closed_for_non_positive_source_freshness_contract(tmp_path) -> None:
    """An invalid contract must never turn a stale official row into a public row."""

    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    live_source = {**source(), "source_type": "html_notice"}
    live_source["config"] = {"minimum_relevance": 0, "max_age_hours": -1}
    database.upsert_source(live_source)
    posting = RawPosting(
        title="地质工程勘查技术岗",
        employer="测试地质单位",
        source_url="https://careers.example.edu.cn/jobs/invalid-freshness",
        application_url="https://careers.example.edu.cn/apply/invalid-freshness",
        text="地质工程硕士可报，工作地点北京，报名截止时间为2026年12月31日。",
        summary="官方岗位级证据。",
        published_date="2026-10-01",
        deadline_date="2026-12-31",
        location="北京",
        external_id="invalid-freshness",
        field_evidence={
            "evidence_scope": "official_detail_block",
            "岗位": "地质工程勘查技术岗",
            "专业范围": "地质工程",
            "学历要求": "硕士",
            "工作地点": "北京",
        },
        qualification_text="学历要求：硕士；专业要求：地质工程",
    )
    result = JobPipeline(settings, database, Collector(posting)).sync_source(
        live_source
    )
    assert result.created == 1
    database.record_source_capture_freshness(
        live_source["id"],
        captured_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        evidence_kind="live_sync",
    )

    jobs, total = database.list_jobs(
        page_size=None,
        as_of_date="2026-10-10",
    )

    assert total == 1
    assert jobs[0]["status"] == "open"


def test_manual_government_style_source_hides_rows_after_capture_window(tmp_path) -> None:
    """A stopped government recheck worker cannot leave an old row public."""

    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    manual_source = {
        **source(),
        "id": "government-style-source",
        "source_type": "manual",
        "config": {"minimum_relevance": 0, "max_age_hours": 48},
    }
    database.upsert_source(manual_source)
    pipeline = JobPipeline(settings, database)
    posting = RawPosting(
        title="地质工程勘查技术岗",
        employer="测试事业单位",
        source_url="https://careers.example.edu.cn/notices/government-style",
        application_url="https://careers.example.edu.cn/notices/government-style",
        text="地质工程硕士可报，工作地点北京，报名截止时间为2026年12月31日。",
        summary="官方职位表岗位级证据。",
        published_date="2026-10-01",
        deadline_date="2026-12-31",
        location="北京",
        external_id="government-style-1",
        field_evidence={
            "evidence_scope": "official_attachment_row",
            "岗位": "地质工程勘查技术岗",
            "专业范围": "地质工程",
            "学历要求": "硕士",
            "工作地点": "北京",
        },
        qualification_text="学历要求：硕士；专业要求：地质工程",
    )
    database.save_job(pipeline.normalize_posting(posting, manual_source))
    database.record_source_capture_freshness(
        manual_source["id"],
        captured_at="2026-10-07T00:00:00Z",
        evidence_kind="government_evidence_recheck",
    )

    jobs, total = database.list_jobs(page_size=None, as_of_date="2026-10-10")

    assert total == 1
    assert jobs[0]["status"] == "open"


def test_successful_official_xlsx_sync_does_not_use_sync_time_as_capture_evidence(tmp_path) -> None:
    """A readable hash-locked attachment still needs an artifact timestamp."""

    settings = replace(make_settings(tmp_path), default_source_max_age_hours=72)
    xlsx_source = {
        **source(),
        "source_type": "official_xlsx_rows",
        "config": {
            "minimum_relevance": 0,
            "notice_url": "https://careers.example.edu.cn/notices/xlsx",
            "attachment_url": "https://careers.example.edu.cn/notices/xlsx/jobs.xlsx",
            "sha256": "a" * 64,
            "verified_rows": [2],
            "allowed_hosts": ["careers.example.edu.cn"],
        },
    }
    settings.source_registry_path.write_text(
        json.dumps([xlsx_source], ensure_ascii=False),
        encoding="utf-8",
    )
    database = Database(settings.database_path)
    database.initialize()
    pipeline = JobPipeline(settings, database)
    pipeline.bootstrap_sources()
    persisted = database.get_source(xlsx_source["id"])
    assert persisted is not None
    # The default contract is allowed to be materialized, but the successful
    # sync cannot satisfy it without independent artifact capture evidence.
    assert persisted["config"]["max_age_hours"] == 72
    pipeline._record_successful_source_capture(persisted, run_id=None)
    assert database.get_source_capture_freshness(xlsx_source["id"]) is None


def test_bootstrap_invalidates_legacy_live_sync_for_official_xlsx(tmp_path) -> None:
    settings = replace(make_settings(tmp_path), default_source_max_age_hours=72)
    xlsx_source = {
        **source(),
        "source_type": "official_xlsx_rows",
        "config": {
            "minimum_relevance": 0,
            "notice_url": "https://careers.example.edu.cn/notices/xlsx",
            "attachment_url": "https://careers.example.edu.cn/notices/xlsx/jobs.xlsx",
            "sha256": "b" * 64,
            "verified_rows": [2],
            "allowed_hosts": ["careers.example.edu.cn"],
        },
    }
    settings.source_registry_path.write_text(
        json.dumps([xlsx_source], ensure_ascii=False),
        encoding="utf-8",
    )
    database = Database(settings.database_path)
    database.initialize()
    database.upsert_source(xlsx_source)
    database.record_source_capture_freshness(
        xlsx_source["id"],
        captured_at="2026-10-08T00:00:00Z",
        evidence_kind="legacy_live_sync",
    )

    JobPipeline(settings, database).bootstrap_sources()

    assert database.get_source_capture_freshness(xlsx_source["id"]) is None


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
    assert withdrawn == {}
    _public_jobs, public_total = database.list_jobs(page_size=None)
    assert public_total == 1
    history, history_total = database.list_jobs(
        page_size=None, only_open=False, student_visible=False
    )
    assert history_total == 1
    assert history[0]["status"] == "open"
    with database.connect() as connection:
        event = connection.execute(
            "SELECT payload_json FROM job_events WHERE job_id = ? ORDER BY id DESC LIMIT 1",
            (history[0]["id"],),
        ).fetchone()
    assert event is not None
    assert event is not None


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

    assert database.list_jobs(page_size=None)[1] == 1
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

    assert database.list_jobs(page_size=None)[1] == 1
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


def test_disabled_non_manual_source_rows_remain_visible_without_withdrawal(tmp_path) -> None:
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
    assert withdrawn == {}
    public_jobs, public_total = database.list_jobs(page_size=None)
    assert public_total == 1
    assert public_jobs[0]["publication_layer"] == "source_unavailable"
    assert public_jobs[0]["status"] == "open"
