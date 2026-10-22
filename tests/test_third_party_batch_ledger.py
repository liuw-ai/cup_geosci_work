from __future__ import annotations

import hashlib

import pytest
from openpyxl import Workbook

from job_hub.app import create_app
from job_hub.db import Database
from job_hub.third_party_tables import (
    ingest_third_party_table_file,
    persist_third_party_table_candidates,
)

from conftest import make_settings


def _position_table(tmp_path):
    table = tmp_path / "position-table.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "北京岗位"
    sheet.append(["岗位代码", "用人单位", "岗位名称", "专业要求", "学历要求", "招录人数", "工作地点"])
    sheet.append(["GEO-01", "自然资源局", "地质管理岗", "地质工程", "硕士", 1, "北京市"])
    workbook.save(table)
    return table


def test_persisted_third_party_table_has_durable_batch_and_candidate_links(tmp_path) -> None:
    table = _position_table(tmp_path)
    payload = ingest_third_party_table_file(
        table_path=table,
        source_id="offcn-discovery",
        third_party_table_url="https://www.offcn.com/2026/positions.xlsx",
        batch_id="offcn-ledger-test-2026",
        position_type="public_institution",
        province="北京",
        discovery_page_url="https://www.offcn.com/2026/notice.html",
        storage_dir=tmp_path / "captures",
    )
    database = Database(tmp_path / "phase6-isolated.sqlite3")
    database.initialize()

    result = persist_third_party_table_candidates(database, payload)
    batch = database.get_third_party_batch("offcn-ledger-test-2026")
    assert batch is not None
    assert batch["status"] == "imported"
    assert batch["capture_mode"] == "operator_provided_file"
    assert batch["content_sha256"] == hashlib.sha256(table.read_bytes()).hexdigest()
    assert batch["parsed_row_count"] == 1
    assert batch["candidate_row_count"] == 1
    assert batch["robots_evidence"]["robots_allowed"] is None
    assert batch["official_application_url"] is None
    assert result["linked_candidate_count"] == 1

    links = database.list_third_party_batch_candidates(batch["batch_id"])
    assert len(links) == 1
    assert links[0]["position_code"] == "GEO-01"
    assert links[0]["row_locator"].startswith("北京岗位!2")
    assert links[0]["verification_status"] == "candidate"
    assert database.list_candidate_leads(limit=10)[0]["official_url"] is None
    listed_batch = database.list_third_party_batches()[0]
    assert listed_batch["candidate_links"] == 1
    assert listed_batch["published_links"] == 0


def test_batch_ledger_preserves_a_verified_official_application_url(tmp_path) -> None:
    table = _position_table(tmp_path)
    payload = ingest_third_party_table_file(
        table_path=table,
        source_id="offcn-discovery",
        third_party_table_url="https://www.offcn.com/2026/positions.xlsx",
        batch_id="offcn-application-url-test-2026",
        position_type="public_institution",
        province="北京",
        official_application_url="https://example.gov.cn/apply",
        storage_dir=tmp_path / "captures",
    )
    database = Database(tmp_path / "phase6-isolated.sqlite3")
    database.initialize()
    persist_third_party_table_candidates(database, payload)

    batch = database.get_third_party_batch("offcn-application-url-test-2026")
    assert batch is not None
    assert batch["official_application_url"] == "https://example.gov.cn/apply"


def test_batch_id_cannot_be_rebound_to_different_capture_evidence(tmp_path) -> None:
    database = Database(tmp_path / "phase6-isolated.sqlite3")
    database.initialize()
    common = {
        "batch_id": "immutable-batch",
        "discovery_source_id": "offcn-discovery",
        "third_party_table_url": "https://www.offcn.com/2026/table.xlsx",
        "capture_mode": "operator_provided_file",
        "captured_at": "2026-10-23T00:00:00Z",
        "content_sha256": "a" * 64,
    }
    database.upsert_third_party_batch(common)
    with pytest.raises(ValueError, match="different capture evidence"):
        database.upsert_third_party_batch({**common, "content_sha256": "b" * 64})


def test_batch_ledger_api_is_token_private(tmp_path) -> None:
    app = create_app(make_settings(tmp_path))
    database = app.extensions["database"]
    database.upsert_third_party_batch(
        {
            "batch_id": "private-api-batch",
            "discovery_source_id": "offcn-discovery",
            "third_party_table_url": "https://www.offcn.com/2026/table.xlsx",
            "capture_mode": "operator_provided_file",
            "captured_at": "2026-10-23T00:00:00Z",
            "content_sha256": "c" * 64,
        }
    )
    client = app.test_client()
    assert client.get("/api/admin/third-party-batches").status_code == 403
    response = client.get(
        "/api/admin/third-party-batches",
        headers={"X-Admin-Token": "test-admin-token"},
    )
    assert response.status_code == 200
    assert response.get_json()["items"][0]["batch_id"] == "private-api-batch"
    assert client.get("/api/jobs").get_json()["total"] == 0
