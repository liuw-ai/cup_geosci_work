"""Compliant browser runner for the CNPC list -> detail -> position flow.

This module is deliberately separate from the web worker.  It needs a browser
runtime and is therefore run by an optional browser worker, which writes a
versioned capture under APP_DATA_DIR.  The normal worker consumes that capture
only after :mod:`job_hub.cnpc_browser_capture` validates it.
"""

from __future__ import annotations

import json
import re
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

from job_hub.browser_capture import BrowserCaptureError, _robots_permit
from job_hub.cnpc_browser_capture import CnpcJobCaptureError
from job_hub.contracts import is_http_url


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _required(value: Any, field: str) -> str:
    result = _text(value)
    if not result:
        raise BrowserCaptureError(f"CNPC rendered detail is missing {field}")
    return result


def _official_url(value: str, hosts: set[str], field: str) -> str:
    if not is_http_url(value):
        raise BrowserCaptureError(f"{field} must be an HTTP(S) URL")
    host = (urlparse(value).hostname or "").lower().rstrip(".")
    if host not in hosts:
        raise BrowserCaptureError(f"{field} is outside the CNPC allowlist: {host}")
    return value


def _resolve_cdp_websocket(cdp_url: str) -> str:
    """Resolve headless-shell's websocket while preserving its Host quirk."""

    parsed = urlparse(cdp_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise BrowserCaptureError("CNPC_BROWSER_CDP_URL must be an HTTP(S) endpoint")
    response = requests.get(
        f"{cdp_url.rstrip('/')}/json/version",
        headers={"Host": "localhost"},
        timeout=10,
    )
    if not response.ok:
        raise BrowserCaptureError(
            f"headless-shell CDP version endpoint returned HTTP {response.status_code}"
        )
    try:
        websocket = str(response.json()["webSocketDebuggerUrl"])
    except (ValueError, KeyError, TypeError) as error:
        raise BrowserCaptureError("headless-shell CDP version response lacks websocket URL") from error
    websocket_parsed = urlparse(websocket)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    host = parsed.hostname or ""
    resolved_host = socket.gethostbyname(host)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return urlunparse(
        (
            scheme,
            f"{resolved_host}:{port}",
            websocket_parsed.path,
            websocket_parsed.params,
            websocket_parsed.query,
            websocket_parsed.fragment,
        )
    )


def _announcement_id(url: str, fallback: str) -> str:
    query = parse_qs(urlparse(url).query)
    return _text(query.get("id", [""])[0]) or fallback


def extract_cnpc_announcements(
    html: str,
    *,
    page_url: str,
    allowed_hosts: set[str],
    config: dict[str, Any],
) -> list[dict[str, str]]:
    """Extract announcement links from one rendered CNPC listing page.

    The public page has changed its surrounding layout over time, so the
    stable contract is the official ``recruitInfoshow.html?id=...`` link.  A
    configurable selector may narrow it, but arbitrary external links are
    never accepted.
    """

    selector = str(config.get("announcement_link_selector") or "a[href*='recruitInfoshow']")
    soup = BeautifulSoup(html, "html.parser")
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, link in enumerate(soup.select(selector), start=1):
        href = str(link.get("href") or "").strip()
        if not href:
            continue
        detail_url = _official_url(urljoin(page_url, href), allowed_hosts, "announcement detail URL")
        if "recruitInfoshow" not in detail_url:
            continue
        external_id = _announcement_id(detail_url, f"announcement-{index}")
        if external_id in seen:
            continue
        seen.add(external_id)
        parent = link.parent or link
        parent_text = _text(parent.get_text(" ", strip=True))
        title = _required(link.get_text(" ", strip=True), "announcement title")
        deadline_match = re.search(
            r"(?:报名截止|截止时间|截止日期)[：:\s]*([0-9]{4}[年/-][0-9]{1,2}[月/-][0-9]{1,2}日?)",
            parent_text,
        )
        rows.append(
            {
                "external_id": external_id,
                "title": title,
                "employer": _text(config.get("default_employer")) or "中国石油天然气集团有限公司",
                "detail_url": detail_url,
                "deadline": _text(
                    deadline_match.group(1)
                    if deadline_match
                    else config.get("default_deadline")
                )
                or "未从列表读取（待详情核验）",
                "detail_status": "not_observed",
                "observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "reason": "待打开官方岗位详情页",
            }
        )
    return rows


def _normalized_header(value: str) -> str:
    return re.sub(r"[\s:：()（）/]+", "", _text(value)).lower()


def _column_index(headers: list[str], aliases: tuple[str, ...]) -> int | None:
    normalized = [_normalized_header(item) for item in headers]
    for alias in aliases:
        key = _normalized_header(alias)
        for index, header in enumerate(normalized):
            if key in header or header in key:
                return index
    return None


def extract_cnpc_detail_jobs(
    html: str,
    *,
    detail_url: str,
    announcement: dict[str, str],
    allowed_hosts: set[str],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    """Extract complete job rows from official detail tables.

    Headers are matched by Chinese field names instead of fixed column
    positions.  Every exported row must contain title, major, degree, place
    and deadline; missing fields fail the announcement rather than producing a
    partial public row.
    """

    detail_url = _official_url(detail_url, allowed_hosts, "detail URL")
    soup = BeautifulSoup(html, "html.parser")
    table_selector = str(config.get("detail_table_selector") or "table")
    jobs: list[dict[str, Any]] = []
    table_seen = 0
    aliases = {
        "title": ("岗位名称", "招聘岗位", "职位名称", "岗位"),
        "major": ("专业要求", "所需专业", "专业"),
        "degree": ("学历要求", "学历"),
        "location": ("工作地点", "工作地区", "地点"),
        "deadline": ("报名截止", "截止日期", "截止时间"),
        "headcount": ("招聘人数", "人数", "需求人数"),
    }
    for table in soup.select(table_selector):
        rows = table.select("tr")
        if not rows:
            continue
        headers = [_text(cell.get_text(" ", strip=True)) for cell in rows[0].select("th,td")]
        indices = {key: _column_index(headers, values) for key, values in aliases.items()}
        required_keys = ("title", "major", "degree", "location", "deadline")
        if any(indices[key] is None for key in required_keys):
            continue
        table_seen += 1
        for row_index, row in enumerate(rows[1:], start=1):
            cells = [_text(cell.get_text(" ", strip=True)) for cell in row.select("td,th")]
            if not cells or len(cells) < len(headers):
                continue
            values = {key: cells[index] for key, index in indices.items() if index is not None}
            if not all(_text(values.get(key)) for key in required_keys):
                raise BrowserCaptureError(
                    f"CNPC detail row {row_index} is missing a required field"
                )
            external_id = _text(values.get("title"))
            code_match = re.search(r"[（(]([A-Za-z0-9-]{5,})[）)]", external_id)
            if code_match:
                external_id = code_match.group(1)
            external_id = f"{announcement['external_id']}:{external_id}"
            evidence = {
                "岗位": values["title"],
                "专业范围": values["major"],
                "学历要求": values["degree"],
                "工作地点": values["location"],
                "报名截止": values["deadline"],
            }
            if values.get("headcount"):
                evidence["招聘人数"] = values["headcount"]
            jobs.append(
                {
                    "external_id": external_id,
                    "announcement_id": announcement["external_id"],
                    "title": values["title"],
                    "employer": announcement["employer"],
                    "detail_url": detail_url,
                    "evidence_url": detail_url,
                    "major": values["major"],
                    "degree": values["degree"],
                    "location": values["location"],
                    "deadline": values["deadline"],
                    "headcount": values.get("headcount", ""),
                    "observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "field_evidence": evidence,
                }
            )
    if not jobs and table_seen == 0:
        raise BrowserCaptureError("CNPC detail page has no table with required job fields")
    return jobs


def run_cnpc_browser_capture(
    *,
    url: str,
    output: Path | str,
    config: dict[str, Any],
    allowed_hosts: list[str] | set[str],
    user_agent: str,
    timeout_ms: int = 45_000,
) -> dict[str, Any]:
    """Run the two-level capture in a public, robots-permitted browser session."""

    hosts = {str(host).strip().lower().rstrip(".") for host in allowed_hosts if str(host).strip()}
    target_url = _official_url(url, hosts, "browser URL")
    _robots_permit(target_url, user_agent=user_agent)
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise BrowserCaptureError(
            "Playwright is not installed; install the browser worker extra before enabling CNPC capture"
        ) from error

    announcements: list[dict[str, str]] = []
    seen: set[str] = set()
    pages_scanned = 0
    pagination_complete = True
    jobs: list[dict[str, Any]] = []
    failed_details = 0
    try:
        with sync_playwright() as playwright:
            cdp_url = _text(config.get("cdp_url"))
            if cdp_url:
                browser = playwright.chromium.connect_over_cdp(
                    _resolve_cdp_websocket(cdp_url)
                )
                context = browser.contexts[0] if browser.contexts else browser.new_context(
                    user_agent=user_agent
                )
                page = context.new_page()
            else:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(user_agent=user_agent)
            response = page.goto(target_url, wait_until="networkidle", timeout=timeout_ms)
            if response is not None and response.status >= 400:
                raise BrowserCaptureError(
                    f"CNPC listing returned HTTP {response.status}; capture is access-limited"
                )
            max_pages = max(1, int(config.get("max_pages", 30)))
            next_selector = str(config.get("next_selector") or "").strip()
            for _ in range(max_pages):
                pages_scanned += 1
                for item in extract_cnpc_announcements(
                    page.content(), page_url=page.url, allowed_hosts=hosts, config=config
                ):
                    if item["external_id"] not in seen:
                        seen.add(item["external_id"])
                        announcements.append(item)
                if not next_selector:
                    break
                next_button = page.locator(next_selector).first
                if next_button.count() == 0 or next_button.get_attribute("disabled") is not None:
                    break
                if pages_scanned >= max_pages:
                    pagination_complete = False
                    break
                next_button.click()
                page.wait_for_load_state("networkidle", timeout=timeout_ms)

            for announcement in announcements:
                try:
                    response = page.goto(
                        announcement["detail_url"],
                        wait_until="networkidle",
                        timeout=timeout_ms,
                    )
                    if response is not None and response.status >= 400:
                        raise BrowserCaptureError(
                            f"CNPC detail returned HTTP {response.status}"
                        )
                    detail_jobs = extract_cnpc_detail_jobs(
                        page.content(),
                        detail_url=page.url,
                        announcement=announcement,
                        allowed_hosts=hosts,
                        config=config,
                    )
                    jobs.extend(detail_jobs)
                    announcement["detail_status"] = "fields_verified"
                    announcement["reason"] = "岗位表字段完整并保留官方详情证据"
                    if announcement["deadline"].startswith("未从列表读取"):
                        announcement["deadline"] = str(detail_jobs[0]["deadline"])
                except Exception as error:
                    failed_details += 1
                    announcement["detail_status"] = "official_detail_api_degraded"
                    announcement["reason"] = str(error)[:500]
            browser.close()
    except PlaywrightTimeoutError as error:
        raise BrowserCaptureError(f"CNPC browser page timed out: {error}") from error
    except BrowserCaptureError:
        raise
    except Exception as error:
        raise BrowserCaptureError(f"CNPC browser capture failed: {error}") from error

    status = "success" if pagination_complete and failed_details == 0 else "partial"
    payload = {
        "version": 1,
        "status": status,
        "platform_url": target_url,
        "captured_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "scan": {
            "pages_scanned": pages_scanned,
            "pagination_complete": pagination_complete,
            "announcements_discovered": len(announcements),
            "announcements_targeted": len(announcements),
            "failed_announcement_details": failed_details,
            "jobs_discovered": len(jobs),
            "jobs_exported": len(jobs),
        },
        "announcements": announcements,
        "jobs": jobs,
    }
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(destination)
    return payload


def write_cnpc_capture_failure(
    *, output: Path | str, platform_url: str, status: str, reason: str
) -> dict[str, Any]:
    """Persist an access-limited/parse-failed observation atomically.

    Replacing an older successful artifact prevents a stale zero-row capture
    from being mistaken for today's result.  The normal source adapter rejects
    this status for student publication but keeps it visible in operator audit.
    """

    if status not in {"access_limited", "parse_failed", "partial"}:
        raise ValueError("failure capture status must be access_limited, parse_failed or partial")
    payload: dict[str, Any] = {
        "version": 1,
        "status": status,
        "platform_url": platform_url,
        "captured_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "scan": {
            "pages_scanned": 0,
            "pagination_complete": False,
            "announcements_discovered": 0,
            "announcements_targeted": 0,
            "failed_announcement_details": 0,
            "jobs_discovered": 0,
            "jobs_exported": 0,
            "failure_reason": str(reason)[:1000],
        },
        "announcements": [],
        "jobs": [],
    }
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(destination)
    return payload
