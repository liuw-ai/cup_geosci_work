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
                ":!docs/current/phase4-current-state-audit-2026-10-18.json",
                ":!docs/current/phase4-release-manifest.json",
                ":!docs/current/phase4-local-validation-2026-10-18.json",
                ":!docs/current/phase4-local-web-recheck-2026-10-18.json",
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
        "docs/current/CODEBASE-2026-10-17.md",
        "docs/current/PHASE3-2026-10-17.md",
        "docs/current/phase3-audit-preview-2026-10-17.json",
        "docs/current/phase3-backfill-result-2026-10-17.json",
    ]
    # The audit is consumed by the manifest, so hashing the audit itself or
    # the manifest here would create a self-referential, never-stable chain.
    # Keep source/current documents separate from metadata documents whose
    # hashes are bound one way by the manifest.
    current_paths = [
        "docs/current/PHASE4-2026-10-17.md",
        "docs/current/phase4-local-validation-2026-10-18.json",
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
    entry_matrix = ROOT / "docs" / "current" / "phase4-three-oil-unit-entry-matrix-2026-10-18.json"
    geoscience_followup = ROOT / "docs" / "current" / "phase4-third-party-geoscience-followup-2026-10-18.json"
    validation = ROOT / "docs" / "current" / "phase4-local-validation-2026-10-18.json"
    manual_candidate = ROOT / "docs" / "current" / "phase4-third-party-candidate-2026-10-18.json"
    hrinm_trial = ROOT / "docs" / "current" / "phase4-third-party-hrinm-2026-10-18.json"
    cgs_trial = ROOT / "docs" / "current" / "phase4-third-party-cgs-2026-10-18.json"
    blocked_batches = ROOT / "docs" / "current" / "phase4-third-party-blocked-batches-2026-10-18.json"
    access_recheck = ROOT / "docs" / "current" / "phase4-third-party-access-recheck-2026-10-19.json"
    local_repair = ROOT / "docs" / "current" / "phase4-local-repair-2026-10-19.json"
    third_party_funnel = ROOT / "docs" / "current" / "phase4-third-party-funnel-audit-2026-10-18.json"
    local_web_recheck = ROOT / "docs" / "current" / "phase4-local-web-recheck-2026-10-18.json"
    phase4_record = ROOT / "docs" / "current" / "PHASE4-2026-10-17.md"
    report = {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "phase": "phase4",
        "scope": "local_read_only_current_state",
        "git_sha": git(["git", "rev-parse", "HEAD"]),
        "git_status_porcelain": input_tree_status(),
        "database": current_database_facts(),
        "sources_and_organizations": source_facts(),
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
            "phase4_three_oil_unit_entry_matrix": {
                "path": "docs/current/phase4-three-oil-unit-entry-matrix-2026-10-18.json",
                "sha256": sha256(entry_matrix),
            },
            "phase4_third_party_geoscience_followup": {
                "path": "docs/current/phase4-third-party-geoscience-followup-2026-10-18.json",
                "sha256": sha256(geoscience_followup),
            },
            "phase4_local_validation": {
                "path": "docs/current/phase4-local-validation-2026-10-18.json",
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
                "path": "docs/current/phase4-local-web-recheck-2026-10-18.json",
                "sha256": sha256(local_web_recheck),
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
                "A third-party discovery page or inaccessible attachment is not a job-level official evidence record.",
                "Historical snapshots remain archived and are not overwritten by current facts.",
            ],
        },
    }
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(OUTPUT)


if __name__ == "__main__":
    main()
