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
