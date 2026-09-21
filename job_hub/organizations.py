"""Hierarchical organization and official-entry registry for Phase 3.

The registry answers a different question from ``employer_registry.json``:
the employer registry normalizes a job's employer name, while this module
records the organization hierarchy and the official campus/social/announcement
entrances that can be reviewed or bound to a crawler source.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

from job_hub.contracts import (
    ContractValidationError,
    validate_organization_registry,
)


REGISTRY_PATH = Path(__file__).resolve().parent.parent / "data" / "organization_registry.json"
SOURCE_REGISTRY_PATH = Path(__file__).resolve().parent.parent / "data" / "sources.json"
PROVINCIAL_SOURCE_REGISTRY_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "provincial_sources.json"
)


def _registered_source_ids() -> set[str]:
    source_ids: set[str] = set()
    for path in (SOURCE_REGISTRY_PATH, PROVINCIAL_SOURCE_REGISTRY_PATH):
        if not path.exists():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            source_ids.update(
                str(item.get("id"))
                for item in payload
                if isinstance(item, dict) and item.get("id")
            )
    return source_ids


def default_organization_registry_path() -> Path:
    return REGISTRY_PATH


def load_organization_registry(
    path: Path | None = None,
    *,
    source_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Load and validate the versioned organization matrix."""
    registry_path = path or default_organization_registry_path()
    try:
        payload = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Invalid organization registry: {registry_path}") from error
    try:
        return validate_organization_registry(
            payload,
            source_ids=_registered_source_ids() if source_ids is None else source_ids,
        )
    except ContractValidationError as error:
        raise ValueError(f"organization_registry.json is invalid: {error}") from error


@lru_cache(maxsize=8)
def _cached_registry(path: str) -> dict[str, Any]:
    return load_organization_registry(Path(path))


def organization_items(
    registry: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return organization rows as independent dictionaries for callers."""
    payload = registry or _cached_registry(str(default_organization_registry_path()))
    return [dict(item) for item in payload["organizations"]]


def get_organization(
    organization_id: str,
    registry: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    for organization in organization_items(registry):
        if organization["id"] == organization_id:
            return organization
    return None


def _host_matches_domain(url: str, domain: str) -> bool:
    hostname = (urlparse(url).hostname or "").lower().rstrip(".")
    expected = domain.lower().rstrip(".")
    return hostname == expected or hostname.endswith(f".{expected}")


def organization_source_bindings(
    registry: dict[str, Any] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Group official channels by registered crawler source id."""
    bindings: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for organization in organization_items(registry):
        for channel in organization["channels"]:
            source_id = channel.get("source_id")
            if source_id:
                bindings[str(source_id)].append(
                    {
                        "organization_id": organization["id"],
                        "organization_name": organization["canonical_name"],
                        "channel_id": channel["id"],
                        "channel_type": channel["channel_type"],
                        "verification_status": channel["verification_status"],
                    }
                )
    return dict(bindings)


def organization_matrix_rows(
    registry: dict[str, Any] | None = None,
    *,
    source_records: Iterable[dict[str, Any]] | None = None,
    organization_role: str | None = None,
    affiliation: str | None = None,
) -> list[dict[str, Any]]:
    """Return administrator-facing hierarchy rows with source runtime state.

    The rows are intentionally not used by public pages.  They help an
    operator distinguish a known official entry from a source that is
    currently enabled in the database.  A disabled source is not a failed
    source and does not make an official entry invalid.
    """
    payload = registry or _cached_registry(str(default_organization_registry_path()))
    records = list(source_records or [])
    source_by_id = {
        str(record["id"]): record
        for record in records
        if record.get("id")
    }
    parents = {
        organization["id"]: organization["canonical_name"]
        for organization in organization_items(payload)
    }
    rows: list[dict[str, Any]] = []
    for organization in organization_items(payload):
        if organization_role and organization["organization_role"] != organization_role:
            continue
        if affiliation and organization["affiliation"] != affiliation:
            continue
        channels = []
        for channel in organization["channels"]:
            source_id = channel.get("source_id")
            source = source_by_id.get(str(source_id)) if source_id else None
            channels.append(
                {
                    **channel,
                    "source_registered_in_runtime": source is not None,
                    "source_enabled": (
                        bool(source.get("enabled")) if source is not None else None
                    ),
                    "source_name": str(source.get("name")) if source else None,
                }
            )
        rows.append(
            {
                "id": organization["id"],
                "canonical_name": organization["canonical_name"],
                "parent_id": organization["parent_id"],
                "parent_name": parents.get(organization["parent_id"]),
                "organization_role": organization["organization_role"],
                "industry_path": organization["industry_path"],
                "affiliation": organization["affiliation"],
                "aliases": list(organization["aliases"]),
                "official_domains": list(organization["official_domains"]),
                "channels": channels,
                "notes": organization["notes"],
            }
        )
    return rows


def organization_matrix_summary(
    registry: dict[str, Any] | None = None,
    *,
    source_ids: set[str] | None = None,
    source_records: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Produce coverage metrics for organizations and official entrances.

    ``official_confirmed`` means the entrance identity was reviewed; it does
    not mean the page is currently crawlable.  ``automation_ready`` is the
    only status that may be treated as bound to an active adapter, and even
    then the source health report remains authoritative for runtime access.
    """
    payload = registry or _cached_registry(str(default_organization_registry_path()))
    organizations = organization_items(payload)
    records = list(source_records or [])
    if source_ids is None:
        # Static source registries establish whether a channel can be bound at
        # all; runtime records separately establish whether it is enabled now.
        source_ids = _registered_source_ids() | {
            str(record.get("id")) for record in records if record.get("id")
        }
    channels = [
        (organization, channel)
        for organization in organizations
        for channel in organization["channels"]
    ]
    status_counts = Counter(str(channel["verification_status"]) for _, channel in channels)
    role_counts = Counter(str(organization["organization_role"]) for organization in organizations)
    affiliation_counts = Counter(str(organization["affiliation"]) for organization in organizations)
    with_backup = sum(1 for _, channel in channels if channel["backup_urls"])
    bound = [
        (organization, channel)
        for organization, channel in channels
        if channel.get("source_id")
    ]
    bound_registered = [
        (organization, channel)
        for organization, channel in bound
        if str(channel["source_id"]) in source_ids
    ]
    enabled_by_id = {
        str(record.get("id")): bool(record.get("enabled"))
        for record in records
        if record.get("id")
    }
    automation_ready = [
        (organization, channel)
        for organization, channel in channels
        if channel["verification_status"] == "automation_ready"
    ]
    automation_ready_registered = [
        (organization, channel)
        for organization, channel in automation_ready
        if str(channel.get("source_id")) in source_ids
    ]
    automation_ready_enabled = [
        (organization, channel)
        for organization, channel in automation_ready_registered
        if enabled_by_id.get(str(channel["source_id"]), False)
    ]
    missing_backup = [
        {
            "organization_id": organization["id"],
            "organization_name": organization["canonical_name"],
            "channel_id": channel["id"],
        }
        for organization, channel in channels
        if not channel["backup_urls"]
    ]
    unbound_sources = sorted(
        {
            str(channel["source_id"])
            for _, channel in bound
            if str(channel["source_id"]) not in source_ids
        }
    )
    return {
        "registry_version": payload.get("version", 1),
        "organization_count": len(organizations),
        "channel_count": len(channels),
        "organization_role_counts": dict(sorted(role_counts.items())),
        "affiliation_counts": dict(sorted(affiliation_counts.items())),
        "channel_status_counts": dict(sorted(status_counts.items())),
        "channels_with_backup": with_backup,
        "backup_rate": (with_backup / len(channels)) if channels else 0.0,
        "channels_with_registered_source": len(bound_registered),
        "automation_ready_channels": len(automation_ready),
        "automation_ready_registered": len(automation_ready_registered),
        "automation_ready_enabled_channels": len(automation_ready_enabled),
        "missing_backup_channels": missing_backup,
        "unbound_source_ids": unbound_sources,
        "source_bindings": organization_source_bindings(payload),
        "scope_note": str(payload.get("description") or "").strip(),
    }
