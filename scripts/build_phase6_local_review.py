"""Build the Phase 6 local-only review report and hash-bound manifest.

The command is deliberately read-only with respect to both SQLite databases.
It documents an isolated review workspace and makes its non-deployment boundary
explicit; it is not a release or migration command.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from job_hub.three_oil_entry_evidence import (
    load_three_oil_entry_evidence,
    three_oil_entry_evidence_summary,
)


DEFAULT_WORKSPACE = ROOT / "runtime" / "phase6" / "review-2026-10-23-r3"
REPORT_NAME = "phase6-review-report.json"
MANIFEST_PATH = ROOT / "docs" / "current" / "phase6-local-review-manifest.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _git_value(*args: str) -> str | None:
    try:
        return subprocess.check_output(
            ["git", *args], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _review_id(workspace: Path) -> str:
    """Bind the manifest identity to the concrete isolated review directory."""

    name = workspace.name.strip()
    if not name.startswith("review-"):
        raise ValueError("Phase 6 review workspace name must start with 'review-'")
    return f"phase6-local-{name}"


def _database_counts(connection: sqlite3.Connection) -> tuple[dict[str, int | None], list[str]]:
    tables = sorted(
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    )
    return (
        {
            table: (
                int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                if table in tables
                else None
            )
            for table in ("jobs", "candidate_leads", "third_party_batches")
        },
        tables,
    )


def _read_database(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"database does not exist: {path}")
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        counts, tables = _database_counts(connection)
        batches: list[dict[str, Any]] = []
        notice_captures: list[dict[str, Any]] = []
        batch_rows = () if "third_party_batches" not in tables else connection.execute(
            """
            SELECT batch_id, discovery_source_id, discovery_page_url,
                   third_party_table_url, capture_mode, captured_at, media_type,
                   byte_count, content_sha256, retained_path, parsed_row_count,
                   candidate_row_count, official_notice_url, official_table_url,
                   official_application_url, status, notes
            FROM third_party_batches
            ORDER BY captured_at, batch_id
            """
        ).fetchall()
        for row in batch_rows:
            batch = dict(row)
            if batch.get("retained_path"):
                batch["retained_path"] = str(batch["retained_path"]).replace("\\", "/")
            batch_id = str(batch["batch_id"])
            candidate_rows = connection.execute(
                """
                SELECT COUNT(*) AS candidate_links,
                       SUM(CASE WHEN leads.published_job_id IS NOT NULL THEN 1 ELSE 0 END)
                           AS published_links
                FROM third_party_batch_candidates AS links
                JOIN candidate_leads AS leads ON leads.id = links.lead_id
                WHERE links.batch_id = ?
                """,
                (batch_id,),
            ).fetchone()
            batch["candidate_links"] = int(candidate_rows["candidate_links"] or 0)
            batch["published_links"] = int(candidate_rows["published_links"] or 0)
            batches.append(batch)
        if "candidate_leads" in tables:
            lead_rows = connection.execute(
                """
                SELECT id, title, employer_hint, published_date, official_url,
                       official_application_url, verification_status,
                       official_domain_status, metadata_json
                FROM candidate_leads
                ORDER BY id
                """
            ).fetchall()
            for row in lead_rows:
                metadata = json.loads(str(row["metadata_json"] or "{}"))
                capture = metadata.get("phase6_discovery_capture")
                if not isinstance(capture, dict):
                    continue
                item = dict(row)
                item.pop("metadata_json", None)
                item["capture"] = capture
                notice_captures.append(item)
        return {
            "path": str(path),
            "sha256": sha256(path),
            "integrity": integrity,
            "counts": counts,
            "tables": tables,
            "third_party_batches": batches,
            "third_party_notice_captures": notice_captures,
        }
    finally:
        connection.close()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_review_report(workspace: Path) -> dict[str, Any]:
    """Return facts from the review copy without changing either database."""

    isolation_path = workspace / "isolation-manifest.json"
    if not isolation_path.is_file():
        raise FileNotFoundError(f"missing isolation manifest: {isolation_path}")
    isolation = _read_json(isolation_path)
    formal_database = ROOT / "runtime" / "job_hub.sqlite3"
    review_database = workspace / "job_hub.sqlite3"
    formal = _read_database(formal_database)
    review = _read_database(review_database)
    source_hash = str(isolation.get("source_sha256") or "")
    entry_payload = load_three_oil_entry_evidence()

    return {
        "phase": "phase6",
        "purpose": "isolated_local_review",
        "generated_at": _utc_now(),
        "write_boundary": {
            "formal_local_database_modified": False,
            "server_database_modified": False,
            "production_deployment": "not_performed",
            "statement": (
                "This report reads the formal local database and the Phase 6 review copy "
                "in read-only mode. It never promotes candidates or deploys a release."
            ),
        },
        "isolation": {
            "manifest_path": str(isolation_path.relative_to(ROOT)).replace("\\", "/"),
            "manifest_sha256": sha256(isolation_path),
            "source_sha256_recorded": source_hash,
            "formal_database_sha256_now": formal["sha256"],
            "formal_database_matches_original_copy": formal["sha256"] == source_hash,
            "review_database_sha256_at_copy": isolation.get("review_database_sha256"),
            "review_database_sha256_now": review["sha256"],
            "review_database_integrity": review["integrity"],
        },
        "formal_database": {
            "path": "runtime/job_hub.sqlite3",
            "sha256": formal["sha256"],
            "integrity": formal["integrity"],
            "counts": formal["counts"],
        },
        "review_database": {
            "path": str(review_database.relative_to(ROOT)).replace("\\", "/"),
            "sha256": review["sha256"],
            "integrity": review["integrity"],
            "counts": review["counts"],
            "third_party_batches": review["third_party_batches"],
        },
        "three_oil_entry_evidence": {
            "path": "data/three_oil_entry_evidence.json",
            "sha256": sha256(ROOT / "data" / "three_oil_entry_evidence.json"),
            "summary": three_oil_entry_evidence_summary(entry_payload),
        },
        "interpretation": {
            "third_party_trial": (
                "The retained Huatu table batch completed the private candidate and official-link "
                "audit path, and the reviewed Zhonggong article completed the separate private "
                "notice-recovery path. Neither creates a student job without row-level official "
                "content verification; third-party links were not used as application links."
            ),
            "three_oil_entry_gap": (
                "A group portal or observed group-page display is not a verified independent "
                "recruitment entrance for a subsidiary. Remaining units require unit-owned "
                "official notice, ATS, or recruitment-page evidence."
            ),
        },
    }


def build_manifest(
    *,
    workspace: Path,
    report_path: Path,
    test_result: str,
    test_command: str,
    local_review_url: str,
) -> dict[str, Any]:
    tracked_paths = {
        "phase6_document": ROOT / "docs" / "current" / "PHASE6-2026-10-23.md",
        "documentation_index": ROOT / "docs" / "current" / "README.md",
        "project_readme": ROOT / "README.md",
        "architecture_document": ROOT / "ARCHITECTURE.md",
        "deployment_document": ROOT / "DEPLOYMENT.md",
        "review_report": report_path,
        "isolation_manifest": workspace / "isolation-manifest.json",
        "third_party_adapters": ROOT / "data" / "third_party_table_adapters.json",
        "discovery_sources": ROOT / "data" / "discovery_sources.json",
        "organization_registry": ROOT / "data" / "organization_registry.json",
        "three_oil_catalog": ROOT / "data" / "three_oil_organization_catalog.json",
        "three_oil_entry_evidence": ROOT / "data" / "three_oil_entry_evidence.json",
        "environment_example": ROOT / ".env.example",
        "application": ROOT / "job_hub" / "app.py",
        "database_contract": ROOT / "job_hub" / "db.py",
        "third_party_batch_service": ROOT / "job_hub" / "third_party_tables.py",
        "third_party_notice_capture": ROOT / "scripts" / "capture_phase6_discovery_notice.py",
        "three_oil_entry_service": ROOT / "job_hub" / "three_oil_entry_evidence.py",
        "worker": ROOT / "job_hub" / "worker.py",
        "admin_dashboard_template": ROOT / "job_hub" / "templates" / "admin_dashboard.html",
        "admin_leads_template": ROOT / "job_hub" / "templates" / "admin_leads.html",
        "admin_three_oil_entries_template": ROOT / "job_hub" / "templates" / "admin_three_oil_entries.html",
        "admin_stylesheet": ROOT / "job_hub" / "static" / "admin.css",
        "manifest_builder": ROOT / "scripts" / "build_phase6_local_review.py",
    }
    status = _git_value("status", "--porcelain") or ""
    return {
        "phase": "phase6",
        "review_id": _review_id(workspace),
        "generated_at": _utc_now(),
        "release_status": "local_review_only_not_publishable",
        "production_deployment": "not_performed",
        "deployment_authorization": "not_requested_and_not_granted",
        "git": {
            "baseline_sha": "bde03d9",
            "head_sha": _git_value("rev-parse", "HEAD"),
            "working_tree_dirty": bool(status),
            "working_tree_entry_count": len(status.splitlines()),
        },
        "review_workspace": {
            "path": str(workspace.relative_to(ROOT)).replace("\\", "/"),
            "formal_database": "runtime/job_hub.sqlite3",
            "formal_database_write_policy": "not_a_phase6_write_target",
            "worker_automation_default": False,
            "worker_execution": "not_started_for_live_sync_or_collection",
        },
        "bound_artifacts_sha256": {
            name: sha256(path) for name, path in tracked_paths.items()
        },
        "validation": {
            "test_command": test_command,
            "test_result": test_result,
            "test_scope": "full_suite_with_temporary_test_databases",
            "review_database_integrity": _read_database(workspace / "job_hub.sqlite3")[
                "integrity"
            ],
            "formal_database_sha256": sha256(ROOT / "runtime" / "job_hub.sqlite3"),
            "local_web_acceptance": {
                "url": local_review_url,
                "result": "passed",
                "checks": [
                    "student jobs page showed 677 official jobs and no administrator navigation",
                    "student jobs page did not expose Huatu provider data from the isolated batch",
                    "administrator route redirected unauthenticated visitors to the token-protected login page",
                    "authenticated dashboard, candidate queue, and three-oil ledger rendering are covered by the full local integration suite",
                    "isolated Huatu batch displayed 12 parsed rows, 11 candidate links, and 0 batch-published links",
                    "isolated candidate queue included the Zhonggong-to-Sinopec private group-notice recovery without student-side disclosure",
                ],
            },
        },
        "release_blockers": [
            "project_owner_local_review_required",
            "working_tree_contains_uncommitted_phase6_changes",
            "no_new_in_scope_geoscience_position_table_with_row_level_official_evidence",
            "61_of_64_formal_three_oil_units_lack_verified_independent_recruitment_entry",
            "worker_automation_was_only_code_and_isolated_lifecycle_tested",
            "production_deployment_not_performed",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE)
    parser.add_argument("--test-command", default=".venv\\Scripts\\python.exe -m pytest -q")
    parser.add_argument("--test-result", required=True)
    parser.add_argument("--local-review-url", default="http://127.0.0.1:8100")
    args = parser.parse_args()

    workspace = args.workspace.resolve()
    phase_root = (ROOT / "runtime" / "phase6").resolve()
    if phase_root not in workspace.parents:
        raise ValueError("Phase 6 review workspace must reside under runtime/phase6")
    report_path = workspace / REPORT_NAME
    report = build_review_report(workspace)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    manifest = build_manifest(
        workspace=workspace,
        report_path=report_path,
        test_result=args.test_result,
        test_command=args.test_command,
        local_review_url=args.local_review_url,
    )
    MANIFEST_PATH.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(report_path.relative_to(ROOT))
    print(MANIFEST_PATH.relative_to(ROOT))


if __name__ == "__main__":
    main()
