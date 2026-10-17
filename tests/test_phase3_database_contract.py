import sqlite3
from datetime import datetime, timezone

from job_hub.db import Database


def test_phase3_columns_are_migrated_and_freshness_withdrawal_is_noop(tmp_path):
    database = Database(tmp_path / "jobs.sqlite3")
    database.initialize()
    with database.connect() as connection:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(jobs)")}
    assert {
        "trust_grade",
        "source_availability_status",
        "publication_layer",
        "official_table_url",
        "third_party_table_url",
        "official_application_url",
        "withdrawal_reason",
        "no_deadline_expires_at",
        "superseded_successor_job_id",
        "superseded_reason",
        "superseded_at",
    } <= columns
    assert database.withdraw_stale_source_jobs("missing", reason="30 hour freshness expired") == 0
    assert database.withdraw_missing_source_jobs("missing", set(), reason="not seen") == 0


def test_artifact_withdrawal_requires_a_bound_official_evidence_url(tmp_path):
    database = Database(tmp_path / "artifact-contract.sqlite3")
    database.initialize()
    assert database.withdraw_published_artifact_job(
        1, reason="official_cancelled"
    ) is False


def test_third_party_table_contract_never_becomes_application_url(tmp_path):
    database = Database(tmp_path / "phase3-contract-test.sqlite3")
    database.initialize()
    from job_hub.third_party_tables import normalize_third_party_table

    result = normalize_third_party_table(
        [{
            "岗位代码": "A001",
            "用人单位": "测试单位",
            "岗位名称": "地质岗位",
            "专业要求": "地质学类",
            "学历要求": "本科",
            "招聘人数": "1",
        }],
        source_id="fenbi-discovery",
        third_party_table_url="https://www.fenbi.com/table.xlsx",
        official_table_url="https://example.gov.cn/table.xlsx",
        batch_id="phase3-test",
        position_type="civil_service",
        province="北京",
    )
    assert result["provenance"]["publication_status"] == "candidate_only"
    assert result["provenance"]["application_url"] is None
