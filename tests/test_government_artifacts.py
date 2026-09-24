from __future__ import annotations

from pathlib import Path

import pytest

from job_hub.government_artifacts import (
    GovernmentArtifactContractError,
    load_government_artifact_manifest,
    register_government_artifacts,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_provincial_manifest_contains_real_official_attachments() -> None:
    manifest = load_government_artifact_manifest(
        PROJECT_ROOT / "data" / "government_artifact_manifest.json"
    )
    assert len(manifest["artifacts"]) == 3
    assert {item["province"] for item in manifest["artifacts"]} == {"山东", "河南", "天津"}
    assert all(item["status"] == "historical_closed" for item in manifest["artifacts"])
    assert all(item["attachment_url"].endswith((".xlsx", ".xls")) for item in manifest["artifacts"])


def test_manifest_rejects_unknown_status(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(
        '{"version":1,"as_of":"2026-09-25","artifacts":[{"id":"x","source_id":"s","province":"山东","position_type":"public_institution","notice_url":"https://example.gov.cn/a","attachment_url":"https://example.gov.cn/a.xlsx","artifact_kind":"position_table","deadline_date":"2026-09-25","observed_on":"2026-09-25","status":"published"}]}',
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

    assert len(rows) == 3
    assert len(database.items) == 3
    assert all(item["extraction_status"] == "registered" for item in database.items)
    assert all("government_artifact_id" in item["metadata"] for item in database.items)

