"""Build a non-publishable local Phase 4 audit manifest."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "docs" / "current" / "phase4-release-manifest.json"


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
        result = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, text=True, capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return True
    return bool(result.stdout.strip())


def db_facts(path: Path) -> dict[str, object]:
    digest = sha256(path)
    with sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        statuses = dict(connection.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status").fetchall())
        layers = dict(connection.execute("SELECT publication_layer, COUNT(*) FROM jobs GROUP BY publication_layer").fetchall())
        leads = int(connection.execute("SELECT COUNT(*) FROM candidate_leads").fetchone()[0])
    return {
        "path": "runtime/job_hub.sqlite3",
        "sha256": digest,
        "integrity_check": integrity,
        "job_status_counts": statuses,
        "publication_layer_counts": layers,
        "candidate_lead_count": leads,
    }


def main() -> None:
    sources = json.loads((ROOT / "data" / "discovery_sources.json").read_text(encoding="utf-8"))["sources"]
    adapters = json.loads((ROOT / "data" / "third_party_table_adapters.json").read_text(encoding="utf-8"))["adapters"]
    reconciliation = json.loads(
        (ROOT / "docs" / "current" / "phase4-organization-reconciliation-2026-10-17.json").read_text(encoding="utf-8")
    )
    database = db_facts(ROOT / "runtime" / "job_hub.sqlite3")
    working_tree_dirty = dirty()
    release_blockers = []
    if working_tree_dirty:
        release_blockers.append("working_tree_contains_uncommitted_or_untracked_changes")
    release_blockers.extend(
        [
            "official_site_browser_permission_blocked",
            "no_audited_third_party_table_batch_imported",
            "three_oil_external_identity_evidence_pending",
            "three_oil_unit_entry_evidence_incomplete",
            "local_phase4_validation_not_approved_for_server",
        ]
    )
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
            "third_party_adapters": sha256(ROOT / "data" / "third_party_table_adapters.json"),
            "organization_registry": sha256(ROOT / "data" / "organization_registry.json"),
            "three_oil_catalog": sha256(ROOT / "data" / "three_oil_organization_catalog.json"),
            "phase4_organization_reconciliation": sha256(
                ROOT / "docs" / "current" / "phase4-organization-reconciliation-2026-10-17.json"
            ),
            "phase4_three_oil_catalog_audit": sha256(
                ROOT / "docs" / "current" / "phase4-three-oil-catalog-audit-2026-10-17.json"
            ),
        },
        "database": database,
        "third_party": {
            "registered_sources": len(sources),
            "batch_candidate_sources": sum(1 for item in sources if item.get("batch_candidate_allowed")),
            "table_adapters": len(adapters),
            "real_candidate_leads_in_database": database["candidate_lead_count"],
        },
        "three_oil_organization_reconciliation": reconciliation["summary"],
        "automation_model": "gpt-5.6-terra",
        "automation_status": "paused",
        "production_deployment": "not_performed",
        "release_blockers": release_blockers,
    }
    OUTPUT.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(OUTPUT)


if __name__ == "__main__":
    main()
