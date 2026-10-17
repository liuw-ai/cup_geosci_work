"""Browser capture for the CUPB official employment portal.

The portal is rendered in a browser while the existing CUPB parser remains
the single source of row/major/degree/location semantics. A complete set of
configured official notices is required before the success-only snapshot is
replaced.
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from job_hub.browser_capture import BrowserCaptureError, launch_playwright_browser
from job_hub.config import Settings
from job_hub.sources import OfficialSourceCollector


# CUPB pages also contain announcement-level notices.  Those notices are useful
# discovery material, but they are not publishable jobs until the parser has a
# concrete row/role/detail evidence scope and a location.
_PUBLISHABLE_EVIDENCE_SCOPES = frozenset(
    {
        "official_html_table_row",
        "official_role_section",
        "official_detail_block",
    }
)


def _publishable_posting(posting: Any) -> bool:
    evidence = posting.field_evidence if isinstance(posting.field_evidence, dict) else {}
    scope = str(evidence.get("evidence_scope") or "").strip()
    required = (
        str(posting.title or "").strip(),
        str(posting.employer or "").strip(),
        str(posting.location or "").strip(),
        str(posting.qualification_text or posting.text or "").strip(),
    )
    return scope in _PUBLISHABLE_EVIDENCE_SCOPES and all(required)


def _cupb_external_id(posting: Any) -> str:
    """Keep browser refreshes compatible with the earlier CUPB ID ledger."""

    raw = str(posting.external_id or posting.source_url or "").strip()
    match = re.search(r"/campus/view/id/(\d+)(?:#(row|section)-(\d+))?", raw)
    if not match:
        return raw
    notice_id, kind, number = match.groups()
    if kind and number:
        # Earlier verified snapshots used ``row`` for both table rows and
        # numbered prose sections; retaining that spelling prevents duplicate
        # records when a browser refresh replaces the same announcement.
        return f"cupb-{notice_id}-row-{number}"
    return f"cupb-{notice_id}"


def _browser_listing_candidates(
    *,
    page: Any,
    collector: OfficialSourceCollector,
    source: dict[str, Any],
    listing_url: str,
    seen_listing_urls: set[str],
    seen_detail_urls: set[str],
) -> tuple[list[tuple[str, str]], list[str]]:
    """Extract CUPB detail links and the next bounded listing page."""

    config = dict(source.get("config") or {})
    detail_patterns = config.get(
        "detail_path_patterns",
        [r"/campus/view/id/", r"/job/view/id/", r"/news/view/.+tag/xwzp"],
    )
    excludes = config.get(
        "listing_exclude_patterns", config.get("exclude_patterns", [])
    )
    hosts = {
        str(item).strip().lower().rstrip(".")
        for item in config.get("allowed_hosts") or []
        if str(item).strip()
    }
    soup = BeautifulSoup(page.content(), "html.parser")
    candidates: list[tuple[str, str]] = []
    next_urls: list[str] = []
    for anchor in soup.find_all("a"):
        href = str(anchor.get("href") or "").strip()
        title = str(anchor.get("title") or anchor.get_text(" ", strip=True)).strip()
        if not href:
            continue
        detail_url = collector._cupb_listing_url(urljoin(page.url, href))
        parsed = urlparse(detail_url)
        path_and_query = f"{parsed.path}?{parsed.query}"
        if parsed.hostname and parsed.hostname.lower().rstrip(".") not in hosts:
            continue
        if any(re.search(pattern, path_and_query, re.IGNORECASE) for pattern in detail_patterns):
            if (
                not title
                or collector._is_non_vacancy_notice_title(title)
                or any(re.search(pattern, title, re.IGNORECASE) for pattern in excludes)
            ):
                continue
            if detail_url not in seen_detail_urls:
                seen_detail_urls.add(detail_url)
                candidates.append((title, detail_url))
            continue
        if collector._cupb_is_next_page_anchor(anchor) and detail_url not in seen_listing_urls:
            next_urls.append(detail_url)
    return candidates, next_urls


def _preflight_snapshot(
    collector: OfficialSourceCollector,
    source: dict[str, Any],
    snapshot_path: Path,
    *,
    captured_at: str,
) -> None:
    """Run the normal official-snapshot contract before promotion."""

    config = dict(source.get("config") or {})
    validation_source = {
        **source,
        "config": {
            **config,
            "snapshot_path": str(snapshot_path),
            "snapshot_captured_at": captured_at,
        },
    }
    collector._collect_official_snapshot_rows(validation_source)


def _promote_snapshot(
    collector: OfficialSourceCollector,
    source: dict[str, Any],
    destination: Path,
    rows: list[dict[str, Any]],
    *,
    captured_at: str,
) -> str:
    """Validate and atomically promote a candidate, returning its rollback path."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".candidate.tmp")
    rollback = destination.with_suffix(destination.suffix + ".previous")
    temporary.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        _preflight_snapshot(
            collector,
            source,
            temporary,
            captured_at=captured_at,
        )
        had_previous = destination.exists()
        if had_previous:
            shutil.copyfile(destination, rollback)
        temporary.replace(destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return str(rollback) if had_previous else ""


def _robots_allowed(page: Any, url: str, *, user_agent: str) -> None:
    """Check the official robots document through the same browser session."""

    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    page.goto(robots_url, wait_until="domcontentloaded", timeout=30_000)
    status = int(page.evaluate("() => performance.getEntriesByType('navigation')[0]?.responseStatus || 200") or 200)
    if status == 404:
        return
    if status < 200 or status >= 400:
        raise BrowserCaptureError(f"robots.txt returned HTTP {status}")
    text = page.locator("body").inner_text()
    from urllib.robotparser import RobotFileParser

    parser = RobotFileParser()
    parser.parse(text.splitlines())
    if not parser.can_fetch(user_agent, url):
        raise BrowserCaptureError("robots.txt does not permit CUPB browser capture")


def _posting_payload(posting: Any, *, captured_at: str) -> dict[str, Any]:
    return {
        "external_id": posting.external_id,
        "title": posting.title,
        "employer": posting.employer,
        "location": posting.location or "",
        "published_date": posting.published_date or "",
        "deadline_date": posting.deadline_date or "",
        "qualification_text": posting.qualification_text or posting.text,
        "summary": posting.summary,
        "description": posting.text,
        "source_url": posting.source_url,
        "application_url": posting.application_url or posting.source_url,
        "official_evidence_url": posting.official_evidence_url or posting.source_url,
        "match_text": posting.match_text or "",
        "field_evidence": {
            **dict(posting.field_evidence or {}),
            "浏览器捕获时间": captured_at,
            "证据来源": "CUPB官方公告Chromium渲染正文/表格",
        },
    }


def run_cupb_browser_capture(
    *,
    settings: Settings,
    source: dict[str, Any],
    output: Path | str,
    executable_path: str = "",
    user_agent: str = "CUPB-Geoscience-Employment-Information-Service/1.0",
) -> dict[str, Any]:
    config = dict(source.get("config") or {})
    urls = []
    listing_urls: list[str] = []
    for item in config.get("listing_urls") or []:
        url = str(item).strip()
        if url and url not in listing_urls:
            listing_urls.append(url)
    for item in config.get("direct_notice_urls") or []:
        url = str(item.get("url") if isinstance(item, dict) else item).strip()
        if url and url not in urls:
            urls.append(url)
    if not urls and not listing_urls:
        raise BrowserCaptureError("CUPB browser capture has no configured official notice URLs")
    hosts = {str(item).strip().lower() for item in config.get("allowed_hosts") or [] if str(item).strip()}
    captured_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    collector = OfficialSourceCollector(settings)
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    rejected_candidates = 0
    browser = None
    owns_browser = False
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            cdp_url = str(config.get("cdp_url") or "").strip()
            if cdp_url:
                from job_hub.cnpc_browser_runner import _resolve_cdp_websocket

                browser = playwright.chromium.connect_over_cdp(
                    _resolve_cdp_websocket(cdp_url)
                )
                if not browser.contexts:
                    raise BrowserCaptureError("CUPB CDP browser has no default context")
                context = browser.contexts[0]
                for opened_page in list(context.pages):
                    try:
                        if not opened_page.is_closed():
                            opened_page.close()
                    except Exception:
                        pass
            else:
                launch_config = {"executable_path": executable_path} if executable_path else {}
                browser = launch_playwright_browser(playwright, launch_config)
                owns_browser = True
                context = browser.new_context(user_agent=user_agent)
            page = context.new_page()
            _robots_allowed(page, (urls or listing_urls)[0], user_agent=user_agent)
            if listing_urls and bool(config.get("browser_discover_listings", True)):
                listing_limit = max(1, min(int(config.get("listing_page_limit", 6)), 20))
                listing_queue = list(listing_urls)
                seen_listing_urls: set[str] = set()
                seen_detail_urls: set[str] = set(seen)
                for _ in range(len(listing_urls) * listing_limit):
                    if not listing_queue:
                        break
                    listing_url = listing_queue.pop(0)
                    if listing_url in seen_listing_urls:
                        continue
                    seen_listing_urls.add(listing_url)
                    page.goto(listing_url, wait_until="domcontentloaded", timeout=45_000)
                    page.wait_for_timeout(500)
                    candidates, next_urls = _browser_listing_candidates(
                        page=page,
                        collector=collector,
                        source=source,
                        listing_url=listing_url,
                        seen_listing_urls=seen_listing_urls,
                        seen_detail_urls=seen_detail_urls,
                    )
                    for title_hint, detail_url in candidates:
                        if len(seen_detail_urls) > int(config.get("candidate_limit", 400)):
                            break
                        page.goto(detail_url, wait_until="domcontentloaded", timeout=45_000)
                        page.wait_for_timeout(500)
                        postings = collector._extract_cupb_details(
                            page.content(), page.url, source, title_hint
                        )
                        for posting in postings:
                            if not _publishable_posting(posting):
                                rejected_candidates += 1
                                continue
                            external_id = _cupb_external_id(posting)
                            if external_id in seen:
                                continue
                            seen.add(external_id)
                            payload = _posting_payload(posting, captured_at=captured_at)
                            payload["external_id"] = external_id
                            rows.append(payload)
                    for next_url in next_urls[:1]:
                        if next_url not in seen_listing_urls:
                            listing_queue.append(next_url)
            for item in config.get("direct_notice_urls") or []:
                url = str(item.get("url") if isinstance(item, dict) else item).strip()
                title_hint = str(item.get("title_hint") if isinstance(item, dict) else "").strip()
                if not url or (urlparse(url).hostname or "").lower() not in hosts:
                    raise BrowserCaptureError(f"CUPB notice host is not allowlisted: {url}")
                page.goto(url, wait_until="domcontentloaded", timeout=45_000)
                page.wait_for_timeout(500)
                postings = collector._extract_cupb_details(page.content(), page.url, source, title_hint)
                for posting in postings:
                    if not _publishable_posting(posting):
                        rejected_candidates += 1
                        continue
                    external_id = _cupb_external_id(posting)
                    key = external_id
                    if key not in seen:
                        seen.add(key)
                        payload = _posting_payload(posting, captured_at=captured_at)
                        payload["external_id"] = external_id
                        rows.append(payload)
            context.close()
    except BrowserCaptureError:
        raise
    except Exception as error:
        raise BrowserCaptureError(f"CUPB browser capture failed: {error}") from error
    finally:
        if browser is not None and owns_browser:
            try:
                browser.close()
            except Exception:
                pass
    if not rows:
        raise BrowserCaptureError("CUPB browser capture rendered no岗位 rows")
    destination = Path(output)
    rollback_path = _promote_snapshot(
        collector,
        source,
        destination,
        rows,
        captured_at=captured_at,
    )
    return {
        "status": "success",
        "captured_at": captured_at,
        "notices_configured": len(urls) + len(listing_urls),
        "notices_scanned": len(urls) + len(listing_urls),
        "rows_exported": len(rows),
        "rows_rejected": rejected_candidates,
        "rollback_path": rollback_path,
    }


def write_cupb_capture_failure(*, output: Path | str, reason: str, status: str = "parse_failed") -> Path:
    destination = Path(str(output) + ".failure.json")
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "status": status,
        "captured_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "diagnostic_for": Path(output).name,
        "failure_reason": str(reason)[:1000],
    }
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(destination)
    return destination


__all__ = ["run_cupb_browser_capture", "write_cupb_capture_failure"]
