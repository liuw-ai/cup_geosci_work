"""Verify the hash-bound, non-publishable Phase 4 local manifest.

This command is intentionally read-only.  It checks the release inputs and
the database facts recorded by the manifest, but it never writes the database
or changes deployment state.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "docs" / "current" / "phase4-release-manifest.json"
DATABASE = ROOT / "runtime" / "job_hub.sqlite3"
GENERATED_METADATA_PATHS = (
    ":!docs/current/phase4-current-state-audit-2026-10-18.json",
    ":!docs/current/phase4-release-manifest.json",
    ":!docs/current/phase4-local-validation-2026-10-19.json",
    ":!docs/current/phase4-local-validation-2026-10-20.json",
    ":!docs/current/phase4-local-web-recheck-2026-10-19.json",
    ":!docs/current/phase4-local-web-live-validation-2026-10-19.json",
    ":!docs/current/phase4-third-party-funnel-audit-2026-10-18.json",
    ":!docs/current/phase4-three-oil-unit-entry-matrix-2026-10-18.json",
    ":!docs/current/phase4-third-party-huatu-table-discovery-2026-10-19.json",
    ":!docs/current/phase4-completion-audit-2026-10-19.json",
    ":!docs/current/phase4-workspace-layout-audit-2026-10-19.json",
    ":!docs/current/phase4-three-oil-catalog-audit-2026-10-17.json",
    ":!docs/current/phase4-third-party-huatu-state-owned-entry-2026-10-19.json",
    ":!docs/current/phase4-three-oil-observed-roster-2026-10-20.json",
    ":!docs/current/phase4-three-oil-homepage-probe-2026-10-19.json",
    ":!docs/current/phase4-third-party-official-recovery-recheck-2026-10-19.json",
    ":!docs/current/phase4-third-party-huatu-cnpc-chuanqing-2026-10-20.json",
)

# These reports are deliberately excluded from the source-tree dirty flag
# because they are generated after the source/config commit.  They are still
# release evidence, however, so every hash recorded by build_phase4_manifest
# must be checked before the manifest can be considered valid.
MANIFEST_BOUND_REPORTS = {
    "phase4_organization_reconciliation": "docs/current/phase4-organization-reconciliation-2026-10-17.json",
    "phase4_three_oil_catalog_audit": "docs/current/phase4-three-oil-catalog-audit-2026-10-17.json",
    "phase4_third_party_trial": "docs/current/phase4-third-party-trial-2026-10-18.json",
    "phase4_three_oil_entry_evidence": "docs/current/phase4-three-oil-entry-evidence-2026-10-18.json",
    "phase4_cnooc_unit_display_evidence": "docs/current/phase4-cnooc-unit-display-evidence-2026-10-19.json",
    "phase4_cnooc_unit_job_evidence": "docs/current/phase4-cnooc-unit-job-evidence-2026-10-19.json",
    "phase4_three_oil_cooec_negative_entry": "docs/current/phase4-three-oil-cooec-entry-negative-evidence-2026-10-19.json",
    "phase4_three_oil_unit_entry_matrix": "docs/current/phase4-three-oil-unit-entry-matrix-2026-10-18.json",
    "phase4_sinopec_display_audit": "docs/current/phase4-sinopec-display-units-2026-10-18.json",
    "phase4_sinopec_page_roster": "docs/current/phase4-sinopec-page-roster-2026-10-19.json",
    "phase4_third_party_geoscience_followup": "docs/current/phase4-third-party-geoscience-followup-2026-10-18.json",
    "phase4_current_state_audit": "docs/current/phase4-current-state-audit-2026-10-18.json",
    "phase4_document_consistency": "docs/current/phase4-document-consistency-2026-10-20.json",
    "phase4_third_party_candidate": "docs/current/phase4-third-party-candidate-2026-10-18.json",
    "phase4_third_party_hrinm": "docs/current/phase4-third-party-hrinm-2026-10-18.json",
    "phase4_third_party_cgs": "docs/current/phase4-third-party-cgs-2026-10-18.json",
    "phase4_third_party_blocked_batches": "docs/current/phase4-third-party-blocked-batches-2026-10-18.json",
    "phase4_third_party_nhri_official_page": "docs/current/phase4-third-party-nhri-official-page-2026-10-19.json",
    "phase4_third_party_huatu_nanjing_land_center": "docs/current/phase4-third-party-huatu-nanjing-land-center-2026-10-19.json",
    "phase4_third_party_lead_recheck": "docs/current/phase4-third-party-lead-recheck-2026-10-19.json",
    "phase4_cnpc_entry_recheck": "docs/current/phase4-cnpc-entry-recheck-2026-10-19.json",
    "phase4_three_oil_unit_entry_recheck": "docs/current/phase4-three-oil-unit-entry-recheck-2026-10-19.json",
    "phase4_sinopec_official_job_evidence": "docs/current/phase4-sinopec-official-job-evidence-2026-10-19.json",
    "phase4_third_party_access_recheck": "docs/current/phase4-third-party-access-recheck-2026-10-19.json",
    "phase4_local_repair": "docs/current/phase4-local-repair-2026-10-19.json",
    "phase4_third_party_funnel": "docs/current/phase4-third-party-funnel-audit-2026-10-18.json",
    "phase4_local_web_recheck": "docs/current/phase4-local-web-recheck-2026-10-19.json",
    "phase4_local_web_live_validation": "docs/current/phase4-local-web-live-validation-2026-10-19.json",
    "phase4_huatu_table_discovery": "docs/current/phase4-third-party-huatu-table-discovery-2026-10-19.json",
    "phase4_third_party_channel_recheck": "docs/current/phase4-third-party-channel-recheck-2026-10-20.json",
    "phase4_document_consistency_2026_10_20": "docs/current/phase4-document-consistency-2026-10-20.json",
    "phase4_huatu_discovery_scan": "docs/current/phase4-third-party-huatu-discovery-2026-10-19.json",
    "phase4_third_party_bingtuan_official_recovery": "docs/current/phase4-third-party-bingtuan-official-recovery-2026-10-19.json",
    "phase4_third_party_huatu_shanxi_geology_group": "docs/current/phase4-third-party-huatu-shanxi-geology-group-2026-10-19.json",
    "phase4_third_party_huatu_hbdk_geophysics": "docs/current/phase4-third-party-huatu-hbdk-geophysics-2026-10-19.json",
    "phase4_third_party_hbdk_official_table": "docs/current/phase4-third-party-hbdk-official-table-2026-10-19.json",
    "phase4_third_party_hbdk_candidate_registration": "docs/current/phase4-third-party-hbdk-candidate-registration-2026-10-19.json",
    "phase4_main_record": "docs/current/PHASE4-2026-10-17.md",
    "phase4_local_validation": "docs/current/phase4-local-validation-2026-10-20.json",
    "phase4_local_db_write_incident": "docs/current/phase4-local-db-write-incident-2026-10-19.json",
    "phase4_three_oil_homepage_probe": "docs/current/phase4-three-oil-homepage-probe-2026-10-19.json",
    "phase4_completion_audit": "docs/current/phase4-completion-audit-2026-10-19.json",
    "phase4_workspace_layout": "docs/current/phase4-workspace-layout-audit-2026-10-19.json",
    "phase4_third_party_huatu_state_owned_entry": "docs/current/phase4-third-party-huatu-state-owned-entry-2026-10-19.json",
    "phase4_third_party_huatu_hebei_water_geology": "docs/current/phase4-third-party-huatu-hebei-water-geology-2026-10-19.json",
    "phase4_three_oil_observed_roster": "docs/current/phase4-three-oil-observed-roster-2026-10-20.json",
    "phase4_third_party_huatu_cnpc_chuanqing": "docs/current/phase4-third-party-huatu-cnpc-chuanqing-2026-10-20.json",
    "phase4_cnooc_geoscience_job_evidence": "docs/current/phase4-cnooc-geoscience-job-evidence-2026-10-20.json",
    "phase4_three_oil_unit_entry_network_recheck": "docs/current/phase4-three-oil-unit-entry-network-recheck-2026-10-20.json",
}


def sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _git_revision_exists(revision: str) -> bool:
    if not revision:
        return False
    result = subprocess.run(
        ["git", "cat-file", "-e", f"{revision}^{{commit}}"],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def _input_tree_dirty() -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain", "--", ".", *GENERATED_METADATA_PATHS],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return bool(result.stdout.strip())


def _database_facts() -> dict[str, Any]:
    with sqlite3.connect(f"file:{DATABASE.resolve().as_posix()}?mode=ro", uri=True) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        job_count = int(connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0])
        status_counts = dict(
            connection.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status").fetchall()
        )
        candidate_leads = int(connection.execute("SELECT COUNT(*) FROM candidate_leads").fetchone()[0])
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
    return {
        "integrity_check": integrity,
        "job_count": job_count,
        "status_counts": status_counts,
        "candidate_lead_count": candidate_leads,
        "formal_third_party_job_count": formal_third_party_jobs,
        "sha256": sha256(DATABASE),
    }


def _record_error(errors: list[str], name: str, expected: Any, actual: Any) -> None:
    if expected != actual:
        errors.append(f"{name}: expected {expected!r}, got {actual!r}")


def verify_manifest(path: Path = MANIFEST) -> dict[str, Any]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    errors: list[str] = []

    _record_error(errors, "phase", "phase4", manifest.get("phase"))
    _record_error(errors, "manifest.working_tree_dirty", False, manifest.get("working_tree_dirty"))
    _record_error(errors, "current_input_tree_dirty", False, _input_tree_dirty())
    _record_error(errors, "production_deployment", "not_performed", manifest.get("production_deployment"))
    _record_error(errors, "automation_status", "paused", manifest.get("automation_status"))
    if not _git_revision_exists(str(manifest.get("git_sha") or "")):
        errors.append("git_sha does not resolve to a commit")

    core_paths = {
        "compose_sha256": ROOT / str(manifest.get("compose_path") or ""),
        "release_env_sha256": ROOT / ".release.env",
        "config_sha256.discovery_sources": ROOT / "data" / "discovery_sources.json",
        "config_sha256.sources": ROOT / "data" / "sources.json",
        "config_sha256.third_party_adapters": ROOT / "data" / "third_party_table_adapters.json",
        "config_sha256.organization_registry": ROOT / "data" / "organization_registry.json",
        "config_sha256.three_oil_catalog": ROOT / "data" / "three_oil_organization_catalog.json",
        "dockerfile_sha256.app": ROOT / "deploy" / "docker" / "Dockerfile.app",
        "dockerfile_sha256.browser": ROOT / "deploy" / "docker" / "Dockerfile.browser",
    }
    for key, file_path in core_paths.items():
        if key == "compose_sha256":
            expected = manifest.get("compose_sha256")
        elif key == "release_env_sha256":
            expected = manifest.get("release_env_sha256")
        elif key.startswith("config_sha256."):
            expected = manifest.get("config_sha256", {}).get(key.split(".", 1)[1])
        else:
            expected = manifest.get("dockerfile_sha256", {}).get(key.split(".", 1)[1])
        _record_error(errors, key, expected, sha256(file_path))

    recorded_reports = manifest.get("config_sha256", {})
    for key, relative_path in MANIFEST_BOUND_REPORTS.items():
        expected = recorded_reports.get(key)
        if expected is None:
            errors.append(f"config_sha256.{key}: missing manifest hash")
            continue
        _record_error(
            errors,
            f"config_sha256.{key}",
            expected,
            sha256(ROOT / relative_path),
        )

    database = _database_facts()
    recorded_database = manifest.get("database", {})
    for key in ("sha256", "integrity_check", "job_count", "status_counts", "candidate_lead_count", "formal_third_party_job_count"):
        _record_error(errors, f"database.{key}", recorded_database.get(key), database.get(key))

    blockers = [str(item) for item in manifest.get("release_blockers", [])]
    for required in (
        "third_party_batch_expansion_incomplete",
        "three_oil_external_identity_evidence_pending",
        "three_oil_unit_entry_evidence_incomplete",
        "local_phase4_validation_not_approved_for_server",
    ):
        if required not in blockers:
            errors.append(f"release blocker missing: {required}")

    try:
        display_path = str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        display_path = str(path)
    return {
        "manifest": display_path,
        "ok": not errors,
        "errors": errors,
        "database": database,
        "production_deployment": manifest.get("production_deployment"),
        "automation_status": manifest.get("automation_status"),
        "release_blockers": blockers,
    }


def main() -> int:
    result = verify_manifest()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
