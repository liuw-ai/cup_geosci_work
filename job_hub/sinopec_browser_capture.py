"""Playwright producer for the official Sinopec campus SPA.

The portal exposes its list and unit-detail data through public read-only
same-origin JSON endpoints after the SPA is rendered.  This module keeps the
browser boundary explicit: it calls those endpoints from an official page
context, validates every page and detail response, and atomically replaces a
capture only when the complete scan succeeds.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from job_hub.browser_capture import BrowserCaptureError, _robots_permit
from job_hub.cnpc_browser_runner import _resolve_cdp_websocket
from job_hub.matching import CORE_MAJOR_KEYWORDS


SINOPEC_HOSTS = {"job.sinopec.com"}
CAPTURE_STATUSES = {"success", "partial", "access_limited", "parse_failed"}
_MAJOR_TERMS = tuple(sorted(CORE_MAJOR_KEYWORDS, key=len, reverse=True))


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _official_url(value: Any, field: str, hosts: set[str]) -> str:
    result = _text(value)
    parsed = urlparse(result)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise BrowserCaptureError(f"{field} must be an HTTP(S) URL")
    if parsed.hostname.lower().rstrip(".") not in hosts:
        raise BrowserCaptureError(f"{field} host is not allowlisted: {parsed.hostname}")
    return result


def _timestamp(value: Any) -> str:
    raw = _text(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as error:
        raise BrowserCaptureError("captured_at must be ISO-8601") from error
    if parsed.tzinfo is None:
        raise BrowserCaptureError("captured_at must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _page_count(data: dict[str, Any], page_size: int) -> int:
    try:
        pages = int(data.get("pages") or 0)
    except (TypeError, ValueError):
        pages = 0
    if pages > 0:
        return pages
    try:
        total = int(data.get("total") or 0)
    except (TypeError, ValueError):
        total = 0
    return max(1, (total + page_size - 1) // page_size)


def _api_payload(value: Any, endpoint: str) -> dict[str, Any]:
    if not isinstance(value, dict) or str(value.get("code")) != "S000000":
        message = value.get("message") if isinstance(value, dict) else "invalid JSON"
        raise BrowserCaptureError(f"Sinopec {endpoint} business error: {message}")
    data = value.get("data")
    if not isinstance(data, dict):
        raise BrowserCaptureError(f"Sinopec {endpoint} returned no data object")
    return data


def _is_candidate(rows: list[dict[str, Any]]) -> bool:
    text = " ".join(
        _text(row.get(key))
        for row in rows
        for key in ("jobPosition", "specialtyNorm", "educationNorm")
    ).lower()
    return any(term.lower() in text for term in _MAJOR_TERMS)


def _detail_url(department_id: str) -> str:
    return (
        "https://job.sinopec.com/#/school/recruitEnterpriseDetail?deptId="
        + department_id
    )


def _row_to_job(
    row: dict[str, Any],
    *,
    department_id: str,
    enterprise_name: str,
    detail_url: str,
    row_number: int,
) -> dict[str, Any]:
    title = _text(row.get("jobPosition"))
    major = _text(row.get("specialtyNorm"))
    degree = _text(row.get("educationNorm"))
    location = _text(row.get("workAddr"))
    deadline = _text(row.get("timeEnd"))
    if not all((title, major, degree, location, deadline)):
        raise BrowserCaptureError(
            f"Sinopec detail row {row_number} is missing title, major, degree, location or deadline"
        )
    stable_id = _text(row.get("id")) or f"row{row_number}"
    external_id = f"sinopec-{department_id}-position-{stable_id}"
    evidence = {
        "岗位名称": title,
        "招聘单位": enterprise_name,
        "学历要求": degree,
        "专业要求": major,
        "工作地点": location,
        "招聘人数": str(row.get("number") or ""),
        "截止时间": deadline,
        "官方详情链接": detail_url,
    }
    return {
        "title": title,
        "major": major,
        "employer": enterprise_name,
        "degree": degree,
        "headcount": str(row.get("number") or ""),
        "location": location,
        "enterprise_id": department_id,
        "enterprise_name": enterprise_name,
        "detail_url": detail_url,
        "evidence_url": detail_url,
        "external_id": external_id,
        "field_evidence": evidence,
        "deadline": deadline,
    }


def load_sinopec_browser_capture(
    path: Path | str,
    *,
    allowed_hosts: list[str] | set[str] | None = None,
    max_age_hours: float | None = 30,
    require_complete_scan: bool = True,
) -> dict[str, Any]:
    """Validate a browser-produced capture before the normal source adapter."""

    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BrowserCaptureError(f"cannot read Sinopec browser capture: {path}") from error
    if not isinstance(payload, dict):
        raise BrowserCaptureError("Sinopec browser capture must be an object")
    if _text(payload.get("status")) != "success":
        raise BrowserCaptureError(
            f"Sinopec capture is not publishable: {_text(payload.get('status'))}"
        )
    hosts = {str(item).lower().rstrip(".") for item in (allowed_hosts or SINOPEC_HOSTS)}
    _official_url(payload.get("platform_url"), "platform_url", hosts)
    captured_at = _timestamp(payload.get("captured_at"))
    if max_age_hours is not None:
        captured = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - captured).total_seconds() / 3600
        if age < -1:
            raise BrowserCaptureError("Sinopec capture timestamp is in the future")
        if age > float(max_age_hours):
            raise BrowserCaptureError(
                f"Sinopec capture is stale ({age:.1f}h > {float(max_age_hours):.1f}h)"
            )
    scan = payload.get("scan")
    if not isinstance(scan, dict):
        raise BrowserCaptureError("Sinopec capture scan metrics are required")
    required = (
        "enterprise_total",
        "enterprise_captured",
        "candidate_enterprise_total",
        "candidate_enterprise_captured",
        "pagination_complete",
        "detail_discovered",
        "detail_succeeded",
        "detail_failed",
        "jobs_exported",
    )
    metrics: dict[str, Any] = {}
    for field in required:
        if field == "pagination_complete":
            metrics[field] = bool(scan.get(field))
            continue
        try:
            metrics[field] = int(scan[field])
        except (KeyError, TypeError, ValueError) as error:
            raise BrowserCaptureError(f"Sinopec scan.{field} must be an integer") from error
        if metrics[field] < 0:
            raise BrowserCaptureError(f"Sinopec scan.{field} cannot be negative")
    enterprises = payload.get("enterprises")
    jobs = payload.get("jobs")
    if not isinstance(enterprises, list) or not isinstance(jobs, list):
        raise BrowserCaptureError("Sinopec enterprises and jobs must be lists")
    if metrics["enterprise_captured"] != len(enterprises):
        raise BrowserCaptureError("Sinopec enterprise count does not match manifest")
    if metrics["jobs_exported"] != len(jobs):
        raise BrowserCaptureError("Sinopec job count does not match manifest")
    complete = (
        metrics["pagination_complete"]
        and metrics["enterprise_total"] == len(enterprises)
        and metrics["candidate_enterprise_total"] == metrics["candidate_enterprise_captured"]
        and metrics["detail_failed"] == 0
        and metrics["detail_succeeded"] == metrics["enterprise_captured"]
        and metrics["detail_discovered"] == metrics["enterprise_captured"]
    )
    if require_complete_scan and not complete:
        raise BrowserCaptureError("Sinopec capture is incomplete; publication is blocked")
    seen: set[str] = set()
    normalized_jobs: list[dict[str, Any]] = []
    for index, raw in enumerate(jobs, start=1):
        if not isinstance(raw, dict):
            raise BrowserCaptureError(f"Sinopec job {index} must be an object")
        item = dict(raw)
        for field in ("external_id", "title", "employer", "major", "degree", "location", "deadline"):
            item[field] = _text(item.get(field))
            if not item[field]:
                raise BrowserCaptureError(f"Sinopec job {index}.{field} is required")
        if item["external_id"] in seen:
            raise BrowserCaptureError(f"duplicate Sinopec external_id: {item['external_id']}")
        seen.add(item["external_id"])
        item["detail_url"] = _official_url(item.get("detail_url"), f"job {index}.detail_url", hosts)
        item["evidence_url"] = _official_url(item.get("evidence_url"), f"job {index}.evidence_url", hosts)
        evidence = item.get("field_evidence")
        if not isinstance(evidence, dict) or not evidence:
            raise BrowserCaptureError(f"Sinopec job {index}.field_evidence is required")
        item["field_evidence"] = {str(key): _text(value) for key, value in evidence.items()}
        normalized_jobs.append(item)
    return {**payload, "captured_at": captured_at, "scan": {**scan, **metrics}, "jobs": normalized_jobs}


def _evaluate_api(page: Any, path: str, body: dict[str, Any]) -> dict[str, Any]:
    result = page.evaluate(
        """async ({path, body}) => {
          const response = await fetch(path, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(body),
          });
          return await response.json();
        }""",
        {"path": path, "body": body},
    )
    if not isinstance(result, dict):
        raise BrowserCaptureError(f"Sinopec {path} did not return JSON")
    return result


def run_sinopec_browser_capture(
    *,
    listing_url: str,
    output: Path | str,
    config: dict[str, Any],
    allowed_hosts: list[str] | set[str],
    user_agent: str,
    timeout_ms: int = 45_000,
) -> dict[str, Any]:
    """Capture every current unit and every unit-detail page in Chromium."""

    hosts = {str(item).lower().rstrip(".") for item in allowed_hosts}
    listing_url = _official_url(listing_url, "listing_url", hosts)
    _robots_permit(listing_url, user_agent=user_agent)
    page_size = max(1, min(int(config.get("page_size", 20)), 100))
    max_pages = max(1, min(int(config.get("max_pages", 20)), 100))
    max_units = max(1, min(int(config.get("max_units", 200)), 500))
    detail_page_size = max(1, min(int(config.get("detail_page_size", 20)), 100))
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise BrowserCaptureError("Playwright is not installed in the Sinopec browser worker") from error
    try:
        with sync_playwright() as playwright:
            cdp_url = _text(config.get("cdp_url"))
            if cdp_url:
                browser = playwright.chromium.connect_over_cdp(_resolve_cdp_websocket(cdp_url))
                context = browser.contexts[0] if browser.contexts else browser.new_context(user_agent=user_agent)
                owned = False
            else:
                browser = playwright.chromium.launch(headless=True)
                context = browser.new_context(user_agent=user_agent)
                owned = True
            page = context.new_page()
            try:
                response = page.goto(listing_url, wait_until="domcontentloaded", timeout=timeout_ms)
                if response is not None and response.status >= 400:
                    raise BrowserCaptureError(f"Sinopec listing returned HTTP {response.status}")
                enterprises: list[dict[str, Any]] = []
                total_pages = 0
                pagination_complete = False
                for page_number in range(1, max_pages + 1):
                    data = _api_payload(
                        _evaluate_api(page, "/api/upgrade/homepage/selectYpList", {"page": page_number, "limit": page_size, "keyword": ""}),
                        "selectYpList",
                    )
                    if page_number == 1:
                        total_pages = _page_count(data, page_size)
                    records = data.get("records")
                    if not isinstance(records, list):
                        raise BrowserCaptureError("Sinopec selectYpList records must be a list")
                    for item in records:
                        if not isinstance(item, dict):
                            raise BrowserCaptureError("Sinopec enterprise record must be an object")
                        department_id = _text(item.get("deptId"))
                        if not department_id:
                            raise BrowserCaptureError("Sinopec enterprise is missing deptId")
                        enterprises.append({
                            "id": department_id,
                            "name": _text(item.get("jobName") or item.get("deptName")),
                            "detail_url": _detail_url(department_id),
                            "candidate_by_keyword": False,
                            "scan_status": "detail_scanning",
                            "scan_metrics": {"pages_scanned": 0, "pages_expected": 0, "pagination_complete": False, "failed_jobs": 0, "jobs_discovered": 0, "jobs_exported": 0},
                            "job_summary": f"招聘岗位：{item.get('postNum') or ''}个",
                            "headcount_summary": f"招聘人数：{item.get('recruitNum') or ''}人",
                            "listed_end_time": _text(item.get("endTime")),
                        })
                    if page_number >= total_pages:
                        pagination_complete = page_number == total_pages
                        break
                if not pagination_complete or len(enterprises) != int(data.get("total") or len(enterprises)):
                    raise BrowserCaptureError("Sinopec enterprise pagination is incomplete")
                if len(enterprises) > max_units:
                    raise BrowserCaptureError(f"Sinopec enterprise count exceeds max_units={max_units}")

                jobs: list[dict[str, Any]] = []
                detail_failures = 0
                for enterprise in enterprises:
                    detail_page = context.new_page()
                    raw_rows: list[dict[str, Any]] = []
                    pages_expected = 0
                    pages_scanned = 0
                    try:
                        detail_response = detail_page.goto(enterprise["detail_url"], wait_until="domcontentloaded", timeout=timeout_ms)
                        if detail_response is not None and detail_response.status >= 400:
                            raise BrowserCaptureError(f"Sinopec detail returned HTTP {detail_response.status}")
                        for detail_number in range(1, max_pages + 1):
                            detail_data = _api_payload(
                                _evaluate_api(detail_page, "/api/upgrade/homepage/selectPositionList", {"departmentIdEq": enterprise["id"], "page": detail_number, "limit": detail_page_size}),
                                "selectPositionList",
                            )
                            if detail_number == 1:
                                pages_expected = _page_count(detail_data, detail_page_size)
                            records = detail_data.get("records")
                            if not isinstance(records, list):
                                raise BrowserCaptureError("Sinopec selectPositionList records must be a list")
                            raw_rows.extend(item for item in records if isinstance(item, dict))
                            pages_scanned = detail_number
                            if detail_number >= pages_expected:
                                break
                        if pages_scanned != pages_expected:
                            raise BrowserCaptureError("Sinopec detail pagination is incomplete")
                        candidate = _is_candidate(raw_rows)
                        enterprise["candidate_by_keyword"] = candidate
                        enterprise["scan_status"] = "success" if candidate else "scan_success_no_match"
                        for row_number, raw_row in enumerate(raw_rows, start=1):
                            if candidate:
                                jobs.append(_row_to_job(raw_row, department_id=enterprise["id"], enterprise_name=enterprise["name"], detail_url=enterprise["detail_url"], row_number=row_number))
                        enterprise["scan_metrics"] = {"pages_scanned": pages_scanned, "pages_expected": pages_expected, "pagination_complete": True, "failed_jobs": 0, "jobs_discovered": len(raw_rows), "jobs_exported": len(raw_rows) if candidate else 0}
                    except Exception as error:
                        detail_failures += 1
                        enterprise["scan_status"] = "parse_failed"
                        enterprise["scan_metrics"] = {"pages_scanned": pages_scanned, "pages_expected": pages_expected, "pagination_complete": False, "failed_jobs": 1, "jobs_discovered": len(raw_rows), "jobs_exported": 0}
                        enterprise["failure_reason"] = str(error)[:500]
                    finally:
                        detail_page.close()
                candidate_total = sum(1 for item in enterprises if item["candidate_by_keyword"])
                captured_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                payload = {
                    "version": 1,
                    "status": "success" if pagination_complete and detail_failures == 0 else "partial",
                    "platform_url": listing_url,
                    "captured_at": captured_at,
                    "enterprise_total": len(enterprises),
                    "candidate_enterprise_total": candidate_total,
                    "candidate_enterprise_captured": candidate_total,
                    "enterprises": enterprises,
                    "jobs": jobs,
                    "scan": {
                        "enterprise_total": len(enterprises),
                        "enterprise_captured": len(enterprises),
                        "candidate_enterprise_total": candidate_total,
                        "candidate_enterprise_captured": candidate_total,
                        "pagination_complete": pagination_complete,
                        "detail_discovered": len(enterprises),
                        "detail_succeeded": sum(
                            1
                            for item in enterprises
                            if item["scan_status"] in {"success", "scan_success_no_match"}
                        ),
                        "detail_failed": detail_failures,
                        "jobs_exported": len(jobs),
                    },
                }
                if payload["status"] == "success":
                    destination = Path(output)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    temporary = destination.with_suffix(destination.suffix + ".tmp")
                    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                    temporary.replace(destination)
                return payload
            finally:
                page.close()
                if owned:
                    browser.close()
    except PlaywrightTimeoutError as error:
        raise BrowserCaptureError(f"Sinopec browser page timed out: {error}") from error
    except BrowserCaptureError:
        raise
    except Exception as error:
        raise BrowserCaptureError(f"Sinopec browser capture failed: {error}") from error


def write_sinopec_capture_failure(*, output: Path | str, platform_url: str, status: str, reason: str) -> dict[str, Any]:
    if status not in {"access_limited", "parse_failed", "partial"}:
        raise ValueError("Sinopec failure status must be access_limited, parse_failed or partial")
    destination = Path(str(output) + ".failure.json")
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "status": status, "platform_url": platform_url, "diagnostic_for": Path(output).name, "captured_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "scan": {"failure_reason": str(reason)[:1000]}, "enterprises": [], "jobs": []}
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(destination)
    return payload


__all__ = ["SINOPEC_HOSTS", "load_sinopec_browser_capture", "run_sinopec_browser_capture", "write_sinopec_capture_failure"]
