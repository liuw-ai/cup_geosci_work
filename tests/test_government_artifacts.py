from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

import pytest

from job_hub.government_artifacts import (
    GovernmentArtifactContractError,
    government_artifact_publication_block_reason,
    government_artifact_refresh_summary,
    load_government_artifact_manifest,
    project_government_artifact_manifest,
    register_government_artifacts,
)
from job_hub.db import Database

from conftest import make_settings, source


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_provincial_manifest_contains_real_official_attachments() -> None:
    manifest = load_government_artifact_manifest(
        PROJECT_ROOT / "data" / "government_artifact_manifest.json"
    )
    assert len(manifest["artifacts"]) == 17
    assert {item["province"] for item in manifest["artifacts"]} == {"全国", "北京", "安徽", "山东", "河南", "天津", "甘肃", "宁夏", "湖北", "湖南"}
    assert all(
        item["status"]
        in {
            "historical_closed",
            "server_download_pending",
            "manual_verified",
            "current_non_student_eligible",
        }
        for item in manifest["artifacts"]
    )
    cea = next(item for item in manifest["artifacts"] if item["id"] == "cea-2027-recruitment-position-table")
    assert cea["status"] == "manual_verified"
    assert "91条" in cea["note"]
    assert all(item["attachment_url"].lower().endswith((".xlsx", ".xls", ".pdf")) for item in manifest["artifacts"])
    ccgc = next(item for item in manifest["artifacts"] if item["id"] == "ccgc-mature-talent-2026-position-table")
    assert ccgc["source_id"] == "ccgc-careers"
    assert ccgc["deadline_date"] == "2026-10-31"
    assert ccgc["status"] == "current_non_student_eligible"
    assert "人员调配" in ccgc["note"]
    assert ccgc["notice_url"].startswith("https://dzjt.ccgc.cn/")

    dingxi = [
        item for item in manifest["artifacts"] if item["source_id"] == "dingxi-party-recruitment"
    ]
    assert len(dingxi) == 4
    assert {item["status"] for item in dingxi[:3]} == {"server_download_pending"}
    assert dingxi[3]["status"] == "current_non_student_eligible"
    assert all(item["evidence_mode"] == "row_self_contained" for item in dingxi)


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


def test_manifest_rejects_unknown_evidence_mode(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "as_of": "2026-09-25",
                "artifacts": [
                    {
                        "id": "x",
                        "source_id": "s",
                        "province": "山东",
                        "position_type": "public_institution",
                        "notice_url": "https://example.gov.cn/a",
                        "attachment_url": "https://example.gov.cn/a.xlsx",
                        "artifact_kind": "position_table",
                        "deadline_date": "2026-12-31",
                        "deadline_policy": "fixed_date",
                        "observed_on": "2026-09-25",
                        "status": "server_download_pending",
                        "evidence_mode": "fuzzy_registry_guess",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(GovernmentArtifactContractError, match="evidence_mode"):
        load_government_artifact_manifest(path)


def test_static_manifest_cannot_inject_runtime_publication_marker(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "as_of": "2026-09-25",
                "runtime_projection_version": "government-artifact-runtime-projection-v1",
                "artifacts": [
                    {
                        "id": "x",
                        "source_id": "s",
                        "province": "北京",
                        "position_type": "public_institution",
                        "notice_url": "https://example.gov.cn/a",
                        "attachment_url": "https://example.gov.cn/a.xlsx",
                        "artifact_kind": "position_table",
                        "deadline_date": "2026-12-31",
                        "deadline_policy": "fixed_date",
                        "observed_on": "2026-09-25",
                        "status": "server_download_pending",
                        "runtime_publication_ready": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    manifest = load_government_artifact_manifest(path)
    assert "runtime_projection_version" not in manifest
    assert "runtime_publication_ready" not in manifest["artifacts"][0]
    assert government_artifact_publication_block_reason(
        "https://example.gov.cn/a.xlsx", manifest
    ) is not None


class FakeDatabase:
    def __init__(self) -> None:
        self.items: list[dict[str, object]] = []

    def upsert_source_artifact(self, item: dict[str, object]) -> dict[str, object]:
        self.items.append(item)
        return {"id": len(self.items), **item}


def test_registration_enforces_manifest_processing_policies() -> None:
    manifest = load_government_artifact_manifest(
        PROJECT_ROOT / "data" / "government_artifact_manifest.json"
    )
    database = FakeDatabase()
    rows = register_government_artifacts(database, manifest)

    assert len(rows) == 17
    assert len(database.items) == 17
    assert all("manifest_processing_policy" in item["metadata"] for item in database.items)
    assert all(
        item["extraction_status"] == "registered"
        for item in database.items
        if item["metadata"]["manifest_status"] == "server_download_pending"
    )
    assert all(
        item["extraction_status"] == "skipped"
        for item in database.items
        if item["metadata"]["manifest_status"] != "server_download_pending"
    )
    dingxi_rows = [
        item
        for item in database.items
        if item["metadata"].get("government_artifact_id", "").startswith("dingxi-")
    ]
    assert len(dingxi_rows) == 4
    assert all(item["metadata"]["evidence_mode"] == "row_self_contained" for item in dingxi_rows)
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


def test_runtime_projection_releases_pending_attachment_only_after_verified_candidate(
    tmp_path: Path,
) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    database.upsert_source(source())
    parent_url = "https://careers.example.edu.cn/notice.html"
    artifact_url = "https://careers.example.edu.cn/table.xlsx"
    artifact = database.upsert_source_artifact(
        {
            "source_id": "official-test-source",
            "parent_url": parent_url,
            "artifact_url": artifact_url,
            "artifact_kind": "position_table",
            "extraction_status": "extracted",
            "content_sha256": "a" * 64,
            "storage_path": "sha256/aa/" + "a" * 64 + ".xlsx",
            "parser_version": "test",
            "metadata": {
                "government_artifact_id": "test-artifact",
                "government_auto_verification": {
                    "row_evidence_verified": True,
                    "publication_ready": True,
                    "matched_record_ids": ["record-1"],
                    "matched_candidate_ids": ["1"],
                    "checked_at": "2026-10-07T00:00:00Z",
                },
            },
        }
    )
    row = database.upsert_source_artifact_rows(
        int(artifact["id"]),
        [{"sheet_name": "岗位表", "row_number": 2, "cells": {}, "row_text": "岗位表!2"}],
    )[0]
    database.upsert_artifact_job_candidate(
        {
            "artifact_row_id": row["id"],
            "source_id": "official-test-source",
            "official_page_url": parent_url,
            "title": "地质技术岗",
            "employer": "测试单位",
            "summary": "verified",
            "description": "verified",
            "field_evidence": {"table_row": "岗位表!2"},
            "review_status": "official_content_verified",
        }
    )
    manifest = {
        "version": 1,
        "as_of": "2026-10-07",
        "artifacts": [
            {
                "id": "test-artifact",
                "source_id": "official-test-source",
                "province": "北京",
                "position_type": "public_institution",
                "notice_url": parent_url,
                "attachment_url": artifact_url,
                "artifact_kind": "position_table",
                "deadline_date": "2099-12-31",
                "deadline_policy": "fixed_date",
                "observed_on": "2026-10-07",
                "status": "server_download_pending",
            }
        ],
    }

    projected = project_government_artifact_manifest(
        database,
        manifest,
        registry={
            "records": [
                {
                    "id": "record-1",
                    "source_id": "official-test-source",
                    "official_attachment_url": artifact_url,
                    "record_status": "verified_open",
                    "match_status": "explicit_match",
                }
            ]
        },
    )
    assert projected is not None
    item = projected["artifacts"][0]
    assert item["status"] == "server_download_pending"
    assert item["runtime_publication_ready"] is True

    database.update_source_artifact_processing(
        int(artifact["id"]),
        extraction_status="extracted",
        metadata_updates={"government_auto_verification": {
            "row_evidence_verified": True,
            "publication_ready": True,
            "matched_record_ids": ["record-1"],
            "matched_candidate_ids": ["999"],
            "checked_at": "2026-10-07T00:00:00Z",
        }},
    )
    stale_projection = project_government_artifact_manifest(database, manifest, registry={"records": [{
        "id": "record-1",
        "source_id": "official-test-source",
        "official_attachment_url": artifact_url,
        "record_status": "verified_open",
        "match_status": "explicit_match",
    }]})
    assert "runtime_publication_ready" not in stale_projection["artifacts"][0]


def test_runtime_projection_accepts_explicitly_blocked_registry_rows(
    tmp_path: Path,
) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    database.upsert_source(source())
    parent_url = "https://careers.example.edu.cn/notice.html"
    artifact_url = "https://careers.example.edu.cn/table.xlsx"
    artifact = database.upsert_source_artifact(
        {
            "source_id": "official-test-source",
            "parent_url": parent_url,
            "artifact_url": artifact_url,
            "artifact_kind": "position_table",
            "extraction_status": "extracted",
            "content_sha256": "d" * 64,
            "storage_path": "sha256/dd/" + "d" * 64 + ".xlsx",
            "parser_version": "test",
            "metadata": {
                "government_artifact_id": "test-artifact",
                "government_auto_verification": {
                    "row_evidence_verified": True,
                    "publication_ready": True,
                    "matched_record_ids": ["record-1"],
                    "blocked_record_ids": ["record-2"],
                    "blocked_records": [
                        {
                            "record_id": "record-2",
                            "row_locator": "岗位表!3",
                            "reason": "专业不匹配",
                        }
                    ],
                    "matched_candidate_ids": ["1"],
                    "checked_at": "2026-10-07T00:00:00Z",
                },
            },
        }
    )
    row = database.upsert_source_artifact_rows(
        int(artifact["id"]),
        [{"sheet_name": "岗位表", "row_number": 2, "cells": {}, "row_text": "岗位表!2"}],
    )[0]
    database.upsert_artifact_job_candidate(
        {
            "artifact_row_id": row["id"],
            "source_id": "official-test-source",
            "official_page_url": parent_url,
            "title": "地质技术岗",
            "employer": "测试单位",
            "summary": "verified",
            "description": "verified",
            "field_evidence": {"table_row": "岗位表!2"},
            "review_status": "official_content_verified",
        }
    )
    manifest = {
        "version": 1,
        "as_of": "2026-10-07",
        "artifacts": [
            {
                "id": "test-artifact",
                "source_id": "official-test-source",
                "province": "北京",
                "position_type": "public_institution",
                "notice_url": parent_url,
                "attachment_url": artifact_url,
                "artifact_kind": "position_table",
                "deadline_date": "2099-12-31",
                "deadline_policy": "fixed_date",
                "observed_on": "2026-10-07",
                "status": "server_download_pending",
            }
        ],
    }

    projected = project_government_artifact_manifest(
        database,
        manifest,
        registry={
            "records": [
                {
                    "id": "record-1",
                    "source_id": "official-test-source",
                    "official_attachment_url": artifact_url,
                    "record_status": "verified_open",
                    "match_status": "explicit_match",
                },
                {
                    "id": "record-2",
                    "source_id": "official-test-source",
                    "official_attachment_url": artifact_url,
                    "record_status": "verified_open",
                    "match_status": "explicit_match",
                },
            ]
        },
    )
    assert projected is not None
    assert projected["artifacts"][0]["runtime_publication_ready"] is True
    assert projected["artifacts"][0]["runtime_projection"]["blocked_registry_records"] == 1


def test_runtime_projection_keeps_pending_attachment_private_without_candidate_verification(
    tmp_path: Path,
) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    database.upsert_source(source())
    artifact = database.upsert_source_artifact(
        {
            "source_id": "official-test-source",
            "parent_url": "https://careers.example.edu.cn/notice.html",
            "artifact_url": "https://careers.example.edu.cn/table.xlsx",
            "artifact_kind": "position_table",
            "extraction_status": "extracted",
            "content_sha256": "b" * 64,
            "storage_path": "sha256/bb/" + "b" * 64 + ".xlsx",
            "parser_version": "test",
            "metadata": {
                "government_artifact_id": "test-artifact",
                "government_auto_verification": {
                    "row_evidence_verified": True,
                    "publication_ready": True,
                    "matched_record_ids": ["record-1"],
                },
            },
        }
    )
    manifest = {
        "version": 1,
        "as_of": "2026-10-07",
        "artifacts": [
            {
                "id": "test-artifact",
                "source_id": "official-test-source",
                "province": "北京",
                "position_type": "public_institution",
                "notice_url": "https://careers.example.edu.cn/notice.html",
                "attachment_url": "https://careers.example.edu.cn/table.xlsx",
                "artifact_kind": "position_table",
                "deadline_date": "2099-12-31",
                "deadline_policy": "fixed_date",
                "observed_on": "2026-10-07",
                "status": "server_download_pending",
            }
        ],
    }

    projected = project_government_artifact_manifest(database, manifest)
    assert projected is not None
    assert "runtime_publication_ready" not in projected["artifacts"][0]


def test_runtime_projection_accepts_verified_self_contained_rows_without_registry_ids(
    tmp_path: Path,
) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    database.upsert_source(source())
    parent_url = "https://careers.example.edu.cn/notice.html"
    artifact_url = "https://careers.example.edu.cn/table.xlsx"
    artifact = database.upsert_source_artifact(
        {
            "source_id": "official-test-source",
            "parent_url": parent_url,
            "artifact_url": artifact_url,
            "artifact_kind": "position_table",
            "extraction_status": "extracted",
            "content_sha256": "c" * 64,
            "storage_path": "sha256/cc/" + "c" * 64 + ".xlsx",
            "parser_version": "test",
            "metadata": {
                "government_artifact_id": "test-artifact",
                "government_auto_verification": {
                    "row_evidence_verified": True,
                    "publication_ready": True,
                    "matched_candidate_ids": ["1"],
                    "checked_at": "2026-10-07T00:00:00Z",
                },
            },
        }
    )
    row = database.upsert_source_artifact_rows(
        int(artifact["id"]),
        [{"sheet_name": "岗位表", "row_number": 2, "cells": {}, "row_text": "岗位表!2"}],
    )[0]
    candidate = database.upsert_artifact_job_candidate(
        {
            "artifact_row_id": row["id"],
            "source_id": "official-test-source",
            "official_page_url": parent_url,
            "title": "地质技术岗",
            "employer": "测试单位",
            "summary": "verified",
            "description": "verified",
            "field_evidence": {"table_row": "岗位表!2"},
            "review_status": "official_content_verified",
        }
    )
    assert str(candidate["id"]) == "1"
    manifest = {
        "version": 1,
        "as_of": "2026-10-07",
        "artifacts": [
            {
                "id": "test-artifact",
                "source_id": "official-test-source",
                "province": "北京",
                "position_type": "public_institution",
                "notice_url": parent_url,
                "attachment_url": artifact_url,
                "artifact_kind": "position_table",
                "deadline_date": "2099-12-31",
                "deadline_policy": "fixed_date",
                "observed_on": "2026-10-07",
                "status": "server_download_pending",
                "evidence_mode": "row_self_contained",
            }
        ],
    }

    projected = project_government_artifact_manifest(database, manifest, registry={"records": []})
    assert projected is not None
    assert projected["artifacts"][0]["runtime_publication_ready"] is True


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
