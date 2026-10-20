"""Build a requirement-by-requirement, read-only Phase 4 completion audit."""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DATABASE = ROOT / "runtime" / "job_hub.sqlite3"
OUTPUT = ROOT / "docs" / "current" / "phase4-completion-audit-2026-10-19.json"


def _read(path: str) -> dict[str, Any]:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def _database_facts() -> dict[str, Any]:
    with sqlite3.connect(f"file:{DATABASE.resolve().as_posix()}?mode=ro", uri=True) as connection:
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        job_count = int(connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0])
        withdrawal_reasons = dict(
            connection.execute(
                "SELECT COALESCE(withdrawal_reason, ''), COUNT(*) FROM jobs "
                "WHERE status = 'withdrawn' GROUP BY withdrawal_reason"
            ).fetchall()
        )
        audit_count = int(
            connection.execute("SELECT COUNT(*) FROM phase3_withdrawal_audits").fetchone()[0]
        )
        candidate_count = int(connection.execute("SELECT COUNT(*) FROM candidate_leads").fetchone()[0])
        published_candidate_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM candidate_leads "
                "WHERE verification_status = 'published' AND published_job_id IS NOT NULL"
            ).fetchone()[0]
        )
    return {
        "integrity_check": integrity,
        "job_count": job_count,
        "withdrawal_reasons": withdrawal_reasons,
        "phase3_withdrawal_audits": audit_count,
        "candidate_leads": candidate_count,
        "published_candidate_leads": published_candidate_count,
    }


def _published_candidate_facts(candidate_ids: list[int]) -> dict[str, int]:
    """Read current publication facts for a named candidate batch."""
    if not candidate_ids:
        return {"formal_jobs_created": 0, "student_visible": 0}
    placeholders = ", ".join("?" for _ in candidate_ids)
    with sqlite3.connect(f"file:{DATABASE.resolve().as_posix()}?mode=ro", uri=True) as connection:
        row = connection.execute(
            f"""
            SELECT
                COUNT(CASE WHEN cl.verification_status = 'published'
                           AND cl.published_job_id IS NOT NULL THEN 1 END),
                COUNT(CASE WHEN cl.verification_status = 'published'
                           AND cl.published_job_id IS NOT NULL
                           AND j.status = 'open'
                           AND j.publication_status IN ('student_eligible', 'unrestricted_eligible')
                           THEN 1 END)
            FROM candidate_leads cl
            LEFT JOIN jobs j ON j.id = cl.published_job_id
            WHERE cl.id IN ({placeholders})
            """,
            candidate_ids,
        ).fetchone()
    return {
        "formal_jobs_created": int(row[0] or 0),
        "student_visible": int(row[1] or 0),
    }


def build_report() -> dict[str, Any]:
    manifest = _read("docs/current/phase4-release-manifest.json")
    validation = _read("docs/current/phase4-local-validation-2026-10-20.json")
    funnel = _read("docs/current/phase4-third-party-funnel-audit-2026-10-18.json")
    huatu_shanxi = _read("docs/current/phase4-third-party-huatu-shanxi-geology-group-2026-10-19.json")
    huatu_hbdk = _read("docs/current/phase4-third-party-huatu-hbdk-geophysics-2026-10-19.json")
    huatu_hebei_water_geology = _read("docs/current/phase4-third-party-huatu-hebei-water-geology-2026-10-19.json")
    huatu_cnpc_chuanqing = _read("docs/current/phase4-third-party-huatu-cnpc-chuanqing-2026-10-20.json")
    nhri_browser_recheck = _read("docs/current/phase4-third-party-nhri-browser-recheck-2026-10-20.json")
    cnpc_bgp_entry_recheck = _read("docs/current/phase4-cnpc-bgp-entry-recheck-2026-10-20.json")
    cnooc_energy_development_browser_recheck = _read("docs/current/phase4-cnooc-energy-development-browser-recheck-2026-10-20.json")
    cnooc_portal_unit_display_recheck = _read("docs/current/phase4-cnooc-portal-unit-display-recheck-2026-10-20.json")
    cnooc_portal_live_recheck = _read("docs/current/phase4-cnooc-portal-live-recheck-2026-10-20.json")
    three_oil_official_homepage_entry_recheck = _read("docs/current/phase4-three-oil-official-homepage-entry-recheck-2026-10-20.json")
    cnooc_geoscience_job_evidence = _read("docs/current/phase4-cnooc-geoscience-job-evidence-2026-10-20.json")
    cnooc_shenzhen_job_evidence = _read("docs/current/phase4-cnooc-shenzhen-job-evidence-2026-10-20.json")
    three_oil_unit_entry_network_recheck = _read("docs/current/phase4-three-oil-unit-entry-network-recheck-2026-10-20.json")
    sinopec_company_intro_recheck = _read("docs/current/phase4-sinopec-company-intro-recheck-2026-10-20.json")
    hbdk_official_table = _read("docs/current/phase4-third-party-hbdk-official-table-2026-10-19.json")
    hbdk_registration = _read("docs/current/phase4-third-party-hbdk-candidate-registration-2026-10-19.json")
    entry_matrix = _read("docs/current/phase4-three-oil-unit-entry-matrix-2026-10-18.json")
    catalog_audit = _read("docs/current/phase4-three-oil-catalog-audit-2026-10-17.json")
    observed_roster = _read("docs/current/phase4-three-oil-observed-roster-2026-10-20.json")
    workspace_layout = _read("docs/current/phase4-workspace-layout-audit-2026-10-19.json")
    database = _database_facts()
    manifest_boundary_ok = (
        manifest.get("working_tree_dirty") is False
        and manifest.get("production_deployment") == "not_performed"
        and manifest.get("automation_status") == "paused"
    )

    student_target = manifest.get("target_metrics", {}).get("student_job_target", {})
    third_party = funnel.get("summary", {})
    entry_summary = entry_matrix.get("summary", {})
    validation_tests = validation.get("tests", {})
    web = validation.get("local_web_read_only", {})
    hbdk_candidate_ids = [
        int(item)
        for item in hbdk_registration.get("candidate_leads", {}).get("ids", [])
        if str(item).isdigit()
    ]
    hbdk_publication = _published_candidate_facts(hbdk_candidate_ids)

    requirements = [
        {
            "id": "phase3_lifecycle_and_withdrawal_policy",
            "status": "pass",
            "evidence": [
                "docs/current/PHASE4-2026-10-17.md",
                "job_hub/db.py",
                "job_hub/pipeline.py",
            ],
            "facts": {
                "withdrawal_reasons": database["withdrawal_reasons"],
                "allowed_reasons": [
                    "major_mismatch",
                    "deadline_passed",
                    "official_cancelled",
                    "official_closed",
                    "official_replaced",
                ],
            },
        },
        {
            "id": "phase3_historical_withdrawal_audit",
            "status": "pass",
            "evidence": ["docs/current/phase4-current-state-audit-2026-10-18.json"],
            "facts": {"audited_rows": database["phase3_withdrawal_audits"]},
        },
        {
            "id": "third_party_table_to_official_recovery_trial",
            "status": "pass",
            "evidence": [
                "docs/current/phase4-third-party-funnel-audit-2026-10-18.json",
                "docs/current/phase4-third-party-cgs-2026-10-18.json",
                "docs/current/phase4-third-party-huatu-nanjing-land-center-2026-10-19.json",
            ],
            "facts": {
                "candidate_leads": third_party.get("candidate_leads", 0),
                "published_leads": third_party.get("published_leads", 0),
                "failed_leads": third_party.get("failed_leads", 0),
            },
        },
        {
            "id": "third_party_batch_expansion",
            "status": "blocked",
            "evidence": [
                "docs/current/phase4-third-party-access-recheck-2026-10-19.json",
                "docs/current/phase4-third-party-blocked-batches-2026-10-18.json",
                "docs/current/phase4-third-party-huatu-hebei-water-geology-2026-10-19.json",
                "docs/current/phase4-third-party-huatu-cnpc-chuanqing-2026-10-20.json",
            ],
            "facts": {
                "formal_jobs": database["published_candidate_leads"],
                "pending_chuanqing_rows": len(huatu_cnpc_chuanqing.get("candidates", [])),
                "pending_chuanqing_official_recovery": huatu_cnpc_chuanqing.get("official_recovery", {}).get("status"),
                "blocker": "remaining sources or table assets are inaccessible under current TLS/robots/browser policy",
                "latest_discovery_boundary": huatu_hebei_water_geology.get("publication_decision", {}).get("reason"),
            },
        },
        {
            "id": "third_party_official_recovery_evidence",
            "status": "partial",
            "evidence": [
                "docs/current/phase4-third-party-huatu-shanxi-geology-group-2026-10-19.json",
            ],
            "facts": {
                "official_notice_reachable": huatu_shanxi.get("verification", {}).get("official_notice_reachable"),
                "official_table_links_observed": huatu_shanxi.get("verification", {}).get("official_table_links_observed"),
                "download_permission": huatu_shanxi.get("verification", {}).get("official_table_download_permission"),
                "row_level_evidence_obtained": huatu_shanxi.get("verification", {}).get("row_level_evidence_obtained"),
                "publication_status": huatu_shanxi.get("publication_decision", {}).get("status"),
            },
        },
        {
            "id": "third_party_nhri_browser_recheck",
            "status": "partial",
            "evidence": [
                "docs/current/phase4-third-party-nhri-official-page-2026-10-19.json",
                "docs/current/phase4-third-party-nhri-browser-recheck-2026-10-20.json",
            ],
            "facts": {
                "official_notice_url": nhri_browser_recheck.get("official_source", {}).get("notice_url"),
                "official_table_url_observed": bool(nhri_browser_recheck.get("official_source", {}).get("official_table_url")),
                "deadline_date": nhri_browser_recheck.get("official_source", {}).get("deadline_date"),
                "attachment_status": nhri_browser_recheck.get("attachment_recheck", {}).get("status"),
                "row_level_evidence_obtained": False,
                "candidate_leads_created": nhri_browser_recheck.get("publication_decision", {}).get("candidate_leads_created", 0),
                "formal_jobs_created": nhri_browser_recheck.get("publication_decision", {}).get("formal_jobs_created", 0),
                "publication_boundary": "公告级事实保留；未取得逐行官方表格证据，不进入正式岗位库。",
            },
        },
        {
            "id": "third_party_hbdk_geoscience_recovery",
            "status": "partial",
            "evidence": [
                "docs/current/phase4-third-party-huatu-hbdk-geophysics-2026-10-19.json",
                "docs/current/phase4-third-party-hbdk-official-table-2026-10-19.json",
            ],
            "facts": {
                "official_notice_url": huatu_hbdk.get("official_recovery", {}).get("official_notice_url"),
                "official_table_url": huatu_hbdk.get("official_recovery", {}).get("official_table_url"),
                "rows_observed": len(hbdk_official_table.get("rows", [])),
                "original_response_sha256": hbdk_official_table.get("capture", {}).get("sha256"),
                "original_response_bytes": hbdk_official_table.get("capture", {}).get("bytes"),
                "capture_path": hbdk_official_table.get("capture", {}).get("retained_path"),
                "deadline_policy": hbdk_official_table.get("official_deadline", {}).get("policy"),
                "official_row_evidence": True,
                "formal_jobs_created": hbdk_publication["formal_jobs_created"],
                "candidate_leads_registered": hbdk_registration.get("candidate_leads", {}).get("count", 0),
                "candidate_lead_ids": hbdk_registration.get("candidate_leads", {}).get("ids", []),
                "student_visible": hbdk_publication["student_visible"] > 0,
                "publication_status": (
                    "published"
                    if hbdk_publication["formal_jobs_created"] > 0
                    else hbdk_official_table.get("publication_decision", {}).get("status")
                ),
            },
        },
        {
            "id": "three_oil_catalog_and_entry_evidence",
            "status": "partial",
            "evidence": [
                "data/three_oil_organization_catalog.json",
                "docs/current/phase4-three-oil-unit-entry-matrix-2026-10-18.json",
                "docs/current/phase4-three-oil-observed-roster-2026-10-20.json",
                "docs/current/phase4-cnooc-shenzhen-job-evidence-2026-10-20.json",
            ],
            "facts": {
                "formal_catalog_units": entry_summary.get("formal_catalog_units", 0),
                "unit_specific_entry_evidence_complete": entry_summary.get(
                    "unit_specific_entry_evidence_complete", False
                ),
                "unit_specific_entry_status_counts": entry_summary.get(
                    "unit_specific_entry_status_counts", {}
                ),
                "external_identity_evidence_review": catalog_audit.get(
                    "external_identity_evidence_review", {}
                ),
                "observed_display_roster": observed_roster.get("summary", {}),
                "latest_cnooc_geoscience_job_evidence": {
                    "source_url": cnooc_geoscience_job_evidence.get("source", {}).get("url"),
                    "selected_unit": cnooc_geoscience_job_evidence.get("selected_unit_evidence", {}).get("unit"),
                    "geoscience_job_count": len(cnooc_geoscience_job_evidence.get("selected_unit_evidence", {}).get("geoscience_jobs", [])),
                    "independent_unit_entry_proven": cnooc_geoscience_job_evidence.get("publication_decision", {}).get("independent_unit_entry_proven"),
                },
                "latest_cnooc_shenzhen_job_evidence": {
                    "selected_unit": cnooc_shenzhen_job_evidence.get("selected_unit", {}).get("canonical_name"),
                    "job_card_count": cnooc_shenzhen_job_evidence.get("official_job_cards", {}).get("job_card_count", 0),
                    "application_url_count": cnooc_shenzhen_job_evidence.get("official_job_cards", {}).get("application_url_count", 0),
                    "geoscience_job_count": len(cnooc_shenzhen_job_evidence.get("geoscience_job_evidence", [])),
                    "independent_unit_entry_proven": cnooc_shenzhen_job_evidence.get("evidence_boundary", {}).get("independent_unit_recruitment_portal_proven", False),
                    "student_jobs_created": cnooc_shenzhen_job_evidence.get("evidence_boundary", {}).get("student_jobs_created", 0),
                },
                "latest_bgp_entry_recheck": {
                    "catalog_id": cnpc_bgp_entry_recheck.get("catalog_id"),
                    "entry_status": cnpc_bgp_entry_recheck.get("entry", {}).get("entry_status"),
                    "recruitment_url": cnpc_bgp_entry_recheck.get("entry", {}).get("recruitment_url"),
                    "student_job_created": cnpc_bgp_entry_recheck.get("policy_checks", {}).get("student_job_created"),
                    "cnooc_energy_development_browser_status": cnooc_energy_development_browser_recheck.get("browser_result", {}).get("status"),
                    "cnooc_energy_development_entry_created": cnooc_energy_development_browser_recheck.get("entry_decision", {}).get("unit_specific_entry_url_created"),
                    "latest_cnooc_portal_unit_display": {
                        "observed_unit_count": cnooc_portal_unit_display_recheck.get("observed_unit_count"),
                        "independent_portals_proven": cnooc_portal_unit_display_recheck.get("interpretation", {}).get("independent_unit_recruitment_portals_proven"),
                        "catalog_units_not_observed": cnooc_portal_unit_display_recheck.get("formal_catalog_units_not_observed_in_current_unit_tabs", []),
                    },
                    "latest_unit_entry_network_recheck": {
                        "checked": three_oil_unit_entry_network_recheck.get("summary", {}).get("checked", 0),
                        "http_success": three_oil_unit_entry_network_recheck.get("summary", {}).get("http_success", 0),
                        "source_unavailable": three_oil_unit_entry_network_recheck.get("summary", {}).get("source_unavailable", 0),
                        "catalog_changed": three_oil_unit_entry_network_recheck.get("summary", {}).get("catalog_changed", False),
                        "interpretation": "网络失败只标记来源暂时不可用，不覆盖既有官方入口证据，也不创建或撤回岗位。",
                    },
                    "latest_sinopec_company_intro_recheck": {
                        "official_unit_display_name_count": sinopec_company_intro_recheck.get("observations", {}).get("official_unit_display_name_count", 0),
                        "catalog_changed": sinopec_company_intro_recheck.get("promotion_policy", {}).get("catalog_changed", False),
                        "registry_changed": sinopec_company_intro_recheck.get("promotion_policy", {}).get("registry_changed", False),
                        "interpretation": "官方企业介绍页用于组织关系和展示名称补证；简称仍需法定身份和单位入口证据，不能直接升级目录。",
                    },
                },
                "latest_cnooc_portal_live_recheck": {
                    "checked_at": cnooc_portal_live_recheck.get("checked_at"),
                    "group_portal_url": cnooc_portal_live_recheck.get("source", {}).get("group_portal_url"),
                    "observed_unit_count": len(cnooc_portal_live_recheck.get("observed_unit_display_names", [])),
                    "selected_unit": cnooc_portal_live_recheck.get("selected_unit_evidence", {}).get("unit"),
                    "visible_job_card_count": cnooc_portal_live_recheck.get("selected_unit_evidence", {}).get("visible_job_card_count", 0),
                    "application_url_observed": cnooc_portal_live_recheck.get("selected_unit_evidence", {}).get("application_url_observed", False),
                    "independent_unit_recruitment_portals_proven": cnooc_portal_live_recheck.get("policy", {}).get("independent_unit_recruitment_portals_proven", False),
                    "catalog_changed": cnooc_portal_live_recheck.get("policy", {}).get("catalog_changed", False),
                    "jobs_created": cnooc_portal_live_recheck.get("policy", {}).get("no_jobs_created") is False,
                },
                "latest_official_homepage_entry_recheck": {
                    "observations": len(three_oil_official_homepage_entry_recheck.get("observations", [])),
                    "recruitment_entries_proven": sum(
                        1
                        for item in three_oil_official_homepage_entry_recheck.get("observations", [])
                        if item.get("recruitment_navigation_observed") or item.get("unit_specific_recruitment_link_observed")
                    ),
                    "no_jobs_created": three_oil_official_homepage_entry_recheck.get("policy", {}).get("no_jobs_created"),
                    "catalog_changed": three_oil_official_homepage_entry_recheck.get("policy", {}).get("catalog_changed"),
                },
            },
        },
        {
            "id": "isolated_local_regression",
            "status": "pass",
            "evidence": ["docs/current/phase4-local-validation-2026-10-20.json"],
            "facts": {
                "passed": validation_tests.get("passed", 0),
                "skipped": validation_tests.get("skipped", 0),
                "failed": validation_tests.get("failed", 0),
                "formal_database_stable": validation.get("tests", {}).get(
                    "formal_database_stable_during_tests", False
                ),
            },
        },
        {
            "id": "student_site_acceptance",
            "status": "pass",
            "evidence": ["docs/current/phase4-local-web-live-validation-2026-10-20.json"],
            "facts": {
                "healthz": web.get("healthz_status"),
                "home": web.get("home_status"),
                "layer_api_statuses": web.get("layer_api_statuses", {}),
                "database_mode": web.get("database_mode"),
            },
        },
        {
            "id": "manifest_and_deployment_boundary",
            "status": "pass" if manifest_boundary_ok else "fail",
            "evidence": ["docs/current/phase4-release-manifest.json"],
            "facts": {
                "manifest_boundary_fields_valid": manifest_boundary_ok,
                "production_deployment": manifest.get("production_deployment"),
                "automation_status": manifest.get("automation_status"),
                "release_blockers": manifest.get("release_blockers", []),
            },
        },
        {
            "id": "workspace_layout_audit",
            "status": workspace_layout.get("status", "review_required"),
            "evidence": ["docs/current/phase4-workspace-layout-audit-2026-10-19.json"],
            "facts": {
                "production_entrypoint": workspace_layout.get("production_entrypoint"),
                "root_compatibility_files": len(workspace_layout.get("root_compatibility_files", [])),
                "current_docs_database_files": workspace_layout.get("current_docs_database_files", []),
                "history_is_present": workspace_layout.get("history_is_present", False),
            },
        },
    ]

    blockers = sorted({str(item) for item in manifest.get("release_blockers", []) if str(item).strip()})
    if not blockers:
        blockers = sorted(
            item["id"]
            for item in requirements
            if item["status"] in {"blocked", "partial"}
        )
    return {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "phase": "phase4",
        "status": "not_complete" if blockers else "complete",
        "database": database,
        "student_target": student_target,
        "requirements": requirements,
        "release_blockers": blockers,
        "policy": "A pass means the scoped evidence exists; blocked or partial items remain release blockers and cannot be inferred complete from tests alone.",
    }


def main() -> None:
    report = build_report()
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"path": str(OUTPUT), "status": report["status"], "release_blockers": report["release_blockers"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
