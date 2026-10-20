"""Build a non-publishable local Phase 4 audit manifest."""

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


OUTPUT = ROOT / "docs" / "current" / "phase4-release-manifest.json"
LOCAL_VALIDATION = ROOT / "docs" / "current" / "phase4-local-validation-2026-10-21.json"

# Keep the dirty flag about source/config/database inputs. These reports are
# generated after the source commit and are committed as metadata afterward.
GENERATED_METADATA_PATHS = (
    ":!docs/current/phase4-current-state-audit-2026-10-18.json",
    ":!docs/current/phase4-current-state-audit-2026-10-21.json",
    ":!docs/current/phase4-release-manifest.json",
    ":!docs/current/phase4-local-validation-2026-10-19.json",
    ":!docs/current/phase4-local-validation-2026-10-20.json",
    ":!docs/current/phase4-local-validation-2026-10-21.json",
    ":!docs/current/phase4-local-web-recheck-2026-10-19.json",
    ":!docs/current/phase4-local-web-live-validation-2026-10-20.json",
    ":!docs/current/phase4-local-web-live-validation-2026-10-21.json",
    ":!docs/current/PHASE4-2026-10-17.md",
    ":!docs/current/phase4-third-party-funnel-audit-2026-10-18.json",
    ":!docs/current/phase4-third-party-huatu-table-discovery-2026-10-19.json",
    ":!docs/current/phase4-third-party-channel-recheck-2026-10-20.json",
    ":!docs/current/phase4-document-consistency-2026-10-20.json",
    ":!docs/current/phase4-third-party-official-recovery-recheck-2026-10-19.json",
    ":!docs/current/phase4-three-oil-unit-entry-matrix-2026-10-18.json",
    ":!docs/current/phase4-cnpc-bgp-entry-recheck-2026-10-19.json",
    ":!docs/current/phase4-completion-audit-2026-10-19.json",
    ":!docs/current/phase4-workspace-layout-audit-2026-10-19.json",
    ":!docs/current/phase4-three-oil-catalog-audit-2026-10-17.json",
    ":!docs/current/phase4-third-party-huatu-state-owned-entry-2026-10-19.json",
    ":!docs/current/phase4-third-party-huatu-hebei-water-geology-2026-10-19.json",
    ":!docs/current/phase4-three-oil-observed-roster-2026-10-20.json",
    ":!docs/current/phase4-three-oil-homepage-probe-2026-10-19.json",
    ":!docs/current/phase4-third-party-huatu-cnpc-chuanqing-2026-10-20.json",
    ":!docs/current/phase4-cnooc-geoscience-job-evidence-2026-10-20.json",
    ":!docs/current/phase4-three-oil-unit-entry-network-recheck-2026-10-20.json",
    ":!docs/current/phase4-sinopec-company-intro-recheck-2026-10-20.json",
    ":!docs/current/phase4-cnooc-portal-live-recheck-2026-10-20.json",
    ":!docs/current/phase4-three-oil-official-homepage-entry-recheck-2026-10-20.json",
    ":!docs/current/phase4-third-party-hbdk-water-official-evidence-2026-10-20.json",
    ":!docs/current/phase4-third-party-hbdk-water-trial-2026-10-21.json",
    ":!docs/current/phase4-third-party-huatu-official-recovery-2026-10-21.json",
    ":!docs/current/phase4-third-party-gdzys-2026-10-21.json",
    ":!docs/current/phase4-third-party-hbdk-water-promotion-2026-10-21.json",
    ":!docs/current/phase4-cnooc-api-unit-evidence-2026-10-21.json",
)


def sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git_sha() -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def dirty() -> bool:
    try:
        # The manifest is generated after the source/config commit and then
        # committed as a metadata-only follow-up.  Exclude only this output
        # file so the recorded dirty flag describes the release inputs rather
        # than the manifest being written itself.
        result = subprocess.run(
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
            capture_output=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return True
    return bool(result.stdout.strip())


def db_facts(path: Path) -> dict[str, object]:
    digest = sha256(path)
    with sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        job_count = int(connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0])
        statuses = dict(connection.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status").fetchall())
        layers = dict(connection.execute("SELECT publication_layer, COUNT(*) FROM jobs GROUP BY publication_layer").fetchall())
        leads = int(connection.execute("SELECT COUNT(*) FROM candidate_leads").fetchone()[0])
        formal_candidate_converted_jobs = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM candidate_leads
                WHERE verification_status = 'published'
                  AND published_job_id IS NOT NULL
                """
            ).fetchone()[0]
        )
        formal_discovery_attributed_jobs = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM candidate_leads
                WHERE verification_status = 'published'
                  AND published_job_id IS NOT NULL
                  AND COALESCE(discovery_source_id, '') <> COALESCE(official_source_id, '')
                """
            ).fetchone()[0]
        )
    as_of_date = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    read_database = Database(path, read_only=True)
    student_visible_counts = {
        layer: read_database.list_jobs(
            page_size=None,
            as_of_date=as_of_date,
            publication_layer=layer,
        )[1]
        for layer in sorted(PUBLICATION_LAYERS)
    }
    student_visible_total = read_database.list_jobs(
        page_size=None,
        as_of_date=as_of_date,
    )[1]
    return {
        "path": "runtime/job_hub.sqlite3",
        "sha256": digest,
        "integrity_check": integrity,
        "job_count": job_count,
        "status_counts": statuses,
        "publication_layer_counts": layers,
        "student_read_as_of_date": as_of_date,
        "student_visible_layer_counts": student_visible_counts,
        "student_visible_total": student_visible_total,
        "candidate_lead_count": leads,
        "formal_candidate_converted_job_count": formal_candidate_converted_jobs,
        "formal_discovery_attributed_job_count": formal_discovery_attributed_jobs,
        # Kept as the strict third-party metric; the old name is part of the
        # current Manifest contract and is now explicitly defined by the
        # discovery-source attribution query above.
        "formal_third_party_job_count": formal_discovery_attributed_jobs,
    }


def local_validation_facts(path: Path = LOCAL_VALIDATION) -> dict[str, object] | None:
    """Load the last isolated validation record without inventing test facts."""
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("local validation report must be an object")
    return payload


def main() -> None:
    sources = json.loads((ROOT / "data" / "discovery_sources.json").read_text(encoding="utf-8"))["sources"]
    adapters = json.loads((ROOT / "data" / "third_party_table_adapters.json").read_text(encoding="utf-8"))["adapters"]
    reconciliation = json.loads(
        (ROOT / "docs" / "current" / "phase4-organization-reconciliation-2026-10-17.json").read_text(encoding="utf-8")
    )
    pre_candidate_backup = ROOT / "backups" / "job_hub-pre-phase4-third-party-candidate-20261018.sqlite3"
    pre_hrinm_backup = ROOT / "backups" / "job_hub-pre-phase4-hrinm-20261018.sqlite3"
    pre_conversion_audit_backup = ROOT / "backups" / "job_hub-pre-phase4-conversion-audit-20261018.sqlite3"
    pre_local_repair_backup = ROOT / "backups" / "job_hub-pre-phase4-deadline-location-repair-20261019.sqlite3"
    current_after_local_server_stop_backup = ROOT / "backups" / "job_hub-phase4-current-after-local-server-stop-20261019.sqlite3"
    # The current backup must be replaced whenever the local database schema or
    # evidence changes; never silently compare runtime data with an older hash.
    current_backup = ROOT / "backups" / "job_hub-phase4-current-20261021.sqlite3"
    database = db_facts(ROOT / "runtime" / "job_hub.sqlite3")
    database["pre_third_party_candidate_backup"] = {
        "path": "backups/job_hub-pre-phase4-third-party-candidate-20261018.sqlite3",
        "sha256": sha256(pre_candidate_backup),
    }
    database["pre_hrinm_backup"] = {
        "path": "backups/job_hub-pre-phase4-hrinm-20261018.sqlite3",
        "sha256": sha256(pre_hrinm_backup),
    }
    database["pre_conversion_audit_backup"] = {
        "path": "backups/job_hub-pre-phase4-conversion-audit-20261018.sqlite3",
        "sha256": sha256(pre_conversion_audit_backup),
    }
    database["pre_deadline_location_repair_backup"] = {
        "path": "backups/job_hub-pre-phase4-deadline-location-repair-20261019.sqlite3",
        "sha256": sha256(pre_local_repair_backup),
    }
    database["current_after_local_server_stop_backup"] = {
        "path": "backups/job_hub-phase4-current-after-local-server-stop-20261019.sqlite3",
        "sha256": sha256(current_after_local_server_stop_backup),
    }
    database["current_backup"] = {
        "path": "backups/job_hub-phase4-current-20261021.sqlite3",
        "sha256": sha256(current_backup),
        "matches_runtime_database": sha256(current_backup) == database["sha256"],
    }
    validation = local_validation_facts()
    trial_report = ROOT / "docs" / "current" / "phase4-third-party-trial-2026-10-18.json"
    entry_evidence = ROOT / "docs" / "current" / "phase4-three-oil-entry-evidence-2026-10-18.json"
    cnooc_unit_evidence = ROOT / "docs" / "current" / "phase4-cnooc-unit-display-evidence-2026-10-19.json"
    cnooc_full_job_evidence = ROOT / "docs" / "current" / "phase4-cnooc-unit-job-evidence-2026-10-19.json"
    cooec_negative_entry = ROOT / "docs" / "current" / "phase4-three-oil-cooec-entry-negative-evidence-2026-10-19.json"
    entry_matrix = ROOT / "docs" / "current" / "phase4-three-oil-unit-entry-matrix-2026-10-18.json"
    entry_matrix_payload = json.loads(entry_matrix.read_text(encoding="utf-8")) if entry_matrix.is_file() else {}
    display_audit = ROOT / "docs" / "current" / "phase4-sinopec-display-units-2026-10-18.json"
    sinopec_page_roster = ROOT / "docs" / "current" / "phase4-sinopec-page-roster-2026-10-19.json"
    geoscience_followup = ROOT / "docs" / "current" / "phase4-third-party-geoscience-followup-2026-10-18.json"
    current_audit = ROOT / "docs" / "current" / "phase4-current-state-audit-2026-10-21.json"
    manual_candidate = ROOT / "docs" / "current" / "phase4-third-party-candidate-2026-10-18.json"
    hrinm_trial = ROOT / "docs" / "current" / "phase4-third-party-hrinm-2026-10-18.json"
    cgs_trial = ROOT / "docs" / "current" / "phase4-third-party-cgs-2026-10-18.json"
    blocked_batches = ROOT / "docs" / "current" / "phase4-third-party-blocked-batches-2026-10-18.json"
    nhri_official_page = ROOT / "docs" / "current" / "phase4-third-party-nhri-official-page-2026-10-19.json"
    huatu_nanjing_land_center = ROOT / "docs" / "current" / "phase4-third-party-huatu-nanjing-land-center-2026-10-19.json"
    lead_recheck = ROOT / "docs" / "current" / "phase4-third-party-lead-recheck-2026-10-19.json"
    cnpc_entry_recheck = ROOT / "docs" / "current" / "phase4-cnpc-entry-recheck-2026-10-19.json"
    cnpc_bgp_entry_recheck = ROOT / "docs" / "current" / "phase4-cnpc-bgp-entry-recheck-2026-10-19.json"
    three_oil_unit_entry_recheck = ROOT / "docs" / "current" / "phase4-three-oil-unit-entry-recheck-2026-10-19.json"
    sinopec_official_job_evidence = ROOT / "docs" / "current" / "phase4-sinopec-official-job-evidence-2026-10-19.json"
    access_recheck = ROOT / "docs" / "current" / "phase4-third-party-access-recheck-2026-10-19.json"
    official_recovery_recheck = ROOT / "docs" / "current" / "phase4-third-party-official-recovery-recheck-2026-10-19.json"
    local_repair = ROOT / "docs" / "current" / "phase4-local-repair-2026-10-19.json"
    third_party_funnel = ROOT / "docs" / "current" / "phase4-third-party-funnel-audit-2026-10-18.json"
    local_web_recheck = ROOT / "docs" / "current" / "phase4-local-web-recheck-2026-10-19.json"
    local_web_live_validation = ROOT / "docs" / "current" / "phase4-local-web-live-validation-2026-10-21.json"
    huatu_table_discovery = ROOT / "docs" / "current" / "phase4-third-party-huatu-table-discovery-2026-10-19.json"
    third_party_channel_recheck = ROOT / "docs" / "current" / "phase4-third-party-channel-recheck-2026-10-20.json"
    document_consistency = ROOT / "docs" / "current" / "phase4-document-consistency-2026-10-20.json"
    huatu_discovery_scan = ROOT / "docs" / "current" / "phase4-third-party-huatu-discovery-2026-10-19.json"
    huatu_state_owned_entry = ROOT / "docs" / "current" / "phase4-third-party-huatu-state-owned-entry-2026-10-19.json"
    huatu_hebei_water_geology = ROOT / "docs" / "current" / "phase4-third-party-huatu-hebei-water-geology-2026-10-19.json"
    bingtuan_official_recovery = ROOT / "docs" / "current" / "phase4-third-party-bingtuan-official-recovery-2026-10-19.json"
    huatu_shanxi_geology_group = ROOT / "docs" / "current" / "phase4-third-party-huatu-shanxi-geology-group-2026-10-19.json"
    huatu_hbdk_geophysics = ROOT / "docs" / "current" / "phase4-third-party-huatu-hbdk-geophysics-2026-10-19.json"
    hbdk_official_table = ROOT / "docs" / "current" / "phase4-third-party-hbdk-official-table-2026-10-19.json"
    hbdk_registration = ROOT / "docs" / "current" / "phase4-third-party-hbdk-candidate-registration-2026-10-19.json"
    hbdk_water_evidence = ROOT / "docs" / "current" / "phase4-third-party-hbdk-water-official-evidence-2026-10-20.json"
    hbdk_water_trial = ROOT / "docs" / "current" / "phase4-third-party-hbdk-water-trial-2026-10-21.json"
    hbdk_water_promotion = ROOT / "docs" / "current" / "phase4-third-party-hbdk-water-promotion-2026-10-21.json"
    huatu_official_recovery = ROOT / "docs" / "current" / "phase4-third-party-huatu-official-recovery-2026-10-21.json"
    gdzys_trial = ROOT / "docs" / "current" / "phase4-third-party-gdzys-2026-10-21.json"
    huatu_cnpc_chuanqing = ROOT / "docs" / "current" / "phase4-third-party-huatu-cnpc-chuanqing-2026-10-20.json"
    cnpc_official_recheck = ROOT / "docs" / "current" / "phase4-cnpc-official-recheck-2026-10-20.json"
    sxdkj_official_recheck = ROOT / "docs" / "current" / "phase4-third-party-sxdkj-official-recheck-2026-10-20.json"
    cnooc_shenzhen_job_evidence = ROOT / "docs" / "current" / "phase4-cnooc-shenzhen-job-evidence-2026-10-20.json"
    local_db_write_incident = ROOT / "docs" / "current" / "phase4-local-db-write-incident-2026-10-19.json"
    three_oil_homepage_probe = ROOT / "docs" / "current" / "phase4-three-oil-homepage-probe-2026-10-19.json"
    three_oil_observed_roster = ROOT / "docs" / "current" / "phase4-three-oil-observed-roster-2026-10-20.json"
    cnooc_geoscience_job_evidence = ROOT / "docs" / "current" / "phase4-cnooc-geoscience-job-evidence-2026-10-20.json"
    three_oil_unit_entry_network_recheck = ROOT / "docs" / "current" / "phase4-three-oil-unit-entry-network-recheck-2026-10-20.json"
    sinopec_company_intro_recheck = ROOT / "docs" / "current" / "phase4-sinopec-company-intro-recheck-2026-10-20.json"
    cnooc_portal_live_recheck = ROOT / "docs" / "current" / "phase4-cnooc-portal-live-recheck-2026-10-20.json"
    cnooc_api_unit_evidence = ROOT / "docs" / "current" / "phase4-cnooc-api-unit-evidence-2026-10-21.json"
    sinopec_display_cross_group_audit = ROOT / "docs" / "current" / "phase4-sinopec-display-cross-group-audit-2026-10-20.json"
    three_oil_official_homepage_entry_recheck = ROOT / "docs" / "current" / "phase4-three-oil-official-homepage-entry-recheck-2026-10-20.json"
    completion_audit = ROOT / "docs" / "current" / "phase4-completion-audit-2026-10-19.json"
    workspace_layout = ROOT / "docs" / "current" / "phase4-workspace-layout-audit-2026-10-19.json"
    phase4_record = ROOT / "docs" / "current" / "PHASE4-2026-10-17.md"
    huatu_cnpc_chuanqing_payload = json.loads(huatu_cnpc_chuanqing.read_text(encoding="utf-8"))
    working_tree_dirty = dirty()
    release_blockers = []
    if working_tree_dirty:
        release_blockers.append("working_tree_contains_uncommitted_or_untracked_changes")
    if validation is not None:
        release_blockers.extend(
            str(item)
            for item in validation.get("release_blockers", [])
            if str(item).strip()
        )
    else:
        release_blockers.append("local_validation_report_missing")
    for blocker in (
        "third_party_batch_expansion_incomplete",
        "three_oil_external_identity_evidence_pending",
        "three_oil_unit_entry_evidence_incomplete",
        "local_phase4_validation_not_approved_for_server",
    ):
        if blocker not in release_blockers:
            release_blockers.append(blocker)
    manifest = {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "phase": "phase4",
        "status": "local_audit_in_progress",
        "logical_baseline": "phase532-logical-phase1",
        "git_sha": git_sha(),
        "working_tree_dirty": working_tree_dirty,
        "compose_path": "deploy/compose.yaml",
        "compose_sha256": sha256(ROOT / "deploy" / "compose.yaml"),
        "dockerfile_sha256": {
            "app": sha256(ROOT / "deploy" / "docker" / "Dockerfile.app"),
            "browser": sha256(ROOT / "deploy" / "docker" / "Dockerfile.browser"),
        },
        "release_env_sha256": sha256(ROOT / ".release.env"),
        "config_sha256": {
            "discovery_sources": sha256(ROOT / "data" / "discovery_sources.json"),
            "sources": sha256(ROOT / "data" / "sources.json"),
            "third_party_adapters": sha256(ROOT / "data" / "third_party_table_adapters.json"),
            "organization_registry": sha256(ROOT / "data" / "organization_registry.json"),
            "three_oil_catalog": sha256(ROOT / "data" / "three_oil_organization_catalog.json"),
            "browser_ca_certificate": sha256(
                ROOT / "deploy" / "certs" / "gsrsaovsslca2018.crt"
            ),
            "phase4_organization_reconciliation": sha256(
                ROOT / "docs" / "current" / "phase4-organization-reconciliation-2026-10-17.json"
            ),
            "phase4_three_oil_catalog_audit": sha256(
                ROOT / "docs" / "current" / "phase4-three-oil-catalog-audit-2026-10-17.json"
            ),
            "phase4_third_party_trial": sha256(trial_report),
            "phase4_three_oil_entry_evidence": sha256(entry_evidence),
            "phase4_cnooc_unit_display_evidence": sha256(cnooc_unit_evidence),
            "phase4_cnooc_unit_job_evidence": sha256(cnooc_full_job_evidence),
            "phase4_three_oil_cooec_negative_entry": sha256(cooec_negative_entry),
            "phase4_three_oil_unit_entry_matrix": sha256(entry_matrix),
            "phase4_sinopec_display_audit": sha256(display_audit),
            "phase4_sinopec_page_roster": sha256(sinopec_page_roster),
            "phase4_third_party_geoscience_followup": sha256(geoscience_followup),
            "phase4_current_state_audit": sha256(current_audit),
            "phase4_document_consistency": sha256(document_consistency),
            "phase4_third_party_candidate": sha256(manual_candidate),
            "phase4_third_party_hrinm": sha256(hrinm_trial),
            "phase4_third_party_cgs": sha256(cgs_trial),
            "phase4_third_party_blocked_batches": sha256(blocked_batches),
            "phase4_third_party_nhri_official_page": sha256(nhri_official_page),
            "phase4_third_party_huatu_nanjing_land_center": sha256(huatu_nanjing_land_center),
            "phase4_third_party_lead_recheck": sha256(lead_recheck),
            "phase4_cnpc_entry_recheck": sha256(cnpc_entry_recheck),
            "phase4_cnpc_bgp_entry_recheck": sha256(cnpc_bgp_entry_recheck),
            "phase4_three_oil_unit_entry_recheck": sha256(three_oil_unit_entry_recheck),
            "phase4_sinopec_official_job_evidence": sha256(sinopec_official_job_evidence),
            "phase4_third_party_access_recheck": sha256(access_recheck),
            "phase4_third_party_official_recovery_recheck": sha256(official_recovery_recheck),
            "phase4_local_repair": sha256(local_repair),
            "phase4_third_party_funnel": sha256(third_party_funnel),
            "phase4_local_web_recheck": sha256(local_web_recheck),
            "phase4_local_web_live_validation": sha256(local_web_live_validation),
            "phase4_huatu_table_discovery": sha256(huatu_table_discovery),
            "phase4_third_party_channel_recheck": sha256(third_party_channel_recheck),
            "phase4_document_consistency_2026_10_20": sha256(document_consistency),
            "phase4_huatu_discovery_scan": sha256(huatu_discovery_scan),
            "phase4_third_party_huatu_state_owned_entry": sha256(huatu_state_owned_entry),
            "phase4_third_party_huatu_hebei_water_geology": sha256(huatu_hebei_water_geology),
            "phase4_third_party_bingtuan_official_recovery": sha256(bingtuan_official_recovery),
            "phase4_third_party_huatu_shanxi_geology_group": sha256(huatu_shanxi_geology_group),
            "phase4_third_party_huatu_hbdk_geophysics": sha256(huatu_hbdk_geophysics),
            "phase4_third_party_hbdk_official_table": sha256(hbdk_official_table),
            "phase4_third_party_hbdk_candidate_registration": sha256(hbdk_registration),
            "phase4_third_party_hbdk_water_official_evidence": sha256(hbdk_water_evidence),
            "phase4_third_party_hbdk_water_trial": sha256(hbdk_water_trial),
            "phase4_third_party_hbdk_water_promotion": sha256(hbdk_water_promotion),
            "phase4_third_party_huatu_official_recovery": sha256(huatu_official_recovery),
            "phase4_third_party_gdzys": sha256(gdzys_trial),
            "phase4_third_party_huatu_cnpc_chuanqing": sha256(huatu_cnpc_chuanqing),
            "phase4_cnpc_official_recheck": sha256(cnpc_official_recheck),
            "phase4_third_party_sxdkj_official_recheck": sha256(sxdkj_official_recheck),
            "phase4_cnooc_shenzhen_job_evidence": sha256(cnooc_shenzhen_job_evidence),
            "phase4_main_record": sha256(phase4_record),
            "phase4_local_validation": sha256(LOCAL_VALIDATION),
            "phase4_local_db_write_incident": sha256(local_db_write_incident),
            "phase4_three_oil_homepage_probe": sha256(three_oil_homepage_probe),
            "phase4_three_oil_observed_roster": sha256(three_oil_observed_roster),
            "phase4_cnooc_geoscience_job_evidence": sha256(cnooc_geoscience_job_evidence),
            "phase4_three_oil_unit_entry_network_recheck": sha256(three_oil_unit_entry_network_recheck),
            "phase4_sinopec_company_intro_recheck": sha256(sinopec_company_intro_recheck),
            "phase4_cnooc_portal_live_recheck": sha256(cnooc_portal_live_recheck),
            "phase4_cnooc_api_unit_evidence": sha256(cnooc_api_unit_evidence),
            "phase4_sinopec_display_cross_group_audit": sha256(sinopec_display_cross_group_audit),
            "phase4_three_oil_official_homepage_entry_recheck": sha256(three_oil_official_homepage_entry_recheck),
            "phase4_completion_audit": sha256(completion_audit),
            "phase4_workspace_layout": sha256(workspace_layout),
        },
        "database": database,
        "target_metrics": {
            "student_job_target": {
                "target": 500,
                "actual": database.get("student_visible_total", 0),
                "status": (
                    "met"
                    if int(database.get("student_visible_total", 0) or 0) >= 500
                    else "not_met"
                ),
                "basis": "data/effective_job_target_plan.json",
                "note": "This is the overall student-visible target; it is separate from third-party expansion readiness.",
            },
            "third_party_expansion": {
                "candidate_leads": database.get("candidate_lead_count", 0),
                "formal_jobs": database.get("formal_third_party_job_count", 0),
                "formal_candidate_converted_jobs": database.get("formal_candidate_converted_job_count", 0),
                "status": "in_progress",
                "release_gate": "third_party_batch_expansion_incomplete",
            },
        },
        "local_validation": validation,
        "completion_audit": {
            "path": "docs/current/phase4-completion-audit-2026-10-19.json",
            "status": (
                json.loads(completion_audit.read_text(encoding="utf-8")).get("status")
                if completion_audit.is_file()
                else "missing"
            ),
        },
        "third_party": {
            "registered_sources": len(sources),
            "batch_candidate_sources": sum(1 for item in sources if item.get("batch_candidate_allowed")),
            "table_adapters": len(adapters),
            "real_candidate_leads_in_database": database["candidate_lead_count"],
            "private_candidate_evidence": "docs/current/phase4-third-party-candidate-2026-10-18.json",
            "official_attachment_trial": "docs/current/phase4-third-party-cgs-2026-10-18.json",
            "formal_jobs_published": database.get("formal_third_party_job_count", 0),
            "formal_candidate_converted_jobs": database.get("formal_candidate_converted_job_count", 0),
            "trial_report": "docs/current/phase4-third-party-trial-2026-10-18.json",
            "formal_job_report": "docs/current/phase4-third-party-hrinm-2026-10-18.json",
            "blocked_batch_report": "docs/current/phase4-third-party-blocked-batches-2026-10-18.json",
            "nhri_official_page_evidence": "docs/current/phase4-third-party-nhri-official-page-2026-10-19.json",
            "official_recovery_recheck": "docs/current/phase4-third-party-huatu-nanjing-land-center-2026-10-19.json",
            "unresolved_lead_recheck": "docs/current/phase4-third-party-lead-recheck-2026-10-19.json",
            "cnpc_entry_recheck": "docs/current/phase4-cnpc-entry-recheck-2026-10-19.json",
            "unit_entry_recheck": "docs/current/phase4-three-oil-unit-entry-recheck-2026-10-19.json",
            "funnel_audit": "docs/current/phase4-third-party-funnel-audit-2026-10-18.json",
            "table_discovery_report": "docs/current/phase4-third-party-huatu-table-discovery-2026-10-19.json",
            "channel_recheck_report": "docs/current/phase4-third-party-channel-recheck-2026-10-20.json",
            "document_consistency_report": "docs/current/phase4-document-consistency-2026-10-20.json",
            "discovery_scan_report": "docs/current/phase4-third-party-huatu-discovery-2026-10-19.json",
            "hbdk_geophysics_visual_evidence": "docs/current/phase4-third-party-huatu-hbdk-geophysics-2026-10-19.json",
            "hebei_water_geology_discovery": "docs/current/phase4-third-party-huatu-hebei-water-geology-2026-10-19.json",
            "cnpc_chuanqing_discovery_evidence": "docs/current/phase4-third-party-huatu-cnpc-chuanqing-2026-10-20.json",
            "cnpc_chuanqing_candidate_rows": len(huatu_cnpc_chuanqing_payload.get("candidates", [])),
            "cnpc_chuanqing_official_recovery_status": huatu_cnpc_chuanqing_payload.get("official_recovery", {}).get("status"),
            "cnpc_chuanqing_formal_jobs_created": 0,
            "hbdk_water_official_evidence": "docs/current/phase4-third-party-hbdk-water-official-evidence-2026-10-20.json",
            "hbdk_water_isolated_trial": "docs/current/phase4-third-party-hbdk-water-trial-2026-10-21.json",
            "hbdk_water_promotion": "docs/current/phase4-third-party-hbdk-water-promotion-2026-10-21.json",
            "huatu_official_recovery": "docs/current/phase4-third-party-huatu-official-recovery-2026-10-21.json",
            "gdzys_trial": "docs/current/phase4-third-party-gdzys-2026-10-21.json",
        },
        "three_oil_organization_reconciliation": reconciliation["summary"],
        "three_oil_entry_evidence": {
            "matrix_summary": entry_matrix_payload.get("summary", {}),
            "cnooc_unit_display_evidence": "docs/current/phase4-cnooc-unit-display-evidence-2026-10-19.json",
            "cnooc_unit_job_evidence": "docs/current/phase4-cnooc-unit-job-evidence-2026-10-19.json",
            "cooec_negative_entry_evidence": "docs/current/phase4-three-oil-cooec-entry-negative-evidence-2026-10-19.json",
            "homepage_probe": "docs/current/phase4-three-oil-homepage-probe-2026-10-19.json",
            "latest_cnooc_portal_live_recheck": "docs/current/phase4-cnooc-portal-live-recheck-2026-10-20.json",
            "latest_cnooc_api_unit_evidence": "docs/current/phase4-cnooc-api-unit-evidence-2026-10-21.json",
            "latest_official_homepage_entry_recheck": "docs/current/phase4-three-oil-official-homepage-entry-recheck-2026-10-20.json",
            "cnooc_units_with_active_job_evidence": int(
                json.loads(cnooc_api_unit_evidence.read_text(encoding="utf-8"))
                .get("summary", {})
                .get("catalog_units_observed", 0)
            ),
            "cnooc_observed_job_cards": int(
                json.loads(cnooc_api_unit_evidence.read_text(encoding="utf-8"))
                .get("summary", {})
                .get("official_job_card_count", 0)
            ),
            "independent_unit_recruitment_portals_proven": 0,
            "interpretation": "官方集团页按单位展示并提供岗位投递链接，不等于每个单位有独立招聘入口。",
        },
        "document_policy": {
            "historical_snapshots_are_evidence_only": True,
            "current_facts_document": "docs/current/phase4-current-state-audit-2026-10-21.json",
            "release_manifest_binds_source_commit": True,
            "manifest_commit_note": (
                "git_sha is the clean source/config commit used to generate this metadata; "
                "the metadata-only commit is intentionally subsequent."
            ),
        },
        "automation_model": "gpt-5.6-terra",
        "automation_status": "paused",
        "production_deployment": "not_performed",
        "release_blockers": release_blockers,
    }
    OUTPUT.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(OUTPUT)


if __name__ == "__main__":
    main()
