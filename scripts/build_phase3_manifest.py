"""Build a local, non-secret Phase 3 release manifest."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from job_hub.policy_contract import publication_policy_digest


ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "docs" / "current" / "phase3-release-manifest.json"


def sha256(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git_sha() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def git_working_tree_dirty() -> bool:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return True
    return bool(result.stdout.strip())


def release_env_values(path: Path) -> dict[str, str]:
    """Read only immutable identity fields from the local release env."""

    if not path.exists():
        return {}
    values: dict[str, str] = {}
    allowed = {
        "APP_RELEASE_VERSION",
        "APP_RELEASE_GIT_SHA",
        "APP_RELEASE_IMAGE_SHA",
        "APP_RELEASE_BUILT_AT",
        "JOB_HUB_IMAGE",
        "RUNTIME_IMAGE",
        "JOB_HUB_BROWSER_IMAGE",
        "BROWSER_DEPS_IMAGE",
        "HEADLESS_SHELL_IMAGE",
    }
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^([A-Z0-9_]+)=(.*)$", line.strip())
        if match and match.group(1) in allowed:
            values[match.group(1)] = match.group(2).strip()
    return values


def image_digest(reference: str | None) -> str | None:
    match = re.search(r"@(sha256:[0-9a-f]{64})$", str(reference or "").strip())
    return match.group(1) if match else None


def main() -> None:
    release_env = ROOT / ".release.env"
    runtime = release_env_values(release_env)
    local_database = ROOT / "runtime" / "job_hub.sqlite3"
    local_backup = ROOT / "backups" / "job_hub-pre-phase3-backfill-20261017.sqlite3"
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "phase": "phase3",
        "logical_baseline": "phase532-logical-phase1",
        "git_sha": git_sha(),
        "working_tree_dirty": git_working_tree_dirty(),
        "compose_sha256": sha256(ROOT / "deploy" / "compose.yaml"),
        "compose_path": "deploy/compose.yaml",
        "development_compose_sha256": sha256(ROOT / "docker-compose.yml"),
        "browser_compose_sha256": sha256(ROOT / "docker-compose.browser.yml"),
        "release_env_sha256": sha256(release_env),
        "config_sha256": {
            "release_env": sha256(release_env),
            "env_example": sha256(ROOT / ".env.example"),
            "source_registry": sha256(ROOT / "data" / "sources.json"),
            "discovery_registry": sha256(ROOT / "data" / "discovery_sources.json"),
            "organization_registry": sha256(ROOT / "data" / "organization_registry.json"),
            "three_oil_catalog": sha256(ROOT / "data" / "three_oil_organization_catalog.json"),
            "third_party_adapters": sha256(ROOT / "data" / "third_party_table_adapters.json"),
        },
        "runtime_identity_scope": "local_release_env_only",
        "web_image_sha256": image_digest(runtime.get("JOB_HUB_IMAGE")),
        "worker_image_sha256": image_digest(runtime.get("RUNTIME_IMAGE")),
        "browser_image_sha256": image_digest(runtime.get("JOB_HUB_BROWSER_IMAGE")),
        "release_env_values": {
            "version": runtime.get("APP_RELEASE_VERSION"),
            "git_sha": runtime.get("APP_RELEASE_GIT_SHA"),
            "built_at": runtime.get("APP_RELEASE_BUILT_AT"),
            "application_image": image_digest(runtime.get("JOB_HUB_IMAGE")),
            "worker_image": image_digest(runtime.get("RUNTIME_IMAGE")),
            "browser_image": image_digest(runtime.get("JOB_HUB_BROWSER_IMAGE")),
            "browser_deps_image": image_digest(runtime.get("BROWSER_DEPS_IMAGE")),
            "headless_shell_image": image_digest(runtime.get("HEADLESS_SHELL_IMAGE")),
        },
        "policy_digest": publication_policy_digest(),
        "schema_version": "phase3-additive-migration",
        "automation_model": "gpt-5.6-terra",
        "automation_status": "paused",
        "production_deployment": "not_performed",
        "database_backup": {
            "path": "/var/lib/job-hub/backups/job_hub-20261013T031851Z.sqlite3",
            "sha256": "afdfd5d26898d984d15c1b9772a7ba36d0efc906c22cb9a30cb989827c46cfde",
            "note": "documented Phase 532 backup; not claimed to contain 223 public rows",
        },
        "local_database": {
            "path": "runtime/job_hub.sqlite3",
            "sha256": sha256(local_database),
        },
        "local_pre_backfill_backup": {
            "path": "backups/job_hub-pre-phase3-backfill-20261017.sqlite3",
            "sha256": sha256(local_backup),
        },
        "database_backup_path": "/var/lib/job-hub/backups/job_hub-20261013T031851Z.sqlite3",
        "database_backup_sha256": "afdfd5d26898d984d15c1b9772a7ba36d0efc906c22cb9a30cb989827c46cfde",
        "missing_runtime_artifacts": [
            key
            for key, value in {
                "web_image_sha256": image_digest(runtime.get("JOB_HUB_IMAGE")),
                "worker_image_sha256": image_digest(runtime.get("RUNTIME_IMAGE")),
                "browser_image_sha256": image_digest(runtime.get("JOB_HUB_BROWSER_IMAGE")),
                "release_env_sha256": sha256(release_env),
            }.items()
            if value is None
        ],
    }
    manifest["release_blockers"] = []
    if manifest["working_tree_dirty"]:
        manifest["release_blockers"].append(
            "working_tree_contains_uncommitted_or_untracked_changes"
        )
    if manifest["production_deployment"] != "performed":
        manifest["release_blockers"].append("production_deployment_not_performed")
    OUTPUT.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(OUTPUT)


if __name__ == "__main__":
    main()
