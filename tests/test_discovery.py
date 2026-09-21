from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from job_hub.app import create_app
from job_hub.contracts import ContractValidationError, validate_discovery_source_registry
from job_hub.db import Database, SCHEMA
from job_hub.discovery import (
    discovery_funnel,
    load_discovery_source_registry,
    normalize_lead_url,
    official_domain_assessment,
)

from conftest import make_settings, source


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_discovery_registry_is_private_and_validated() -> None:
    registry = load_discovery_source_registry()
    assert len(registry["sources"]) == 9
    assert all(
        item["publication_policy"] == "private_discovery_only"
        for item in registry["sources"]
    )
    malformed = json.loads(
        (PROJECT_ROOT / "data" / "discovery_sources.json").read_text(encoding="utf-8")
    )
    malformed["sources"][0]["publication_policy"] = "student_facing"
    with pytest.raises(ContractValidationError, match="private_discovery_only"):
        validate_discovery_source_registry(malformed)


def test_lead_url_normalization_and_domain_assessment() -> None:
    assert normalize_lead_url(
        "https://Example.com/jobs/1/?utm_source=wechat&from=share&x=2"
    ) == "https://example.com/jobs/1?x=2"
    records = [
        {
            "id": "official",
            "homepage_url": "https://jobs.example.gov.cn/",
            "config": {"allowed_hosts": ["example.gov.cn"]},
        }
    ]
    assert official_domain_assessment(
        "https://notice.example.gov.cn/jobs/1", records, official_source_id="official"
    )["status"] == "registered_source_match"
    assert official_domain_assessment(
        "https://notice.other.example/jobs/1", records, official_source_id="official"
    )["status"] == "source_domain_mismatch"


def test_duplicate_leads_are_idempotent_but_retain_source_mentions(tmp_path) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    database.upsert_source(source())
    first = database.create_candidate_lead(
        {
            "lead_provider": "华图教育",
            "discovery_source_id": "huatu-discovery",
            "lead_url": "https://clue.example/jobs/1/?utm_source=wechat",
            "title": "某省地质局招聘",
            "employer_hint": "某省地质局",
        }
    )
    duplicate = database.create_candidate_lead(
        {
            "lead_provider": "中公教育",
            "discovery_source_id": "offcn-discovery",
            "lead_url": "https://clue.example/jobs/1/",
            "title": "某省地质局招聘",
            "employer_hint": "某省地质局",
        }
    )
    assert duplicate["id"] == first["id"]
    assert len(database.list_candidate_leads(limit=5000)) == 1
    mentions = database.list_candidate_lead_mentions(first["id"])
    assert {item["discovery_source_id"] for item in mentions} == {
        "huatu-discovery",
        "offcn-discovery",
    }
    funnel = discovery_funnel(
        database.list_candidate_leads(limit=5000), load_discovery_source_registry()
    )
    assert funnel["lead_total"] == 1
    assert funnel["attributed_leads"] == 1


def test_mismatched_registered_official_domain_cannot_be_verified(tmp_path) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    registered = source()
    registered["id"] = "registered-official"
    registered["config"] = {"allowed_hosts": ["official.example.gov.cn"]}
    database.upsert_source(registered)
    lead = database.create_candidate_lead(
        {
            "lead_provider": "行业平台",
            "lead_url": "https://clue.example/jobs/2",
            "title": "地质工程岗位",
        }
    )
    located = database.update_candidate_lead(
        lead["id"],
        {
            "official_url": "https://fake.example.com/jobs/2",
            "official_source_id": "registered-official",
            "verification_status": "official_url_found",
        },
    )
    assert located["official_domain_status"] == "source_domain_mismatch"
    with pytest.raises(ValueError, match="does not match"):
        database.update_candidate_lead(
            lead["id"],
            {
                "official_source_id": "registered-official",
                "verification_status": "official_content_verified",
                "verification_note": "尝试核验。",
            },
        )
    unscoped = database.create_candidate_lead(
        {
            "lead_provider": "行业平台",
            "lead_url": "https://clue.example/jobs/3",
            "title": "未登记来源的地质岗位",
        }
    )
    database.update_candidate_lead(
        unscoped["id"],
        {
            "official_url": "https://official.example.gov.cn/jobs/3",
            "verification_status": "official_url_found",
        },
    )
    with pytest.raises(ValueError, match="domain_status must match"):
        database.update_candidate_lead(
            unscoped["id"],
            {
                "official_domain_status": "registered_source_match",
                "verification_status": "official_content_verified",
                "verification_note": "不能在没有来源绑定时伪造匹配状态。",
            },
        )
    approved = database.update_candidate_lead(
        lead["id"],
        {
            "official_source_id": "registered-official",
            "official_domain_status": "manual_review_approved",
            "verification_status": "official_content_verified",
            "verification_note": "管理员已核对该政府公告主体和原文。",
        },
    )
    assert approved["official_domain_status"] == "manual_review_approved"


def test_admin_discovery_endpoints_are_private(tmp_path) -> None:
    settings = make_settings(tmp_path)
    app = create_app(settings)
    client = app.test_client()
    assert client.get("/api/admin/discovery-sources").status_code == 403
    assert client.get("/api/admin/discovery-funnel").status_code == 403
    response = client.get(
        "/api/admin/discovery-sources",
        headers={"X-Admin-Token": "test-admin-token"},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["summary"]["registered_sources"] == 9
    assert payload["items"][0]["publication_policy"] == "private_discovery_only"


def test_old_candidate_leads_table_gets_additive_phase5_columns(tmp_path) -> None:
    database_path = tmp_path / "old.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE candidate_leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lead_provider TEXT NOT NULL,
                lead_url TEXT NOT NULL,
                title TEXT NOT NULL,
                employer_hint TEXT,
                location_hint TEXT,
                province_hint TEXT,
                published_date TEXT,
                official_url TEXT,
                verification_status TEXT NOT NULL DEFAULT 'candidate',
                verification_note TEXT NOT NULL DEFAULT '',
                published_job_id INTEGER,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            INSERT INTO candidate_leads (
                lead_provider, lead_url, title, created_at, updated_at
            ) VALUES ('旧平台', 'https://old.example/1', '旧线索', '2026-09-21T00:00:00Z', '2026-09-21T00:00:00Z');
            """
        )
    database = Database(database_path)
    database.initialize()
    row = database.list_candidate_leads(limit=10)[0]
    assert row["lead_fingerprint"]
    assert row["official_domain_status"] == "unverified"
