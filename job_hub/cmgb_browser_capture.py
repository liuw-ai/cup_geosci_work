"""Official browser capture contract for the CMGB/国聘 campus portal.

The 国聘 page is a JavaScript application: the list card is not the evidence
and a card can only be published after its official detail tab has been read.
This module keeps the browser concern separate from the normal HTTP collector.
It validates a capture manifest before any row reaches the publication gate.

The capture producer intentionally fails closed.  A blocked detail tab, a
missing page, or an incomplete pagination pass is recorded as ``partial`` or
``access_limited`` and never becomes an empty successful scan.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse

from job_hub.browser_capture import BrowserCaptureError, _robots_permit
from job_hub.cnpc_browser_runner import _resolve_cdp_websocket
from job_hub.contracts import is_http_url


CMGB_CAPTURE_STATUSES = frozenset({"success", "partial", "access_limited", "parse_failed"})
CMGB_DEFAULT_HOSTS = {"cmgb.iguopin.com", "www.iguopin.com", "iguopin.com", "www.cmgb.com.cn"}
CMGB_REQUIRED_ROW_FIELDS = (
    "external_id",
    "title",
    "employer",
    "major",
    "degree",
    "location",
    "headcount",
    "deadline",
    "detail_url",
    "evidence_url",
)
CMGB_REQUIRED_EVIDENCE_KEYS = (
    "岗位",
    "专业范围",
    "学历要求",
    "工作地点",
    "招聘人数",
    "报名截止",
    "官方详情链接",
)


class CmgbBrowserCaptureError(ValueError):
    """Raised when a CMGB capture is unsafe to publish."""


def _text(value: Any, field: str) -> str:
    result = " ".join(str(value or "").split()).strip()
    if not result:
        raise CmgbBrowserCaptureError(f"{field} must be non-empty")
    return result


def _int(value: Any, field: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as error:
        raise CmgbBrowserCaptureError(f"{field} must be an integer") from error
    if result < 0:
        raise CmgbBrowserCaptureError(f"{field} cannot be negative")
    return result


def _timestamp(value: Any, field: str = "captured_at") -> str:
    raw = _text(value, field).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as error:
        raise CmgbBrowserCaptureError(f"{field} must be ISO-8601") from error
    if parsed.tzinfo is None:
        raise CmgbBrowserCaptureError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _official_url(value: Any, field: str, hosts: set[str]) -> str:
    result = _text(value, field)
    if not is_http_url(result):
        raise CmgbBrowserCaptureError(f"{field} must be an HTTP(S) URL")
    host = (urlparse(result).hostname or "").lower().rstrip(".")
    if hosts and host not in hosts:
        raise CmgbBrowserCaptureError(f"{field} host is not allowlisted: {host}")
    return result


def _scan_metrics(payload: dict[str, Any], *, require_complete: bool) -> dict[str, Any]:
    scan = payload.get("scan")
    if not isinstance(scan, dict):
        raise CmgbBrowserCaptureError("scan metrics are required")
    required = (
        "pages_scanned",
        "pagination_complete",
        "rows_discovered",
        "rows_exported",
        "failed_rows",
        "detail_discovered",
        "detail_succeeded",
        "detail_failed",
    )
    normalized = {field: _int(scan.get(field), f"scan.{field}") for field in required if field != "pagination_complete"}
    if "pagination_complete" not in scan:
        raise CmgbBrowserCaptureError("scan.pagination_complete is required")
    normalized["pagination_complete"] = bool(scan["pagination_complete"])
    if normalized["rows_exported"] > normalized["rows_discovered"]:
        raise CmgbBrowserCaptureError("scan.rows_exported exceeds scan.rows_discovered")
    if normalized["detail_succeeded"] + normalized["detail_failed"] > normalized["detail_discovered"]:
        raise CmgbBrowserCaptureError("detail outcomes exceed detail_discovered")
    complete = (
        normalized["pagination_complete"]
        and normalized["failed_rows"] == 0
        and normalized["detail_failed"] == 0
        and normalized["detail_succeeded"] == normalized["detail_discovered"]
        and normalized["rows_exported"] == normalized["rows_discovered"]
    )
    if require_complete and not complete:
        raise CmgbBrowserCaptureError("CMGB capture is incomplete; publication is blocked")
    return {**scan, **normalized}


def load_cmgb_browser_capture(
    path: Path | str,
    *,
    allowed_hosts: Iterable[str] | None = None,
    max_age_hours: float | None = 30,
    require_complete_scan: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Load and validate one server-generated CMGB browser manifest."""

    capture_path = Path(path)
    try:
        payload = json.loads(capture_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CmgbBrowserCaptureError(f"cannot read CMGB capture: {capture_path}") from error
    if not isinstance(payload, dict):
        raise CmgbBrowserCaptureError("CMGB capture must be a JSON object")
    status = _text(payload.get("status"), "status")
    if status not in CMGB_CAPTURE_STATUSES:
        raise CmgbBrowserCaptureError(f"unsupported CMGB capture status: {status}")
    if status != "success":
        raise CmgbBrowserCaptureError(f"CMGB capture is not publishable: {status}")
    hosts = {
        str(host).strip().lower().rstrip(".")
        for host in (allowed_hosts or CMGB_DEFAULT_HOSTS)
        if str(host).strip()
    }
    if not hosts:
        raise CmgbBrowserCaptureError("allowed_hosts must not be empty")
    platform_url = _official_url(payload.get("platform_url"), "platform_url", hosts)
    captured_at = _timestamp(payload.get("captured_at"))
    captured = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    age_hours = (current - captured).total_seconds() / 3600
    if age_hours < -1:
        raise CmgbBrowserCaptureError("captured_at is in the future")
    if max_age_hours is not None and age_hours > float(max_age_hours):
        raise CmgbBrowserCaptureError(
            f"CMGB capture is stale ({age_hours:.1f}h > {float(max_age_hours):.1f}h)"
        )
    scan = _scan_metrics(payload, require_complete=require_complete_scan)
    rows = payload.get("rows")
    if not isinstance(rows, list) or not rows:
        raise CmgbBrowserCaptureError("CMGB capture rows must be a non-empty list")
    if scan["rows_exported"] != len(rows):
        raise CmgbBrowserCaptureError("scan.rows_exported does not match rows length")
    normalized_rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(rows, start=1):
        if not isinstance(raw, dict):
            raise CmgbBrowserCaptureError(f"row {index} must be an object")
        row = dict(raw)
        for field in CMGB_REQUIRED_ROW_FIELDS:
            row[field] = _text(row.get(field), f"row {index}.{field}")
        if row["external_id"] in seen:
            raise CmgbBrowserCaptureError(f"duplicate CMGB external_id: {row['external_id']}")
        seen.add(row["external_id"])
        row["detail_url"] = _official_url(row["detail_url"], f"row {index}.detail_url", hosts)
        row["evidence_url"] = _official_url(row["evidence_url"], f"row {index}.evidence_url", hosts)
        evidence = row.get("field_evidence")
        if not isinstance(evidence, dict):
            raise CmgbBrowserCaptureError(f"row {index}.field_evidence is required")
        for key in CMGB_REQUIRED_EVIDENCE_KEYS:
            if not str(evidence.get(key) or "").strip():
                raise CmgbBrowserCaptureError(f"row {index}.field_evidence.{key} is required")
        row["field_evidence"] = {
            str(key): _text(value, f"row {index}.field_evidence.{key}")
            for key, value in evidence.items()
        }
        normalized_rows.append(row)
    return {
        **payload,
        "status": status,
        "platform_url": platform_url,
        "captured_at": captured_at,
        "capture_age_hours": round(max(0.0, age_hours), 3),
        "scan": scan,
        "rows": normalized_rows,
    }


def _body_text(page: Any) -> str:
    return " ".join(str(page.locator("body").inner_text() or "").split()).strip()


def _first_text(container: Any, selectors: Iterable[str]) -> str:
    for selector in selectors:
        locator = container.locator(selector).first
        if locator.count():
            value = " ".join(str(locator.inner_text() or "").split()).strip()
            if value:
                return value
    return ""


def _label_value(text: str, labels: Iterable[str]) -> str:
    all_labels = (
        "专业要求",
        "专业范围",
        "需求专业",
        "专业",
        "最低学历",
        "学历要求",
        "学历",
        "工作地点",
        "工作城市",
        "工作地区",
        "地点",
        "招聘人数",
        "需求人数",
        "人数",
        "报名截止",
        "截止日期",
        "截止时间",
        "岗位名称",
        "职位名称",
        "招聘单位",
        "用人单位",
        "单位",
    )
    boundary = "|".join(re.escape(label) for label in all_labels)
    for label in labels:
        match = re.search(
            rf"{re.escape(label)}\s*[：:]\s*(.*?)(?=\s+(?:{boundary})\s*[：:]|[|；;。\n]|$)",
            text,
            flags=re.IGNORECASE,
        )
        if match and match.group(1).strip():
            return " ".join(match.group(1).split()).strip()
    return ""


def extract_cmgb_detail(page: Any, *, detail_url: str, allowed_hosts: set[str]) -> dict[str, Any]:
    """Extract fields from a rendered official detail tab.

    Reading ``body.inner_text`` deliberately supports both text and generic
    nodes used by different versions of the React detail page.  Missing fields
    raise instead of producing a misleading partial job.
    """

    official_detail_url = _official_url(detail_url, "detail_url", allowed_hosts)
    body = _body_text(page)
    title = _first_text(page, (".job-name", "h1", "h2"))
    employer = _first_text(page, (".company-name", ".requirement .company-name", ".company"))
    major = _label_value(body, ("专业要求", "专业范围", "需求专业", "专业"))
    degree = _label_value(body, ("最低学历", "学历要求", "学历"))
    location = _label_value(body, ("工作地点", "工作城市", "工作地区", "地点"))
    headcount = _label_value(body, ("招聘人数", "需求人数", "人数"))
    deadline = _label_value(body, ("报名截止", "截止日期", "截止时间"))
    values = {
        "title": title or _label_value(body, ("岗位名称", "职位名称")),
        "employer": employer or _label_value(body, ("招聘单位", "用人单位", "单位")),
        "major": major,
        "degree": degree,
        "location": location,
        "headcount": headcount,
        "deadline": deadline,
    }
    missing = [field for field, value in values.items() if not value]
    if missing:
        raise BrowserCaptureError(
            "CMGB detail is missing required fields: " + ", ".join(missing)
        )
    evidence = {
        "岗位": values["title"],
        "专业范围": values["major"],
        "学历要求": values["degree"],
        "工作地点": values["location"],
        "招聘人数": values["headcount"],
        "报名截止": values["deadline"],
        "官方详情链接": official_detail_url,
    }
    return {
        **values,
        "detail_url": official_detail_url,
        "evidence_url": official_detail_url,
        "field_evidence": evidence,
        "description": body,
    }


def _write_capture(path: Path | str, payload: dict[str, Any]) -> dict[str, Any]:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(destination)
    return payload


def write_cmgb_capture_failure(
    *, output: Path | str, platform_url: str, status: str, reason: str
) -> dict[str, Any]:
    """Persist a non-publishable observation for the operator audit."""

    if status not in {"access_limited", "parse_failed", "partial"}:
        raise ValueError("CMGB failure status must be access_limited, parse_failed or partial")
    return _write_capture(
        output,
        {
            "version": 1,
            "status": status,
            "platform_url": platform_url,
            "captured_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "scan": {
                "pages_scanned": 0,
                "pagination_complete": False,
                "rows_discovered": 0,
                "rows_exported": 0,
                "failed_rows": 0,
                "detail_discovered": 0,
                "detail_succeeded": 0,
                "detail_failed": 0,
                "failure_reason": str(reason)[:1000],
            },
            "rows": [],
        },
    )


def run_cmgb_browser_capture(
    *,
    url: str,
    output: Path | str,
    config: dict[str, Any],
    allowed_hosts: Iterable[str],
    user_agent: str,
    timeout_ms: int = 45_000,
) -> dict[str, Any]:
    """Capture all visible CMGB pages and their official detail tabs.

    This is a conservative producer: it does not call hidden write APIs,
    submit forms, or bypass robots.  The selectors are configuration-driven so
    a portal DOM change can be reviewed without changing publication code.
    """

    hosts = {str(host).strip().lower().rstrip(".") for host in allowed_hosts if str(host).strip()}
    target_url = _official_url(url, "browser_url", hosts)
    _robots_permit(target_url, user_agent=user_agent)
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise BrowserCaptureError(
            "Playwright is not installed; install the browser worker extra before enabling CMGB capture"
        ) from error

    max_pages = max(1, int(config.get("max_pages", 20)))
    row_selector = str(config.get("row_selector") or ".ant-card")
    next_selector = str(config.get("next_selector") or ".ant-pagination-next")
    detail_button_selector = str(config.get("detail_button_selector") or "button")
    detail_popup_timeout_ms = max(
        1_000, min(timeout_ms, int(config.get("detail_popup_timeout_ms", 5_000)))
    )
    detail_render_wait_ms = max(250, min(10_000, int(config.get("detail_render_wait_ms", 1_000))))
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    pages_scanned = 0
    failed_rows = 0
    detail_failed = 0
    detail_discovered = 0
    pagination_complete = False
    try:
        with sync_playwright() as playwright:
            cdp_url = str(config.get("cdp_url") or "").strip()
            if cdp_url:
                browser = playwright.chromium.connect_over_cdp(
                    _resolve_cdp_websocket(cdp_url)
                )
                context = browser.contexts[0] if browser.contexts else browser.new_context(
                    user_agent=user_agent
                )
            else:
                browser = playwright.chromium.launch(headless=True)
                context = browser.new_context(user_agent=user_agent)
            page = context.new_page()
            page.goto(target_url, wait_until="domcontentloaded", timeout=timeout_ms)
            while pages_scanned < max_pages:
                pages_scanned += 1
                page.wait_for_selector(row_selector, timeout=timeout_ms)
                cards = page.locator(row_selector)
                for index in range(cards.count()):
                    card = cards.nth(index)
                    detail_discovered += 1
                    list_url = page.url
                    detail_page = None
                    detail_page_is_current = False
                    try:
                        title = _first_text(card, (".job-name", "h3", "h4"))
                        employer = _first_text(card, (".company-name", ".requirement .company-name"))
                        card_id = card.get_attribute("data-id") or card.get_attribute("id") or f"page-{pages_scanned}-row-{index + 1}"
                        fold = card.locator(".fold").first
                        if fold.count() and fold.is_visible():
                            fold.click()
                        detail_link = card.locator("a[href]").filter(
                            has_text=re.compile(r"查\s*看")
                        ).first
                        href = (
                            str(detail_link.get_attribute("href") or "").strip()
                            if detail_link.count()
                            else ""
                        )
                        if href:
                            detail_page = context.new_page()
                            detail_page.goto(
                                urljoin(list_url, href),
                                wait_until="domcontentloaded",
                                timeout=timeout_ms,
                            )
                        detail_button = card.locator(detail_button_selector).filter(
                            has_text=re.compile(r"查\s*看")
                        ).first
                        if not detail_button.count():
                            detail_button = card.get_by_text(
                                re.compile(r"查\s*看"), exact=True
                            ).first
                        if detail_page is None and not detail_button.count():
                            raise BrowserCaptureError("CMGB card has no 查看 detail control")
                        if detail_page is None:
                            try:
                                with context.expect_page(timeout=detail_popup_timeout_ms) as detail_info:
                                    detail_button.click()
                                detail_page = detail_info.value
                            except PlaywrightTimeoutError:
                                # Some deployments use an SPA route in the same
                                # tab.  The click has already happened; use the
                                # current page only when its route/content changed.
                                page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
                                page.wait_for_timeout(detail_render_wait_ms)
                                if page.url == list_url and page.locator(row_selector).count():
                                    raise BrowserCaptureError(
                                        "CMGB 查看 control did not open an official detail route"
                                    )
                                detail_page = page
                                detail_page_is_current = True
                        detail_page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
                        detail_page.wait_for_timeout(detail_render_wait_ms)
                        detail_url = detail_page.url
                        detail = extract_cmgb_detail(detail_page, detail_url=detail_url, allowed_hosts=hosts)
                        if not detail_page_is_current:
                            detail_page.close()
                        else:
                            page.go_back(wait_until="domcontentloaded", timeout=timeout_ms)
                            page.wait_for_selector(row_selector, timeout=timeout_ms)
                        external_id = str(card_id).strip()
                        if external_id in seen:
                            raise BrowserCaptureError(
                                f"duplicate CMGB card identifier: {external_id}"
                            )
                        seen.add(external_id)
                        rows.append({
                            "external_id": external_id,
                            "title": detail["title"] or title,
                            "employer": detail["employer"] or employer,
                            "major": detail["major"],
                            "degree": detail["degree"],
                            "location": detail["location"],
                            "headcount": detail["headcount"],
                            "deadline": detail["deadline"],
                            "detail_url": detail["detail_url"],
                            "evidence_url": detail["evidence_url"],
                            "description": detail["description"],
                            "field_evidence": detail["field_evidence"],
                        })
                    except Exception:
                        if detail_page is not None and not detail_page_is_current:
                            try:
                                detail_page.close()
                            except Exception:
                                pass
                        failed_rows += 1
                        detail_failed += 1
                next_button = page.locator(next_selector).first
                disabled = (
                    not next_button.count()
                    or next_button.get_attribute("disabled") is not None
                    or "disabled" in str(next_button.get_attribute("class") or "").lower()
                    or str(next_button.get_attribute("aria-disabled") or "").lower() == "true"
                )
                if disabled:
                    pagination_complete = True
                    break
                if pages_scanned >= max_pages:
                    break
                next_button.click()
                page.wait_for_timeout(detail_render_wait_ms)
                page.wait_for_selector(row_selector, timeout=timeout_ms)
            browser.close()
    except PlaywrightTimeoutError as error:
        raise BrowserCaptureError(f"CMGB browser page timed out: {error}") from error
    except BrowserCaptureError:
        raise
    except Exception as error:
        raise BrowserCaptureError(f"CMGB browser capture failed: {error}") from error

    status = "success" if pagination_complete and failed_rows == 0 else "partial"
    payload = {
        "version": 1,
        "status": status,
        "platform_url": target_url,
        "captured_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "scan": {
            "pages_scanned": pages_scanned,
            "pagination_complete": pagination_complete,
            "rows_discovered": detail_discovered,
            "rows_exported": len(rows),
            "failed_rows": failed_rows,
            "detail_discovered": detail_discovered,
            "detail_succeeded": len(rows),
            "detail_failed": detail_failed,
        },
        "rows": rows,
    }
    return _write_capture(output, payload)
