"""Create a read-only, reproducible Phase 4 current-state audit.

The report is deliberately separate from release approval.  It records the
current local database and repository facts, while preserving older snapshots
as historical evidence instead of silently rewriting them.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from job_hub.db import Database
from job_hub.phase3_policy import PUBLICATION_LAYERS


OUTPUT = ROOT / "docs" / "current" / "phase4-current-state-audit-2026-10-18.json"
DATABASE = ROOT / "runtime" / "job_hub.sqlite3"

# These are generated audit artifacts. They are deliberately excluded from
# the input-tree status so the two-step source/config commit plus metadata
# commit records the state of actual release inputs, not its own output.
GENERATED_METADATA_PATHS = (
    ":!docs/current/phase4-current-state-audit-2026-10-18.json",
    ":!docs/current/phase4-release-manifest.json",
    ":!docs/current/phase4-local-validation-2026-10-19.json",
    ":!docs/current/phase4-local-validation-2026-10-20.json",
    ":!docs/current/phase4-local-web-recheck-2026-10-19.json",
    ":!docs/current/phase4-local-web-live-validation-2026-10-19.json",
    ":!docs/current/phase4-third-party-funnel-audit-2026-10-18.json",
    ":!docs/current/phase4-third-party-huatu-table-discovery-2026-10-19.json",
    ":!docs/current/phase4-third-party-official-recovery-recheck-2026-10-19.json",
    ":!docs/current/phase4-three-oil-unit-entry-matrix-2026-10-18.json",
    ":!docs/current/phase4-cnpc-bgp-entry-recheck-2026-10-19.json",
    ":!docs/current/phase4-three-oil-catalog-audit-2026-10-17.json",
    ":!docs/current/phase4-third-party-huatu-state-owned-entry-2026-10-19.json",
    ":!docs/current/phase4-third-party-huatu-hebei-water-geology-2026-10-19.json",
    ":!docs/current/phase4-three-oil-observed-roster-2026-10-20.json",
    ":!docs/current/phase4-completion-audit-2026-10-19.json",
    ":!docs/current/phase4-workspace-layout-audit-2026-10-19.json",
    ":!docs/current/phase4-third-party-huatu-cnpc-chuanqing-2026-10-20.json",
    ":!docs/current/phase4-third-party-nhri-browser-recheck-2026-10-20.json",
    ":!docs/current/phase4-cnooc-geoscience-job-evidence-2026-10-20.json",
    ":!docs/current/phase4-three-oil-unit-entry-network-recheck-2026-10-20.json",
)


def sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git(command: list[str]) -> str:
    try:
        return subprocess.check_output(command, cwd=ROOT, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def input_tree_status() -> str:
    """Report input changes while excluding generated audit metadata."""
    try:
        return subprocess.check_output(
            [
                "git",
                "status",
                "--porcelain",
                "--",
                ".",
                *GENERATED_METADATA_PATHS,
            ],
            cwd=ROOT,
            text=True,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "audit_status_unavailable"


def current_database_facts() -> dict[str, object]:
    as_of_date = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    with sqlite3.connect(f"file:{DATABASE.resolve().as_posix()}?mode=ro", uri=True) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        status_counts = dict(connection.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status").fetchall())
        layer_counts = dict(
            connection.execute("SELECT publication_layer, COUNT(*) FROM jobs GROUP BY publication_layer").fetchall()
        )
        lead_count = int(connection.execute("SELECT COUNT(*) FROM candidate_leads").fetchone()[0])
        formal_third_party_jobs = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM jobs
                WHERE id IN (
                    SELECT published_job_id
                    FROM candidate_leads
                    WHERE verification_status = 'published'
                      AND published_job_id IS NOT NULL
                )
                """
            ).fetchone()[0]
        )
        audit_count = int(connection.execute("SELECT COUNT(*) FROM phase3_withdrawal_audits").fetchone()[0])
        job_count = int(connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0])
    database = Database(DATABASE, read_only=True)
    visible_by_layer = {
        layer: database.list_jobs(page_size=None, as_of_date=as_of_date, publication_layer=layer)[1]
        for layer in sorted(PUBLICATION_LAYERS)
    }
    visible_total = database.list_jobs(page_size=None, as_of_date=as_of_date)[1]
    return {
        "path": "runtime/job_hub.sqlite3",
        "sha256": sha256(DATABASE),
        "integrity_check": integrity,
        "job_count": job_count,
        "status_counts": status_counts,
        "publication_layer_counts": layer_counts,
        "phase3_withdrawal_audit_count": audit_count,
        "candidate_lead_count": lead_count,
        "formal_third_party_job_count": formal_third_party_jobs,
        "student_read_as_of_date": as_of_date,
        "student_visible_layer_counts": visible_by_layer,
        "student_visible_total": visible_total,
    }


def source_facts() -> dict[str, object]:
    sources = json.loads((ROOT / "data" / "discovery_sources.json").read_text(encoding="utf-8"))["sources"]
    adapters = json.loads((ROOT / "data" / "third_party_table_adapters.json").read_text(encoding="utf-8"))["adapters"]
    official_sources = json.loads((ROOT / "data" / "sources.json").read_text(encoding="utf-8"))
    catalog = json.loads((ROOT / "data" / "three_oil_organization_catalog.json").read_text(encoding="utf-8"))
    registry = json.loads((ROOT / "data" / "organization_registry.json").read_text(encoding="utf-8"))
    organizations = registry.get("organizations", [])
    group_counts = {group["id"]: len(group.get("units", [])) for group in catalog.get("groups", [])}
    return {
        "registered_source_count": len(sources),
        "official_recovery_source_count": sum(
            1
            for source in official_sources
            if str(source.get("id") or "") == "hunan-nonferrous-institute-recruitment"
        ),
        "trust_grade_counts": {
            grade: sum(1 for source in sources if str(source.get("trust_grade")) == grade)
            for grade in ("A", "A-", "B", "C", "D")
        },
        "batch_candidate_source_count": sum(1 for source in sources if source.get("batch_candidate_allowed")),
        "table_adapter_count": len(adapters),
        "three_oil_catalog_group_counts": group_counts,
        "three_oil_catalog_unit_count": sum(group_counts.values()),
        "organization_registry_count": len(organizations),
        "official_confirmed_channel_count": sum(
            1
            for organization in organizations
            for channel in organization.get("channels", [])
            if channel.get("verification_status") == "official_confirmed"
        ),
    }


def code_facts() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for directory in (ROOT / "job_hub", ROOT / "scripts", ROOT / "tests"):
        for path in sorted(directory.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            rows.append(
                {
                    "path": path.relative_to(ROOT).as_posix(),
                    "line_count": len(text.splitlines()),
                    "sha256": sha256(path),
                }
            )
    return rows


def document_consistency_facts() -> dict[str, object]:
    """Make the current-vs-historical document boundary machine-readable."""
    historical_paths = [
        "history/docs/CODEBASE-2026-10-17.md",
        "history/docs/PHASE3-2026-10-17.md",
        "history/docs/phase3-audit-preview-2026-10-17.json",
        "history/docs/phase3-backfill-result-2026-10-17.json",
        "history/docs/phase3-release-manifest.json",
        "history/docs/RESET-2026-10-17.md",
        "history/docs/HISTORY-MIGRATION-2026-10-17.json",
    ]
    # The audit is consumed by the manifest, so hashing the audit itself or
    # the manifest here would create a self-referential, never-stable chain.
    # Keep source/current documents separate from metadata documents whose
    # hashes are bound one way by the manifest.
    current_paths = [
        "docs/current/PHASE4-2026-10-17.md",
        "docs/current/phase4-local-validation-2026-10-19.json",
    ]
    metadata_paths = [
        "docs/current/phase4-current-state-audit-2026-10-18.json",
        "docs/current/phase4-release-manifest.json",
    ]
    historical_checks: list[dict[str, object]] = []
    for relative in historical_paths:
        path = ROOT / relative
        content = path.read_text(encoding="utf-8") if path.is_file() else ""
        declared = True
        if relative.endswith("CODEBASE-2026-10-17.md"):
            declared = all(
                marker in content
                for marker in ("历史代码清点快照", "不应被用作发布输入")
            )
        historical_checks.append(
            {
                "path": relative,
                "exists": path.is_file(),
                "declared_historical": declared,
                "sha256": sha256(path),
            }
        )
    current_checks = [
        {"path": relative, "exists": (ROOT / relative).is_file(), "sha256": sha256(ROOT / relative)}
        for relative in current_paths
    ]
    metadata_checks = [
        {"path": relative, "exists": (ROOT / relative).is_file(), "hash_bound_by": "phase4-release-manifest.json"}
        for relative in metadata_paths
    ]
    return {
        "policy": "historical_snapshots_are_evidence_only; current_audit_and_manifest_are_source_of_truth",
        "historical_documents": historical_checks,
        "current_documents": current_checks,
        "metadata_documents": metadata_checks,
        "historical_documents_are_publish_inputs": False,
    }


def main() -> None:
    catalog_audit = ROOT / "docs" / "current" / "phase4-three-oil-catalog-audit-2026-10-17.json"
    trial_report = ROOT / "docs" / "current" / "phase4-third-party-trial-2026-10-18.json"
    entry_evidence = ROOT / "docs" / "current" / "phase4-three-oil-entry-evidence-2026-10-18.json"
    cnooc_unit_evidence = ROOT / "docs" / "current" / "phase4-cnooc-unit-display-evidence-2026-10-19.json"
    cnooc_full_job_evidence = ROOT / "docs" / "current" / "phase4-cnooc-unit-job-evidence-2026-10-19.json"
    cooec_negative_entry = ROOT / "docs" / "current" / "phase4-three-oil-cooec-entry-negative-evidence-2026-10-19.json"
    sinopec_page_roster = ROOT / "docs" / "current" / "phase4-sinopec-page-roster-2026-10-19.json"
    entry_matrix = ROOT / "docs" / "current" / "phase4-three-oil-unit-entry-matrix-2026-10-18.json"
    entry_matrix_payload = json.loads(entry_matrix.read_text(encoding="utf-8")) if entry_matrix.is_file() else {}
    geoscience_followup = ROOT / "docs" / "current" / "phase4-third-party-geoscience-followup-2026-10-18.json"
    validation = ROOT / "docs" / "current" / "phase4-local-validation-2026-10-20.json"
    manual_candidate = ROOT / "docs" / "current" / "phase4-third-party-candidate-2026-10-18.json"
    hrinm_trial = ROOT / "docs" / "current" / "phase4-third-party-hrinm-2026-10-18.json"
    cgs_trial = ROOT / "docs" / "current" / "phase4-third-party-cgs-2026-10-18.json"
    blocked_batches = ROOT / "docs" / "current" / "phase4-third-party-blocked-batches-2026-10-18.json"
    nhri_official_page = ROOT / "docs" / "current" / "phase4-third-party-nhri-official-page-2026-10-19.json"
    huatu_nanjing_land_center = ROOT / "docs" / "current" / "phase4-third-party-huatu-nanjing-land-center-2026-10-19.json"
    lead_recheck = ROOT / "docs" / "current" / "phase4-third-party-lead-recheck-2026-10-19.json"
    cnpc_entry_recheck = ROOT / "docs" / "current" / "phase4-cnpc-entry-recheck-2026-10-19.json"
    three_oil_unit_entry_recheck = ROOT / "docs" / "current" / "phase4-three-oil-unit-entry-recheck-2026-10-19.json"
    sinopec_official_job_evidence = ROOT / "docs" / "current" / "phase4-sinopec-official-job-evidence-2026-10-19.json"
    access_recheck = ROOT / "docs" / "current" / "phase4-third-party-access-recheck-2026-10-19.json"
    local_repair = ROOT / "docs" / "current" / "phase4-local-repair-2026-10-19.json"
    third_party_funnel = ROOT / "docs" / "current" / "phase4-third-party-funnel-audit-2026-10-18.json"
    local_web_recheck = ROOT / "docs" / "current" / "phase4-local-web-recheck-2026-10-19.json"
    local_web_live_validation = ROOT / "docs" / "current" / "phase4-local-web-live-validation-2026-10-19.json"
    huatu_table_discovery = ROOT / "docs" / "current" / "phase4-third-party-huatu-table-discovery-2026-10-19.json"
    huatu_discovery_scan = ROOT / "docs" / "current" / "phase4-third-party-huatu-discovery-2026-10-19.json"
    huatu_state_owned_entry = ROOT / "docs" / "current" / "phase4-third-party-huatu-state-owned-entry-2026-10-19.json"
    huatu_hebei_water_geology = ROOT / "docs" / "current" / "phase4-third-party-huatu-hebei-water-geology-2026-10-19.json"
    bingtuan_official_recovery = ROOT / "docs" / "current" / "phase4-third-party-bingtuan-official-recovery-2026-10-19.json"
    huatu_shanxi_geology_group = ROOT / "docs" / "current" / "phase4-third-party-huatu-shanxi-geology-group-2026-10-19.json"
    huatu_hbdk_geophysics = ROOT / "docs" / "current" / "phase4-third-party-huatu-hbdk-geophysics-2026-10-19.json"
    hbdk_official_table = ROOT / "docs" / "current" / "phase4-third-party-hbdk-official-table-2026-10-19.json"
    hbdk_registration = ROOT / "docs" / "current" / "phase4-third-party-hbdk-candidate-registration-2026-10-19.json"
    huatu_cnpc_chuanqing = ROOT / "docs" / "current" / "phase4-third-party-huatu-cnpc-chuanqing-2026-10-20.json"
    nhri_browser_recheck = ROOT / "docs" / "current" / "phase4-third-party-nhri-browser-recheck-2026-10-20.json"
    cnpc_bgp_entry_recheck = ROOT / "docs" / "current" / "phase4-cnpc-bgp-entry-recheck-2026-10-20.json"
    cnooc_energy_development_browser_recheck = ROOT / "docs" / "current" / "phase4-cnooc-energy-development-browser-recheck-2026-10-20.json"
    cnooc_portal_unit_display_recheck = ROOT / "docs" / "current" / "phase4-cnooc-portal-unit-display-recheck-2026-10-20.json"
    local_db_write_incident = ROOT / "docs" / "current" / "phase4-local-db-write-incident-2026-10-19.json"
    three_oil_homepage_probe = ROOT / "docs" / "current" / "phase4-three-oil-homepage-probe-2026-10-19.json"
    three_oil_observed_roster = ROOT / "docs" / "current" / "phase4-three-oil-observed-roster-2026-10-20.json"
    cnooc_geoscience_job_evidence = ROOT / "docs" / "current" / "phase4-cnooc-geoscience-job-evidence-2026-10-20.json"
    three_oil_unit_entry_network_recheck = ROOT / "docs" / "current" / "phase4-three-oil-unit-entry-network-recheck-2026-10-20.json"
    phase4_record = ROOT / "docs" / "current" / "PHASE4-2026-10-17.md"
    report = {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "phase": "phase4",
        "scope": "local_read_only_current_state",
        "git_sha": git(["git", "rev-parse", "HEAD"]),
        "git_status_porcelain": input_tree_status(),
        "database": current_database_facts(),
        "sources_and_organizations": source_facts(),
        "three_oil_entry_matrix": entry_matrix_payload.get("summary", {}),
        "deployment": {
            "production_compose": {
                "path": "deploy/compose.yaml",
                "sha256": sha256(ROOT / "deploy" / "compose.yaml"),
            },
            "canonical_dockerfiles": {
                "app": {
                    "path": "deploy/docker/Dockerfile.app",
                    "sha256": sha256(ROOT / "deploy" / "docker" / "Dockerfile.app"),
                },
                "browser": {
                    "path": "deploy/docker/Dockerfile.browser",
                    "sha256": sha256(ROOT / "deploy" / "docker" / "Dockerfile.browser"),
                },
            },
            "root_compatibility_files_retained": [
                "docker-compose.yml",
                "docker-compose.browser.yml",
                "docker-compose.location-fix.yml",
                "docker-compose.public.yml",
                "Dockerfile",
                "Dockerfile.browser",
                "Dockerfile.business-date",
                "Dockerfile.experience-gate",
            ],
            "production_entrypoint": "deploy/compose.yaml",
        },
        "document_consistency": document_consistency_facts(),
        "evidence_documents": {
            "phase4_three_oil_catalog_audit": {
                "path": "docs/current/phase4-three-oil-catalog-audit-2026-10-17.json",
                "sha256": sha256(catalog_audit),
            },
            "phase4_third_party_trial": {
                "path": "docs/current/phase4-third-party-trial-2026-10-18.json",
                "sha256": sha256(trial_report),
            },
            "phase4_three_oil_entry_evidence": {
                "path": "docs/current/phase4-three-oil-entry-evidence-2026-10-18.json",
                "sha256": sha256(entry_evidence),
            },
            "phase4_cnooc_unit_display_evidence": {
                "path": "docs/current/phase4-cnooc-unit-display-evidence-2026-10-19.json",
                "sha256": sha256(cnooc_unit_evidence),
            },
            "phase4_cnooc_unit_job_evidence": {
                "path": "docs/current/phase4-cnooc-unit-job-evidence-2026-10-19.json",
                "sha256": sha256(cnooc_full_job_evidence),
            },
            "phase4_three_oil_cooec_negative_entry": {
                "path": "docs/current/phase4-three-oil-cooec-entry-negative-evidence-2026-10-19.json",
                "sha256": sha256(cooec_negative_entry),
            },
            "phase4_sinopec_page_roster": {
                "path": "docs/current/phase4-sinopec-page-roster-2026-10-19.json",
                "sha256": sha256(sinopec_page_roster),
            },
            "phase4_three_oil_unit_entry_matrix": {
                "path": "docs/current/phase4-three-oil-unit-entry-matrix-2026-10-18.json",
                "sha256": sha256(entry_matrix),
            },
            "phase4_third_party_geoscience_followup": {
                "path": "docs/current/phase4-third-party-geoscience-followup-2026-10-18.json",
                "sha256": sha256(geoscience_followup),
            },
            "phase4_local_validation": {
                "path": "docs/current/phase4-local-validation-2026-10-19.json",
                "sha256": sha256(validation),
            },
            "phase4_third_party_candidate": {
                "path": "docs/current/phase4-third-party-candidate-2026-10-18.json",
                "sha256": sha256(manual_candidate),
            },
            "phase4_third_party_hrinm": {
                "path": "docs/current/phase4-third-party-hrinm-2026-10-18.json",
                "sha256": sha256(hrinm_trial),
            },
            "phase4_third_party_cgs": {
                "path": "docs/current/phase4-third-party-cgs-2026-10-18.json",
                "sha256": sha256(cgs_trial),
            },
            "phase4_third_party_blocked_batches": {
                "path": "docs/current/phase4-third-party-blocked-batches-2026-10-18.json",
                "sha256": sha256(blocked_batches),
            },
            "phase4_third_party_nhri_official_page": {
                "path": "docs/current/phase4-third-party-nhri-official-page-2026-10-19.json",
                "sha256": sha256(nhri_official_page),
            },
            "phase4_third_party_huatu_nanjing_land_center": {
                "path": "docs/current/phase4-third-party-huatu-nanjing-land-center-2026-10-19.json",
                "sha256": sha256(huatu_nanjing_land_center),
            },
            "phase4_third_party_lead_recheck": {
                "path": "docs/current/phase4-third-party-lead-recheck-2026-10-19.json",
                "sha256": sha256(lead_recheck),
            },
            "phase4_cnpc_entry_recheck": {
                "path": "docs/current/phase4-cnpc-entry-recheck-2026-10-19.json",
                "sha256": sha256(cnpc_entry_recheck),
            },
            "phase4_three_oil_unit_entry_recheck": {
                "path": "docs/current/phase4-three-oil-unit-entry-recheck-2026-10-19.json",
                "sha256": sha256(three_oil_unit_entry_recheck),
            },
            "phase4_sinopec_official_job_evidence": {
                "path": "docs/current/phase4-sinopec-official-job-evidence-2026-10-19.json",
                "sha256": sha256(sinopec_official_job_evidence),
            },
            "phase4_third_party_access_recheck": {
                "path": "docs/current/phase4-third-party-access-recheck-2026-10-19.json",
                "sha256": sha256(access_recheck),
            },
            "phase4_local_repair": {
                "path": "docs/current/phase4-local-repair-2026-10-19.json",
                "sha256": sha256(local_repair),
            },
            "phase4_third_party_funnel": {
                "path": "docs/current/phase4-third-party-funnel-audit-2026-10-18.json",
                "sha256": sha256(third_party_funnel),
            },
            "phase4_local_web_recheck": {
                "path": "docs/current/phase4-local-web-recheck-2026-10-19.json",
                "sha256": sha256(local_web_recheck),
            },
            "phase4_local_web_live_validation": {
                "path": "docs/current/phase4-local-web-live-validation-2026-10-19.json",
                "sha256": sha256(local_web_live_validation),
            },
            "phase4_huatu_table_discovery": {
                "path": "docs/current/phase4-third-party-huatu-table-discovery-2026-10-19.json",
                "sha256": sha256(huatu_table_discovery),
            },
            "phase4_huatu_discovery_scan": {
                "path": "docs/current/phase4-third-party-huatu-discovery-2026-10-19.json",
                "sha256": sha256(huatu_discovery_scan),
            },
            "phase4_third_party_huatu_state_owned_entry": {
                "path": "docs/current/phase4-third-party-huatu-state-owned-entry-2026-10-19.json",
                "sha256": sha256(huatu_state_owned_entry),
            },
            "phase4_third_party_huatu_hebei_water_geology": {
                "path": "docs/current/phase4-third-party-huatu-hebei-water-geology-2026-10-19.json",
                "sha256": sha256(huatu_hebei_water_geology),
            },
            "phase4_third_party_bingtuan_official_recovery": {
                "path": "docs/current/phase4-third-party-bingtuan-official-recovery-2026-10-19.json",
                "sha256": sha256(bingtuan_official_recovery),
            },
            "phase4_third_party_huatu_shanxi_geology_group": {
                "path": "docs/current/phase4-third-party-huatu-shanxi-geology-group-2026-10-19.json",
                "sha256": sha256(huatu_shanxi_geology_group),
            },
            "phase4_third_party_huatu_hbdk_geophysics": {
                "path": "docs/current/phase4-third-party-huatu-hbdk-geophysics-2026-10-19.json",
                "sha256": sha256(huatu_hbdk_geophysics),
            },
            "phase4_third_party_hbdk_official_table": {
                "path": "docs/current/phase4-third-party-hbdk-official-table-2026-10-19.json",
                "sha256": sha256(hbdk_official_table),
            },
            "phase4_third_party_hbdk_candidate_registration": {
                "path": "docs/current/phase4-third-party-hbdk-candidate-registration-2026-10-19.json",
                "sha256": sha256(hbdk_registration),
            },
            "phase4_third_party_huatu_cnpc_chuanqing": {
                "path": "docs/current/phase4-third-party-huatu-cnpc-chuanqing-2026-10-20.json",
                "sha256": sha256(huatu_cnpc_chuanqing),
            },
            "phase4_third_party_nhri_browser_recheck": {
                "path": "docs/current/phase4-third-party-nhri-browser-recheck-2026-10-20.json",
                "sha256": sha256(nhri_browser_recheck),
            },
            "phase4_cnpc_bgp_entry_recheck_2026_10_20": {
                "path": "docs/current/phase4-cnpc-bgp-entry-recheck-2026-10-20.json",
                "sha256": sha256(cnpc_bgp_entry_recheck),
            },
            "phase4_cnooc_energy_development_browser_recheck": {
                "path": "docs/current/phase4-cnooc-energy-development-browser-recheck-2026-10-20.json",
                "sha256": sha256(cnooc_energy_development_browser_recheck),
            },
            "phase4_cnooc_portal_unit_display_recheck": {
                "path": "docs/current/phase4-cnooc-portal-unit-display-recheck-2026-10-20.json",
                "sha256": sha256(cnooc_portal_unit_display_recheck),
            },
            "phase4_local_db_write_incident": {
                "path": "docs/current/phase4-local-db-write-incident-2026-10-19.json",
                "sha256": sha256(local_db_write_incident),
            },
            "phase4_three_oil_homepage_probe": {
                "path": "docs/current/phase4-three-oil-homepage-probe-2026-10-19.json",
                "sha256": sha256(three_oil_homepage_probe),
            },
            "phase4_three_oil_observed_roster": {
                "path": "docs/current/phase4-three-oil-observed-roster-2026-10-20.json",
                "sha256": sha256(three_oil_observed_roster),
            },
            "phase4_cnooc_geoscience_job_evidence": {
                "path": "docs/current/phase4-cnooc-geoscience-job-evidence-2026-10-20.json",
                "sha256": sha256(cnooc_geoscience_job_evidence),
            },
            "phase4_three_oil_unit_entry_network_recheck": {
                "path": "docs/current/phase4-three-oil-unit-entry-network-recheck-2026-10-20.json",
                "sha256": sha256(three_oil_unit_entry_network_recheck),
            },
            "phase4_main_record": {
                "path": "docs/current/PHASE4-2026-10-17.md",
                "sha256": sha256(phase4_record),
            },
        },
        "code_inventory": code_facts(),
        "release_boundary": {
            "server_deployment": False,
            "automation_status": "paused",
            "formal_third_party_jobs_published": True,
            "notes": [
                "One C-level discovery lead was recovered to an official employer notice and published as job 5432; four B-level Huatu discovery rows were recovered to the official CGS attachment and published as jobs 5433-5436; no server deployment was performed.",
                "The remaining C-level lead (candidate 1) was rechecked on 2026-10-19; no job-specific official evidence or application URL was found, so it remains private and unresolved.",
                "A second Huatu announcement was recovered to the Nanjing Planning and Natural Resources Bureau official notice, table URL, and application URL; its deadline had passed and row-level table evidence was unavailable, so no candidate or formal job was created.",
                "The Huatu Chuanqing high-level recruitment page yielded 12 row-level private candidates; it supplied a cnpc.com.cn application mailbox but no verifiable official notice, table, or application URL, so all 12 remain private pending official recovery.",
                "A third-party discovery page or inaccessible attachment is not a job-level official evidence record.",
                "Historical snapshots remain archived and are not overwritten by current facts.",
            ],
        },
    }
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(OUTPUT)


if __name__ == "__main__":
    main()
