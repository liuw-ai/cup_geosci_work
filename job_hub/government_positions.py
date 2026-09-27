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

from job_hub.sources import RawPosting


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REGISTRY_PATH = PROJECT_ROOT / "data" / "government_position_registry.json"

POSITION_TYPES = frozenset({"public_institution", "civil_service", "postdoctoral"})
RECORD_STATUSES = frozenset(
    {"verified_open", "verified_closed", "manual_review_required", "source_unavailable"}
)
MATCH_STATUSES = frozenset({"explicit_match", "unrestricted_match", "needs_review", "out_of_scope"})
DEADLINE_POLICIES = frozenset({"fixed_date", "open_until_filled"})
ASSESSMENT_STATUSES = frozenset(
    {
        "verified_open_sample",
        "verified_source_fixture",
        "verified_scan_no_current_match",
        "manual_review_required",
        "source_unavailable",
    }
)
REQUIRED_FIELDS = (
    "id",
    "source_id",
    "position_type",
    "province",
    "employer",
    "position_code",
    "title",
    "major_requirement",
    "degree_requirement",
    "location",
    "headcount",
    "deadline_date",
    "deadline_policy",
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
        "position_code",
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
    normalized["deadline_policy"] = _text(normalized["deadline_policy"], f"{context}.deadline_policy")
    if normalized["deadline_policy"] not in DEADLINE_POLICIES:
        raise GovernmentPositionContractError(
            f"{context}.deadline_policy must be one of {sorted(DEADLINE_POLICIES)}"
        )
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
    if normalized["record_status"] == "verified_open":
        if normalized["deadline_policy"] == "fixed_date" and not normalized["deadline_date"]:
            raise GovernmentPositionContractError(
                f"{context} fixed_date records require deadline_date"
            )
        if normalized["deadline_policy"] == "open_until_filled" and normalized["deadline_date"]:
            raise GovernmentPositionContractError(
                f"{context} open_until_filled records must not invent a fixed deadline_date"
            )
    return normalized


def _expand_position_batches(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Expand compact, spreadsheet-derived batches into auditable position rows.

    A batch stores shared official URLs and lifecycle policy once while each
    row retains its employer, code, title, qualification evidence, headcount
    and spreadsheet locator.  Expansion happens before normal contract
    validation, so generated rows have exactly the same safeguards as hand-
    entered records and remain easy to diff when an attachment changes.
    """
    batches = payload.get("position_batches") or []
    if not isinstance(batches, list):
        raise GovernmentPositionContractError("registry.position_batches must be a list")
    expanded: list[dict[str, Any]] = []
    for batch_index, batch in enumerate(batches):
        if not isinstance(batch, dict):
            raise GovernmentPositionContractError(
                f"registry.position_batches[{batch_index}] must be an object"
            )
        batch_id = _text(batch.get("id"), f"registry.position_batches[{batch_index}].id")
        shared_fields = {
            "source_id": batch.get("source_id"),
            "position_type": batch.get("position_type"),
            "province": batch.get("province"),
            "location": batch.get("location"),
            "deadline_date": batch.get("deadline_date", ""),
            "deadline_policy": batch.get("deadline_policy"),
            "official_notice_url": batch.get("official_notice_url"),
            "official_attachment_url": batch.get("official_attachment_url"),
            "record_status": batch.get("record_status"),
            "match_status": batch.get("match_status"),
        }
        rows = batch.get("rows")
        if not isinstance(rows, list):
            raise GovernmentPositionContractError(
                f"registry.position_batches[{batch_index}].rows must be a list"
            )
        for row_index, row in enumerate(rows):
            if not isinstance(row, list) or len(row) not in {7, 8}:
                raise GovernmentPositionContractError(
                    f"{batch_id}.rows[{row_index}] must contain code, employer, title, "
                    "major, degree, headcount and evidence locator, with an optional location"
                )
            code, employer, title, major, degree, headcount, locator = row[:7]
            row_location = row[7] if len(row) == 8 else shared_fields["location"]
            expanded.append(
                {
                    **shared_fields,
                    "location": row_location,
                    "id": f"{batch_id}-{str(code).strip().lower()}",
                    "employer": employer,
                    "position_code": code,
                    "title": title,
                    "major_requirement": major,
                    "degree_requirement": degree,
                    "headcount": headcount,
                    "evidence_locator": locator,
                    "note": str(batch.get("note") or "").strip(),
                }
            )
    return expanded


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
    records = [*records, *_expand_position_batches(payload)]
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
    scan_no_current_match = sum(
        1
        for item in payload.get("source_assessments", [])
        if item.get("status") == "verified_scan_no_current_match"
    )
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
        "open_until_filled_records": sum(
            1 for item in open_records if item.get("deadline_policy") == "open_until_filled"
        ),
        "fixed_deadline_records": sum(
            1 for item in open_records if item.get("deadline_policy") == "fixed_date"
        ),
        "explicit_student_matches": len(explicit_matches),
        "verified_closed_records": len(closed),
        "source_failures_or_pending": len(failures),
        "verified_scan_no_current_match": scan_no_current_match,
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
                "position_code",
                "major_requirement",
                "degree_requirement",
                "location",
                "deadline_date",
                "deadline_policy",
                "official_notice_url",
                "official_attachment_url",
                "evidence_locator",
            )
        },
        "scan_interpretation": (
            "存在来源故障或待核验记录，不能把缺少岗位解释为无岗位。"
            if failures
            else "部分官方来源已扫描成功但当前无可发布匹配；这不是来源故障。"
            if scan_no_current_match
            else "台账中的正式来源均已完成当前记录核验。"
        ),
    }


def current_publishable_position_records(
    registry: dict[str, Any], *, today: str,
) -> list[dict[str, Any]]:
    """Return current official rows that passed the explicit student match gate."""
    target = date.fromisoformat(today)
    rows: list[dict[str, Any]] = []
    for item in registry.get("records", []):
        if item["record_status"] != "verified_open":
            continue
        if item["match_status"] not in {"explicit_match", "unrestricted_match"}:
            continue
        deadline = str(item.get("deadline_date") or "").strip()
        if deadline and date.fromisoformat(deadline) < target:
            continue
        rows.append(dict(item))
    return rows


def position_record_to_posting(record: dict[str, Any]) -> RawPosting:
    """Convert one verified government table row into the normal job contract."""
    major = str(record["major_requirement"]).strip()
    degree = str(record["degree_requirement"]).strip()
    notice_url = str(record["official_notice_url"]).strip()
    attachment_url = str(record["official_attachment_url"]).strip()
    code = str(record["position_code"]).strip()
    evidence = {
        "evidence_scope": "official_attachment_row",
        "岗位": str(record["title"]).strip(),
        "政府岗位类型": str(record["position_type"]),
        "职位代码": code,
        "专业要求": major,
        "学历要求": degree,
        "招聘人数": str(record["headcount"]),
        "表格定位": str(record["evidence_locator"]),
        "工作地点": str(record["location"]).strip(),
        "官方公告链接": notice_url,
        "官方附件链接": attachment_url,
    }
    description = (
        f"官方职位表岗位代码：{code}；专业要求：{major}；学历要求：{degree}；"
        f"工作地点：{record['location']}；招聘人数：{record['headcount']}。"
    )
    if record["deadline_policy"] == "open_until_filled":
        description += "报名政策：招满即止，需每日复核公告状态。"
    return RawPosting(
        title=str(record["title"]).strip(),
        employer=str(record["employer"]).strip(),
        source_url=notice_url,
        application_url=notice_url,
        text=description,
        summary=str(record.get("note") or description).strip(),
        published_date=None,
        deadline_date=str(record.get("deadline_date") or "").strip() or None,
        location=str(record["location"]).strip(),
        external_id=f"government-position:{record['id']}:{code}",
        match_text=f"{record['title']} {major}",
        official_evidence_url=attachment_url,
        field_evidence=evidence,
        qualification_text=f"专业要求：{major}；学历要求：{degree}",
    )
