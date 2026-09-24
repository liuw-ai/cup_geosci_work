"""Contracts for browser-captured China Sinopec recruitment snapshots.

The Sinopec campus portal is a JavaScript application.  The server-side
collector must therefore consume an administrator-captured, versioned
snapshot until a public read-only API has been verified.  This module validates
that capture without treating the portal's keyword result as proof of a
student-facing match.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from job_hub.contracts import is_http_url


SINOPEC_HOST = "job.sinopec.com"
SINOPEC_CAPTURE_STATUSES = frozenset(
    {
        "queued",
        "listed",
        "candidate",
        "detail_scanning",
        "success",
        "scan_success_no_match",
        "access_limited",
        "parse_failed",
        "manual_review_required",
    }
)

# A unit-level terminal state is different from a publishable job row.  In
# particular, access_limited and parse_failed are completed *diagnoses*, not
# evidence that a unit had no vacancies.
SINOPEC_TERMINAL_SCAN_STATUSES = frozenset(
    {
        "success",
        "scan_success_no_match",
        "access_limited",
        "parse_failed",
        "manual_review_required",
    }
)
SINOPEC_COMPLETED_SCAN_STATUSES = frozenset(
    {"success", "scan_success_no_match"}
)


class SinopecCaptureError(ValueError):
    """Raised when a browser capture is incomplete or unsafe to publish."""


def _text(value: Any, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise SinopecCaptureError(f"{field} must be non-empty")
    return result


def _url(value: Any, field: str, allowed_hosts: set[str]) -> str:
    result = _text(value, field)
    if not is_http_url(result):
        raise SinopecCaptureError(f"{field} must be an HTTP(S) URL")
    host = (urlparse(result).hostname or "").lower()
    if host not in allowed_hosts:
        raise SinopecCaptureError(f"{field} host is not allowlisted: {host}")
    return result


def _non_negative_int(value: Any, field: str, *, default: int | None = None) -> int | None:
    """Normalize optional browser counters without inventing scan success."""

    if value is None or str(value).strip() == "":
        return default
    try:
        result = int(value)
    except (TypeError, ValueError) as error:
        raise SinopecCaptureError(f"{field} must be a non-negative integer") from error
    if result < 0:
        raise SinopecCaptureError(f"{field} must be a non-negative integer")
    return result


def _boolean(value: Any, *, default: bool = False) -> bool:
    """Read JSON/browser booleans without treating the string ``"false"`` as true."""

    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "是"}:
        return True
    if normalized in {"0", "false", "no", "n", "否", ""}:
        return False
    raise SinopecCaptureError(f"boolean value is invalid: {value}")


def _job_enterprise_id(item: dict[str, Any]) -> str | None:
    """Extract the SPA department id used to bind a row to its unit detail."""

    detail_url = str(item.get("detail_url") or "")
    parsed = urlparse(detail_url)
    query = parse_qs(parsed.query)
    # Vue hash routes keep their query after ``#`` (for example
    # ``#/school/recruitEnterpriseDetail?deptId=...``), so it must be parsed
    # separately from the URL query above.
    if not query and parsed.fragment and "?" in parsed.fragment:
        query = parse_qs(parsed.fragment.split("?", 1)[1])
    value = (query.get("deptId") or [None])[0]
    if value:
        return str(value).strip()
    return _external_id_enterprise_id(item.get("external_id"))


def _external_id_enterprise_id(value: Any) -> str | None:
    external_id = str(value or "")
    if external_id.startswith("sinopec-"):
        suffix = external_id[len("sinopec-") :]
        if "-" in suffix:
            return suffix.rsplit("-", 1)[0]
    return None


def load_sinopec_capture(
    path: Path | str,
    *,
    allowed_hosts: list[str] | set[str] | None = None,
    require_complete_manifest: bool = False,
) -> dict[str, Any]:
    """Load and validate one browser-captured Sinopec snapshot.

    ``require_complete_manifest`` is enabled only when the source is promoted
    to production.  Partial captures are useful for adapter development, but
    they must remain disabled and visibly marked as incomplete.
    """

    capture_path = Path(path)
    try:
        payload = json.loads(capture_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise SinopecCaptureError(f"capture file is missing: {capture_path}") from error
    except (OSError, json.JSONDecodeError) as error:
        raise SinopecCaptureError(f"capture file cannot be read: {capture_path}") from error
    if not isinstance(payload, dict):
        raise SinopecCaptureError("Sinopec capture must be a JSON object")

    hosts = {str(host).strip().lower() for host in (allowed_hosts or [SINOPEC_HOST])}
    if not hosts:
        raise SinopecCaptureError("allowed_hosts must not be empty")
    platform_url = _url(payload.get("platform_url"), "platform_url", hosts)
    captured_at = _text(payload.get("captured_at"), "captured_at")
    try:
        enterprise_total = int(payload.get("enterprise_total"))
        candidate_total = int(payload.get("candidate_enterprise_total"))
    except (TypeError, ValueError) as error:
        raise SinopecCaptureError("enterprise totals must be integers") from error
    if enterprise_total < 1 or candidate_total < 0 or candidate_total > enterprise_total:
        raise SinopecCaptureError("enterprise totals are inconsistent")
    try:
        candidate_captured = int(
            payload.get("candidate_enterprise_captured", candidate_total)
        )
    except (TypeError, ValueError) as error:
        raise SinopecCaptureError("candidate_enterprise_captured must be an integer") from error
    if candidate_captured < 0 or candidate_captured > candidate_total:
        raise SinopecCaptureError("candidate enterprise capture total is inconsistent")

    enterprises = payload.get("enterprises")
    jobs = payload.get("jobs")
    if not isinstance(enterprises, list) or not isinstance(jobs, list):
        raise SinopecCaptureError("enterprises and jobs must be lists")
    if require_complete_manifest and len(enterprises) != enterprise_total:
        raise SinopecCaptureError(
            f"complete manifest requires {enterprise_total} enterprises, got {len(enterprises)}"
        )
    if require_complete_manifest and candidate_captured != candidate_total:
        raise SinopecCaptureError(
            f"complete manifest requires {candidate_total} candidate enterprises, got {candidate_captured}"
        )

    # Count rows before normalizing units so a capture can report whether each
    # detail route actually produced rows.  This is deliberately separate from
    # the portal's recruitment_jobs listing count, which is not scan evidence.
    job_counts_by_enterprise: dict[str, int] = {}
    raw_jobs = jobs if isinstance(jobs, list) else []
    for raw_job in raw_jobs:
        if isinstance(raw_job, dict):
            enterprise_id = _job_enterprise_id(raw_job)
            if enterprise_id:
                job_counts_by_enterprise[enterprise_id] = (
                    job_counts_by_enterprise.get(enterprise_id, 0) + 1
                )

    normalized_enterprises: list[dict[str, Any]] = []
    seen_enterprises: set[str] = set()
    for index, item in enumerate(enterprises, start=1):
        if not isinstance(item, dict):
            raise SinopecCaptureError(f"enterprise {index} must be an object")
        enterprise_id = _text(item.get("id"), f"enterprise {index}.id")
        if enterprise_id in seen_enterprises:
            raise SinopecCaptureError(f"duplicate enterprise id: {enterprise_id}")
        seen_enterprises.add(enterprise_id)
        status = _text(item.get("scan_status"), f"enterprise {enterprise_id}.scan_status")
        if status not in SINOPEC_CAPTURE_STATUSES:
            raise SinopecCaptureError(f"unsupported enterprise status: {status}")
        metrics = item.get("scan_metrics") or {}
        if not isinstance(metrics, dict):
            raise SinopecCaptureError(
                f"enterprise {enterprise_id}.scan_metrics must be an object"
            )
        rows_exported = _non_negative_int(
            metrics.get("jobs_exported"),
            f"enterprise {enterprise_id}.scan_metrics.jobs_exported",
            default=job_counts_by_enterprise.get(enterprise_id, 0),
        )
        jobs_discovered = _non_negative_int(
            metrics.get("jobs_discovered"),
            f"enterprise {enterprise_id}.scan_metrics.jobs_discovered",
        )
        if rows_exported is not None and jobs_discovered is not None and rows_exported > jobs_discovered:
            raise SinopecCaptureError(
                f"enterprise {enterprise_id} exported more jobs than discovered"
            )
        normalized_enterprises.append(
            {
                **item,
                "id": enterprise_id,
                "name": _text(item.get("name"), f"enterprise {enterprise_id}.name"),
                "scan_status": status,
                "candidate_by_keyword": _boolean(item.get("candidate_by_keyword", False)),
                "detail_url": _url(
                    item.get("detail_url"),
                    f"enterprise {enterprise_id}.detail_url",
                    hosts,
                ),
                "scan_metrics": {
                    "pages_scanned": _non_negative_int(
                        metrics.get("pages_scanned"),
                        f"enterprise {enterprise_id}.scan_metrics.pages_scanned",
                        default=0,
                    ),
                    "pages_expected": _non_negative_int(
                        metrics.get("pages_expected"),
                        f"enterprise {enterprise_id}.scan_metrics.pages_expected",
                    ),
                    "jobs_discovered": jobs_discovered,
                    "jobs_exported": rows_exported,
                    "failed_jobs": _non_negative_int(
                        metrics.get("failed_jobs"),
                        f"enterprise {enterprise_id}.scan_metrics.failed_jobs",
                        default=0,
                    ),
                    "pagination_complete": _boolean(metrics.get("pagination_complete", False)),
                },
            }
        )

    normalized_jobs: list[dict[str, Any]] = []
    seen_jobs: set[str] = set()
    for index, item in enumerate(jobs, start=1):
        if not isinstance(item, dict):
            raise SinopecCaptureError(f"job {index} must be an object")
        external_id = _text(item.get("external_id"), f"job {index}.external_id")
        if external_id in seen_jobs:
            raise SinopecCaptureError(f"duplicate job external_id: {external_id}")
        seen_jobs.add(external_id)
        evidence_url = _url(
            item.get("evidence_url") or item.get("detail_url"),
            f"job {external_id}.evidence_url",
            hosts,
        )
        detail_url = _url(item.get("detail_url"), f"job {external_id}.detail_url", hosts)
        field_evidence = item.get("field_evidence")
        if not isinstance(field_evidence, dict) or not field_evidence:
            raise SinopecCaptureError(f"job {external_id}.field_evidence is required")
        normalized_jobs.append(
            {
                **item,
                "external_id": external_id,
                "title": _text(item.get("title"), f"job {external_id}.title"),
                "employer": _text(item.get("employer"), f"job {external_id}.employer"),
                "degree": _text(item.get("degree"), f"job {external_id}.degree"),
                "major": _text(item.get("major"), f"job {external_id}.major"),
                "location": _text(item.get("location"), f"job {external_id}.location"),
                "deadline": _text(item.get("deadline"), f"job {external_id}.deadline"),
                "detail_url": detail_url,
                "evidence_url": evidence_url,
                "field_evidence": {str(key): _text(value, f"job {external_id}.field_evidence.{key}") for key, value in field_evidence.items()},
            }
        )

    return {
        **payload,
        "platform_url": platform_url,
        "captured_at": captured_at,
        "enterprise_total": enterprise_total,
        "candidate_enterprise_total": candidate_total,
        "candidate_enterprise_captured": candidate_captured,
        "enterprises": normalized_enterprises,
        "jobs": normalized_jobs,
        "complete_manifest": len(normalized_enterprises) == enterprise_total,
    }


def sinopec_capture_summary(payload: dict[str, Any]) -> dict[str, int | bool]:
    """Return auditable capture metrics without treating them as job counts."""

    enterprises = list(payload.get("enterprises", []))
    jobs = list(payload.get("jobs", []))
    statuses = {status: 0 for status in SINOPEC_CAPTURE_STATUSES}
    candidate_statuses = {status: 0 for status in SINOPEC_CAPTURE_STATUSES}
    pages_complete = 0
    candidate_scan_complete = 0
    jobs_discovered = 0
    jobs_exported = 0
    failed_jobs = 0
    for item in enterprises:
        status = str(item.get("scan_status") or "")
        if status in statuses:
            statuses[status] += 1
        if item.get("candidate_by_keyword") and status in candidate_statuses:
            candidate_statuses[status] += 1
        metrics = item.get("scan_metrics") or {}
        if metrics.get("pagination_complete"):
            pages_complete += 1
        if metrics.get("jobs_discovered") is not None:
            jobs_discovered += int(metrics["jobs_discovered"])
        if metrics.get("jobs_exported") is not None:
            jobs_exported += int(metrics["jobs_exported"])
        failed_jobs += int(metrics.get("failed_jobs") or 0)
        if item.get("candidate_by_keyword") and (
            status in SINOPEC_COMPLETED_SCAN_STATUSES
            and bool(metrics.get("pagination_complete"))
            and int(metrics.get("failed_jobs") or 0) == 0
        ):
            candidate_scan_complete += 1
    candidate_total = int(payload["candidate_enterprise_total"])
    return {
        "enterprise_total": int(payload["enterprise_total"]),
        "candidate_enterprise_total": int(payload["candidate_enterprise_total"]),
        "candidate_enterprise_captured": int(
            payload.get("candidate_enterprise_captured", 0)
        ),
        "enterprise_captured": len(enterprises),
        "job_rows_captured": len(jobs),
        "complete_manifest": bool(payload.get("complete_manifest")),
        "enterprise_pagination_complete": pages_complete,
        "candidate_scan_complete": candidate_scan_complete,
        "candidate_scan_complete_all": candidate_scan_complete == candidate_total,
        "jobs_discovered": jobs_discovered,
        "jobs_exported": jobs_exported,
        "failed_jobs": failed_jobs,
        **{f"enterprise_{key}": value for key, value in statuses.items()},
        **{f"candidate_{key}": value for key, value in candidate_statuses.items()},
    }
