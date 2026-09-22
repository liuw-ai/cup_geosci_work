from __future__ import annotations

import copy
import json
import sys

import pytest

from job_hub.app import create_app
from job_hub.cli import main as cli_main
from job_hub.contracts import ContractValidationError, validate_national_source_matrix
from job_hub.national_sources import (
    load_national_source_matrix,
    national_source_matrix_rows,
    national_source_matrix_summary,
)

from conftest import make_settings


def test_national_matrix_expands_every_target_channel() -> None:
    matrix = load_national_source_matrix()
    rows = national_source_matrix_rows(matrix)
    assert len(rows) == 37
    assert {row["affiliation"] for row in rows} == set(matrix["target_affiliations"])
    assert all(row["backup_urls"] for row in rows)
    assert not [row for row in rows if row["source_id"] and not row["assessment_source_id"]]


def test_access_limited_sources_cannot_claim_successful_no_match() -> None:
    matrix = load_national_source_matrix()
    summary = national_source_matrix_summary(matrix)
    assert summary["source_unavailable_channels"] > 0
    assert summary["successful_no_match_channels"] == 0
    cnooc = next(
        item
        for item in matrix["source_assessments"]
        if item["source_id"] == "cnooc-career"
    )
    assert cnooc["observed_http_status"] == 200
    assert cnooc["scan_conclusion"] == "adapter_probe_failed"


def test_national_matrix_contract_rejects_unknown_source_and_false_no_match() -> None:
    payload = load_national_source_matrix()
    unknown = copy.deepcopy(payload)
    unknown["source_assessments"][0]["source_id"] = "not-registered"
    with pytest.raises(ContractValidationError, match="unknown source_id"):
        validate_national_source_matrix(unknown, source_ids={"cnpc-career"})

    impossible = copy.deepcopy(payload)
    impossible["source_assessments"][0]["scan_conclusion"] = "scan_success_no_match"
    with pytest.raises(ContractValidationError, match="access is limited"):
        validate_national_source_matrix(impossible, source_ids={
            "cnpc-career",
            "sinopec-career",
            "cnooc-career",
            "pipechina-career",
        })


def test_national_matrix_cli_runs_with_isolated_database(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("APP_DATABASE_PATH", str(tmp_path / "jobs.sqlite3"))
    monkeypatch.setattr(
        sys,
        "argv",
        ["job_hub.cli", "national-source-matrix", "--runtime-status", "access_limited"],
    )
    cli_main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["channel_count"] == 37
    assert payload["items"]
    assert all(item["runtime_status"] == "access_limited" for item in payload["items"])


def test_national_matrix_admin_endpoint_is_private(tmp_path) -> None:
    settings = make_settings(tmp_path)
    app = create_app(settings)
    client = app.test_client()
    assert client.get("/api/admin/national-source-matrix").status_code == 403
    response = client.get(
        "/api/admin/national-source-matrix?runtime_status=access_limited",
        headers={"X-Admin-Token": "test-admin-token"},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["summary"]["channel_count"] == 37
    assert payload["items"]
    assert all(item["runtime_status"] == "access_limited" for item in payload["items"])
