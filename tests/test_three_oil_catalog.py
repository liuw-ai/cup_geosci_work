from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest

from job_hub.contracts import ContractValidationError
from job_hub.cli import main as cli_main
from job_hub.db import Database
from job_hub.three_oil_catalog import (
    build_three_oil_catalog_audit,
    build_three_oil_dns_audit,
    load_three_oil_catalog,
    three_oil_catalog_summary,
    three_oil_units,
    validate_three_oil_catalog,
)
from job_hub.organizations import load_organization_registry


def test_three_oil_catalog_contains_the_three_groups_and_excludes_pipechina() -> None:
    catalog = load_three_oil_catalog()
    summary = three_oil_catalog_summary(catalog)

    assert summary["group_count"] == 3
    assert set(summary["units_by_group"]) == {"cnpc", "sinopec", "cnooc"}
    assert summary["unit_count"] >= 50
    assert "pipechina" in summary["excluded_group_ids"]
    assert all(unit["group_id"] in {"cnpc", "sinopec", "cnooc"} for unit in three_oil_units(catalog))


def test_three_oil_units_keep_group_and_operating_unit_separate() -> None:
    rows = {item["id"]: item for item in three_oil_units()}

    assert rows["cnpc"]["role"] == "group"
    assert rows["cnpc-bgp"]["parent_id"] == "cnpc"
    assert rows["cnpc-bgp"]["role"] == "internal_service_unit"
    assert rows["cnpc-economic-research"]["role"] == "research_institute"
    assert rows["sinopec-shengli"]["parent_id"] == "sinopec"
    assert rows["cnooc-research"]["parent_id"] == "cnooc"


def test_sinopec_display_roster_retains_all_observed_units_without_promotion() -> None:
    report = json.loads(
        (
            Path("docs/current/phase4-sinopec-display-units-2026-10-18.json")
        ).read_text(encoding="utf-8")
    )

    source = report["source"]
    assert source["observed_display_unit_total"] == 132
    assert source["retained_registry_record_count"] + len(
        source["excluded_non_unit_display_names"]
    ) == 132
    assert source["missing_from_registry_count"] == 0
    assert all(item["promotion_status"] == "display_candidate_only" for item in report["records"])
    assert {item["display_name"] for item in report["records"]} >= {
        "胜利油田",
        "江汉油田",
        "石油工程建设有限公司",
        "石油勘探开发研究院",
    }


def test_real_three_oil_catalog_units_are_all_mapped_to_the_registry() -> None:
    report = build_three_oil_catalog_audit(
        load_three_oil_catalog(),
        organization_registry=load_organization_registry(),
    )

    assert report["catalog_summary"]["unit_count"] == 64
    assert report["missing_registry_unit_count"] == 0
    assert report["name_unverified_unit_count"] == 0
    assert all(item["name_exact_match"] for item in report["items"])
    assert report["external_identity_evidence_review"]["status"] == "pending"
    assert report["external_identity_evidence_review"]["pending_unit_count"] == 64
    assert "does not prove" in report["name_match_semantics"]
    assert report["gate"]["catalog_complete"] is True
    # Mapping a unit to a shared portal is an identity record only.  It must
    # not silently promote every unit to an independently verified adapter.
    assert report["gate"]["runtime_coverage_complete"] is False
    fallback = report["fallback_entry_summary_by_group"]
    assert set(fallback) == {"cnpc", "sinopec", "cnooc"}
    assert fallback["cnpc"]["unit_count"] > 0
    assert fallback["sinopec"]["unit_count"] > 0
    assert "authorized_platform_count" in fallback["cnpc"]
    assert "unit_scoped_recruitment_entry_count" in fallback["cnpc"]
    assert "shared_recruitment_entry_count" in fallback["cnpc"]
    tasks = report["fallback_tasks"]
    assert report["fallback_task_summary"]["task_count"] == len(tasks)
    assert {
        task["required_channel_type"] for task in tasks
    } <= {
        "official_announcement",
        "official_attachment",
        "authorized_platform",
    }
    assert all(
        task["status"] == "missing" and task["evidence_required"]
        for task in tasks
    )
    assert any(
        task["candidate_entry_urls"]
        for task in tasks
    )
    assert all(
        "official_announcement" in item["fallback_entry_coverage"]
        and "official_attachment" in item["fallback_entry_coverage"]
        for item in report["items"]
    )
    riped = next(item for item in report["items"] if item["catalog_id"] == "cnpc-riped")
    assert riped["fallback_entry_coverage"]["unit_homepage"] is False
    assert riped["fallback_entry_coverage"]["group_or_shared_recruitment_platform"] is True
    assert riped["unit_scoped_recruitment_entry_count"] == 0
    assert riped["shared_recruitment_entry_count"] == 1


def test_three_oil_catalog_audit_cli_does_not_write_database(monkeypatch, tmp_path) -> None:
    database_path = tmp_path / "job_hub.sqlite3"
    Database(database_path).initialize()
    before = hashlib.sha256(database_path.read_bytes()).hexdigest()
    output = tmp_path / "three-oil-audit.json"
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("APP_DATABASE_PATH", str(database_path))
    monkeypatch.setattr(
        "job_hub.cli.services",
        lambda: pytest.fail("read-only catalog audit must not initialize services"),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["job_hub.cli", "three-oil-catalog-audit", "--output", str(output)],
    )

    cli_main()

    assert output.is_file()
    assert hashlib.sha256(database_path.read_bytes()).hexdigest() == before


def test_three_oil_dns_audit_keeps_resolution_separate_from_identity() -> None:
    report = build_three_oil_dns_audit(
        organization_registry=load_organization_registry(),
        resolve=lambda host: host != "rip.ed.cnpc.com.cn",
        observed_at="2026-10-11T00:00:00Z",
    )
    assert report["declared_domain_count"] == 20
    assert report["unresolved_declared_domains"] == ["rip.ed.cnpc.com.cn"]
    riped = next(item for item in report["items"] if item["host"] == "rip.ed.cnpc.com.cn")
    assert riped["organization_ids"] == ["cnpc-riped"]
    assert riped["dns_status"] == "unresolved"
    assert "cnpc-riped-homepage" in riped["channel_ids"]
    assert any(
        item["host"] == "zhaopin.cnpc.com.cn"
        and "cnpc-riped-campus" in item["channel_ids"]
        for item in report["items"]
    )


def test_three_oil_dns_audit_cli_is_read_only(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("APP_DATABASE_PATH", str(tmp_path / "jobs.sqlite3"))
    monkeypatch.setattr(
        "job_hub.cli.build_three_oil_dns_audit",
        lambda: {"version": "three-oil-dns-audit-v1", "items": []},
    )
    monkeypatch.setattr(sys, "argv", ["job_hub.cli", "three-oil-dns-audit"])
    cli_main()
    assert json.loads(capsys.readouterr().out)["items"] == []
    assert not (tmp_path / "jobs.sqlite3").exists()


def test_authorized_platform_is_distinct_from_shared_group_portal() -> None:
    catalog = {
        "version": 1,
        "groups": [
            {
                "id": group_id,
                "name": group_id,
                "official_identity_url": "https://example.test",
                "units": [
                    {"id": group_id, "name": group_id, "role": "group"}
                ],
            }
            for group_id in ("cnpc", "sinopec", "cnooc")
        ],
    }
    registry = {
        "organizations": [
            {
                "id": group_id,
                "canonical_name": group_id,
                "parent_id": None,
                "industry_path": "test",
                "affiliation": group_id,
                "aliases": [],
                "official_domains": ["example.test"],
                "organization_role": "group",
                "channels": [
                    {
                        "id": f"{group_id}-shared",
                        "channel_type": "campus_recruitment",
                        "official_url": "https://example.test/shared",
                        "backup_urls": [],
                        "verification_status": "official_confirmed",
                        "is_primary": True,
                    },
                    {
                        "id": f"{group_id}-authorized",
                        "channel_type": "authorized_platform",
                        "official_url": "https://example.test/authorized",
                        "backup_urls": [],
                        "verification_status": "candidate",
                        "is_primary": False,
                    },
                ],
                "notes": "test",
            }
            for group_id in ("cnpc", "sinopec", "cnooc")
        ]
    }
    report = build_three_oil_catalog_audit(
        catalog=validate_three_oil_catalog(catalog),
        organization_registry=registry,
        source_records=[],
    )
    assert report["fallback_entry_summary_by_group"]["cnpc"]["shared_recruitment_platform_count"] == 1
    assert report["fallback_entry_summary_by_group"]["cnpc"]["authorized_platform_count"] == 0
    # A candidate platform is a lead, not verified fallback coverage.
    assert any(
        task["catalog_id"] == "cnpc"
        and task["required_channel_type"] == "authorized_platform"
        for task in report["fallback_tasks"]
    )


def test_three_oil_catalog_rejects_pipechina_as_a_group() -> None:
    payload = copy.deepcopy(load_three_oil_catalog())
    payload["groups"][0]["units"].append(
        {"id": "pipechina", "name": "国家石油天然气管网集团有限公司", "role": "group"}
    )

    with pytest.raises(ContractValidationError, match="forbidden"):
        validate_three_oil_catalog(payload)


def test_three_oil_catalog_audit_distinguishes_scope_from_runtime_readiness() -> None:
    registry = {
        "organizations": [
            {
                "id": "cnpc",
                "canonical_name": "中国石油天然气集团有限公司",
                "aliases": ["中国石油"],
                "channels": [
                    {
                        "id": "cnpc-campus",
                        "channel_type": "campus_recruitment",
                        "verification_status": "official_confirmed",
                        "source_id": "cnpc-career",
                    }
                ],
            },
            {
                "id": "cnpc-bgp",
                "canonical_name": "中国石油集团东方地球物理勘探有限责任公司",
                "aliases": ["东方物探"],
                "channels": [
                    {
                        "id": "bgp-recruitment",
                        "channel_type": "official_announcement",
                        "verification_status": "automation_ready",
                        "source_id": "bgp-source",
                    }
                ],
            },
        ]
    }
    source_records = [
        {"id": "cnpc-career", "enabled": True, "source_type": "shared_portal"},
        {"id": "bgp-source", "enabled": True, "source_type": "official_rows"},
    ]
    catalog = {
        "version": 1,
        "groups": [
            {
                "id": group_id,
                "name": group_id,
                "official_identity_url": "https://example.test",
                "units": [
                    {
                        "id": unit_id,
                        "name": unit_name,
                        "role": role,
                    }
                    for unit_id, unit_name, role in units
                ],
            }
            for group_id, units in {
                "cnpc": [
                    ("cnpc", "中国石油天然气集团有限公司", "group"),
                    ("cnpc-bgp", "中国石油集团东方地球物理勘探有限责任公司", "internal_technical_service"),
                    ("cnpc-missing", "未登记单位", "upstream_operator"),
                ],
                "sinopec": [("sinopec", "中国石油化工集团有限公司", "group")],
                "cnooc": [("cnooc", "中国海洋石油集团有限公司", "group")],
            }.items()
        ],
    }
    report = build_three_oil_catalog_audit(
        catalog=validate_three_oil_catalog(catalog),
        organization_registry=registry,
        source_records=source_records,
    )

    assert report["mapped_unit_count"] == 2
    assert report["missing_registry_unit_ids"] == ["cnpc-missing", "sinopec", "cnooc"]
    assert report["automation_ready_enabled_unit_ids"] == ["cnpc-bgp"]
    assert report["gate"]["catalog_complete"] is False
    assert report["gate"]["runtime_coverage_complete"] is False


def test_three_oil_catalog_audit_does_not_treat_shared_portal_as_ready() -> None:
    catalog = load_three_oil_catalog()
    registry = {
        "organizations": [
            {
                "id": "cnpc",
                "canonical_name": "中国石油天然气集团有限公司",
                "aliases": [],
                "channels": [
                    {
                        "id": "cnpc-campus",
                        "channel_type": "campus_recruitment",
                        "verification_status": "official_confirmed",
                        "source_id": "cnpc-career",
                    }
                ],
            }
        ]
    }
    report = build_three_oil_catalog_audit(
        catalog=catalog,
        organization_registry=registry,
        source_records=[{"id": "cnpc-career", "enabled": True}],
    )
    cnpc = next(item for item in report["items"] if item["catalog_id"] == "cnpc")
    assert cnpc["automation_ready_enabled"] is False
    assert cnpc["shared_recruitment_entry_count"] == 0
    assert cnpc["unbound_recruitment_entry_count"] == 1
    assert cnpc["unit_scoped_recruitment_entry_count"] == 0


def test_three_oil_audit_requires_job_level_employer_and_official_url_binding() -> None:
    report = build_three_oil_catalog_audit(
        load_three_oil_catalog(),
        organization_registry=load_organization_registry(),
        source_records=[],
        job_records=[
            {
                "employer": "东方物探",
                "source_id": "cnpc-bgp-recruitment",
                "official_evidence_url": (
                    "https://www.bgp.com.cn/bgpen/Recruitment/first_common2023hr.shtml"
                ),
                "field_evidence": {"evidence_scope": "official_detail_block"},
            },
            {
                # A generic group label must not claim the subordinate BGP unit.
                "employer": "中国石油",
                "source_id": "cnpc-career",
                "official_evidence_url": "https://zhaopin.cnpc.com.cn/",
                "field_evidence": {"evidence_scope": "official_browser_capture_row"},
            },
        ],
    )
    bgp = next(item for item in report["items"] if item["catalog_id"] == "cnpc-bgp")
    cnpc = next(item for item in report["items"] if item["catalog_id"] == "cnpc")
    assert bgp["job_detail_employer_binding_status"] == "verified"
    assert bgp["job_detail_employer_binding_count"] == 1
    assert bgp["job_detail_employer_binding_source_ids"] == ["cnpc-bgp-recruitment"]
    assert cnpc["job_detail_employer_binding_status"] == "verified"
    assert cnpc["job_detail_employer_binding_count"] == 1
    assert report["job_detail_binding_summary"]["verified_unit_count"] == 2


def test_three_oil_audit_binds_group_prefixed_official_employer_labels() -> None:
    """Official shared portals prefix the legal employer with the group label."""

    jobs = [
        {
            "employer": "中国海油-中海石油（中国）有限公司深圳分公司",
            "source_id": "cnooc-career-browser",
            "official_evidence_url": "https://xiaoyuan.zhaopin.com/job/CC1",
            "field_evidence": {
                "evidence_scope": "official_zhaopin_detail_initial_data",
            },
        },
        {
            "employer": "中国海油-中海油研究总院",
            "source_id": "cnooc-career-browser",
            "official_evidence_url": "https://xiaoyuan.zhaopin.com/job/CC2",
            "field_evidence": {
                "evidence_scope": "official_zhaopin_detail_initial_data",
            },
        },
        {
            "employer": "中国石油东方物探公司",
            "source_id": "cnpc-bgp-recruitment",
            "official_evidence_url": "https://www.bgp.com.cn/recruitment/2027",
            "field_evidence": {"evidence_scope": "official_detail_block"},
        },
    ]
    report = build_three_oil_catalog_audit(
        load_three_oil_catalog(),
        organization_registry=load_organization_registry(),
        source_records=[],
        job_records=jobs,
    )

    expected = {
        "cnooc-shenzhen": 1,
        "cnooc-research": 1,
        "cnpc-bgp": 1,
    }
    for catalog_id, count in expected.items():
        row = next(item for item in report["items"] if item["catalog_id"] == catalog_id)
        assert row["job_detail_employer_binding_count"] == count
        assert row["job_detail_employer_binding_status"] == "verified"

    # A generic group label remains intentionally unbound.
    generic = {
        "employer": "中国海油",
        "source_id": "cnooc-career-browser",
        "official_evidence_url": "https://xiaoyuan.zhaopin.com/job/CC3",
        "field_evidence": {"evidence_scope": "official_zhaopin_detail_initial_data"},
    }
    generic_report = build_three_oil_catalog_audit(
        load_three_oil_catalog(),
        organization_registry=load_organization_registry(),
        source_records=[],
        job_records=[generic],
    )
    assert generic_report["job_detail_binding_summary"]["matched_job_count"] == 1
    generic_group = next(item for item in generic_report["items"] if item["catalog_id"] == "cnooc")
    assert generic_group["job_detail_employer_binding_count"] == 1
    assert all(
        item["job_detail_employer_binding_count"] == 0
        for item in generic_report["items"]
        if item["group_id"] == "cnooc" and item["catalog_id"] != "cnooc"
    )
