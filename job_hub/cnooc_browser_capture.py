"""Manifest contract and Playwright producer for CNOOC campus details.

The public Zhaopin API is useful for discovering the annual batch, but it does
not expose all job fields.  This module first completes API pagination, then
opens each geoscience candidate's official detail URL in Chromium and stores
only rows parsed by :mod:`job_hub.zhaopin_detail`.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from job_hub.browser_capture import (
    BrowserCaptureError,
    _robots_permit,
    launch_playwright_browser,
)
from job_hub.capture_evidence import (
    attach_capture_manifest,
    ensure_capture_manifest,
    validate_capture_manifest,
)
from job_hub.cnpc_browser_runner import _resolve_cdp_websocket
from job_hub.transport import create_session
from job_hub.zhaopin_detail import (
    ZhaopinDetailError,
    extract_zhaopin_degree_requirement,
    extract_zhaopin_major_requirement,
    parse_zhaopin_detail_html,
)


CNOOC_HOSTS = {"cnooc.zhaopin.com", "xiaoyuan.zhaopin.com"}
CAPTURE_STATUSES = {"success", "partial", "access_limited", "parse_failed"}


def _detail_retry_budget(config: dict[str, Any]) -> tuple[int, int]:
    """Return a bounded retry count and delay for transient detail rendering failures."""

    try:
        attempts = int(config.get("detail_retry_attempts", 2))
    except (TypeError, ValueError):
        attempts = 2
    try:
        delay_ms = int(config.get("detail_retry_delay_ms", 500))
    except (TypeError, ValueError):
        delay_ms = 500
    return max(0, min(attempts, 3)), max(0, min(delay_ms, 5_000))


def _detail_failure_budget(config: dict[str, Any]) -> int:
    """Return the bounded number of consecutive detail failures before aborting.

    A campaign can expose hundreds of rows while the detail host is serving a
    single anti-automation page. Retrying every row in that state wastes the
    whole worker deadline and produces no better evidence. The budget is a
    diagnostic circuit breaker only: an aborted pass remains partial and can
    never replace the last complete snapshot.
    """

    try:
        value = int(config.get("max_consecutive_detail_failures", 5))
    except (TypeError, ValueError):
        value = 5
    return max(1, min(value, 50))


def _detail_timeout_budget(config: dict[str, Any], outer_timeout_ms: int) -> int:
    """Bound one detail navigation independently from the whole capture.

    The outer worker deadline protects the process, while this shorter budget
    prevents a challenge page from consuming the full navigation timeout for
    every candidate before the circuit breaker can observe repeated failures.
    """

    default = min(max(1_000, int(outer_timeout_ms)), 15_000)
    try:
        value = int(config.get("detail_timeout_ms", default))
    except (TypeError, ValueError):
        value = default
    return max(1_000, min(value, max(1_000, int(outer_timeout_ms))))


def _detail_data_wait_budget(config: dict[str, Any], detail_timeout_ms: int) -> int:
    """Bound the post-navigation wait for the official detail payload.

    Zhaopin can commit detail HTML before its server-rendered initial-data
    script is attached. A short, separately bounded poll handles that normal
    rendering race without extending the per-detail navigation timeout.
    """

    timeout = max(250, int(detail_timeout_ms))
    default = min(3_000, max(250, timeout // 2))
    try:
        value = int(config.get("detail_data_wait_ms", default))
    except (TypeError, ValueError):
        value = default
    return max(250, min(value, timeout))


def _wait_for_initial_data(page: Any, wait_ms: int) -> str:
    """Poll bounded page content until the official initial-data marker appears."""

    deadline = time.monotonic() + max(250, int(wait_ms)) / 1000
    html = ""
    while True:
        html = page.content()
        if "window.__INITIAL_DATA__" in html:
            return html
        remaining_ms = int((deadline - time.monotonic()) * 1000)
        if remaining_ms <= 0:
            return html
        page.wait_for_timeout(min(250, remaining_ms))


def _is_access_limited_detail_error(error: BaseException) -> bool:
    """Recognize deterministic challenge/access failures for fast fail-closed handling."""

    message = str(error).lower()
    markers = (
        "window.__initial_data__",
        "captcha",
        "challenge",
        "anti-automation",
        "access limited",
        "http 403",
        "http 429",
        "robots",
        "验证码",
        "访问受限",
    )
    return any(marker in message for marker in markers)


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


def _detail_row_from_parsed(parsed: dict[str, Any]) -> dict[str, Any]:
    """Convert one parsed Zhaopin detail into the capture row contract."""

    return {
        "external_id": parsed["job_number"],
        "title": parsed["title"],
        "employer": parsed["employer"],
        "major": parsed["major_text"],
        "degree": parsed["degree"],
        "location": parsed["location"],
        "headcount": parsed["quantity"],
        "deadline": parsed["deadline"],
        "published_date": parsed.get("published"),
        "detail_url": parsed["detail_url"],
        "evidence_url": parsed["detail_url"],
        "description": parsed["description"],
        "field_evidence": parsed["evidence"],
    }


def _load_partial_capture_for_retry(path: Path | str) -> dict[str, Any]:
    """Load only a complete-list partial capture for targeted detail retry.

    The diagnostic must contain every successful row from the same listing
    pass.  A generic failure file with no pagination proof is rejected because
    merging it could make an older success look current.
    """

    capture_path = Path(path)
    try:
        payload = json.loads(capture_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BrowserCaptureError(f"cannot read CNOOC retry capture: {capture_path}") from error
    if not isinstance(payload, dict) or str(payload.get("status") or "") != "partial":
        raise BrowserCaptureError("CNOOC retry requires a partial capture")
    scan = payload.get("scan")
    rows = payload.get("rows")
    failures = payload.get("failure_records")
    if not isinstance(scan, dict) or not isinstance(rows, list) or not isinstance(failures, list):
        raise BrowserCaptureError("CNOOC partial capture is missing scan, rows or failures")
    if not bool(scan.get("pagination_complete")):
        raise BrowserCaptureError("CNOOC retry requires complete listing pagination")
    try:
        discovered = int(scan.get("detail_discovered"))
        succeeded = int(scan.get("detail_succeeded"))
        failed = int(scan.get("detail_failed"))
    except (TypeError, ValueError) as error:
        raise BrowserCaptureError("CNOOC retry scan metrics must be integers") from error
    detail_failures = [item for item in failures if isinstance(item, dict) and str(item.get("detail_url") or "").strip()]
    if discovered <= 0 or succeeded != len(rows) or failed != len(detail_failures):
        raise BrowserCaptureError("CNOOC retry capture has inconsistent detail metrics")
    if succeeded + failed != discovered:
        raise BrowserCaptureError("CNOOC retry requires every discovered detail to be accounted for")
    if len({str(row.get("external_id") or "") for row in rows}) != len(rows):
        raise BrowserCaptureError("CNOOC retry capture contains duplicate successful rows")
    return payload


def merge_cnooc_partial_capture(
    payload: dict[str, Any],
    repaired_rows: list[dict[str, Any]],
    *,
    captured_at: str | None = None,
) -> dict[str, Any]:
    """Atomically turn a complete-list partial capture into a success.

    Only rows from ``payload`` and the explicitly repaired failure URLs are
    used.  Missing repairs keep the result partial, so a retry can never
    silently replace the canonical snapshot with an incomplete batch.
    """

    partial = _load_partial_capture_for_retry_from_payload(payload)
    failures = [
        dict(item)
        for item in partial["failure_records"]
        if isinstance(item, dict) and str(item.get("detail_url") or "").strip()
    ]
    expected_urls = {str(item["detail_url"]).strip() for item in failures}
    repaired_by_url: dict[str, dict[str, Any]] = {}
    for row in repaired_rows:
        if not isinstance(row, dict):
            raise BrowserCaptureError("repaired CNOOC row must be an object")
        detail_url = str(row.get("detail_url") or "").strip()
        if not detail_url or detail_url not in expected_urls:
            raise BrowserCaptureError("repaired CNOOC row does not match a failed detail URL")
        if detail_url in repaired_by_url:
            raise BrowserCaptureError(f"duplicate repaired CNOOC detail: {detail_url}")
        if not str(row.get("external_id") or "").strip():
            raise BrowserCaptureError("repaired CNOOC row is missing external_id")
        repaired_by_url[detail_url] = dict(row)

    remaining = [item for item in failures if str(item["detail_url"]) not in repaired_by_url]
    rows = [dict(row) for row in partial["rows"]] + list(repaired_by_url.values())
    external_ids = [str(row.get("external_id") or "") for row in rows]
    if len(set(external_ids)) != len(external_ids):
        raise BrowserCaptureError("CNOOC retry would create duplicate external IDs")
    scan = dict(partial["scan"])
    scan["detail_succeeded"] = len(rows)
    scan["detail_failed"] = len(remaining)
    scan["rows_exported"] = len(rows)
    scan["failure_records"] = remaining
    result = dict(partial)
    result["captured_at"] = str(captured_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
    result["rows"] = rows
    result["failure_records"] = remaining
    result["scan"] = scan
    result["status"] = "success" if not remaining else "partial"
    result["retry"] = {
        "attempted": len(repaired_rows),
        "repaired": len(repaired_by_url),
        "remaining": len(remaining),
        "source_capture": "same_complete_listing_pass",
    }
    return result


def _load_partial_capture_for_retry_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate an already loaded partial payload using the same retry rules."""

    if not isinstance(payload, dict) or str(payload.get("status") or "") != "partial":
        raise BrowserCaptureError("CNOOC retry requires a partial capture")
    scan = payload.get("scan")
    rows = payload.get("rows")
    failures = payload.get("failure_records")
    if not isinstance(scan, dict) or not isinstance(rows, list) or not isinstance(failures, list):
        raise BrowserCaptureError("CNOOC partial capture is missing scan, rows or failures")
    if not bool(scan.get("pagination_complete")):
        raise BrowserCaptureError("CNOOC retry requires complete listing pagination")
    try:
        discovered = int(scan.get("detail_discovered"))
        succeeded = int(scan.get("detail_succeeded"))
        failed = int(scan.get("detail_failed"))
    except (TypeError, ValueError) as error:
        raise BrowserCaptureError("CNOOC retry scan metrics must be integers") from error
    detail_failures = [item for item in failures if isinstance(item, dict) and str(item.get("detail_url") or "").strip()]
    if discovered <= 0 or succeeded != len(rows) or failed != len(detail_failures) or succeeded + failed != discovered:
        raise BrowserCaptureError("CNOOC retry capture has inconsistent detail metrics")
    if len({str(row.get("external_id") or "") for row in rows}) != len(rows):
        raise BrowserCaptureError("CNOOC retry capture contains duplicate successful rows")
    return payload


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
    robots_cache: dict[str, Any] = {}
    _robots_permit(listing_url, user_agent=user_agent, robots_cache=robots_cache)
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
    api_allowed_hosts = {
        str(item).lower().rstrip(".")
        for item in config.get("api_allowed_hosts", ["fe.zhaopin.com"])
        if str(item).strip()
    }
    api_hostname = (urlparse(api_url).hostname or "").lower().rstrip(".")
    if api_hostname not in api_allowed_hosts:
        raise BrowserCaptureError(
            f"CNOOC API host is not allowlisted: {api_hostname}"
        )
    _robots_permit(api_url, user_agent=user_agent, robots_cache=robots_cache)
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
    transport_mode = str(config.get("transport_mode") or "environment")
    transport_client = str(config.get("http_client") or "requests")
    api_session = create_session(mode=transport_mode, client=transport_client)
    try:
        for page_index in range(1, max_pages + 1):
            response = api_session.post(api_url, json={"orgNumbers": [campaign_id], "jobSource": 2, "pageIndex": page_index, "pageSize": page_size, "orgDepartmentIds": [], "workRegionIds": "", "jobTypes": "", "priorityMajors": "", "customTags": ""}, headers=headers, timeout=30)
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
    finally:
        api_session.close()
    pagination_complete = pages == total_pages
    max_items = min(max(1, int(config.get("max_items", 500))), 2_000)
    candidates = candidates[:max_items]

    rows: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    retry_attempts, retry_delay_ms = _detail_retry_budget(config)
    detail_timeout_ms = _detail_timeout_budget(config, timeout_ms)
    detail_data_wait_ms = _detail_data_wait_budget(config, detail_timeout_ms)
    max_consecutive_failures = _detail_failure_budget(config)
    consecutive_failures = 0
    detail_scan_aborted = False
    detail_abort_reason = ""
    try:
        with sync_playwright() as playwright:
            cdp_url = str(config.get("cdp_url") or "").strip()
            if cdp_url:
                browser = playwright.chromium.connect_over_cdp(_resolve_cdp_websocket(cdp_url))
                context = browser.contexts[0] if browser.contexts else browser.new_context(user_agent=user_agent)
                owned = False
            else:
                browser = launch_playwright_browser(playwright, config)
                context = browser.new_context(user_agent=user_agent)
                owned = True
            try:
                for candidate in candidates:
                    detail_url = _official_url(candidate["url"], "CNOOC detail URL", hosts)
                    last_error: Exception | None = None
                    for attempt in range(retry_attempts + 1):
                        page = context.new_page()
                        try:
                            # Anti-automation responses can keep subresources open
                            # indefinitely while still returning HTTP 200.  The
                            # server-rendered detail only needs the committed HTML;
                            # a short bounded poll distinguishes it from a
                            # challenge page without blocking the whole batch.
                            # Listing permission does not imply that every
                            # detail path is crawlable. Check the exact URL
                            # before opening it and fail closed when the
                            # publisher disallows this path.
                            _robots_permit(
                                detail_url,
                                user_agent=user_agent,
                                robots_cache=robots_cache,
                            )
                            response = page.goto(
                                detail_url,
                                wait_until="commit",
                                timeout=detail_timeout_ms,
                            )
                            if response is not None and response.status >= 400:
                                raise BrowserCaptureError(f"CNOOC detail returned HTTP {response.status}")
                            html = _wait_for_initial_data(page, detail_data_wait_ms)
                            parsed = parse_zhaopin_detail_html(html, detail_url=page.url, expected_job_number=candidate["job_number"] or None, allowed_hosts=hosts)
                            rows.append(_detail_row_from_parsed(parsed))
                            last_error = None
                            break
                        except Exception as error:
                            last_error = error
                            # A challenge/robots response is normally
                            # deterministic for the whole campaign. Retrying
                            # the same URL only multiplies browser time and
                            # can keep the worker alive until its hard
                            # deadline. One such failure is enough to stop
                            # retries for this row; the pass-level circuit
                            # breaker below decides when to abort the scan.
                            if _is_access_limited_detail_error(error):
                                break
                            if attempt < retry_attempts and retry_delay_ms:
                                time.sleep(retry_delay_ms / 1000)
                        finally:
                            try:
                                page.close()
                            except Exception:
                                # The capture is already failed for this row;
                                # never mask the bounded diagnostic with a
                                # renderer cleanup error.
                                pass
                    if last_error is not None:
                        failures.append({"detail_url": detail_url, "title": candidate["title"], "reason": str(last_error)[:500]})
                        consecutive_failures += 1
                        if consecutive_failures >= max_consecutive_failures:
                            detail_scan_aborted = True
                            detail_abort_reason = (
                                "aborted after "
                                f"{consecutive_failures} consecutive detail failures"
                            )
                            break
                    else:
                        consecutive_failures = 0
                if detail_scan_aborted:
                    failures.append(
                        {
                            "detail_url": "",
                            "title": "",
                            "reason": detail_abort_reason,
                        }
                    )
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
            "detail_failed": sum(1 for item in failures if item.get("detail_url")),
            "rows_exported": len(rows),
            "failure_records": failures,
            "detail_scan_aborted": detail_scan_aborted,
            "detail_abort_reason": detail_abort_reason,
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


def run_cnooc_detail_retry(
    *,
    partial_capture: Path | str,
    output: Path | str,
    config: dict[str, Any],
    allowed_hosts: list[str] | set[str],
    user_agent: str,
    timeout_ms: int = 45_000,
) -> dict[str, Any]:
    """Retry only failed details from a complete-list partial capture.

    This is intentionally separate from the full campaign scan. A transient
    renderer failure therefore costs one detail navigation, while the merge
    still requires all rows from the original complete listing pass.
    """

    payload = _load_partial_capture_for_retry(partial_capture)
    hosts = {str(item).lower().rstrip(".") for item in allowed_hosts if str(item).strip()}
    if not hosts:
        raise BrowserCaptureError("CNOOC retry requires an official host allowlist")
    failures = [
        item
        for item in payload["failure_records"]
        if isinstance(item, dict) and str(item.get("detail_url") or "").strip()
    ]
    max_targets = max(1, min(int(config.get("retry_max_targets", 10)), 50))
    if len(failures) > max_targets:
        raise BrowserCaptureError(
            f"CNOOC retry target count exceeds safety limit ({len(failures)} > {max_targets})"
        )
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise BrowserCaptureError("Playwright is not installed in the CNOOC browser worker") from error

    detail_timeout_ms = _detail_timeout_budget(config, timeout_ms)
    detail_data_wait_ms = _detail_data_wait_budget(config, detail_timeout_ms)
    repaired: list[dict[str, Any]] = []
    retry_failures: list[dict[str, str]] = []
    robots_cache: dict[str, Any] = {}
    try:
        with sync_playwright() as playwright:
            cdp_url = str(config.get("cdp_url") or "").strip()
            if cdp_url:
                browser = playwright.chromium.connect_over_cdp(_resolve_cdp_websocket(cdp_url))
                context = browser.contexts[0] if browser.contexts else browser.new_context(user_agent=user_agent)
                owned = False
            else:
                browser = launch_playwright_browser(playwright, config)
                context = browser.new_context(user_agent=user_agent)
                owned = True
            try:
                for failure in failures:
                    detail_url = _official_url(failure["detail_url"], "CNOOC retry detail URL", hosts)
                    page = context.new_page()
                    try:
                        _robots_permit(detail_url, user_agent=user_agent, robots_cache=robots_cache)
                        response = page.goto(detail_url, wait_until="commit", timeout=detail_timeout_ms)
                        if response is not None and response.status >= 400:
                            raise BrowserCaptureError(f"CNOOC detail returned HTTP {response.status}")
                        html = _wait_for_initial_data(page, detail_data_wait_ms)
                        job_number = detail_url.rstrip("/").rsplit("/", 1)[-1] or None
                        parsed = parse_zhaopin_detail_html(
                            html,
                            detail_url=page.url,
                            expected_job_number=job_number,
                            allowed_hosts=hosts,
                        )
                        repaired.append(_detail_row_from_parsed(parsed))
                    except Exception as error:
                        retry_failures.append({
                            "detail_url": detail_url,
                            "title": str(failure.get("title") or ""),
                            "reason": str(error)[:500],
                        })
                    finally:
                        try:
                            page.close()
                        except Exception:
                            pass
            finally:
                if owned:
                    browser.close()
    except PlaywrightTimeoutError as error:
        raise BrowserCaptureError(f"CNOOC detail retry timed out: {error}") from error
    except BrowserCaptureError:
        raise
    except Exception as error:
        raise BrowserCaptureError(f"CNOOC detail retry failed: {error}") from error

    # Keep failed retries attached to the original URL. The pure merge helper
    # sees only repaired rows, so an unresolved failure remains partial.
    merged = merge_cnooc_partial_capture(payload, repaired)
    if retry_failures:
        merged["failure_records"] = retry_failures
        merged["scan"] = {
            **merged["scan"],
            "detail_failed": len(merged["failure_records"]),
            "rows_exported": len(merged["rows"]),
        }
        merged["status"] = "partial"
        merged["retry"]["failed"] = len(retry_failures)
    return persist_cnooc_browser_capture(
        output=output,
        payload=merged,
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
    "merge_cnooc_partial_capture",
    "persist_cnooc_browser_capture",
    "run_cnooc_detail_retry",
    "repair_cnooc_detail_requirement_evidence",
    "run_cnooc_browser_capture",
    "write_cnooc_capture_failure",
]
