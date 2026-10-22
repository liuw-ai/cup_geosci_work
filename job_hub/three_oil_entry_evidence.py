"""Validated unit-level recruitment-entry evidence for the three oil groups.

The formal organization catalog answers *which units are in scope*. This
module answers the separate and narrower question: *what recruitment-entry
evidence exists for each unit right now?* A shared group portal is recorded as
such and is never converted into an independent unit recruitment entrance.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from job_hub.three_oil_catalog import CATALOG_PATH, load_three_oil_catalog, three_oil_units


PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENTRY_EVIDENCE_PATH = PROJECT_ROOT / "data" / "three_oil_entry_evidence.json"
ENTRY_EVIDENCE_STATUSES = frozenset(
    {
        "unit_specific_entry_verified",
        "group_portal_unit_display_observed",
        "group_portal_registered",
        "official_site_checked_no_unit_entry_found",
        "source_unavailable",
        "group_portal_not_revalidated",
    }
)


def _http_url_or_none(value: Any, field: str) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"three-oil entry evidence {field} must be an HTTP(S) URL")
    return text


def validate_three_oil_entry_evidence(
    payload: Any,
    *,
    catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate complete coverage without claiming unsupported unit entries."""

    if not isinstance(payload, dict):
        raise ValueError("three-oil entry evidence must be an object")
    if payload.get("version") != 1:
        raise ValueError("three-oil entry evidence version must be 1")
    units = payload.get("units")
    if not isinstance(units, list):
        raise ValueError("three-oil entry evidence requires a units list")
    formal_units = {
        str(unit["id"]): unit
        for unit in three_oil_units(catalog or load_three_oil_catalog())
    }
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(units):
        if not isinstance(raw, dict):
            raise ValueError(f"three-oil entry evidence unit {index} must be an object")
        item = dict(raw)
        unit_id = str(item.get("catalog_id") or "").strip()
        formal = formal_units.get(unit_id)
        if formal is None:
            raise ValueError(f"three-oil entry evidence has unknown catalog_id: {unit_id}")
        if unit_id in seen:
            raise ValueError(f"duplicate three-oil entry evidence catalog_id: {unit_id}")
        seen.add(unit_id)
        if str(item.get("canonical_name") or "").strip() != str(formal["name"]):
            raise ValueError(f"three-oil entry evidence name mismatch: {unit_id}")
        if str(item.get("group_id") or "").strip() != str(formal["group_id"]):
            raise ValueError(f"three-oil entry evidence group mismatch: {unit_id}")
        status = str(item.get("unit_specific_entry_status") or "").strip()
        if status not in ENTRY_EVIDENCE_STATUSES:
            raise ValueError(f"unsupported three-oil entry status for {unit_id}: {status}")
        unit_entry_url = _http_url_or_none(
            item.get("unit_specific_entry_url"), "unit_specific_entry_url"
        )
        group_entry_url = _http_url_or_none(item.get("group_entry_url"), "group_entry_url")
        if not group_entry_url:
            raise ValueError(f"three-oil entry evidence requires group_entry_url: {unit_id}")
        if status == "unit_specific_entry_verified" and not unit_entry_url:
            raise ValueError(f"verified unit entry requires unit_specific_entry_url: {unit_id}")
        if status != "unit_specific_entry_verified" and unit_entry_url:
            raise ValueError(
                f"non-unit-specific status cannot carry unit_specific_entry_url: {unit_id}"
            )
        for field in (
            "observed_official_application_url",
            "official_identity_url",
            "checked_url",
        ):
            item[field] = _http_url_or_none(item.get(field), field)
        item["catalog_id"] = unit_id
        item["canonical_name"] = str(formal["name"])
        item["group_id"] = str(formal["group_id"])
        item["group_entry_url"] = group_entry_url
        item["unit_specific_entry_url"] = unit_entry_url
        item["unit_specific_entry_status"] = status
        item["checked_at"] = str(item.get("checked_at") or item.get("verified_at") or "").strip() or None
        item["evidence_files"] = [
            str(path).replace("\\", "/")
            for path in item.get("evidence_files", [])
            if str(path).strip()
        ]
        item["notes"] = str(item.get("notes") or "").strip()
        normalized.append(item)
    missing = sorted(set(formal_units).difference(seen))
    if missing:
        raise ValueError("three-oil entry evidence misses catalog units: " + ", ".join(missing))
    result = dict(payload)
    result["units"] = sorted(normalized, key=lambda item: (item["group_id"], item["catalog_id"]))
    return result


def load_three_oil_entry_evidence(
    path: Path = ENTRY_EVIDENCE_PATH,
    *,
    catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read three-oil entry evidence: {path}") from error
    return validate_three_oil_entry_evidence(payload, catalog=catalog)


def three_oil_entry_evidence_summary(payload: dict[str, Any]) -> dict[str, Any]:
    """Summarize evidence coverage while preserving the status distinction."""

    units = list(payload.get("units") or [])
    by_status = Counter(str(item["unit_specific_entry_status"]) for item in units)
    by_group: dict[str, dict[str, int]] = {}
    for group_id in ("cnpc", "sinopec", "cnooc"):
        group_rows = [item for item in units if item["group_id"] == group_id]
        group_statuses = Counter(
            str(item["unit_specific_entry_status"]) for item in group_rows
        )
        by_group[group_id] = {
            "unit_count": len(group_rows),
            **{status: int(group_statuses.get(status, 0)) for status in sorted(ENTRY_EVIDENCE_STATUSES)},
        }
    return {
        "unit_count": len(units),
        "official_identity_page_count": sum(
            1 for item in units if item.get("official_identity_url")
        ),
        "unit_owned_identity_page_count": sum(
            1
            for item in units
            if item.get("official_identity_url")
            and item.get("catalog_id") != item.get("group_id")
        ),
        "status_counts": {
            status: int(by_status.get(status, 0))
            for status in sorted(ENTRY_EVIDENCE_STATUSES)
        },
        "groups": by_group,
        "unit_specific_entry_verified_count": int(
            by_status.get("unit_specific_entry_verified", 0)
        ),
        "shared_group_portal_count": int(
            by_status.get("group_portal_registered", 0)
            + by_status.get("group_portal_unit_display_observed", 0)
        ),
        "incomplete_entry_evidence_count": len(units)
        - int(by_status.get("unit_specific_entry_verified", 0)),
        "policy": (
            "共享集团招聘平台、岗位展示页或官方报名链接可以证明单位展示或岗位归属，"
            "但不证明该下属单位拥有独立招聘入口。"
        ),
    }


__all__ = [
    "ENTRY_EVIDENCE_PATH",
    "ENTRY_EVIDENCE_STATUSES",
    "load_three_oil_entry_evidence",
    "three_oil_entry_evidence_summary",
    "validate_three_oil_entry_evidence",
]
