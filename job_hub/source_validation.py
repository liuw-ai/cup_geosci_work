"""Auditable validation ledger for provincial official employment sources.

The target matrix answers which five official roles each province still needs.
This module records stronger evidence for a bounded subset of those targets:
the official entry, an official announcement sample when available, field-level
evidence, a fallback entry and an offline parser regression fixture.  It does
not enable a crawler and it never turns a source record into a public job.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

from job_hub.contracts import (
    ContractValidationError,
    PROVINCIAL_SOURCE_TARGET_ROLES,
    SOURCE_VALIDATION_STAGES,
    validate_source_validation_registry,
)
from job_hub.locations import PROVINCES
from job_hub.source_targets import load_source_targets, role_label
from job_hub.sources import load_source_registries


PROJECT_ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = PROJECT_ROOT / "data" / "source_validation_registry.json"
SOURCE_REGISTRY_PATH = PROJECT_ROOT / "data" / "sources.json"
PROVINCIAL_SOURCE_REGISTRY_PATH = PROJECT_ROOT / "data" / "provincial_sources.json"


def default_source_validation_registry_path() -> Path:
    """Return the versioned Phase 4 validation ledger path."""
    return REGISTRY_PATH


def _static_source_records() -> list[dict[str, Any]]:
    return load_source_registries(
        [str(SOURCE_REGISTRY_PATH), str(PROVINCIAL_SOURCE_REGISTRY_PATH)]
    )


def load_source_validation_registry(
    path: Path | None = None,
    *,
    source_records: Iterable[dict[str, Any]] | None = None,
    target_matrix: dict[str, Any] | None = None,
    fixture_root: Path | None = None,
) -> dict[str, Any]:
    """Load and cross-check the provincial validation ledger.

    Cross-registry checks are intentionally performed at load time, before a
    record can appear in an administrator report.  A malformed ledger cannot
    silently increase a provincial coverage statistic.
    """
    registry_path = path or default_source_validation_registry_path()
    try:
        payload = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(
            f"Invalid source validation registry: {registry_path}"
        ) from error
    try:
        registry = validate_source_validation_registry(payload)
    except ContractValidationError as error:
        raise ValueError(
            f"source_validation_registry.json is invalid: {error}"
        ) from error
    _validate_cross_registry_bindings(
        registry,
        source_records=list(source_records)
        if source_records is not None
        else _static_source_records(),
        target_matrix=target_matrix or load_source_targets(),
        fixture_root=fixture_root or PROJECT_ROOT,
    )
    return registry


def source_validation_records(
    registry: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return independent validation rows for reporting callers."""
    payload = registry or load_source_validation_registry()
    return deepcopy(list(payload["records"]))


def _target_source_id(target: dict[str, Any]) -> str | None:
    state = str(target.get("state") or "")
    if state == "verified":
        value = target.get("source_id")
    elif state == "candidate":
        value = target.get("candidate_source_id")
    else:
        value = None
    return str(value).strip() if value else None


def _target_slots(matrix: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    slots: dict[tuple[str, str], dict[str, Any]] = {}
    for province, role_map in (matrix.get("province_targets") or {}).items():
        if not isinstance(role_map, dict):
            continue
        for role, target in role_map.items():
            if isinstance(target, dict):
                slots[(str(province), str(role))] = target
    return slots


def _host_is_allowed(url: str, source: dict[str, Any]) -> bool:
    hostname = (urlparse(url).hostname or "").lower().rstrip(".")
    config = source.get("config", {}) if isinstance(source.get("config"), dict) else {}
    allowed_hosts = {
        str(host).lower().rstrip(".")
        for host in config.get("allowed_hosts", [])
        if str(host).strip()
    }
    homepage_host = (urlparse(str(source.get("homepage_url") or "")).hostname or "").lower()
    if homepage_host:
        allowed_hosts.add(homepage_host.rstrip("."))
    return hostname in allowed_hosts


def _validate_cross_registry_bindings(
    registry: dict[str, Any],
    *,
    source_records: list[dict[str, Any]],
    target_matrix: dict[str, Any],
    fixture_root: Path,
) -> None:
    source_by_id = {
        str(source.get("id")): source
        for source in source_records
        if source.get("id")
    }
    target_slots = _target_slots(target_matrix)
    safe_fixture_root = fixture_root.resolve()
    for record in source_validation_records(registry):
        province = str(record["province"])
        role = str(record["role"])
        if province not in PROVINCES:
            raise ValueError(
                f"Source validation record {record['id']} has unsupported province: {province}"
            )
        if role not in PROVINCIAL_SOURCE_TARGET_ROLES:
            raise ValueError(
                f"Source validation record {record['id']} has unsupported role: {role}"
            )
        target = target_slots.get((province, role))
        if target is None:
            raise ValueError(
                f"Source validation record {record['id']} does not map to a target slot"
            )
        expected_source_id = _target_source_id(target)
        if expected_source_id != record["source_id"]:
            raise ValueError(
                f"Source validation record {record['id']} source_id does not match "
                f"{province}/{role} target binding"
            )
        source = source_by_id.get(str(record["source_id"]))
        if source is None:
            raise ValueError(
                f"Source validation record {record['id']} references unknown source_id"
            )
        config = source.get("config", {}) if isinstance(source.get("config"), dict) else {}
        if config.get("province") != province or config.get("source_role") != role:
            raise ValueError(
                f"Source validation record {record['id']} does not match source province/role"
            )
        for url_field, url in (("official_entry_url", record["official_entry_url"]),):
            if not _host_is_allowed(str(url), source):
                raise ValueError(
                    f"Source validation record {record['id']} {url_field} is outside source allowed_hosts"
                )
        sample = record.get("sample")
        if isinstance(sample, dict) and not _host_is_allowed(
            str(sample["official_url"]), source
        ):
            raise ValueError(
                f"Source validation record {record['id']} sample official_url is outside source allowed_hosts"
            )
        fixture_path = record.get("fixture_path")
        if fixture_path:
            fixture = (safe_fixture_root / str(fixture_path)).resolve()
            try:
                fixture.relative_to(safe_fixture_root)
            except ValueError as error:
                raise ValueError(
                    f"Source validation record {record['id']} fixture escapes fixture root"
                ) from error
            if not fixture.is_file():
                raise ValueError(
                    f"Source validation record {record['id']} fixture does not exist: {fixture_path}"
                )


def source_validation_summary(
    registry: dict[str, Any] | None = None,
    *,
    target_matrix: dict[str, Any] | None = None,
    source_records: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Summarize quality of validated provincial sources without counting jobs."""
    payload = registry or load_source_validation_registry()
    matrix = target_matrix or load_source_targets()
    records = source_validation_records(payload)
    targets = _target_slots(matrix)
    stage_counts = Counter(str(record["validation_stage"]) for record in records)
    role_counts = Counter(
        str(record["role"])
        for record in records
        if record["validation_stage"] == "adapter_fixture_verified"
    )
    fixture_records = [
        record
        for record in records
        if record["validation_stage"] == "adapter_fixture_verified"
    ]
    verified_fixture_records = [
        record
        for record in fixture_records
        if str(targets.get((record["province"], record["role"]), {}).get("state"))
        == "verified"
    ]
    candidate_fixture_records = [
        record
        for record in fixture_records
        if str(targets.get((record["province"], record["role"]), {}).get("state"))
        == "candidate"
    ]
    fixture_slots = {
        (str(record["province"]), str(record["role"]))
        for record in fixture_records
    }
    unvalidated_verified_targets = [
        {
            "province": province,
            "role": role,
            "role_label": role_label(role),
            "source_id": str(target.get("source_id") or ""),
        }
        for (province, role), target in sorted(targets.items())
        if target.get("state") == "verified" and (province, role) not in fixture_slots
    ]
    sources = (
        list(source_records) if source_records is not None else _static_source_records()
    )
    source_by_id = {
        str(source.get("id")): source for source in sources if source.get("id")
    }
    fixture_enabled = sum(
        1
        for record in fixture_records
        if source_by_id.get(str(record["source_id"]), {}).get("enabled")
    )
    with_backup = sum(1 for record in records if record["backup_entry_urls"])

    return {
        "registry_version": payload.get("version", 1),
        "record_count": len(records),
        "records_by_validation_stage": dict(sorted(stage_counts.items())),
        "adapter_fixture_verified_records": len(fixture_records),
        "adapter_fixture_verified_targets": len(verified_fixture_records),
        "candidate_targets_with_adapter_fixture": len(candidate_fixture_records),
        "fixture_verified_role_counts": dict(sorted(role_counts.items())),
        "fixture_verified_province_count": len(
            {str(record["province"]) for record in fixture_records}
        ),
        "fixture_backed_records": len(
            [record for record in fixture_records if record.get("fixture_path")]
        ),
        "fixture_verified_enabled_sources": fixture_enabled,
        "records_with_backup": with_backup,
        "backup_rate": with_backup / len(records) if records else 0.0,
        "verified_targets_without_adapter_fixture": len(unvalidated_verified_targets),
        "unvalidated_verified_targets": unvalidated_verified_targets,
        "scope_note": str(payload.get("description") or "").strip(),
    }


def source_validation_matrix_rows(
    registry: dict[str, Any] | None = None,
    *,
    target_matrix: dict[str, Any] | None = None,
    source_records: Iterable[dict[str, Any]] | None = None,
    province: str | None = None,
    role: str | None = None,
    validation_stage: str | None = None,
) -> list[dict[str, Any]]:
    """Return private validation rows with target and runtime source state."""
    payload = registry or load_source_validation_registry()
    targets = _target_slots(target_matrix or load_source_targets())
    records = (
        list(source_records) if source_records is not None else _static_source_records()
    )
    source_by_id = {
        str(source.get("id")): source
        for source in records
        if source.get("id")
    }
    rows: list[dict[str, Any]] = []
    for record in source_validation_records(payload):
        if province and record["province"] != province:
            continue
        if role and record["role"] != role:
            continue
        if validation_stage and record["validation_stage"] != validation_stage:
            continue
        target = targets.get((record["province"], record["role"]), {})
        source = source_by_id.get(str(record["source_id"]))
        rows.append(
            {
                **record,
                "role_label": role_label(str(record["role"])),
                "target_state": target.get("state"),
                "target_source_id": _target_source_id(target),
                "source_registered_in_runtime": source is not None,
                "source_enabled": bool(source.get("enabled")) if source else None,
                "source_name": str(source.get("name")) if source else None,
            }
        )
    return rows


__all__ = [
    "SOURCE_VALIDATION_STAGES",
    "default_source_validation_registry_path",
    "load_source_validation_registry",
    "source_validation_matrix_rows",
    "source_validation_records",
    "source_validation_summary",
]
