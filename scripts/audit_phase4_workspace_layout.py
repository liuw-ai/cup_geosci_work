"""Audit the local Phase 4 workspace boundaries without moving or deleting files.

The report makes the remaining directory debt explicit.  In particular, root
level Docker/Compose files are retained as compatibility inputs but are not
production inputs; only ``deploy/compose.yaml`` is the production entrypoint.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "docs" / "current" / "phase4-workspace-layout-audit-2026-10-19.json"

ROOT_COMPATIBILITY_FILES = (
    "docker-compose.yml",
    "docker-compose.browser.yml",
    "docker-compose.location-fix.yml",
    "docker-compose.public.yml",
    "Dockerfile",
    "Dockerfile.browser",
    "Dockerfile.business-date",
    "Dockerfile.experience-gate",
)


def sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _file_facts(relative_path: str) -> dict[str, Any]:
    path = ROOT / relative_path
    return {
        "path": relative_path,
        "exists": path.exists(),
        "is_file": path.is_file(),
        "bytes": path.stat().st_size if path.is_file() else None,
        "sha256": sha256(path),
    }


def build_report(root: Path = ROOT) -> dict[str, Any]:
    """Return a deterministic layout report for *root* (used by tests)."""
    canonical = {
        relative: {
            "exists": (root / relative).exists(),
            "is_file": (root / relative).is_file(),
        }
        for relative in (
            "deploy/compose.yaml",
            "deploy/docker/Dockerfile.app",
            "deploy/docker/Dockerfile.browser",
            "deploy/certs/gsrsaovsslca2018.crt",
            "job_hub",
            "data",
            "tests",
            "docs/current",
            "runtime",
            "backups",
            "history",
        )
    }
    compatibility = [
        relative
        for relative in ROOT_COMPATIBILITY_FILES
        if (root / relative).exists()
    ]
    current_sqlite = sorted(
        str(path.relative_to(root)).replace("\\", "/")
        for path in (root / "docs" / "current").glob("**/*")
        if path.is_file() and path.suffix.lower() in {".sqlite", ".sqlite3", ".db"}
    ) if (root / "docs" / "current").is_dir() else []
    return {
        "phase": "phase4",
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "scope": "local_read_only_workspace_layout",
        "production_entrypoint": "deploy/compose.yaml",
        "canonical_paths": canonical,
        "root_compatibility_files": [
            _file_facts(relative) for relative in compatibility
        ] if root == ROOT else compatibility,
        "current_docs_database_files": current_sqlite,
        "history_is_present": (root / "history").is_dir(),
        "policy": {
            "no_move_or_delete": True,
            "root_compatibility_files_are_not_production_inputs": True,
            "runtime_and_backups_are_not_source_code": True,
            "history_is_evidence_only": True,
        },
        "status": (
            "pass"
            if canonical["deploy/compose.yaml"]["is_file"]
            and canonical["deploy/docker/Dockerfile.app"]["is_file"]
            and canonical["deploy/docker/Dockerfile.browser"]["is_file"]
            and canonical["deploy/certs/gsrsaovsslca2018.crt"]["is_file"]
            and not current_sqlite
            else "review_required"
        ),
    }


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(build_report(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(OUTPUT)


if __name__ == "__main__":
    main()
