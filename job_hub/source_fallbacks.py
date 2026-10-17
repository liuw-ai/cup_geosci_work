"""Contract and audit helpers for official fallback-source plans.

The fallback ledger is deliberately separate from ``sources.json``.  A URL
found on a group website or a third-party platform is not a crawler source
until its owner, authorization (when applicable), robots/access policy and
job-level fields have been verified.  This module makes that distinction
machine-checkable and keeps candidate URLs useful without publishing them.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse
from typing import Any, Iterable

from job_hub.contracts import ContractValidationError, is_http_url


PROJECT_ROOT = Path(__file__).resolve().parent.parent
FALLBACK_PLAN_PATH = PROJECT_ROOT / "data" / "official_source_fallbacks.json"
FALLBACK_CHANNEL_TYPES = frozenset(
    {"official_announcement", "official_attachment", "authorized_platform"}
)
FALLBACK_STATUSES = frozenset(
    {"candidate", "verified", "access_limited", "retired"}
)
CONFIRMED_STATUSES = frozenset({"verified"})


def load_fallback_plan(path: Path | None = None) -> dict[str, Any]:
    plan_path = path or FALLBACK_PLAN_PATH
    try:
        payload = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Invalid official fallback plan: {plan_path}") from error
    try:
        return validate_fallback_plan(payload)
    except ContractValidationError as error:
        raise ValueError(f"official_source_fallbacks.json is invalid: {error}") from error


def validate_fallback_plan(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ContractValidationError("fallback plan must be an object")
    version = payload.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ContractValidationError("fallback plan version must be positive")
    as_of = str(payload.get("as_of") or "").strip()
    if len(as_of) != 10:
        raise ContractValidationError("fallback plan as_of must be an ISO date")
    groups = payload.get("groups")
    if not isinstance(groups, list) or not groups:
        raise ContractValidationError("fallback plan groups must be a non-empty list")

    seen_group_ids: set[str] = set()
    seen_entry_ids: set[str] = set()
    normalized_groups: list[dict[str, Any]] = []
    for index, raw_group in enumerate(groups):
        if not isinstance(raw_group, dict):
            raise ContractValidationError(f"fallback group {index} must be an object")
        group_id = str(raw_group.get("group_id") or "").strip()
        group_name = str(raw_group.get("group_name") or "").strip()
        primary_source_id = str(raw_group.get("primary_source_id") or "").strip()
        if not group_id or not group_name or not primary_source_id:
            raise ContractValidationError(
                f"fallback group {index} requires group_id, group_name and primary_source_id"
            )
        if group_id in seen_group_ids:
            raise ContractValidationError(f"duplicate fallback group_id: {group_id}")
        seen_group_ids.add(group_id)
        entries = raw_group.get("entries")
        if not isinstance(entries, list) or not entries:
            raise ContractValidationError(f"fallback group {group_id} entries must be non-empty")
        normalized_entries: list[dict[str, Any]] = []
        for entry_index, raw_entry in enumerate(entries):
            if not isinstance(raw_entry, dict):
                raise ContractValidationError(
                    f"fallback entry {group_id}/{entry_index} must be an object"
                )
            entry_id = str(raw_entry.get("entry_id") or "").strip()
            channel_type = str(raw_entry.get("channel_type") or "").strip()
            entry_url = str(raw_entry.get("entry_url") or "").strip()
            status = str(raw_entry.get("status") or "").strip()
            if not entry_id or not entry_url or not status:
                raise ContractValidationError(
                    f"fallback entry {group_id}/{entry_index} requires entry_id, entry_url and status"
                )
            if entry_id in seen_entry_ids:
                raise ContractValidationError(f"duplicate fallback entry_id: {entry_id}")
            seen_entry_ids.add(entry_id)
            if channel_type not in FALLBACK_CHANNEL_TYPES:
                raise ContractValidationError(
                    f"fallback entry {entry_id} has unsupported channel_type: {channel_type}"
                )
            if not is_http_url(entry_url):
                raise ContractValidationError(f"fallback entry {entry_id} entry_url must be HTTP(S)")
            if status not in FALLBACK_STATUSES:
                raise ContractValidationError(
                    f"fallback entry {entry_id} has unsupported status: {status}"
                )
            source_id = raw_entry.get("source_id")
            if source_id is not None and not str(source_id).strip():
                raise ContractValidationError(f"fallback entry {entry_id} source_id cannot be empty")
            evidence_url = raw_entry.get("evidence_url")
            if evidence_url is not None and not is_http_url(str(evidence_url)):
                raise ContractValidationError(
                    f"fallback entry {entry_id} evidence_url must be HTTP(S)"
                )
            authorization_url = raw_entry.get("authorization_evidence_url")
            if authorization_url is not None and not is_http_url(str(authorization_url)):
                raise ContractValidationError(
                    f"fallback entry {entry_id} authorization_evidence_url must be HTTP(S)"
                )
            if channel_type == "authorized_platform" and status == "verified":
                if not authorization_url:
                    raise ContractValidationError(
                        f"verified authorized platform {entry_id} requires authorization_evidence_url"
                    )
                if not str(raw_entry.get("authorization_scope") or "").strip():
                    raise ContractValidationError(
                        f"verified authorized platform {entry_id} requires authorization_scope"
                    )
            if status == "verified" and not evidence_url:
                raise ContractValidationError(
                    f"verified fallback entry {entry_id} requires evidence_url"
                )
            normalized = dict(raw_entry)
            normalized.update(
                {
                    "entry_id": entry_id,
                    "channel_type": channel_type,
                    "entry_url": entry_url,
                    "status": status,
                    "source_id": str(source_id).strip() if source_id is not None else None,
                    "evidence_url": str(evidence_url).strip() if evidence_url else None,
                    "authorization_evidence_url": (
                        str(authorization_url).strip() if authorization_url else None
                    ),
                    "authorization_scope": str(raw_entry.get("authorization_scope") or "").strip(),
                    "notes": str(raw_entry.get("notes") or "").strip(),
                }
            )
            normalized_entries.append(normalized)
        normalized_group = dict(raw_group)
        normalized_group.update(
            {
                "group_id": group_id,
                "group_name": group_name,
                "primary_source_id": primary_source_id,
                "entries": normalized_entries,
            }
        )
        normalized_groups.append(normalized_group)
    result = dict(payload)
    result.update({"version": version, "as_of": as_of, "groups": normalized_groups})
    return result


def fallback_entries(plan: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    payload = plan or load_fallback_plan()
    rows: list[dict[str, Any]] = []
    for group in payload["groups"]:
        for entry in group["entries"]:
            rows.append(
                {
                    **entry,
                    "group_id": group["group_id"],
                    "group_name": group["group_name"],
                    "primary_source_id": group["primary_source_id"],
                }
            )
    return rows


def build_fallback_audit(
    plan: dict[str, Any] | None = None,
    *,
    source_records: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    payload = plan or load_fallback_plan()
    entries = fallback_entries(payload)
    source_by_id = {
        str(record.get("id") or "").strip(): dict(record)
        for record in source_records
        if str(record.get("id") or "").strip()
    }
    by_group: dict[str, dict[str, int]] = {}
    for group in payload["groups"]:
        group_entries = [item for item in entries if item["group_id"] == group["group_id"]]
        by_group[group["group_id"]] = {
            "entry_count": len(group_entries),
            "candidate_count": sum(item["status"] == "candidate" for item in group_entries),
            "verified_count": sum(item["status"] == "verified" for item in group_entries),
            "access_limited_count": sum(
                item["status"] == "access_limited" for item in group_entries
            ),
            "enabled_source_count": sum(
                bool(item.get("source_id"))
                and bool(source_by_id.get(str(item["source_id"]), {}).get("enabled"))
                for item in group_entries
            ),
            "disabled_registered_source_count": sum(
                bool(item.get("source_id")) and item["source_id"] in source_by_id
                and not bool(source_by_id[item["source_id"]].get("enabled"))
                for item in group_entries
            ),
            "authorized_platform_verified_count": sum(
                item["channel_type"] == "authorized_platform"
                and item["status"] == "verified"
                for item in group_entries
            ),
        }
    ready = [
        item
        for item in entries
        if item["status"] in CONFIRMED_STATUSES
        and bool(
            source_by_id.get(str(item.get("source_id") or ""), {}).get("enabled")
        )
        and (
            item["channel_type"] != "authorized_platform"
            or item.get("authorization_evidence_url")
        )
    ]
    return {
        "version": payload["version"],
        "as_of": payload["as_of"],
        "group_count": len(payload["groups"]),
        "entry_count": len(entries),
        "candidate_count": sum(item["status"] == "candidate" for item in entries),
        "verified_count": sum(item["status"] == "verified" for item in entries),
        "access_limited_count": sum(item["status"] == "access_limited" for item in entries),
        "enabled_source_count": sum(
            bool(item.get("source_id"))
            and bool(source_by_id.get(str(item["source_id"]), {}).get("enabled"))
            for item in entries
        ),
        "disabled_registered_source_count": sum(
            bool(item.get("source_id"))
            and item["source_id"] in source_by_id
            and not bool(source_by_id[item["source_id"]].get("enabled"))
            for item in entries
        ),
        "automation_ready_count": len(ready),
        "by_group": by_group,
        "entries": entries,
        "policy": {
            "candidate_is_not_source": True,
            "authorized_platform_requires_official_authorization": True,
            "verified_entry_requires_evidence_url": True,
            "source_id_must_be_registered_before_runtime_use": True,
        },
    }


__all__ = [
    "CONFIRMED_STATUSES",
    "FALLBACK_CHANNEL_TYPES",
    "FALLBACK_PLAN_PATH",
    "build_fallback_audit",
    "fallback_entries",
    "load_fallback_plan",
    "validate_fallback_plan",
]
