from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

from job_hub.cli import main as cli_main
from job_hub.contracts import ContractValidationError, validate_organization_registry
from job_hub.organizations import (
    get_organization,
    load_organization_registry,
    organization_matrix_rows,
    organization_matrix_summary,
    organization_source_bindings,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _organization(
    organization_id: str,
    *,
    parent_id: str | None = None,
    source_id: str | None = "official-source",
    status: str = "automation_ready",
    is_primary: bool = True,
    backup_urls: list[str] | None = None,
) -> dict[str, object]:
    return {
        "id": organization_id,
        "canonical_name": f"测试单位 {organization_id}",
        "parent_id": parent_id,
        "organization_role": "upstream_operator",
        "industry_path": "测试就业路径",
        "affiliation": "测试体系",
        "aliases": [organization_id],
        "official_domains": ["example.edu.cn"],
        "channels": [
            {
                "id": f"{organization_id}-channel",
                "channel_type": "official_announcement",
                "official_url": f"https://example.edu.cn/{organization_id}/jobs",
                "backup_urls": backup_urls
                if backup_urls is not None
                else ["https://example.edu.cn/"],
                "source_id": source_id,
                "verification_status": status,
                "is_primary": is_primary,
            }
        ],
    }


def _registry(*organizations: dict[str, object]) -> dict[str, object]:
    return {"version": 1, "organizations": list(organizations)}


def test_real_organization_registry_is_loadable_and_keeps_industry_roles_separate() -> None:
    registry = load_organization_registry()
    persisted = json.loads(
        (PROJECT_ROOT / "data" / "organization_registry.json").read_text(
            encoding="utf-8"
        )
    )

    assert registry["organizations"]
    assert len(registry["organizations"]) == len(persisted["organizations"])
    assert get_organization("cnpc", registry)["organization_role"] == "group"
    assert (
        get_organization("cnpc-changqing", registry)["organization_role"]
        == "upstream_operator"
    )
    assert (
        get_organization("cnpc-logging", registry)["organization_role"]
        == "internal_technical_service"
    )
    assert get_organization("cosl", registry)["organization_role"] == "internal_technical_service"
    assert (
        get_organization("jereh", registry)["organization_role"]
        == "independent_technical_service"
    )
    assert (
        get_organization("slb", registry)["organization_role"]
        == "international_technical_service"
    )


def test_organization_contract_rejects_unknown_source_cycle_and_invalid_primary_state() -> None:
    root = _organization("root")
    with pytest.raises(ContractValidationError, match="unknown source_id"):
        validate_organization_registry(_registry(root), source_ids=set())

    invalid_version = _registry(root)
    invalid_version["version"] = "one"
    with pytest.raises(ContractValidationError, match="version"):
        validate_organization_registry(invalid_version, source_ids={"official-source"})

    root["channels"][0]["source_id"] = None  # type: ignore[index]
    root["channels"][0]["verification_status"] = "official_confirmed"  # type: ignore[index]
    child = _organization("child", parent_id="root", source_id=None, status="official_confirmed")
    root["parent_id"] = "child"
    with pytest.raises(ContractValidationError, match="cycle"):
        validate_organization_registry(_registry(root, child), source_ids=set())

    root = _organization("root", source_id=None, status="official_confirmed")
    root["channels"][0]["is_primary"] = "yes"  # type: ignore[index]
    with pytest.raises(ContractValidationError, match="is_primary"):
        validate_organization_registry(_registry(root), source_ids=set())


def test_organization_summary_separates_registered_entries_from_enabled_runtime_sources() -> None:
    upstream = _organization("upstream")
    technical_service = _organization("technical-service", source_id=None, status="official_confirmed")
    technical_service["organization_role"] = "internal_technical_service"
    registry = validate_organization_registry(
        _registry(upstream, technical_service),
        source_ids={"official-source"},
    )
    runtime_sources = [
        {"id": "official-source", "name": "测试官方来源", "enabled": True}
    ]

    summary = organization_matrix_summary(
        registry,
        source_ids={"official-source"},
        source_records=runtime_sources,
    )
    rows = organization_matrix_rows(registry, source_records=runtime_sources)

    assert summary["organization_count"] == 2
    assert summary["channel_count"] == 2
    assert summary["channels_with_registered_source"] == 1
    assert summary["automation_ready_channels"] == 1
    assert summary["automation_ready_enabled_channels"] == 1
    assert summary["backup_rate"] == 1.0
    assert organization_source_bindings(registry)["official-source"][0]["organization_id"] == "upstream"
    assert rows[0]["channels"][0]["source_registered_in_runtime"] is True
    assert rows[1]["channels"][0]["source_enabled"] is None


def test_organization_contract_adds_one_primary_when_unspecified_and_rejects_multiple() -> None:
    row = _organization("root", source_id=None, status="official_confirmed", is_primary=False)
    normalized = validate_organization_registry(_registry(row), source_ids=set())
    assert normalized["organizations"][0]["channels"][0]["is_primary"] is True

    duplicate_primary = copy.deepcopy(row)
    duplicate_primary["channels"].append(  # type: ignore[index]
        {
            "id": "root-second-channel",
            "channel_type": "official_homepage",
            "official_url": "https://example.edu.cn/root",
            "backup_urls": [],
            "verification_status": "official_confirmed",
            "is_primary": True,
        }
    )
    duplicate_primary["channels"][0]["is_primary"] = True  # type: ignore[index]
    with pytest.raises(ContractValidationError, match="more than one primary"):
        validate_organization_registry(_registry(duplicate_primary), source_ids=set())


def test_organization_matrix_cli_runs_against_an_isolated_database(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("APP_DATABASE_PATH", str(tmp_path / "jobs.sqlite3"))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "job_hub.cli",
            "organization-matrix",
            "--organization-role",
            "internal_technical_service",
        ],
    )

    cli_main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["items"]
    assert all(
        item["organization_role"] == "internal_technical_service"
        for item in payload["items"]
    )
    assert payload["summary"]["automation_ready_channels"] > 0
