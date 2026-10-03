"""Read-only health checks for student-facing official vacancy links.

The database audit proves that a URL is syntactically valid and belongs to a
registered source.  That is necessary but not sufficient for the link a
student clicks to remain useful: official portals can retire a detail page,
return a maintenance page, or apply an access policy.  This module probes a
bounded set of public detail URLs and reports those states without changing
jobs or treating a blocked page as an empty source.

It deliberately does not use browser automation or attempt to bypass a
challenge.  Dynamic pages are reported as ``reachable_dynamic`` when the
response is an HTML shell with little visible text; a source adapter remains
responsible for extracting the authoritative fields.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from html import unescape
from typing import Any
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

from job_hub.db import Database
from job_hub.sources import USER_AGENT
from job_hub.transport import RequestPolicy, create_session, request_exception_types


ACCESS_POLICY_STATUS = frozenset({401, 403, 407, 412, 429, 451})
TRANSIENT_STATUS = frozenset({408, 425, 500, 502, 503, 504})
REQUEST_ERRORS = request_exception_types()
SOFT_NOT_FOUND_MARKERS = (
    "页面不存在",
    "网页不存在",
    "内容不存在",
    "已被删除",
    "page not found",
    "page does not exist",
    "404 not found",
)


@dataclass(frozen=True)
class LinkHealthResult:
    """A non-publishing result for one official evidence URL."""

    job_id: int
    source_id: str
    url: str
    status: str
    http_status: int | None = None
    final_url: str = ""
    content_type: str = ""
    title: str = ""
    content_bytes: int = 0
    content_sha256: str = ""
    detail: str = ""
    robots_url: str = ""
    robots_status: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "source_id": self.source_id,
            "url": self.url,
            "status": self.status,
            "http_status": self.http_status,
            "final_url": self.final_url,
            "content_type": self.content_type,
            "title": self.title,
            "content_bytes": self.content_bytes,
            "content_sha256": self.content_sha256,
            "detail": self.detail,
            "robots_url": self.robots_url,
            "robots_status": self.robots_status,
        }


def _allowed_hosts(source: dict[str, Any]) -> set[str]:
    config = source.get("config") or {}
    hosts = {
        str(item).lower().rstrip(".")
        for key in ("allowed_hosts", "detail_allowed_hosts", "attachment_allowed_hosts")
        for item in (config.get(key) or [])
    }
    homepage = urlparse(str(source.get("homepage_url") or "")).hostname
    if homepage:
        hosts.add(homepage.lower().rstrip("."))
    return hosts


def _title_and_visible_text(body: bytes, content_type: str) -> tuple[str, str]:
    if "html" not in content_type.lower() and not body.lstrip().startswith(b"<"):
        return "", ""
    text = body.decode("utf-8", errors="replace")
    soup = BeautifulSoup(text[:400_000], "html.parser")
    title = " ".join((soup.title.get_text(" ", strip=True) if soup.title else "").split())
    visible = " ".join(soup.get_text(" ", strip=True).split())
    return title[:200], unescape(visible[:4_000])


def _classify_body(body: bytes, content_type: str) -> tuple[str, str, str]:
    title, visible = _title_and_visible_text(body, content_type)
    lowered = f"{title} {visible}".lower()
    if any(marker in lowered for marker in SOFT_NOT_FOUND_MARKERS):
        return "soft_not_found", "official page returned a not-found marker", title
    if "html" not in content_type.lower():
        return "reachable_non_html", "official URL returned a non-HTML document", title
    # A JavaScript shell can be a valid portal route but does not prove that
    # fields are available to a student without the source-specific adapter.
    scripts = len(re.findall(r"<script\b", body.decode("utf-8", errors="ignore"), re.I))
    if len(visible) < 80 and scripts >= 1:
        return "reachable_dynamic", "official URL returned a dynamic HTML shell", title
    return "reachable", "official URL returned an HTML document", title


class OfficialLinkHealthProbe:
    """Probe registered student-facing detail links without mutating state."""

    def __init__(
        self,
        settings: Any,
        *,
        session: requests.Session | None = None,
        timeout_seconds: float | None = None,
        retries: int | None = None,
    ) -> None:
        self.settings = settings
        self.session = session or create_session(
            settings.http_transport_mode,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,*/*;q=0.2"},
            client=settings.http_client,
        )
        self.policy = RequestPolicy(
            self.session,
            timeout=float(timeout_seconds or settings.request_timeout_seconds),
            retries=settings.http_retry_attempts if retries is None else retries,
            backoff_seconds=settings.http_backoff_seconds,
            max_backoff_seconds=settings.http_max_backoff_seconds,
            jitter_seconds=settings.http_jitter_seconds,
            transport_mode=settings.http_transport_mode,
        )
        # Cache the parsed rules per host, then evaluate the requested path on
        # every call.  Caching one boolean per host would incorrectly apply a
        # first URL's decision to a different disallowed/allowed path.
        self._robots: dict[
            str, tuple[RobotFileParser | None, int | None, str, bool | None]
        ] = {}

    def _robots_check(self, url: str) -> tuple[bool | None, int | None, str, str]:
        parsed = urlparse(url)
        root = f"{parsed.scheme}://{parsed.netloc}"
        robots_url = urljoin(root, "/robots.txt")
        if root in self._robots:
            parser, status, detail, fallback = self._robots[root]
            allowed = parser.can_fetch(USER_AGENT, url) if parser is not None else fallback
            return allowed, status, detail, robots_url
        try:
            response = self.policy.request("GET", robots_url, timeout=min(self.policy.timeout, 10))
        except REQUEST_ERRORS as error:
            detail = f"robots.txt request failed: {error.__class__.__name__}"
            self._robots[root] = (None, None, detail, None)
            return None, None, detail, robots_url
        status = int(response.status_code)
        if status == 404:
            result = (True, status, "robots.txt not found; no disallow rule was published")
        elif 200 <= status < 300:
            parser = RobotFileParser()
            parser.parse(response.text.splitlines())
            self._robots[root] = (parser, status, "robots.txt rules were parsed", None)
            allowed = parser.can_fetch(USER_AGENT, url)
            return bool(allowed), status, "robots.txt permits the official URL" if allowed else "robots.txt disallows the official URL", robots_url
        elif status in ACCESS_POLICY_STATUS:
            result = (None, status, f"robots.txt access policy returned HTTP {status}")
        else:
            result = (None, status, f"robots.txt could not be verified (HTTP {status})")
        self._robots[root] = (None, result[1], result[2], result[0])
        return result[0], result[1], result[2], robots_url

    def probe(self, job: dict[str, Any], source: dict[str, Any]) -> LinkHealthResult:
        job_id = int(job["id"])
        source_id = str(job.get("source_id") or source.get("id") or "")
        url = str(job.get("official_evidence_url") or "").strip()
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return LinkHealthResult(job_id, source_id, url, "invalid_url", detail="official evidence URL is not HTTP(S)")
        allowed_hosts = _allowed_hosts(source)
        if allowed_hosts and parsed.hostname.lower().rstrip(".") not in allowed_hosts:
            return LinkHealthResult(job_id, source_id, url, "host_mismatch", detail="official URL host is outside the registered source allowlist")
        allowed, robots_status, robots_detail, robots_url = self._robots_check(url)
        if allowed is False:
            return LinkHealthResult(job_id, source_id, url, "robots_blocked", robots_url=robots_url, robots_status=robots_status, detail=robots_detail)
        if allowed is None:
            return LinkHealthResult(job_id, source_id, url, "access_unverified", robots_url=robots_url, robots_status=robots_status, detail=robots_detail)
        try:
            response = self.policy.request("GET", url, allow_redirects=True)
        except REQUEST_ERRORS as error:
            return LinkHealthResult(job_id, source_id, url, "transport_error", robots_url=robots_url, robots_status=robots_status, detail=f"official URL request failed: {error.__class__.__name__}")
        status = int(response.status_code)
        final_url = str(getattr(response, "url", "") or url)
        final_host = urlparse(final_url).hostname
        if final_host and allowed_hosts and final_host.lower().rstrip(".") not in allowed_hosts:
            return LinkHealthResult(job_id, source_id, url, "redirect_outside_allowlist", status, final_url, detail="official URL redirected outside the registered source allowlist", robots_url=robots_url, robots_status=robots_status)
        if status in ACCESS_POLICY_STATUS:
            return LinkHealthResult(job_id, source_id, url, "access_limited", status, final_url, detail=f"official URL returned access-policy HTTP {status}", robots_url=robots_url, robots_status=robots_status)
        if status == 404:
            return LinkHealthResult(job_id, source_id, url, "not_found", status, final_url, detail="official detail URL returned HTTP 404", robots_url=robots_url, robots_status=robots_status)
        if status in TRANSIENT_STATUS or status >= 500:
            return LinkHealthResult(job_id, source_id, url, "server_error", status, final_url, detail=f"official URL returned HTTP {status}; retry before any lifecycle decision", robots_url=robots_url, robots_status=robots_status)
        if not 200 <= status < 400:
            return LinkHealthResult(job_id, source_id, url, "http_error", status, final_url, detail=f"official URL returned HTTP {status}", robots_url=robots_url, robots_status=robots_status)
        body = bytes(getattr(response, "content", b"") or b"")
        content_type = str(getattr(response, "headers", {}).get("Content-Type", ""))
        classification, detail, title = _classify_body(body, content_type)
        return LinkHealthResult(
            job_id, source_id, url, classification, status, final_url, content_type, title,
            len(body), hashlib.sha256(body).hexdigest() if body else "", detail,
            robots_url, robots_status,
        )


def build_link_health_report(
    database: Database,
    settings: Any,
    *,
    source_id: str | None = None,
    job_id: int | None = None,
    limit: int = 50,
    include_non_public: bool = False,
) -> dict[str, Any]:
    """Probe a bounded job set and return a JSON-safe diagnostic report."""
    sources = {str(item["id"]): item for item in database.list_sources()}
    if source_id and source_id not in sources:
        raise ValueError(f"source_id is not registered: {source_id}")
    if job_id is not None:
        # ``list_jobs`` has no job-id predicate.  Read the private set before
        # filtering; taking the first row and then filtering would silently
        # report zero for every ID outside the global sort order.
        jobs, _ = database.list_jobs(
            page=1, page_size=None, only_open=False, student_visible=False
        )
        jobs = [job for job in jobs if int(job["id"]) == int(job_id)]
    else:
        jobs, _ = database.list_jobs(
            page=1,
            # Source filtering happens below because the DB API intentionally
            # keeps source selection separate from public query filters.
            # Fetching the complete bounded public set avoids false zeroes for
            # a source whose rows are not first in the relevance ordering.
            page_size=None,
            only_open=True,
            student_visible=not include_non_public,
        )
    if source_id:
        jobs = [job for job in jobs if str(job.get("source_id")) == source_id]
    if not include_non_public:
        jobs = [job for job in jobs if job.get("publication_status") in {"student_eligible", "unrestricted_eligible"}]
    bounded_limit = max(1, min(int(limit), 500))
    if source_id or job_id is not None:
        jobs = jobs[:bounded_limit]
    else:
        # A relevance-ordered global slice can be dominated by one large
        # source (currently CNOOC).  Round-robin the bounded audit across
        # sources so the daily diagnostic tests the diversity students see.
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for job in jobs:
            grouped[str(job.get("source_id") or "")].append(job)
        selected: list[dict[str, Any]] = []
        source_groups = [group for group in grouped.values() if group]
        while source_groups and len(selected) < bounded_limit:
            next_groups: list[list[dict[str, Any]]] = []
            for group in source_groups:
                selected.append(group.pop(0))
                if len(selected) >= bounded_limit:
                    break
                if group:
                    next_groups.append(group)
            source_groups = next_groups
        jobs = selected
    probe = OfficialLinkHealthProbe(settings)
    results = [probe.probe(job, sources.get(str(job.get("source_id")), {"id": job.get("source_id") or ""})) for job in jobs]
    items = [item.as_dict() for item in results]
    counts = Counter(item.status for item in results)
    return {
        "checked": len(items),
        "status_counts": dict(sorted(counts.items())),
        "items": items,
        "policy": "read_only; access-limited, robots-blocked and server-error links are not withdrawn automatically",
    }


__all__ = ["LinkHealthResult", "OfficialLinkHealthProbe", "build_link_health_report"]
