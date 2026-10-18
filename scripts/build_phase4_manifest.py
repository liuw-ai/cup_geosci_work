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
LOCAL_VALIDATION = ROOT / "docs" / "current" / "phase4-local-validation-2026-10-18.json"


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
                ":!docs/current/phase4-current-state-audit-2026-10-18.json",
                ":!docs/current/phase4-release-manifest.json",
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
        "formal_third_party_job_count": formal_third_party_jobs,
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
    validation = local_validation_facts()
    trial_report = ROOT / "docs" / "current" / "phase4-third-party-trial-2026-10-18.json"
    entry_evidence = ROOT / "docs" / "current" / "phase4-three-oil-entry-evidence-2026-10-18.json"
    entry_matrix = ROOT / "docs" / "current" / "phase4-three-oil-unit-entry-matrix-2026-10-18.json"
    display_audit = ROOT / "docs" / "current" / "phase4-sinopec-display-units-2026-10-18.json"
    geoscience_followup = ROOT / "docs" / "current" / "phase4-third-party-geoscience-followup-2026-10-18.json"
    current_audit = ROOT / "docs" / "current" / "phase4-current-state-audit-2026-10-18.json"
    manual_candidate = ROOT / "docs" / "current" / "phase4-third-party-candidate-2026-10-18.json"
    hrinm_trial = ROOT / "docs" / "current" / "phase4-third-party-hrinm-2026-10-18.json"
    cgs_trial = ROOT / "docs" / "current" / "phase4-third-party-cgs-2026-10-18.json"
    blocked_batches = ROOT / "docs" / "current" / "phase4-third-party-blocked-batches-2026-10-18.json"
    third_party_funnel = ROOT / "docs" / "current" / "phase4-third-party-funnel-audit-2026-10-18.json"
    local_web_recheck = ROOT / "docs" / "current" / "phase4-local-web-recheck-2026-10-18.json"
    phase4_record = ROOT / "docs" / "current" / "PHASE4-2026-10-17.md"
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
        "release_env_sha256": sha256(ROOT / ".release.env"),
        "config_sha256": {
            "discovery_sources": sha256(ROOT / "data" / "discovery_sources.json"),
            "sources": sha256(ROOT / "data" / "sources.json"),
            "third_party_adapters": sha256(ROOT / "data" / "third_party_table_adapters.json"),
            "organization_registry": sha256(ROOT / "data" / "organization_registry.json"),
            "three_oil_catalog": sha256(ROOT / "data" / "three_oil_organization_catalog.json"),
            "phase4_organization_reconciliation": sha256(
                ROOT / "docs" / "current" / "phase4-organization-reconciliation-2026-10-17.json"
            ),
            "phase4_three_oil_catalog_audit": sha256(
                ROOT / "docs" / "current" / "phase4-three-oil-catalog-audit-2026-10-17.json"
            ),
            "phase4_third_party_trial": sha256(trial_report),
            "phase4_three_oil_entry_evidence": sha256(entry_evidence),
            "phase4_three_oil_unit_entry_matrix": sha256(entry_matrix),
            "phase4_sinopec_display_audit": sha256(display_audit),
            "phase4_third_party_geoscience_followup": sha256(geoscience_followup),
            "phase4_current_state_audit": sha256(current_audit),
            "phase4_document_consistency": sha256(current_audit),
            "phase4_third_party_candidate": sha256(manual_candidate),
            "phase4_third_party_hrinm": sha256(hrinm_trial),
            "phase4_third_party_cgs": sha256(cgs_trial),
            "phase4_third_party_blocked_batches": sha256(blocked_batches),
            "phase4_third_party_funnel": sha256(third_party_funnel),
            "phase4_local_web_recheck": sha256(local_web_recheck),
            "phase4_main_record": sha256(phase4_record),
            "phase4_local_validation": sha256(LOCAL_VALIDATION),
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
                "status": "in_progress",
                "release_gate": "third_party_batch_expansion_incomplete",
            },
        },
        "local_validation": validation,
        "third_party": {
            "registered_sources": len(sources),
            "batch_candidate_sources": sum(1 for item in sources if item.get("batch_candidate_allowed")),
            "table_adapters": len(adapters),
            "real_candidate_leads_in_database": database["candidate_lead_count"],
            "private_candidate_evidence": "docs/current/phase4-third-party-candidate-2026-10-18.json",
            "official_attachment_trial": "docs/current/phase4-third-party-cgs-2026-10-18.json",
            "formal_jobs_published": database.get("formal_third_party_job_count", 0),
            "trial_report": "docs/current/phase4-third-party-trial-2026-10-18.json",
            "formal_job_report": "docs/current/phase4-third-party-hrinm-2026-10-18.json",
            "blocked_batch_report": "docs/current/phase4-third-party-blocked-batches-2026-10-18.json",
            "funnel_audit": "docs/current/phase4-third-party-funnel-audit-2026-10-18.json",
        },
        "three_oil_organization_reconciliation": reconciliation["summary"],
        "document_policy": {
            "historical_snapshots_are_evidence_only": True,
            "current_facts_document": "docs/current/phase4-current-state-audit-2026-10-18.json",
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
