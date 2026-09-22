"""National energy-system source acquisition matrix.

The organization registry answers *who owns an entrance*.  This module answers
*how that entrance may be collected today*.  Keeping the two ledgers separate
means a homepage can remain useful as identity evidence while its recruitment
portal is blocked, dynamic, or still awaiting a dedicated adapter.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from job_hub.contracts import (
    ContractValidationError,
    validate_national_source_matrix,
)
from job_hub.organizations import load_organization_registry
from job_hub.sources import load_source_registry


PROJECT_ROOT = Path(__file__).resolve().parent.parent
MATRIX_PATH = PROJECT_ROOT / "data" / "national_source_matrix.json"
SOURCE_REGISTRY_PATH = PROJECT_ROOT / "data" / "sources.json"
PROVINCIAL_SOURCE_REGISTRY_PATH = PROJECT_ROOT / "data" / "provincial_sources.json"


def _registered_source_ids() -> set[str]:
    source_ids: set[str] = set()
    for path in (SOURCE_REGISTRY_PATH, PROVINCIAL_SOURCE_REGISTRY_PATH):
        if not path.exists():
            continue
        source_ids.update(
            str(item["id"])
            for item in load_source_registry(str(path))
            if item.get("id")
        )
    return source_ids


def load_national_source_matrix(
    path: Path | None = None,
    *,
    source_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Load and validate the versioned national acquisition ledger."""
    matrix_path = path or MATRIX_PATH
    try:
        payload = json.loads(matrix_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Cannot read national source matrix: {matrix_path}") from error
    try:
        return validate_national_source_matrix(
            payload,
            source_ids=_registered_source_ids() if source_ids is None else source_ids,
        )
    except ContractValidationError as error:
        raise ValueError(f"national_source_matrix.json is invalid: {error}") from error


def _organization_rows(
    organization_registry: dict[str, Any],
    target_affiliations: set[str],
) -> Iterable[dict[str, Any]]:
    organizations = organization_registry.get("organizations", [])
    by_id = {str(item["id"]): item for item in organizations}
    for organization in organizations:
        if organization.get("affiliation") not in target_affiliations:
            continue
        parent_id = organization.get("parent_id")
        yield {
            **organization,
            "parent_name": (
                by_id.get(str(parent_id), {}).get("canonical_name")
                if parent_id
                else None
            ),
        }


def national_source_matrix_rows(
    matrix: dict[str, Any] | None = None,
    *,
    organization_registry: dict[str, Any] | None = None,
    source_records: Iterable[dict[str, Any]] | None = None,
    source_health: Iterable[dict[str, Any]] | None = None,
    latest_runs: Iterable[dict[str, Any]] | None = None,
    affiliation: str | None = None,
    organization_role: str | None = None,
    runtime_status: str | None = None,
) -> list[dict[str, Any]]:
    """Expand the compact policy ledger into one row per official channel."""
    payload = matrix or load_national_source_matrix()
    organizations = organization_registry or load_organization_registry()
    target_affiliations = set(payload["target_affiliations"])
    if affiliation:
        target_affiliations &= {affiliation}
    assessments = {
        str(item["source_id"]): item for item in payload["source_assessments"]
    }
    defaults = payload["channel_defaults"]
    source_by_id = {
        str(item["id"]): item
        for item in (source_records or [])
        if item.get("id")
    }
    health_by_id = {
        str(item["source_id"]): item
        for item in (source_health or [])
        if item.get("source_id")
    }
    run_by_id = {
        str(item["source_id"]): item
        for item in (latest_runs or [])
        if item.get("source_id")
    }
    rows: list[dict[str, Any]] = []
    for organization in _organization_rows(organizations, target_affiliations):
        if organization_role and organization.get("organization_role") != organization_role:
            continue
        for channel in organization.get("channels", []):
            if channel.get("channel_type") not in payload["target_channel_types"]:
                continue
            source_id = str(channel.get("source_id") or "").strip() or None
            policy = assessments.get(source_id) if source_id else None
            if policy is None:
                policy = defaults[channel["channel_type"]]
            declared_status = str(policy["runtime_status"])
            if runtime_status and declared_status != runtime_status:
                continue
            health = health_by_id.get(source_id or "", {})
            latest = run_by_id.get(source_id or "", {})
            source = source_by_id.get(source_id or "", {})
            rows.append(
                {
                    "organization_id": organization["id"],
                    "organization_name": organization["canonical_name"],
                    "parent_id": organization.get("parent_id"),
                    "parent_name": organization.get("parent_name"),
                    "organization_role": organization["organization_role"],
                    "industry_path": organization["industry_path"],
                    "affiliation": organization["affiliation"],
                    "channel_id": channel["id"],
                    "channel_type": channel["channel_type"],
                    "official_url": channel["official_url"],
                    "backup_urls": list(channel.get("backup_urls", [])),
                    "source_id": source_id,
                    "source_name": source.get("name"),
                    "source_registered": bool(source),
                    "source_enabled": bool(source.get("enabled")) if source else None,
                    "source_health_status": health.get("status"),
                    "latest_crawl_status": latest.get("status"),
                    "last_synced_at": latest.get("finished_at"),
                    "acquisition_mode": policy["acquisition_mode"],
                    "runtime_status": declared_status,
                    "scan_conclusion": policy["scan_conclusion"],
                    "observed_on": policy.get("observed_on", payload["as_of"]),
                    "observed_http_status": policy.get("observed_http_status"),
                    "observed_error_class": policy.get(
                        "observed_error_class", "not_applicable"
                    ),
                    "sample_announcement_url": policy.get("sample_announcement_url"),
                    "field_validation": dict(
                        policy.get(
                            "field_validation",
                            {key: False for key in (
                                "title",
                                "employer",
                                "degree",
                                "major",
                                "location",
                                "deadline",
                                "application_url",
                            )},
                        )
                    ),
                    "note": policy["note"],
                    "assessment_source_id": (
                        source_id if source_id in assessments else None
                    ),
                }
            )
    return rows


def national_source_matrix_summary(
    matrix: dict[str, Any] | None = None,
    *,
    organization_registry: dict[str, Any] | None = None,
    source_records: Iterable[dict[str, Any]] | None = None,
    source_health: Iterable[dict[str, Any]] | None = None,
    latest_runs: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Summarize readiness without presenting registrations as job counts."""
    payload = matrix or load_national_source_matrix()
    rows = national_source_matrix_rows(
        payload,
        organization_registry=organization_registry,
        source_records=source_records,
        source_health=source_health,
        latest_runs=latest_runs,
    )
    by_mode = Counter(str(row["acquisition_mode"]) for row in rows)
    by_status = Counter(str(row["runtime_status"]) for row in rows)
    by_conclusion = Counter(str(row["scan_conclusion"]) for row in rows)
    by_affiliation = Counter(str(row["affiliation"]) for row in rows)
    assessed = sum(1 for row in rows if row["assessment_source_id"])
    with_backup = sum(1 for row in rows if row["backup_urls"])
    return {
        "as_of": payload["as_of"],
        "target_affiliations": list(payload["target_affiliations"]),
        "organization_count": len({row["organization_id"] for row in rows}),
        "channel_count": len(rows),
        "source_assessment_count": len(payload["source_assessments"]),
        "channels_with_explicit_assessment": assessed,
        "channels_with_backup": with_backup,
        "backup_rate": round(with_backup / len(rows), 4) if rows else 0.0,
        "acquisition_mode_counts": dict(sorted(by_mode.items())),
        "runtime_status_counts": dict(sorted(by_status.items())),
        "scan_conclusion_counts": dict(sorted(by_conclusion.items())),
        "affiliation_counts": dict(sorted(by_affiliation.items())),
        "source_unavailable_channels": by_conclusion["source_unavailable"],
        "successful_no_match_channels": by_conclusion["scan_success_no_match"],
        "official_api_channels": by_mode["official_api"],
        "scope_note": (
            "入口矩阵数量、来源状态和扫描结论都不等于公开岗位数量；"
            "访问受限不能解释为无匹配。"
        ),
    }
