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
