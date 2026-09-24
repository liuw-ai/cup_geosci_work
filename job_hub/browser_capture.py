"""Validation and optional execution helpers for public dynamic portals.

Dynamic recruitment portals expose rows only after JavaScript has rendered the
page.  The capture file is therefore a small, auditable hand-off between a
server browser and the normal publication pipeline.  It is deliberately
strict: incomplete scans, stale captures and non-official detail URLs are
rejected instead of being presented as a successful empty scan.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from job_hub.contracts import is_http_url


CAPTURE_STATUSES = frozenset({"success", "access_limited", "parse_failed", "partial"})
REQUIRED_ROW_FIELDS = ("external_id", "title", "employer", "degree", "major", "location", "deadline")


class BrowserCaptureError(ValueError):
    """Raised when a dynamic capture cannot be safely consumed."""


def _text(value: Any, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise BrowserCaptureError(f"{field} must be non-empty")
    return result


def _url(value: Any, field: str, allowed_hosts: set[str]) -> str:
    result = _text(value, field)
    if not is_http_url(result):
        raise BrowserCaptureError(f"{field} must be an HTTP(S) URL")
    hostname = (urlparse(result).hostname or "").lower()
    if allowed_hosts and hostname not in allowed_hosts:
        raise BrowserCaptureError(f"{field} host is not allowlisted: {hostname}")
    return result


def _captured_at(value: Any) -> datetime:
    text = _text(value, "captured_at").replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as error:
        raise BrowserCaptureError("captured_at must be ISO-8601") from error
    if parsed.tzinfo is None:
        raise BrowserCaptureError("captured_at must include a timezone")
    return parsed.astimezone(timezone.utc)


def load_browser_capture(
    path: Path | str,
    *,
    allowed_hosts: list[str] | set[str],
    max_age_hours: float | None = None,
    require_complete_scan: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Load a browser capture and enforce its freshness and scan contract."""

    capture_path = Path(path)
    try:
        payload = json.loads(capture_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise BrowserCaptureError(f"browser capture file is missing: {capture_path}") from error
    except (OSError, json.JSONDecodeError) as error:
        raise BrowserCaptureError(f"browser capture file cannot be read: {capture_path}") from error
    if not isinstance(payload, dict):
        raise BrowserCaptureError("browser capture must be a JSON object")

    hosts = {str(host).strip().lower() for host in allowed_hosts if str(host).strip()}
    if not hosts:
        raise BrowserCaptureError("allowed_hosts must not be empty")
    status = _text(payload.get("status"), "status")
    if status not in CAPTURE_STATUSES:
        raise BrowserCaptureError(f"unsupported browser capture status: {status}")
    if status != "success":
        raise BrowserCaptureError(f"browser capture is not publishable: {status}")
    platform_url = _url(payload.get("platform_url"), "platform_url", hosts)
    captured = _captured_at(payload.get("captured_at"))
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    age_hours = (current - captured).total_seconds() / 3600
    if age_hours < -1:
        raise BrowserCaptureError("captured_at is in the future")
    if max_age_hours is not None and age_hours > float(max_age_hours):
        raise BrowserCaptureError(
            f"browser capture is stale ({age_hours:.1f}h > {float(max_age_hours):.1f}h)"
        )

    scan = payload.get("scan")
    if not isinstance(scan, dict):
        raise BrowserCaptureError("browser capture scan metrics are required")
    try:
        discovered = int(scan.get("rows_discovered"))
        exported = int(scan.get("rows_exported"))
        failed = int(scan.get("failed_rows", 0))
    except (TypeError, ValueError) as error:
        raise BrowserCaptureError("scan row metrics must be integers") from error
    pagination_complete = bool(scan.get("pagination_complete"))
    if min(discovered, exported, failed) < 0 or exported > discovered:
        raise BrowserCaptureError("scan row metrics are inconsistent")
    if require_complete_scan and (not pagination_complete or failed or exported != discovered):
        raise BrowserCaptureError("browser capture is incomplete; it cannot publish rows")

    rows = payload.get("rows")
    if not isinstance(rows, list) or not rows:
        raise BrowserCaptureError("browser capture rows must be a non-empty list")
    if exported != len(rows):
        raise BrowserCaptureError("rows_exported does not match rows length")
    normalized_rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(rows, start=1):
        if not isinstance(raw, dict):
            raise BrowserCaptureError(f"browser capture row {index} must be an object")
        item = dict(raw)
        for field in REQUIRED_ROW_FIELDS:
            item[field] = _text(item.get(field), f"row {index}.{field}")
        if item["external_id"] in seen:
            raise BrowserCaptureError(f"duplicate browser capture external_id: {item['external_id']}")
        seen.add(item["external_id"])
        detail_url = _url(item.get("detail_url"), f"row {index}.detail_url", hosts)
        evidence_url = _url(
            item.get("evidence_url") or detail_url,
            f"row {index}.evidence_url",
            hosts,
        )
        evidence = item.get("field_evidence")
        if not isinstance(evidence, dict) or not evidence:
            raise BrowserCaptureError(f"row {index}.field_evidence is required")
        item["detail_url"] = detail_url
        item["evidence_url"] = evidence_url
        item["field_evidence"] = {str(key): _text(value, f"row {index}.field_evidence.{key}") for key, value in evidence.items()}
        normalized_rows.append(item)

    return {
        **payload,
        "status": status,
        "platform_url": platform_url,
        "captured_at": captured.isoformat().replace("+00:00", "Z"),
        "capture_age_hours": round(max(0.0, age_hours), 3),
        "scan": {**scan, "rows_discovered": discovered, "rows_exported": exported, "failed_rows": failed},
        "rows": normalized_rows,
    }


def browser_capture_summary(payload: dict[str, Any]) -> dict[str, Any]:
    """Return operator-facing metrics without exposing row content."""

    scan = payload.get("scan") or {}
    return {
        "status": payload.get("status"),
        "captured_at": payload.get("captured_at"),
        "capture_age_hours": payload.get("capture_age_hours"),
        "rows_discovered": int(scan.get("rows_discovered", 0)),
        "rows_exported": int(scan.get("rows_exported", 0)),
        "failed_rows": int(scan.get("failed_rows", 0)),
        "pagination_complete": bool(scan.get("pagination_complete")),
        "rows_with_evidence": sum(1 for row in payload.get("rows", []) if row.get("field_evidence")),
    }


def _robots_permit(url: str, *, user_agent: str) -> None:
    """Require a readable, permissive robots policy before launching a browser."""

    parsed = urlparse(url)
    root = f"{parsed.scheme}://{parsed.netloc}"
    robots_url = urljoin(root, "/robots.txt")
    try:
        response = requests.get(robots_url, headers={"User-Agent": user_agent}, timeout=15)
    except requests.RequestException as error:
        raise BrowserCaptureError(f"robots.txt cannot be verified: {error}") from error
    if response.status_code == 404:
        return
    if not response.ok:
        raise BrowserCaptureError(f"robots.txt returned HTTP {response.status_code}")
    from urllib.robotparser import RobotFileParser

    parser = RobotFileParser()
    parser.parse(response.text.splitlines())
    if not parser.can_fetch(user_agent, url):
        raise BrowserCaptureError("robots.txt does not permit browser capture")


def _field_text(row: Any, selector: str, field: str) -> str:
    node = row.select_one(selector)
    if node is None:
        raise BrowserCaptureError(f"rendered row is missing {field} selector: {selector}")
    value = node.get_text(" ", strip=True)
    return _text(value, f"rendered row {field}")


def extract_rendered_rows(
    html: str,
    *,
    page_url: str,
    config: dict[str, Any],
    allowed_hosts: set[str],
) -> list[dict[str, Any]]:
    """Extract one rendered page using an explicit, versioned selector map."""

    soup = BeautifulSoup(html, "html.parser")
    row_selector = _text(config.get("row_selector"), "row_selector")
    selectors = config.get("field_selectors")
    if not isinstance(selectors, dict):
        raise BrowserCaptureError("field_selectors must be an object")
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(soup.select(row_selector), start=1):
        item = {
            field: _field_text(row, str(selector), field)
            for field, selector in selectors.items()
        }
        for field in REQUIRED_ROW_FIELDS:
            if field == "external_id":
                continue
            item[field] = _text(item.get(field), f"rendered row {index}.{field}")
        id_attribute = str(config.get("id_attribute") or "").strip()
        external_id = row.get(id_attribute) if id_attribute else None
        item["external_id"] = str(external_id or f"row-{index}").strip()
        detail_selector = str(config.get("detail_selector") or "a[href]")
        detail = row.select_one(detail_selector)
        if detail is None or not detail.get("href"):
            raise BrowserCaptureError(f"rendered row {index} is missing detail URL")
        detail_url = urljoin(page_url, str(detail["href"]).strip())
        item["detail_url"] = _url(detail_url, f"rendered row {index}.detail_url", allowed_hosts)
        evidence_url = str(config.get("official_evidence_url") or item["detail_url"])
        item["evidence_url"] = _url(evidence_url, f"rendered row {index}.evidence_url", allowed_hosts)
        item["field_evidence"] = {
            "岗位": item["title"],
            "专业范围": item["major"],
            "学历要求": item["degree"],
            "工作地点": item["location"],
            "报名截止": item["deadline"],
            "来源页": page_url,
        }
        rows.append(item)
    return rows


def run_browser_capture(
    *,
    url: str,
    output: Path | str,
    config: dict[str, Any],
    allowed_hosts: list[str] | set[str],
    user_agent: str,
    timeout_ms: int = 45_000,
) -> dict[str, Any]:
    """Render a public page with Playwright and write a versioned capture.

    Playwright is optional so the regular web/worker image stays small.  A
    deployment that enables this source must install Playwright and Chromium
    in a separate browser-enabled worker image.
    """

    target_url = _url(url, "browser_url", {str(host).lower() for host in allowed_hosts})
    _robots_permit(target_url, user_agent=user_agent)
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise BrowserCaptureError(
            "Playwright is not installed; install the browser worker extra before enabling this source"
        ) from error

    hosts = {str(host).strip().lower() for host in allowed_hosts if str(host).strip()}
    rows: list[dict[str, Any]] = []
    pages_scanned = 0
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(user_agent=user_agent)
            page.goto(target_url, wait_until="networkidle", timeout=timeout_ms)
            max_pages = max(1, int(config.get("max_pages", 20)))
            next_selector = str(config.get("next_selector") or "").strip()
            pagination_complete = True
            for _ in range(max_pages):
                pages_scanned += 1
                rows.extend(
                    extract_rendered_rows(
                        page.content(), page_url=page.url, config=config, allowed_hosts=hosts
                    )
                )
                if not next_selector:
                    break
                next_button = page.locator(next_selector).first
                if awaitable_count(next_button) == 0:
                    break
                disabled = next_button.get_attribute("disabled")
                if disabled is not None:
                    break
                if pages_scanned >= max_pages:
                    pagination_complete = False
                    break
                next_button.click()
                page.wait_for_load_state("networkidle", timeout=timeout_ms)
            browser.close()
    except PlaywrightTimeoutError as error:
        raise BrowserCaptureError(f"browser page timed out: {error}") from error
    except BrowserCaptureError:
        raise
    except Exception as error:
        raise BrowserCaptureError(f"browser capture failed: {error}") from error

    if not rows:
        raise BrowserCaptureError("browser capture rendered no rows")
    payload = {
        "status": "success",
        "platform_url": target_url,
        "captured_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "scan": {
            "pages_scanned": pages_scanned,
            "pagination_complete": pagination_complete,
            "rows_discovered": len(rows),
            "rows_exported": len(rows),
            "failed_rows": 0,
        },
        "rows": rows,
    }
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(destination)
    return payload


def awaitable_count(locator: Any) -> int:
    """Synchronously read a Playwright locator count without hiding errors."""

    return int(locator.count())
