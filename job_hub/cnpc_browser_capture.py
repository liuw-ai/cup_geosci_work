"""Contracts for browser-captured CNPC recruitment index evidence.

The CNPC public list is useful for discovering current unit announcements, but
an index row is not a job row.  This module stores the browser observation and
the detail-page outcome separately so a broken detail API cannot accidentally
be published as a geoscience vacancy.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from job_hub.contracts import is_http_url


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CAPTURE_PATH = PROJECT_ROOT / "data" / "verified" / "cnpc-browser-index-20260925.json"
CNPC_HOSTS = {"zhaopin.cnpc.com.cn", "www.cnpc.com.cn", "cnpc.com.cn"}
CAPTURE_STATUSES = frozenset({"success", "partial", "access_limited", "parse_failed"})
DETAIL_STATUSES = frozenset(
    {"fields_verified", "official_detail_api_degraded", "access_limited", "not_observed"}
)

# The CNPC portal has two different levels of data: a paginated announcement
# index and a second page containing a table of positions.  The old capture
# contract only represented the first level, which made it impossible to
# safely publish a role discovered in a browser.  This contract represents
# both levels and keeps the boundary explicit.
JOB_CAPTURE_STATUSES = frozenset({"success", "partial", "access_limited", "parse_failed"})
JOB_REQUIRED_SCAN_FIELDS = (
    "pages_scanned",
    "pagination_complete",
    "announcements_discovered",
    "announcements_targeted",
    "failed_announcement_details",
    "jobs_discovered",
    "jobs_exported",
)
JOB_REQUIRED_FIELDS = (
    "external_id",
    "announcement_id",
    "title",
    "employer",
    "detail_url",
    "evidence_url",
    "major",
    "degree",
    "location",
    "deadline",
)
REQUIRED_SCAN_FIELDS = (
    "pages_scanned",
    "pagination_complete",
    "announcements_discovered",
    "announcements_targeted",
    "failed_detail_rows",
)
REQUIRED_TARGET_FIELDS = (
    "external_id",
    "title",
    "detail_url",
    "deadline",
    "detail_status",
    "observed_at",
    "reason",
)


class CnpcBrowserCaptureError(ValueError):
    """Raised when a CNPC browser capture is incomplete or unsafe to audit."""


class CnpcJobCaptureError(ValueError):
    """Raised when a two-level CNPC job capture cannot be safely published."""


def _text(value: Any, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise CnpcBrowserCaptureError(f"{field} must be non-empty")
    return result


def _official_url(value: Any, field: str) -> str:
    result = _text(value, field)
    if not is_http_url(result):
        raise CnpcBrowserCaptureError(f"{field} must be an HTTP(S) URL")
    host = (urlparse(result).hostname or "").lower().rstrip(".")
    if host not in CNPC_HOSTS:
        raise CnpcBrowserCaptureError(f"{field} host is not an official CNPC host: {host}")
    return result


def _timestamp(value: Any, field: str) -> str:
    raw = _text(value, field).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as error:
        raise CnpcBrowserCaptureError(f"{field} must be ISO-8601") from error
    if parsed.tzinfo is None:
        raise CnpcBrowserCaptureError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def load_cnpc_browser_capture(path: Path | str | None = None) -> dict[str, Any]:
    """Load and validate a versioned CNPC browser index observation."""

    capture_path = Path(path or DEFAULT_CAPTURE_PATH)
    try:
        payload = json.loads(capture_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CnpcBrowserCaptureError(f"cannot read CNPC browser capture: {capture_path}") from error
    if not isinstance(payload, dict):
        raise CnpcBrowserCaptureError("CNPC browser capture must be an object")
    status = _text(payload.get("status"), "status")
    if status not in CAPTURE_STATUSES:
        raise CnpcBrowserCaptureError(f"unsupported capture status: {status}")
    platform_url = _official_url(payload.get("platform_url"), "platform_url")
    captured_at = _timestamp(payload.get("captured_at"), "captured_at")
    scan = payload.get("scan")
    if not isinstance(scan, dict):
        raise CnpcBrowserCaptureError("scan metrics are required")
    normalized_scan: dict[str, Any] = {}
    for field in REQUIRED_SCAN_FIELDS:
        if field not in scan:
            raise CnpcBrowserCaptureError(f"scan.{field} is required")
        if field == "pagination_complete":
            normalized_scan[field] = bool(scan[field])
            continue
        try:
            number = int(scan[field])
        except (TypeError, ValueError) as error:
            raise CnpcBrowserCaptureError(f"scan.{field} must be an integer") from error
        if number < 0:
            raise CnpcBrowserCaptureError(f"scan.{field} cannot be negative")
        normalized_scan[field] = number
    if normalized_scan["announcements_targeted"] > normalized_scan["announcements_discovered"]:
        raise CnpcBrowserCaptureError("targeted announcements exceed discovered announcements")
    if status == "success" and not normalized_scan["pagination_complete"]:
        raise CnpcBrowserCaptureError("success capture must complete pagination")

    targets = payload.get("detail_targets")
    if not isinstance(targets, list):
        raise CnpcBrowserCaptureError("detail_targets must be a list")
    normalized_targets: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(targets, start=1):
        if not isinstance(raw, dict):
            raise CnpcBrowserCaptureError(f"detail_targets[{index}] must be an object")
        missing = [field for field in REQUIRED_TARGET_FIELDS if field not in raw]
        if missing:
            raise CnpcBrowserCaptureError(
                f"detail_targets[{index}] missing: {', '.join(missing)}"
            )
        item = dict(raw)
        item["external_id"] = _text(item["external_id"], f"detail_targets[{index}].external_id")
        if item["external_id"] in seen:
            raise CnpcBrowserCaptureError(f"duplicate detail target: {item['external_id']}")
        seen.add(item["external_id"])
        item["title"] = _text(item["title"], f"detail_targets[{index}].title")
        item["detail_url"] = _official_url(item["detail_url"], f"detail_targets[{index}].detail_url")
        item["deadline"] = _text(item["deadline"], f"detail_targets[{index}].deadline")
        item["detail_status"] = _text(item["detail_status"], f"detail_targets[{index}].detail_status")
        if item["detail_status"] not in DETAIL_STATUSES:
            raise CnpcBrowserCaptureError(
                f"unsupported detail status: {item['detail_status']}"
            )
        item["observed_at"] = _timestamp(item["observed_at"], f"detail_targets[{index}].observed_at")
        item["reason"] = _text(item["reason"], f"detail_targets[{index}].reason")
        normalized_targets.append(item)
    if normalized_scan["announcements_targeted"] != len(normalized_targets):
        raise CnpcBrowserCaptureError("announcements_targeted does not match detail_targets length")

    return {
        **payload,
        "status": status,
        "platform_url": platform_url,
        "captured_at": captured_at,
        "scan": normalized_scan,
        "detail_targets": normalized_targets,
    }


def cnpc_browser_capture_summary(payload: dict[str, Any]) -> dict[str, Any]:
    """Return private operator metrics without promoting index rows."""

    scan = payload["scan"]
    statuses: dict[str, int] = {}
    for item in payload.get("detail_targets", []):
        status = str(item.get("detail_status") or "unknown")
        statuses[status] = statuses.get(status, 0) + 1
    return {
        "status": payload["status"],
        "captured_at": payload["captured_at"],
        "platform_url": payload["platform_url"],
        "pages_scanned": scan["pages_scanned"],
        "pagination_complete": scan["pagination_complete"],
        "announcements_discovered": scan["announcements_discovered"],
        "announcements_targeted": scan["announcements_targeted"],
        "failed_detail_rows": scan["failed_detail_rows"],
        "detail_status": dict(sorted(statuses.items())),
        "publishable_job_rows": 0,
        "publication_note": "公告索引和详情受限记录不能直接作为学生端岗位。",
    }


def build_cnpc_browser_capture_report(path: Path | str | None = None) -> dict[str, Any]:
    """Build the administrator report used by CLI and the protected API."""

    payload = load_cnpc_browser_capture(path)
    return {
        "summary": cnpc_browser_capture_summary(payload),
        "detail_targets": payload["detail_targets"],
        "source_policy": {
            "index_is_job": False,
            "student_publication": "only_after_detail_fields_verified",
            "access_limited_is_no_match": False,
        },
    }


def _job_text(value: Any, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise CnpcJobCaptureError(f"{field} must be non-empty")
    return result


def _job_url(value: Any, field: str, hosts: set[str]) -> str:
    result = _job_text(value, field)
    if not is_http_url(result):
        raise CnpcJobCaptureError(f"{field} must be an HTTP(S) URL")
    host = (urlparse(result).hostname or "").lower().rstrip(".")
    if host not in hosts:
        raise CnpcJobCaptureError(f"{field} host is not an official CNPC host: {host}")
    return result


def load_cnpc_job_capture(
    path: Path | str,
    *,
    allowed_hosts: list[str] | set[str],
    max_age_hours: float | None = None,
    require_complete_scan: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Load a CNPC list-plus-detail browser capture.

    A successful capture must finish every list page, visit every targeted
    announcement detail, and export exactly the rows observed there.  Any
    incomplete run is rejected so the worker cannot turn an access failure
    into a false "no matching jobs" result.
    """

    capture_path = Path(path)
    try:
        payload = json.loads(capture_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise CnpcJobCaptureError(f"CNPC job capture file is missing: {capture_path}") from error
    except (OSError, json.JSONDecodeError) as error:
        raise CnpcJobCaptureError(f"CNPC job capture file cannot be read: {capture_path}") from error
    if not isinstance(payload, dict):
        raise CnpcJobCaptureError("CNPC job capture must be an object")
    status = _job_text(payload.get("status"), "status")
    if status not in JOB_CAPTURE_STATUSES:
        raise CnpcJobCaptureError(f"unsupported CNPC job capture status: {status}")
    if status != "success":
        raise CnpcJobCaptureError(f"CNPC job capture is not publishable: {status}")
    hosts = {str(host).strip().lower().rstrip(".") for host in allowed_hosts if str(host).strip()}
    if not hosts:
        raise CnpcJobCaptureError("allowed_hosts must not be empty")
    platform_url = _job_url(payload.get("platform_url"), "platform_url", hosts)
    captured_at = _timestamp(payload.get("captured_at"), "captured_at")
    captured = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    age_hours = (current - captured).total_seconds() / 3600
    if age_hours < -1:
        raise CnpcJobCaptureError("captured_at is in the future")
    if max_age_hours is not None and age_hours > float(max_age_hours):
        raise CnpcJobCaptureError(
            f"CNPC job capture is stale ({age_hours:.1f}h > {float(max_age_hours):.1f}h)"
        )

    scan = payload.get("scan")
    if not isinstance(scan, dict):
        raise CnpcJobCaptureError("scan metrics are required")
    normalized_scan: dict[str, Any] = {}
    for field in JOB_REQUIRED_SCAN_FIELDS:
        if field not in scan:
            raise CnpcJobCaptureError(f"scan.{field} is required")
        if field == "pagination_complete":
            normalized_scan[field] = bool(scan[field])
            continue
        try:
            value = int(scan[field])
        except (TypeError, ValueError) as error:
            raise CnpcJobCaptureError(f"scan.{field} must be an integer") from error
        if value < 0:
            raise CnpcJobCaptureError(f"scan.{field} cannot be negative")
        normalized_scan[field] = value
    if normalized_scan["announcements_targeted"] > normalized_scan["announcements_discovered"]:
        raise CnpcJobCaptureError("targeted announcements exceed discovered announcements")
    if normalized_scan["jobs_exported"] > normalized_scan["jobs_discovered"]:
        raise CnpcJobCaptureError("exported jobs exceed discovered jobs")

    announcements = payload.get("announcements")
    if not isinstance(announcements, list):
        raise CnpcJobCaptureError("announcements must be a list")
    normalized_announcements: list[dict[str, Any]] = []
    announcement_ids: set[str] = set()
    for index, raw in enumerate(announcements, start=1):
        if not isinstance(raw, dict):
            raise CnpcJobCaptureError(f"announcements[{index}] must be an object")
        item = dict(raw)
        item["external_id"] = _job_text(item.get("external_id"), f"announcements[{index}].external_id")
        if item["external_id"] in announcement_ids:
            raise CnpcJobCaptureError(f"duplicate announcement id: {item['external_id']}")
        announcement_ids.add(item["external_id"])
        for field in ("title", "employer", "deadline", "detail_status", "observed_at", "reason"):
            item[field] = _job_text(item.get(field), f"announcements[{index}].{field}")
        item["detail_url"] = _job_url(item.get("detail_url"), f"announcements[{index}].detail_url", hosts)
        if item["detail_status"] not in DETAIL_STATUSES:
            raise CnpcJobCaptureError(f"unsupported announcement detail status: {item['detail_status']}")
        item["observed_at"] = _timestamp(item["observed_at"], f"announcements[{index}].observed_at")
        normalized_announcements.append(item)
    if normalized_scan["announcements_targeted"] != len(normalized_announcements):
        raise CnpcJobCaptureError("announcements_targeted does not match announcements length")

    jobs = payload.get("jobs")
    if not isinstance(jobs, list):
        raise CnpcJobCaptureError("jobs must be a list")
    normalized_jobs: list[dict[str, Any]] = []
    job_ids: set[str] = set()
    for index, raw in enumerate(jobs, start=1):
        if not isinstance(raw, dict):
            raise CnpcJobCaptureError(f"jobs[{index}] must be an object")
        item = dict(raw)
        for field in JOB_REQUIRED_FIELDS:
            item[field] = _job_text(item.get(field), f"jobs[{index}].{field}")
        if item["external_id"] in job_ids:
            raise CnpcJobCaptureError(f"duplicate job id: {item['external_id']}")
        job_ids.add(item["external_id"])
        if item["announcement_id"] not in announcement_ids:
            raise CnpcJobCaptureError(
                f"jobs[{index}] references unknown announcement: {item['announcement_id']}"
            )
        item["detail_url"] = _job_url(item["detail_url"], f"jobs[{index}].detail_url", hosts)
        item["evidence_url"] = _job_url(item["evidence_url"], f"jobs[{index}].evidence_url", hosts)
        evidence = item.get("field_evidence")
        if not isinstance(evidence, dict) or not evidence:
            raise CnpcJobCaptureError(f"jobs[{index}].field_evidence is required")
        item["field_evidence"] = {
            str(key): _job_text(value, f"jobs[{index}].field_evidence.{key}")
            for key, value in evidence.items()
        }
        item["observed_at"] = _timestamp(item.get("observed_at"), f"jobs[{index}].observed_at")
        normalized_jobs.append(item)
    if normalized_scan["jobs_exported"] != len(normalized_jobs):
        raise CnpcJobCaptureError("jobs_exported does not match jobs length")
    if status == "success" and normalized_scan["announcements_discovered"] == 0:
        raise CnpcJobCaptureError(
            "CNPC job capture is not publishable: listing rendered no announcements"
        )
    if status == "success" and require_complete_scan:
        if not normalized_scan["pagination_complete"] or normalized_scan["failed_announcement_details"]:
            raise CnpcJobCaptureError("CNPC job capture is incomplete; it cannot publish rows")
        if normalized_scan["jobs_exported"] != normalized_scan["jobs_discovered"]:
            raise CnpcJobCaptureError("CNPC job capture did not export every discovered job")

    return {
        **payload,
        "status": status,
        "platform_url": platform_url,
        "captured_at": captured_at,
        "capture_age_hours": round(max(0.0, age_hours), 3),
        "scan": normalized_scan,
        "announcements": normalized_announcements,
        "jobs": normalized_jobs,
    }


def cnpc_job_capture_summary(payload: dict[str, Any]) -> dict[str, Any]:
    scan = payload["scan"]
    detail_status: dict[str, int] = {}
    for item in payload.get("announcements", []):
        status = str(item.get("detail_status") or "unknown")
        detail_status[status] = detail_status.get(status, 0) + 1
    return {
        "status": payload["status"],
        "captured_at": payload["captured_at"],
        "capture_age_hours": payload.get("capture_age_hours"),
        "pages_scanned": scan["pages_scanned"],
        "pagination_complete": scan["pagination_complete"],
        "announcements_discovered": scan["announcements_discovered"],
        "announcements_targeted": scan["announcements_targeted"],
        "failed_announcement_details": scan["failed_announcement_details"],
        "jobs_discovered": scan["jobs_discovered"],
        "jobs_exported": scan["jobs_exported"],
        "detail_status": dict(sorted(detail_status.items())),
        "publishable_job_rows": len(payload.get("jobs", [])) if payload["status"] == "success" else 0,
        "publication_note": "只有岗位级详情字段完整且官方证据可回溯时，才会进入学生端专业/学历/截止日期门禁。",
    }
