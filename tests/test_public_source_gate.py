from __future__ import annotations

from job_hub.db import Database
from job_hub.pipeline import JobPipeline
from job_hub.sources import RawPosting

from conftest import make_settings, source


def test_disabled_source_rows_are_private_but_remain_auditable(tmp_path) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    registered = source()
    registered["source_type"] = "landing_page"
    database.upsert_source(registered)
    pipeline = JobPipeline(settings, database)
    job_id, _ = database.save_job(
        pipeline.normalize_posting(
            RawPosting(
                title="地质工程师",
                employer="测试能源集团",
                source_url="https://careers.example.edu.cn/jobs/disabled-source",
                application_url=None,
                text="地质工程专业硕士岗位。",
                summary="官方地质工程岗位。",
                published_date="2026-10-01",
                deadline_date="2099-12-31",
                location="北京",
                field_evidence={
                    "evidence_scope": "official_html_table_row",
                    "岗位": "地质工程师",
                    "专业范围": "地质工程",
                    "学历要求": "硕士",
                    "工作地点": "北京",
                },
            ),
            registered,
        )
    )

    assert database.find_public_job(job_id, as_of_date="2026-10-02") is not None
    assert database.count_open_jobs(as_of_date="2026-10-02") == 1
    assert database.list_categories(as_of_date="2026-10-02")

    database.set_source_enabled(registered["id"], False)

    assert database.find_public_job(job_id, as_of_date="2026-10-02") is not None
    public_rows, public_total = database.list_jobs(as_of_date="2026-10-02")
    assert public_total == 1
    assert public_rows[0]["publication_layer"] == "source_unavailable"
    assert database.count_open_jobs(as_of_date="2026-10-02") == 1
    assert database.list_categories(as_of_date="2026-10-02")

    internal_rows, internal_total = database.list_jobs(
        only_open=False,
        student_visible=False,
    )
    assert internal_total == 1
    assert internal_rows[0]["id"] == job_id
    assert internal_rows[0]["status"] == "open"


def test_source_unavailable_layer_does_not_override_publication_status(tmp_path) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    registered = source()
    database.upsert_source(registered)
    pipeline = JobPipeline(settings, database)
    job_id, _ = database.save_job(
        pipeline.normalize_posting(
            RawPosting(
                title="不匹配岗位",
                employer="测试能源集团",
                source_url="https://careers.example.edu.cn/jobs/out-of-scope",
                application_url=None,
                text="仅要求石油工程专业。",
                summary="官方岗位。",
                published_date="2026-10-01",
                deadline_date="2099-12-31",
                location="北京",
                field_evidence={
                    "evidence_scope": "official_html_table_row",
                    "岗位": "不匹配岗位",
                    "专业范围": "石油工程",
                    "学历要求": "硕士",
                    "工作地点": "北京",
                },
            ),
            registered,
        )
    )

    with database.transaction() as connection:
        connection.execute(
            """
            UPDATE jobs
               SET publication_status = 'out_of_scope',
                   publication_layer = 'source_unavailable',
                   source_availability_status = 'source_unavailable'
             WHERE id = ?
            """,
            (job_id,),
        )

    assert database.list_jobs(page_size=None, student_visible=True)[1] == 0
    internal = database.find_job(job_id, student_visible=False)
    assert internal is not None
    assert internal["publication_status"] == "out_of_scope"
    assert internal["publication_layer"] == "source_unavailable"
