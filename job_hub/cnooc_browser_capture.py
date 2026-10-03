"""Manifest contract and Playwright producer for CNOOC campus details.

The public Zhaopin API is useful for discovering the annual batch, but it does
not expose all job fields.  This module first completes API pagination, then
opens each geoscience candidate's official detail URL in Chromium and stores
only rows parsed by :mod:`job_hub.zhaopin_detail`.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from job_hub.browser_capture import BrowserCaptureError, _robots_permit
from job_hub.capture_evidence import (
    attach_capture_manifest,
    ensure_capture_manifest,
    validate_capture_manifest,
)
from job_hub.cnpc_browser_runner import _resolve_cdp_websocket
from job_hub.zhaopin_detail import (
    ZhaopinDetailError,
    extract_zhaopin_degree_requirement,
    extract_zhaopin_major_requirement,
    parse_zhaopin_detail_html,
)


CNOOC_HOSTS = {"cnooc.zhaopin.com", "xiaoyuan.zhaopin.com"}
CAPTURE_STATUSES = {"success", "partial", "access_limited", "parse_failed"}


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


def _timestamp(value: Any, field: str = "captured_at") -> str:
    raw = _text(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as error:
        raise BrowserCaptureError(f"{field} must be ISO-8601") from error
    if parsed.tzinfo is None:
        raise BrowserCaptureError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _age_hours(captured_at: str, max_age_hours: float | None) -> float:
    captured = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
    age = (datetime.now(timezone.utc) - captured.astimezone(timezone.utc)).total_seconds() / 3600
    if age < -1:
        raise BrowserCaptureError("CNOOC capture timestamp is in the future")
    if max_age_hours is not None and age > float(max_age_hours):
        raise BrowserCaptureError(f"CNOOC capture is stale ({age:.1f}h > {float(max_age_hours):.1f}h)")
    return max(0.0, age)


def repair_cnooc_detail_requirement_evidence(
    row: dict[str, Any],
    evidence: dict[str, str],
    *,
    index: int,
) -> None:
    """Recover precise fields from a legacy CNOOC job-level detail snapshot.

    Earlier capture versions persisted a complete ``jobDesc`` in the major
    field and an ATS education enum such as ``硕士`` in the degree field.  The
    underlying text is still a single official detail block for this row, so
    it can be repaired deterministically.  This is deliberately not a
    keyword fallback: a missing labelled major condition remains a capture
    error and cannot become a public record.
    """

    detail_text = _text(
        " ".join(
            str(value or "")
            for value in (
                row.get("description"),
                evidence.get("专业要求"),
                evidence.get("专业范围"),
                evidence.get("学历要求"),
            )
        )
    )
    major = extract_zhaopin_major_requirement(detail_text)
    if not major:
        # Current captures already carry a bounded job-level major field.  It
        # is safe only when it is not an old full description.
        fallback_major = _text(
            evidence.get("专业要求")
            or evidence.get("专业范围")
            or row.get("major")
        )
        if not fallback_major or any(
            marker in fallback_major
            for marker in ("岗位职责", "任职要求", "学历要求", "专业要求")
        ):
            raise BrowserCaptureError(
                f"CNOOC row {index} is missing a bounded job-level major requirement"
            )
        major = fallback_major

    degree = extract_zhaopin_degree_requirement(
        detail_text,
        _text(evidence.get("学历要求") or row.get("degree")),
    )
    if not degree:
        raise BrowserCaptureError(
            f"CNOOC row {index} is missing a job-level degree requirement"
        )

    row["major"] = major
    row["degree"] = degree
    evidence["专业要求"] = major
    evidence["专业范围"] = major
    evidence["学历要求"] = degree


def load_cnooc_browser_capture(
    path: Path | str,
    *,
    allowed_hosts: list[str] | set[str] | None = None,
    max_age_hours: float | None = 30,
    require_complete_scan: bool = True,
    require_capture_manifest: bool = False,
) -> dict[str, Any]:
    """Validate a server-produced CNOOC detail manifest fail-closed."""

    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BrowserCaptureError(f"cannot read CNOOC browser capture: {path}") from error
    if not isinstance(payload, dict):
        raise BrowserCaptureError("CNOOC browser capture must be an object")
    if require_capture_manifest:
        try:
            validate_capture_manifest(payload)
        except ValueError as error:
            raise BrowserCaptureError(str(error)) from error
    status = _text(payload.get("status"))
    if status not in CAPTURE_STATUSES:
        raise BrowserCaptureError(f"unsupported CNOOC capture status: {status}")
    if status != "success":
        raise BrowserCaptureError(f"CNOOC capture is not publishable: {status}")
    hosts = {str(item).lower().rstrip(".") for item in (allowed_hosts or CNOOC_HOSTS)}
    platform_url = _official_url(payload.get("platform_url"), "platform_url", hosts)
    captured_at = _timestamp(payload.get("captured_at"))
    age = _age_hours(captured_at, max_age_hours)
    scan = payload.get("scan")
    if not isinstance(scan, dict):
        raise BrowserCaptureError("CNOOC capture scan metrics are required")
    required = (
        "pages_scanned", "pagination_complete", "listing_total", "candidate_rows",
        "detail_discovered", "detail_succeeded", "detail_failed", "rows_exported",
    )
    normalized: dict[str, Any] = {}
    for field in required:
        if field not in scan:
            raise BrowserCaptureError(f"scan.{field} is required")
        if field == "pagination_complete":
            normalized[field] = bool(scan[field])
        else:
            try:
                normalized[field] = int(scan[field])
            except (TypeError, ValueError) as error:
                raise BrowserCaptureError(f"scan.{field} must be an integer") from error
            if normalized[field] < 0:
                raise BrowserCaptureError(f"scan.{field} cannot be negative")
    rows = payload.get("rows")
    if not isinstance(rows, list):
        raise BrowserCaptureError("CNOOC capture rows must be a list")
    if normalized["rows_exported"] != len(rows) or normalized["detail_succeeded"] != len(rows):
        raise BrowserCaptureError("CNOOC scan row counts do not match rows")
    complete = (
        normalized["pagination_complete"]
        and normalized["detail_failed"] == 0
        and normalized["detail_succeeded"] == normalized["detail_discovered"]
        and normalized["candidate_rows"] == normalized["detail_discovered"]
    )
    if require_complete_scan and not complete:
        raise BrowserCaptureError("CNOOC capture is incomplete; publication is blocked")
    normalized_rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    required_row = ("external_id", "title", "employer", "major", "degree", "location", "headcount", "deadline", "detail_url", "evidence_url")
    for index, raw in enumerate(rows, start=1):
        if not isinstance(raw, dict):
            raise BrowserCaptureError(f"CNOOC row {index} must be an object")
        row = dict(raw)
        for field in required_row:
            row[field] = _text(row.get(field))
            if not row[field]:
                raise BrowserCaptureError(f"CNOOC row {index}.{field} is required")
        if row["external_id"] in seen:
            raise BrowserCaptureError(f"duplicate CNOOC external_id: {row['external_id']}")
        seen.add(row["external_id"])
        row["detail_url"] = _official_url(row["detail_url"], f"row {index}.detail_url", hosts)
        row["evidence_url"] = _official_url(row["evidence_url"], f"row {index}.evidence_url", hosts)
        evidence = row.get("field_evidence")
        if not isinstance(evidence, dict):
            raise BrowserCaptureError(f"CNOOC row {index}.field_evidence is required")
        for key in ("岗位", "专业范围", "学历要求", "工作地点", "招聘人数", "报名截止", "官方岗位详情"):
            if not _text(evidence.get(key)):
                raise BrowserCaptureError(f"CNOOC row {index}.field_evidence.{key} is required")
        normalized_evidence = {
            str(key): _text(value) for key, value in evidence.items()
        }
        repair_cnooc_detail_requirement_evidence(
            row,
            normalized_evidence,
            index=index,
        )
        row["field_evidence"] = normalized_evidence
        normalized_rows.append(row)
    return {
        **payload,
        "status": status,
        "platform_url": platform_url,
        "captured_at": captured_at,
        "capture_age_hours": round(age, 3),
        "scan": {**scan, **normalized},
        "rows": normalized_rows,
    }


def _candidate_job(
    row: dict[str, Any],
    patterns: list[str],
    exclude_patterns: list[str] | None = None,
    *,
    allow_unrestricted_candidates: bool = False,
) -> tuple[str, str] | None:
    job = row.get("job") if isinstance(row.get("job"), dict) else row
    title = _text(job.get("title") or job.get("positionName"))
    url = _text(job.get("url") or job.get("positionURL") or job.get("positionUrl"))
    if not title or not url:
        return None
    searchable = " ".join(_text(str(job.get(key) or "")) for key in ("title", "detail", "jobDetail", "jobCategories"))
    has_included_pattern = any(
        re.search(pattern, searchable, re.IGNORECASE) for pattern in patterns
    )
    # Some official listings omit the major field from the index row. Keep
    # those rows for detail-level verification when the source explicitly
    # opts into unrestricted-major discovery; the publication gate still
    # requires the official detail to say ``不限专业`` and expose a supported
    # degree. This widens discovery without weakening publication.
    if patterns and not has_included_pattern and not allow_unrestricted_candidates:
        return None
    # Exclusion terms are intentionally evaluated against the title and
    # category only. Detailed responsibilities frequently mention generic
    # words such as "合规" or "市场" even for drilling/geoscience roles;
    # applying the exclusion to the full body silently drops valid jobs.
    exclusion_text = " ".join(
        _text(str(job.get(key) or "")) for key in ("title", "jobCategories")
    )
    if exclude_patterns and any(
        re.search(pattern, exclusion_text, re.IGNORECASE)
        for pattern in exclude_patterns
    ):
        return None
    return title, url


def run_cnooc_browser_capture(
    *,
    listing_url: str,
    output: Path | str,
    config: dict[str, Any],
    allowed_hosts: list[str] | set[str],
    user_agent: str,
    timeout_ms: int = 45_000,
) -> dict[str, Any]:
    """Scan the public campaign API and enrich candidates in Chromium."""

    hosts = {str(item).lower().rstrip(".") for item in allowed_hosts}
    listing_host = (urlparse(listing_url).hostname or "").lower().rstrip(".")
    if listing_host not in hosts:
        raise BrowserCaptureError(f"CNOOC listing URL host is not allowlisted: {listing_host}")
    _robots_permit(listing_url, user_agent=user_agent)
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise BrowserCaptureError("Playwright is not installed in the CNOOC browser worker") from error

    campaign_id = _text(config.get("company_id"))
    if not campaign_id:
        raise BrowserCaptureError("CNOOC browser capture requires company_id")
    api_host = str(config.get("api_host") or "https://fe.zhaopin.com").rstrip("/")
    api_url = api_host + str(config.get("api_path") or "/grace/api/dsc/search-job-list")
    page_size = max(1, min(int(config.get("page_size", 100)), 100))
    max_pages = max(1, min(int(config.get("max_pages", 20)), 100))
    patterns = [str(item) for item in config.get("include_patterns", []) if str(item)]
    allow_unrestricted_candidates = bool(config.get("allow_unrestricted_candidates", False))
    exclude_patterns = [
        str(item) for item in config.get("exclude_patterns", []) if str(item)
    ]
    headers = {"Accept": "application/json", "Content-Type": "application/json", "Origin": "https://cnooc.zhaopin.com", "Referer": listing_url, "User-Agent": user_agent}
    candidates: list[dict[str, str]] = []
    pages = 0
    listing_total = 0
    total_pages = 0
    for page_index in range(1, max_pages + 1):
        response = requests.post(api_url, json={"orgNumbers": [campaign_id], "jobSource": 2, "pageIndex": page_index, "pageSize": page_size, "orgDepartmentIds": [], "workRegionIds": "", "jobTypes": "", "priorityMajors": "", "customTags": ""}, headers=headers, timeout=30)
        if not response.ok:
            raise BrowserCaptureError(f"CNOOC public API returned HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as error:
            raise BrowserCaptureError("CNOOC public API did not return JSON") from error
        if not isinstance(payload, dict) or str(payload.get("code")) != "200":
            raise BrowserCaptureError(f"CNOOC public API business error: {payload.get('message') if isinstance(payload, dict) else 'invalid payload'}")
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        info = data.get("pageInfo") if isinstance(data.get("pageInfo"), dict) else {}
        rows = data.get("jobList") if isinstance(data.get("jobList"), list) else []
        if page_index == 1:
            listing_total = int(info.get("totalNum") or 0)
            total_pages = int(info.get("totalPage") or ((listing_total + page_size - 1) // page_size))
        pages += 1
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            candidate = _candidate_job(
                raw,
                patterns,
                exclude_patterns,
                allow_unrestricted_candidates=allow_unrestricted_candidates,
            )
            if not candidate:
                continue
            title, url = candidate
            candidates.append({"title": title, "url": url, "job_number": _text((raw.get("job") if isinstance(raw.get("job"), dict) else raw).get("jobNumber"))})
        if page_index >= total_pages:
            break
    pagination_complete = pages == total_pages
    max_items = min(max(1, int(config.get("max_items", 500))), 2_000)
    candidates = candidates[:max_items]

    rows: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    try:
        with sync_playwright() as playwright:
            cdp_url = str(config.get("cdp_url") or "").strip()
            if cdp_url:
                browser = playwright.chromium.connect_over_cdp(_resolve_cdp_websocket(cdp_url))
                context = browser.contexts[0] if browser.contexts else browser.new_context(user_agent=user_agent)
                owned = False
            else:
                browser = playwright.chromium.launch(headless=True)
                context = browser.new_context(user_agent=user_agent)
                owned = True
            try:
                for candidate in candidates:
                    page = context.new_page()
                    try:
                        detail_url = _official_url(candidate["url"], "CNOOC detail URL", hosts)
                        # Anti-automation responses can keep subresources open
                        # indefinitely while still returning HTTP 200.  The
                        # server-rendered detail only needs the committed HTML;
                        # a short bounded poll distinguishes it from a
                        # challenge page without blocking the whole batch.
                        response = page.goto(detail_url, wait_until="commit", timeout=timeout_ms)
                        if response is not None and response.status >= 400:
                            raise BrowserCaptureError(f"CNOOC detail returned HTTP {response.status}")
                        html = ""
                        for _ in range(4):
                            html = page.content()
                            if "window.__INITIAL_DATA__" in html:
                                break
                            page.wait_for_timeout(250)
                        parsed = parse_zhaopin_detail_html(html, detail_url=page.url, expected_job_number=candidate["job_number"] or None, allowed_hosts=hosts)
                        rows.append({"external_id": parsed["job_number"], "title": parsed["title"], "employer": parsed["employer"], "major": parsed["major_text"], "degree": parsed["degree"], "location": parsed["location"], "headcount": parsed["quantity"], "deadline": parsed["deadline"], "published_date": parsed.get("published"), "detail_url": parsed["detail_url"], "evidence_url": parsed["detail_url"], "description": parsed["description"], "field_evidence": parsed["evidence"]})
                    except Exception as error:
                        failures.append({"detail_url": candidate["url"], "title": candidate["title"], "reason": str(error)[:500]})
                    finally:
                        page.close()
            finally:
                if owned:
                    browser.close()
    except PlaywrightTimeoutError as error:
        raise BrowserCaptureError(f"CNOOC browser page timed out: {error}") from error
    except BrowserCaptureError:
        raise
    except Exception as error:
        raise BrowserCaptureError(f"CNOOC browser capture failed: {error}") from error

    payload = {
        "version": 1,
        "status": "success" if pagination_complete and not failures else "partial",
        "platform_url": listing_url,
        "captured_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "scan": {
            "pages_scanned": pages,
            "pagination_complete": pagination_complete,
            "listing_total": listing_total,
            "candidate_rows": len(candidates),
            "detail_discovered": len(candidates),
            "detail_succeeded": len(rows),
            "detail_failed": len(failures),
            "rows_exported": len(rows),
            "failure_records": failures,
        },
        "rows": rows,
        "failure_records": failures,
    }
    payload = ensure_capture_manifest(
        payload,
        source_id=str(config.get("source_id") or "cnooc-career-browser"),
        adapter_version=str(config.get("adapter_version") or "cnooc-browser-v1"),
    )
    return persist_cnooc_browser_capture(
        output=output,
        payload=payload,
        source_id=str(config.get("source_id") or "cnooc-career-browser"),
        adapter_version=str(config.get("adapter_version") or "cnooc-browser-v1"),
    )


def _write_capture(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    return payload


def persist_cnooc_browser_capture(
    *,
    output: Path | str,
    payload: dict[str, Any],
    source_id: str = "cnooc-career-browser",
    adapter_version: str = "cnooc-browser-v1",
) -> dict[str, Any]:
    """Keep the canonical CNOOC path success-only.

    A partial browser pass is useful diagnostic evidence but cannot replace a
    previously complete snapshot.  The source collector reads ``output`` as
    the publication pointer, so non-success payloads are written beside it
    and the prior success remains available until a complete pass succeeds.
    """

    payload = ensure_capture_manifest(
        payload,
        source_id=source_id,
        adapter_version=adapter_version,
    )
    destination = Path(output)
    status = _text(payload.get("status"))
    if status not in CAPTURE_STATUSES:
        raise BrowserCaptureError(f"unsupported CNOOC capture status: {status}")
    if status == "success":
        scan = payload.get("scan")
        if not isinstance(scan, dict):
            raise BrowserCaptureError("successful CNOOC capture is missing scan metrics")
        if (
            not bool(scan.get("pagination_complete"))
            or int(scan.get("detail_failed", 0)) != 0
            or int(scan.get("detail_succeeded", 0)) != int(scan.get("detail_discovered", 0))
        ):
            raise BrowserCaptureError("successful CNOOC capture is incomplete")
        rows = payload.get("rows")
        if not isinstance(rows, list) or not rows:
            raise BrowserCaptureError("successful CNOOC capture rows must be non-empty")
        return _write_capture(destination, payload)

    # Keep a deterministic diagnostic path for the next targeted retry.  The
    # canonical success-only file is intentionally untouched.
    failure_path = destination.with_suffix(".failure.json")
    diagnostic = dict(payload)
    diagnostic["diagnostic_for"] = destination.name
    _write_capture(failure_path, diagnostic)
    return payload


def write_cnooc_capture_failure(
    *,
    output: Path | str,
    platform_url: str,
    status: str,
    reason: str,
    source_id: str = "cnooc-career-browser",
    adapter_version: str = "cnooc-browser-v1",
) -> dict[str, Any]:
    """Write a diagnostic beside the last successful capture."""

    if status not in {"access_limited", "parse_failed", "partial"}:
        raise ValueError("CNOOC failure status must be access_limited, parse_failed or partial")
    payload: dict[str, Any] = {
        "version": 1,
        "status": status,
        "platform_url": platform_url,
        "diagnostic_for": Path(output).name,
        "captured_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "scan": {
            "pages_scanned": 0,
            "pagination_complete": False,
            "listing_total": 0,
            "candidate_rows": 0,
            "detail_discovered": 0,
            "detail_succeeded": 0,
            "detail_failed": 0,
            "rows_exported": 0,
            "failure_reason": str(reason)[:1000],
        },
        "rows": [],
        "failure_records": [],
    }
    payload = attach_capture_manifest(
        payload,
        source_id=source_id,
        adapter_version=adapter_version,
    )
    destination = Path(str(output) + ".failure.json")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(destination)
    return payload


__all__ = [
    "CNOOC_HOSTS",
    "load_cnooc_browser_capture",
    "persist_cnooc_browser_capture",
    "repair_cnooc_detail_requirement_evidence",
    "run_cnooc_browser_capture",
    "write_cnooc_capture_failure",
]
