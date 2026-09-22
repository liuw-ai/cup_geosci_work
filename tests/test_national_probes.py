from __future__ import annotations

import copy
import json
import sys

import pytest

from job_hub.app import create_app
from job_hub.cli import main as cli_main
from job_hub.contracts import ContractValidationError, validate_national_source_probes
from job_hub.national_probes import load_national_source_probes, national_source_probe_summary

from conftest import make_settings


def test_national_probe_evidence_is_valid_and_not_a_job_count() -> None:
    probes = load_national_source_probes()
    summary = national_source_probe_summary(probes)
    assert summary["probe_count"] == 1
    assert summary["published_jobs"] == 0
    assert summary["result_counts"] == {"adapter_ready_probe_failed": 1}


def test_probe_contract_rejects_unknown_source_and_false_published_jobs() -> None:
    payload = load_national_source_probes()
    unknown = copy.deepcopy(payload)
    unknown["probes"][0]["source_id"] = "unknown"
    with pytest.raises(ContractValidationError, match="unknown source_id"):
        validate_national_source_probes(unknown, source_ids={"cnooc-career"})

    impossible = copy.deepcopy(payload)
    impossible["probes"][0]["published_jobs"] = 1
    with pytest.raises(ContractValidationError, match="cannot claim published jobs"):
        validate_national_source_probes(impossible, source_ids={"cnooc-career"})


def test_national_probe_cli_is_read_only(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("APP_DATABASE_PATH", str(tmp_path / "jobs.sqlite3"))
    monkeypatch.setattr(sys, "argv", ["job_hub.cli", "national-source-probes"])
    cli_main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["probe_count"] == 1
    assert payload["items"][0]["api_business_code"] == 500


def test_national_probe_admin_endpoint_is_private(tmp_path) -> None:
    settings = make_settings(tmp_path)
    app = create_app(settings)
    client = app.test_client()
    assert client.get("/api/admin/national-source-probes").status_code == 403
    response = client.get(
        "/api/admin/national-source-probes",
        headers={"X-Admin-Token": "test-admin-token"},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["summary"]["probe_count"] == 1
    assert payload["items"][0]["result"] == "adapter_ready_probe_failed"
