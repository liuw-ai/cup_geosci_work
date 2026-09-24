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
from urllib.parse import urlparse

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

    enterprises = payload.get("enterprises")
    jobs = payload.get("jobs")
    if not isinstance(enterprises, list) or not isinstance(jobs, list):
        raise SinopecCaptureError("enterprises and jobs must be lists")
    if require_complete_manifest and len(enterprises) != enterprise_total:
        raise SinopecCaptureError(
            f"complete manifest requires {enterprise_total} enterprises, got {len(enterprises)}"
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
        normalized_enterprises.append(
            {
                **item,
                "id": enterprise_id,
                "name": _text(item.get("name"), f"enterprise {enterprise_id}.name"),
                "scan_status": status,
                "detail_url": _url(
                    item.get("detail_url"),
                    f"enterprise {enterprise_id}.detail_url",
                    hosts,
                ),
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
        "enterprises": normalized_enterprises,
        "jobs": normalized_jobs,
        "complete_manifest": len(normalized_enterprises) == enterprise_total,
    }


def sinopec_capture_summary(payload: dict[str, Any]) -> dict[str, int | bool]:
    """Return auditable capture metrics without treating them as job counts."""

    enterprises = list(payload.get("enterprises", []))
    jobs = list(payload.get("jobs", []))
    statuses = {status: 0 for status in SINOPEC_CAPTURE_STATUSES}
    for item in enterprises:
        status = str(item.get("scan_status") or "")
        if status in statuses:
            statuses[status] += 1
    return {
        "enterprise_total": int(payload["enterprise_total"]),
        "candidate_enterprise_total": int(payload["candidate_enterprise_total"]),
        "enterprise_captured": len(enterprises),
        "job_rows_captured": len(jobs),
        "complete_manifest": bool(payload.get("complete_manifest")),
        **{f"enterprise_{key}": value for key, value in statuses.items()},
    }
