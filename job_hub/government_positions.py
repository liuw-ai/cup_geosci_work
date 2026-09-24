"""Contracts and quality reports for government position tables.

Government recruitment is not a single source type: a provincial institution
notice, a PDF/XLS position table and the annual civil-service table have
different lifecycles.  This module keeps their shared, auditable fields in a
small versioned ledger.  It deliberately does not turn a notice title into a
vacancy; a row is publishable only when its official row/detail evidence and
student-match decision are explicit.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REGISTRY_PATH = PROJECT_ROOT / "data" / "government_position_registry.json"

POSITION_TYPES = frozenset({"public_institution", "civil_service"})
RECORD_STATUSES = frozenset(
    {"verified_open", "verified_closed", "manual_review_required", "source_unavailable"}
)
MATCH_STATUSES = frozenset({"explicit_match", "unrestricted_match", "needs_review", "out_of_scope"})
ASSESSMENT_STATUSES = frozenset(
    {"verified_open_sample", "verified_source_fixture", "manual_review_required", "source_unavailable"}
)
REQUIRED_FIELDS = (
    "id",
    "source_id",
    "position_type",
    "province",
    "employer",
    "title",
    "major_requirement",
    "degree_requirement",
    "location",
    "headcount",
    "deadline_date",
    "official_notice_url",
    "official_attachment_url",
    "evidence_locator",
    "record_status",
    "match_status",
)


class GovernmentPositionContractError(ValueError):
    """Raised when a government-position ledger is incomplete or unsafe."""


def _text(value: Any, field: str, *, allow_empty: bool = False) -> str:
    result = str(value or "").strip()
    if not result and not allow_empty:
        raise GovernmentPositionContractError(f"{field} must be non-empty")
    return result


def _url(value: Any, field: str) -> str:
    result = _text(value, field)
    parsed = urlparse(result)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise GovernmentPositionContractError(f"{field} must be an HTTP(S) URL")
    return result


def _optional_url(value: Any, field: str) -> str:
    result = str(value or "").strip()
    return _url(result, field) if result else ""


def _date(value: Any, field: str, *, allow_empty: bool = False) -> str:
    result = str(value or "").strip()
    if not result and allow_empty:
        return ""
    try:
        date.fromisoformat(result)
    except ValueError as error:
        raise GovernmentPositionContractError(
            f"{field} must use YYYY-MM-DD"
        ) from error
    return result


def validate_position_record(value: Any, *, context: str = "position") -> dict[str, Any]:
    if not isinstance(value, dict):
        raise GovernmentPositionContractError(f"{context} must be an object")
    missing = [field for field in REQUIRED_FIELDS if field not in value]
    if missing:
        raise GovernmentPositionContractError(
            f"{context} is missing: {', '.join(missing)}"
        )
    normalized = dict(value)
    for field in (
        "id",
        "source_id",
        "province",
        "employer",
        "title",
        "major_requirement",
        "degree_requirement",
        "evidence_locator",
    ):
        normalized[field] = _text(normalized[field], f"{context}.{field}")
    normalized["location"] = str(normalized.get("location") or "").strip()
    normalized["position_type"] = _text(normalized["position_type"], f"{context}.position_type")
    if normalized["position_type"] not in POSITION_TYPES:
        raise GovernmentPositionContractError(
            f"{context}.position_type must be one of {sorted(POSITION_TYPES)}"
        )
    normalized["record_status"] = _text(normalized["record_status"], f"{context}.record_status")
    if normalized["record_status"] not in RECORD_STATUSES:
        raise GovernmentPositionContractError(
            f"{context}.record_status must be one of {sorted(RECORD_STATUSES)}"
        )
    normalized["match_status"] = _text(normalized["match_status"], f"{context}.match_status")
    if normalized["match_status"] not in MATCH_STATUSES:
        raise GovernmentPositionContractError(
            f"{context}.match_status must be one of {sorted(MATCH_STATUSES)}"
        )
    try:
        headcount = int(normalized["headcount"])
    except (TypeError, ValueError) as error:
        raise GovernmentPositionContractError(f"{context}.headcount must be a non-negative integer") from error
    if headcount < 0:
        raise GovernmentPositionContractError(f"{context}.headcount must be a non-negative integer")
    normalized["headcount"] = headcount
    normalized["deadline_date"] = _date(normalized["deadline_date"], f"{context}.deadline_date", allow_empty=True)
    normalized["official_notice_url"] = _url(normalized["official_notice_url"], f"{context}.official_notice_url")
    normalized["official_attachment_url"] = _url(
        normalized["official_attachment_url"], f"{context}.official_attachment_url"
    )
    if not normalized["location"] and normalized["record_status"] == "verified_open":
        raise GovernmentPositionContractError(
            f"{context}.location is required before a row can be verified_open"
        )
    if normalized["record_status"] == "verified_open" and normalized["match_status"] == "out_of_scope":
        raise GovernmentPositionContractError(
            f"{context} cannot be verified_open while marked out_of_scope"
        )
    if normalized["record_status"] == "verified_open" and not normalized["deadline_date"]:
        raise GovernmentPositionContractError(
            f"{context} verified_open records require a deadline or explicit open-ended policy"
        )
    return normalized


def load_position_registry(path: Path | str | None = None) -> dict[str, Any]:
    registry_path = Path(path or DEFAULT_REGISTRY_PATH)
    try:
        payload = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise GovernmentPositionContractError(f"Cannot read {registry_path}") from error
    if not isinstance(payload, dict):
        raise GovernmentPositionContractError("government position registry must be an object")
    version = payload.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise GovernmentPositionContractError("registry version must be a positive integer")
    as_of = _date(payload.get("as_of"), "registry.as_of")
    records = payload.get("records")
    if not isinstance(records, list):
        raise GovernmentPositionContractError("registry.records must be a list")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, value in enumerate(records):
        record = validate_position_record(value, context=f"registry.records[{index}]")
        if record["id"] in seen:
            raise GovernmentPositionContractError(f"duplicate position id: {record['id']}")
        seen.add(record["id"])
        normalized.append(record)
    assessments = payload.get("source_assessments") or []
    if not isinstance(assessments, list):
        raise GovernmentPositionContractError("registry.source_assessments must be a list")
    normalized_assessments: list[dict[str, Any]] = []
    for index, value in enumerate(assessments):
        if not isinstance(value, dict):
            raise GovernmentPositionContractError(f"source_assessments[{index}] must be an object")
        assessment = dict(value)
        for field in ("source_id", "position_type", "province", "status", "official_notice_url"):
            assessment[field] = _text(assessment.get(field), f"source_assessments[{index}].{field}")
        assessment["official_attachment_url"] = str(
            assessment.get("official_attachment_url") or ""
        ).strip()
        if assessment["position_type"] not in POSITION_TYPES:
            raise GovernmentPositionContractError(f"source_assessments[{index}].position_type is unsupported")
        if assessment["status"] not in ASSESSMENT_STATUSES:
            raise GovernmentPositionContractError(f"source_assessments[{index}].status is unsupported")
        assessment["official_notice_url"] = _url(assessment["official_notice_url"], f"source_assessments[{index}].official_notice_url")
        assessment["official_attachment_url"] = _optional_url(
            assessment["official_attachment_url"],
            f"source_assessments[{index}].official_attachment_url",
        )
        assessment["deadline_date"] = _date(assessment.get("deadline_date"), f"source_assessments[{index}].deadline_date", allow_empty=True)
        normalized_assessments.append(assessment)
    return {
        "version": version,
        "as_of": as_of,
        "description": str(payload.get("description") or "").strip(),
        "source_assessments": normalized_assessments,
        "records": normalized,
    }


def government_position_quality_report(
    registry: dict[str, Any] | None = None,
    *,
    today: str | None = None,
) -> dict[str, Any]:
    payload = registry or load_position_registry()
    records = list(payload.get("records", []))
    today_date = date.fromisoformat(today) if today else date.today()
    status_counts = Counter(str(item["record_status"]) for item in records)
    type_counts = Counter(str(item["position_type"]) for item in records)
    province_counts = Counter(str(item["province"]) for item in records)
    source_counts = Counter(str(item["source_id"]) for item in records)
    open_records = [
        item
        for item in records
        if item["record_status"] == "verified_open"
        and (not item["deadline_date"] or date.fromisoformat(item["deadline_date"]) >= today_date)
    ]
    explicit_matches = [
        item for item in open_records if item["match_status"] in {"explicit_match", "unrestricted_match"}
    ]
    failures = [item for item in records if item["record_status"] in {"source_unavailable", "manual_review_required"}]
    closed = [item for item in records if item["record_status"] == "verified_closed"]
    return {
        "as_of": payload.get("as_of"),
        "today": today_date.isoformat(),
        "records": len(records),
        "source_assessments": len(payload.get("source_assessments") or []),
        "sources": len(source_counts),
        "by_position_type": dict(sorted(type_counts.items())),
        "by_record_status": dict(sorted(status_counts.items())),
        "by_province": dict(sorted(province_counts.items())),
        "by_source": dict(sorted(source_counts.items())),
        "verified_open_records": len(open_records),
        "explicit_student_matches": len(explicit_matches),
        "verified_closed_records": len(closed),
        "source_failures_or_pending": len(failures),
        "field_completeness": {
            field: {
                "complete": sum(1 for item in records if str(item.get(field) or "").strip()),
                "total": len(records),
                "rate": round(
                    sum(1 for item in records if str(item.get(field) or "").strip()) / len(records),
                    4,
                ) if records else 0.0,
            }
            for field in (
                "major_requirement",
                "degree_requirement",
                "location",
                "deadline_date",
                "official_notice_url",
                "official_attachment_url",
                "evidence_locator",
            )
        },
        "scan_interpretation": (
            "存在来源故障或待核验记录，不能把缺少岗位解释为无岗位。"
            if failures
            else "台账中的正式来源均已完成当前记录核验。"
        ),
    }
