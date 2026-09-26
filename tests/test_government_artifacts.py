from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

import pytest

from job_hub.government_artifacts import (
    GovernmentArtifactContractError,
    government_artifact_refresh_summary,
    load_government_artifact_manifest,
    register_government_artifacts,
)
from job_hub.db import Database

from conftest import make_settings, source


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_provincial_manifest_contains_real_official_attachments() -> None:
    manifest = load_government_artifact_manifest(
        PROJECT_ROOT / "data" / "government_artifact_manifest.json"
    )
    assert len(manifest["artifacts"]) == 11
    assert {item["province"] for item in manifest["artifacts"]} == {"全国", "安徽", "山东", "河南", "天津", "甘肃", "宁夏", "湖北", "湖南"}
    assert all(item["status"] in {"historical_closed", "server_download_pending"} for item in manifest["artifacts"])
    assert all(item["attachment_url"].lower().endswith((".xlsx", ".xls", ".pdf")) for item in manifest["artifacts"])


def test_manifest_hosts_are_registered_by_their_source() -> None:
    """Every controlled attachment must pass the runtime host allow-list."""
    manifest = load_government_artifact_manifest(
        PROJECT_ROOT / "data" / "government_artifact_manifest.json"
    )
    source_rows = json.loads(
        (PROJECT_ROOT / "data" / "sources.json").read_text(encoding="utf-8")
    ) + json.loads(
        (PROJECT_ROOT / "data" / "provincial_sources.json").read_text(encoding="utf-8")
    )
    sources = {str(row["id"]): row for row in source_rows}
    for artifact in manifest["artifacts"]:
        source = sources[artifact["source_id"]]
        config = source.get("config") or {}
        allowed = {
            str(host).strip().lower()
            for host in config.get("allowed_hosts", [])
            if str(host).strip()
        }
        homepage_host = urlparse(str(source.get("homepage_url") or "")).hostname
        if homepage_host:
            allowed.add(homepage_host.lower())
        attachment_allowed = allowed | {
            str(host).strip().lower()
            for host in config.get("attachment_allowed_hosts", [])
            if str(host).strip()
        }
        assert (urlparse(artifact["notice_url"]).hostname or "").lower() in allowed
        assert (urlparse(artifact["attachment_url"]).hostname or "").lower() in attachment_allowed


def test_manifest_rejects_unknown_status(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(
        '{"version":1,"as_of":"2026-09-25","artifacts":[{"id":"x","source_id":"s","province":"山东","position_type":"public_institution","notice_url":"https://example.gov.cn/a","attachment_url":"https://example.gov.cn/a.xlsx","artifact_kind":"position_table","deadline_date":"2026-09-25","deadline_policy":"fixed_date","observed_on":"2026-09-25","status":"published"}]}',
        encoding="utf-8",
    )
    with pytest.raises(GovernmentArtifactContractError, match="unsupported"):
        load_government_artifact_manifest(path)


class FakeDatabase:
    def __init__(self) -> None:
        self.items: list[dict[str, object]] = []

    def upsert_source_artifact(self, item: dict[str, object]) -> dict[str, object]:
        self.items.append(item)
        return {"id": len(self.items), **item}


def test_registration_only_creates_private_artifact_rows() -> None:
    manifest = load_government_artifact_manifest(
        PROJECT_ROOT / "data" / "government_artifact_manifest.json"
    )
    database = FakeDatabase()
    rows = register_government_artifacts(database, manifest)

    assert len(rows) == 11
    assert len(database.items) == 11
    assert all(item["extraction_status"] == "registered" for item in database.items)
    assert all("government_artifact_id" in item["metadata"] for item in database.items)


def test_manifest_refresh_does_not_reset_processed_artifact(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    database.upsert_source(source())
    manifest = {
        "as_of": "2026-09-25",
        "artifacts": [
            {
                "id": "test-artifact",
                "source_id": "official-test-source",
                "province": "北京",
                "position_type": "public_institution",
                "notice_url": "https://careers.example.edu.cn/notice",
                "attachment_url": "https://careers.example.edu.cn/notice.xlsx",
                "artifact_kind": "position_table",
                "deadline_date": "2099-12-31",
                "deadline_policy": "fixed_date",
                "observed_on": "2026-09-25",
                "status": "server_download_pending",
                "note": "test",
            }
        ],
    }
    first = register_government_artifacts(database, manifest)[0]
    database.update_source_artifact_processing(
        first["id"], extraction_status="failed", metadata_updates={"last_error": "temporary"}
    )

    register_government_artifacts(database, manifest)

    refreshed = database.get_source_artifact(first["id"])
    assert refreshed["extraction_status"] == "failed"
    assert refreshed["metadata"]["last_error"] == "temporary"
    summary = government_artifact_refresh_summary(database, manifest=manifest, today="2026-09-25")
    assert summary["registered_artifacts"] == 1
    assert summary["source_failures_or_unavailable"] == 1


def test_expire_stale_attachment_candidates_preserves_evidence(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    database.upsert_source(source())
    artifact = database.upsert_source_artifact(
        {
            "source_id": "official-test-source",
            "parent_url": "https://careers.example.edu.cn/notice",
            "artifact_url": "https://careers.example.edu.cn/notice.xlsx",
            "artifact_kind": "position_table",
            "extraction_status": "registered",
            "metadata": {"deadline_date": "2026-09-25"},
        }
    )
    row = database.upsert_source_artifact_rows(
        int(artifact["id"]),
        [
            {
                "sheet_name": "岗位表",
                "row_number": 2,
                "cells": {"岗位": "地质工程师"},
                "row_text": "岗位：地质工程师",
            }
        ],
    )[0]
    candidate = database.upsert_artifact_job_candidate(
        {
            "artifact_row_id": row["id"],
            "source_id": "official-test-source",
            "official_page_url": "https://careers.example.edu.cn/notice",
            "title": "地质工程师",
            "employer": "测试能源集团",
            "deadline_date": "2026-09-25",
            "summary": "官方岗位表第2行",
            "description": "官方岗位表第2行",
            "field_evidence": {"table_row": "岗位表!2"},
            "major_tags": ["地质工程"],
            "review_status": "needs_review",
        }
    )

    changed = database.expire_stale_artifact_candidates(as_of="2026-09-26")

    assert changed == 1
    refreshed = database.get_artifact_job_candidate(int(candidate["id"]))
    assert refreshed["review_status"] == "expired"
    assert "系统自动过期" in refreshed["review_note"]
    assert refreshed["field_evidence"]["table_row"] == "岗位表!2"
