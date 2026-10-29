"""Compliant, private scanning of explicitly enabled discovery channels.

Discovery pages are only a lead source.  This module never creates a public
job and never treats a third-party page as official evidence.  It fetches only
registered URLs whose discovery policy explicitly opts into public-page or RSS
scanning and stops before a page when robots cannot be verified.
"""

from __future__ import annotations

import html
import re
import warnings
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable
from urllib.parse import parse_qs, unquote, urljoin, urlparse
from urllib.robotparser import RobotFileParser
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, MarkupResemblesLocatorWarning

from job_hub.config import Settings
from job_hub.discovery import (
    is_non_recruitment_official_host,
    normalize_recovered_url,
    load_discovery_source_registry,
    discovery_link_quality_reason,
    reconcile_navigation_discovery_leads,
    recover_persisted_official_url_candidates,
    resolve_official_url_candidates,
)
from job_hub.transport import (
    RequestPolicy,
    ResponseCache,
    configure_session,
    create_session,
    looks_like_non_robots_document,
)
from job_hub.sources import USER_AGENT


class DiscoveryScanError(RuntimeError):
    """A discovery scan failed without implying that there were no leads."""


class DiscoveryAccessLimited(DiscoveryScanError):
    """The publisher's robots or access policy prevented a scan."""


@dataclass(frozen=True)
class DiscoveryScanItem:
    source_id: str
    status: str
    scanned_urls: int
    discovered_leads: tuple[dict[str, Any], ...]
    detail: str = ""
    enriched_leads: int = 0

    def as_dict(self, *, created_leads: int = 0, enriched_leads: int | None = None) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "status": self.status,
            "scanned_urls": self.scanned_urls,
            "discovered_leads": len(self.discovered_leads),
            "created_leads": int(created_leads),
            "enriched_leads": int(self.enriched_leads if enriched_leads is None else enriched_leads),
            "leads": list(self.discovered_leads),
            "detail": self.detail,
        }


class DiscoveryScanner:
    """Scan configured discovery pages with the normal outbound policy."""

    DEFAULT_INCLUDE_PATTERNS = (
        "招聘",
        "招考",
        "招录",
        "事业单位",
        "公务员",
        "校园",
        "校招",
        "人才",
        "博士后",
        "地质",
        "自然资源",
    )

    def __init__(
        self,
        settings: Settings,
        session: requests.Session | None = None,
    ) -> None:
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/rss+xml;q=0.8,*/*;q=0.5",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
        }
        self.settings = settings
        self.session = session or create_session(
            settings.http_transport_mode,
            headers=headers,
            client=settings.http_client,
        )
        if session is not None:
            configure_session(session, settings.http_transport_mode, headers=headers)
        cache = None
        if settings.http_cache_enabled:
            cache = ResponseCache(
                settings.data_dir / "http-cache",
                max_entries=settings.http_cache_max_entries,
            )
        self.policy = RequestPolicy(
            self.session,
            timeout=settings.request_timeout_seconds,
            retries=settings.http_retry_attempts,
            backoff_seconds=settings.http_backoff_seconds,
            max_backoff_seconds=settings.http_max_backoff_seconds,
            jitter_seconds=settings.http_jitter_seconds,
            cache=cache,
            transport_mode=settings.http_transport_mode,
        )
        self._robots: dict[str, RobotFileParser] = {}
        # Populated by the scan orchestration layer from the registered
        # first-party source registry.  Discovery pages may link to official
        # company domains that are neither ``.gov.cn`` nor ``.edu.cn``.
        # Keeping this as an explicit allowlist prevents arbitrary external
        # links from becoming official candidates.
        self._official_hosts: set[str] = set()

    def scan_source(self, source: dict[str, Any]) -> DiscoveryScanItem:
        source_id = str(source.get("id") or "").strip()
        mode = str(source.get("access_mode") or "").strip()
        if mode not in {"public_page", "rss"}:
            return DiscoveryScanItem(
                source_id, "not_scheduled", 0, (), "access_mode is not enabled for unattended scanning"
            )
        if str(source.get("status") or "") not in {"registered", "checked"}:
            return DiscoveryScanItem(
                source_id, "not_scheduled", 0, (), "discovery source status is not active"
            )
        config = source.get("scan_config")
        if not isinstance(config, dict):
            config = {}
        urls = config.get("scan_urls") or source.get("scan_urls") or [source["homepage_url"]]
        if not isinstance(urls, list) or not urls:
            return DiscoveryScanItem(source_id, "configuration_error", 0, (), "scan_urls must be a non-empty list")
        max_pages = _bounded_int(config.get("max_pages", source.get("max_pages", 3)), 1, 10)
        max_links = _bounded_int(config.get("max_links", source.get("max_links", 60)), 1, 200)
        include = _include_patterns(
            config.get("include_patterns", source.get("include_patterns"))
        )
        exclude = _exclude_patterns(
            config.get("exclude_patterns", source.get("exclude_patterns"))
        )
        try:
            lead_url_patterns = _url_patterns(
                config.get("lead_url_patterns", source.get("lead_url_patterns"))
            )
            program_relevance_patterns = _optional_text_patterns(
                config.get(
                    "program_relevance_patterns",
                    source.get("program_relevance_patterns"),
                ),
                field="program_relevance_patterns",
            )
            announcement_title_patterns = _optional_text_patterns(
                config.get(
                    "announcement_title_patterns",
                    source.get("announcement_title_patterns"),
                ),
                field="announcement_title_patterns",
            )
        except ValueError as error:
            return DiscoveryScanItem(source_id, "configuration_error", 0, (), str(error))
        root_host = (urlparse(str(source["homepage_url"])).hostname or "").lower().rstrip(".")
        configured_hosts = config.get("allowed_hosts") or source.get("allowed_hosts") or []
        if not isinstance(configured_hosts, list):
            configured_hosts = []
        scan_hosts = {
            str(item).strip().lower().rstrip(".")
            for item in configured_hosts
            if str(item).strip()
        }
        if not scan_hosts:
            scan_hosts = {root_host}
        if not root_host:
            return DiscoveryScanItem(source_id, "configuration_error", 0, (), "homepage_url has no hostname")

        leads: list[dict[str, Any]] = []
        seen_urls: set[str] = set()
        scanned = 0
        try:
            for raw_url in urls[:max_pages]:
                url = str(raw_url).strip()
                if not _host_allowed(url, scan_hosts):
                    continue
                normalized = url.split("#", 1)[0]
                if normalized in seen_urls:
                    continue
                seen_urls.add(normalized)
                self._assert_robots_allowed(normalized)
                response = self.policy.request(
                    "GET",
                    normalized,
                    timeout=self.settings.request_timeout_seconds,
                    allow_redirects=True,
                )
                response.raise_for_status()
                final_url = str(getattr(response, "url", normalized) or normalized)
                if not _host_allowed(final_url, scan_hosts):
                    raise DiscoveryAccessLimited(
                        f"redirected outside registered discovery hosts: {final_url}"
                    )
                scanned += 1
                content_type = str(response.headers.get("Content-Type", "")).lower()
                body = _response_text(response)
                if mode == "rss" or "xml" in content_type or body.lstrip().startswith("<?xml"):
                    page_leads = self._rss_leads(source, normalized, body, include, exclude, max_links, scan_hosts)
                else:
                    page_leads = self._html_leads(
                        source,
                        normalized,
                        body,
                        include,
                        exclude,
                        max_links,
                        scan_hosts,
                        lead_url_patterns,
                        program_relevance_patterns,
                        announcement_title_patterns,
                    )
                for lead in page_leads:
                    if lead["lead_url"] not in {item["lead_url"] for item in leads}:
                        leads.append(lead)
                    if len(leads) >= max_links:
                        break
                if len(leads) >= max_links:
                    break
        except DiscoveryAccessLimited as error:
            return DiscoveryScanItem(source_id, "access_limited", scanned, tuple(leads), str(error))
        except (requests.RequestException, ValueError, ElementTree.ParseError) as error:
            return DiscoveryScanItem(source_id, "source_unavailable", scanned, tuple(leads), str(error)[:400])
        return DiscoveryScanItem(source_id, "scanned", scanned, tuple(leads), "public discovery scan completed")

    def enrich_lead(self, lead: dict[str, Any], *, allowed_hosts: set[str]) -> dict[str, Any]:
        """Fetch one private article and extract recovery evidence only.

        This method never promotes a lead or treats an attachment as official.
        It records the publisher attribution, attachment URLs, and narrowly
        selected government/education/organization link candidates so a later
        official verifier can resolve the original notice.
        """
        url = str(lead.get("lead_url") or "").strip()
        if not url or _host_allowed(url, allowed_hosts) is False:
            raise DiscoveryScanError("lead URL is not on the registered discovery host")
        self._assert_robots_allowed(url)
        response = self.policy.request(
            "GET",
            url,
            timeout=self.settings.request_timeout_seconds,
            allow_redirects=True,
        )
        response.raise_for_status()
        final_url = str(getattr(response, "url", url) or url)
        if not _host_allowed(final_url, allowed_hosts):
            raise DiscoveryAccessLimited(
                f"redirected outside registered discovery hosts: {final_url}"
            )
        body = _response_text(response)
        return extract_discovery_evidence(
            body,
            final_url,
            allowed_hosts=allowed_hosts,
            official_hosts=self._official_hosts,
        )

    def _assert_robots_allowed(self, url: str) -> None:
        parsed = urlparse(url)
        root = f"{parsed.scheme}://{parsed.netloc}"
        parser = self._robots.get(root)
        if parser is None:
            robots_url = root + "/robots.txt"
            try:
                response = self.policy.request("GET", robots_url, timeout=min(self.settings.request_timeout_seconds, 10), allow_redirects=True)
            except requests.RequestException as error:
                raise DiscoveryAccessLimited(f"robots.txt unavailable: {error}") from error
            if response.status_code == 404:
                parser = RobotFileParser()
                parser.parse([])
            elif 200 <= response.status_code < 300:
                text = _response_text(response)
                if looks_like_non_robots_document(text):
                    raise DiscoveryAccessLimited("robots.txt returned an HTML error document")
                parser = RobotFileParser()
                parser.parse(text.splitlines())
            else:
                raise DiscoveryAccessLimited(f"robots.txt returned HTTP {response.status_code}")
            self._robots[root] = parser
        if not parser.can_fetch(USER_AGENT, url):
            raise DiscoveryAccessLimited(f"robots.txt disallows {url}")

    def _html_leads(
        self,
        source: dict[str, Any],
        base_url: str,
        body: str,
        include: tuple[str, ...],
        exclude: tuple[str, ...],
        max_links: int,
        scan_hosts: set[str],
        lead_url_patterns: tuple[re.Pattern[str], ...],
        program_relevance_patterns: tuple[str, ...],
        announcement_title_patterns: tuple[str, ...],
    ) -> list[dict[str, Any]]:
        soup = BeautifulSoup(body[:1_000_000], "html.parser")
        leads: list[dict[str, Any]] = []
        for anchor in soup.find_all("a", href=True):
            url = urljoin(base_url, str(anchor.get("href") or "")).split("#", 1)[0]
            if not _host_allowed(url, scan_hosts):
                continue
            if url.rstrip("/") == base_url.rstrip("/"):
                continue
            # An aggregator index often includes navigation, courses, category
            # pages, and notices side by side. When a source declares its
            # article URL grammar, admit only those bounded notice paths before
            # creating any private lead. This is discovery hygiene, not a
            # publication decision.
            if lead_url_patterns and not _matches_url_patterns(url, lead_url_patterns):
                continue
            label = re.sub(r"\s+", " ", f"{anchor.get_text(' ', strip=True)} {url}").strip()
            title = re.sub(r"\s+", " ", anchor.get_text(" ", strip=True)).strip() or url
            # This is deliberately a private intake filter only. It narrows
            # a large education-platform index to likely geoscience-relevant
            # notices; it never determines whether a position actually meets
            # the four core majors or can be published.
            if program_relevance_patterns and not _matches(label, program_relevance_patterns):
                continue
            # Some aggregators publish SEO pages that merely repeat a group
            # recruitment portal URL.  They can mention a target industry but
            # do not contain a vacancy notice or a position table.  A source
            # may therefore require notice-shaped titles before a private
            # lead is created. This remains an intake control only.
            if announcement_title_patterns and not _matches(title, announcement_title_patterns):
                continue
            if not _matches(label, include) or _matches(label, exclude):
                continue
            if discovery_link_quality_reason(url, title):
                continue
            leads.append(self._lead(source, url, title, base_url))
            if len(leads) >= max_links:
                break
        return leads

    def _rss_leads(
        self,
        source: dict[str, Any],
        base_url: str,
        body: str,
        include: tuple[str, ...],
        exclude: tuple[str, ...],
        max_links: int,
        scan_hosts: set[str],
    ) -> list[dict[str, Any]]:
        root = ElementTree.fromstring(body)
        leads: list[dict[str, Any]] = []
        for item in root.iter():
            if item.tag.rsplit("}", 1)[-1].lower() not in {"item", "entry"}:
                continue
            values = {child.tag.rsplit("}", 1)[-1].lower(): (child.text or "").strip() for child in item}
            url = values.get("link") or ""
            if not url:
                for child in item:
                    if child.tag.rsplit("}", 1)[-1].lower() == "link":
                        url = str(child.attrib.get("href") or "").strip()
                        break
            url = urljoin(base_url, url).split("#", 1)[0]
            title = values.get("title") or url
            if not _host_allowed(url, scan_hosts) or not _matches(f"{title} {url}", include) or _matches(f"{title} {url}", exclude):
                continue
            if discovery_link_quality_reason(url, title):
                continue
            leads.append(self._lead(source, url, title, base_url))
            if len(leads) >= max_links:
                break
        return leads

    @staticmethod
    def _lead(source: dict[str, Any], url: str, title: str, listing_url: str) -> dict[str, Any]:
        return {
            "lead_provider": str(source["name"]),
            "discovery_source_id": str(source["id"]),
            "lead_url": url,
            "title": title[:240],
            "metadata": {
                "discovery_scan": True,
                "listing_url": listing_url,
                "captured_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
            },
        }


def run_discovery_scan(
    settings: Settings,
    database: Any,
    *,
    source_ids: Iterable[str] | None = None,
    registry: dict[str, Any] | None = None,
    scanner: DiscoveryScanner | None = None,
) -> dict[str, Any]:
    """Scan opted-in discovery channels and persist only private leads."""
    payload = registry or load_discovery_source_registry()
    requested = {str(item).strip() for item in source_ids or () if str(item).strip()}
    sources = [item for item in payload["sources"] if not requested or str(item["id"]) in requested]
    unknown = sorted(requested - {str(item["id"]) for item in payload["sources"]})
    if unknown:
        raise ValueError("Unknown discovery source id: " + ", ".join(unknown))
    active_scanner = scanner or DiscoveryScanner(settings)
    # The source registry is the only authority for first-party domains.  A
    # discovery page can suggest a URL, but it cannot expand this set.
    official_hosts = _registered_official_hosts(database.list_sources(enabled_only=False))
    if isinstance(active_scanner, DiscoveryScanner):
        active_scanner._official_hosts = official_hosts
    navigation_reconciliation = reconcile_navigation_discovery_leads(database)
    rows: list[dict[str, Any]] = []
    for source in sources:
        result = active_scanner.scan_source(source)
        created = 0
        enriched = 0
        config = source.get("scan_config") if isinstance(source.get("scan_config"), dict) else {}
        enrichment_limit = _bounded_int(config.get("detail_fetch_limit", 0), 0, 40)
        configured_hosts = config.get("allowed_hosts") or source.get("allowed_hosts") or []
        allowed_hosts = {
            str(item).strip().lower().rstrip(".")
            for item in configured_hosts
            if str(item).strip()
        }
        # ``create_candidate_lead`` is idempotent and returns the persisted
        # row.  Compare IDs against the pre-scan set instead of comparing a
        # filtered list length: duplicate leads retain their existing row and
        # only add a source mention, so list lengths do not indicate creation.
        existing_ids = {
            int(item["id"])
            for item in database.list_candidate_leads(
                limit=5000, discovery_source_id=str(source["id"])
            )
            if item.get("id") is not None
        }
        for lead in result.discovered_leads:
            saved = database.create_candidate_lead(lead)
            saved_id = saved.get("id")
            if saved_id is not None and int(saved_id) not in existing_ids:
                created += 1
                existing_ids.add(int(saved_id))
            saved_metadata = saved.get("metadata") if isinstance(saved.get("metadata"), dict) else {}
            if (
                saved_id is not None
                and enriched < enrichment_limit
                and str(saved.get("verification_status") or "candidate")
                in {"candidate", "need_review"}
                and allowed_hosts
                # A Phase 6 manual notice capture preserves article and robots
                # bytes plus a reviewed official recovery. Generic discovery
                # retries must not overwrite that durable evidence.
                and not _has_phase6_discovery_capture(saved_metadata)
            ):
                try:
                    recovery = active_scanner.enrich_lead(
                        lead,
                        allowed_hosts=allowed_hosts,
                    )
                    metadata = dict(saved.get("metadata") or {})
                    # Keep every plausible government, university, or official
                    # company URL as private recovery evidence. Only a URL that
                    # matches an already registered first-party source may
                    # advance to the private ``official_url_found`` state.
                    # An unregistered .gov.cn/.edu.cn URL can be useful for an
                    # auditor, but it is not sufficient source evidence.
                    resolved_candidates = resolve_official_url_candidates(
                        recovery,
                        database.list_sources(enabled_only=False),
                    )
                    if resolved_candidates:
                        recovery = dict(recovery)
                        recovery["resolved_official_url_candidates"] = resolved_candidates
                    metadata["official_recovery"] = recovery
                    changes: dict[str, Any] = {"metadata": metadata}
                    winner = next(
                        (
                            item
                            for item in resolved_candidates
                            if item.get("candidate_quality") == "job_detail_or_notice"
                            and item.get("domain_status") == "registered_source_match"
                            and item.get("official_source_id")
                        ),
                        None,
                    )
                    if winner:
                        changes.update(
                            {
                                "official_url": winner["url"],
                                "official_source_id": winner.get("official_source_id") or None,
                                "official_domain_status": winner["domain_status"],
                                "verification_status": "official_url_found",
                                "verification_note": (
                                    "第三方线索页面发现官方 URL 候选；"
                                    "来源主体仍需登记的候选会保持待复核状态；"
                                    "尚未核验官方内容字段。"
                                ),
                            }
                        )
                    database.update_candidate_lead(int(saved_id), changes)
                    enriched += 1
                except (DiscoveryAccessLimited, DiscoveryScanError, requests.RequestException, ValueError) as error:
                    # A detail article is optional evidence.  Keep the lead
                    # private and preserve the reason for the next run. A
                    # transient failure must never replace a previously
                    # successful recovery record.
                    metadata = dict(saved.get("metadata") or {})
                    attempt = {
                        "status": "unavailable",
                        "checked_url": str(lead.get("lead_url") or ""),
                        "checked_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
                        "detail": str(error)[:400],
                    }
                    if _has_successful_official_recovery(metadata):
                        metadata["official_recovery_last_attempt"] = attempt
                    else:
                        metadata["official_recovery"] = attempt
                    database.update_candidate_lead(int(saved_id), {"metadata": metadata})
        rows.append(result.as_dict(created_leads=created, enriched_leads=enriched))
    retry_recovery = enrich_persisted_candidate_leads(
        active_scanner,
        database,
        sources=sources,
        source_records=database.list_sources(enabled_only=False),
    )
    replay = recover_persisted_official_url_candidates(database)
    return {
        "scope": "private_discovery_only",
        "observed_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "source_count": len(rows),
        "scanned_source_count": sum(item["status"] == "scanned" for item in rows),
        "failed_source_count": sum(
            item["status"]
            in {"source_unavailable", "access_limited", "configuration_error"}
            for item in rows
        ),
        "created_lead_count": sum(int(item["created_leads"]) for item in rows),
        "items": rows,
        "official_url_replay": replay,
        "persisted_lead_recovery": retry_recovery,
        "navigation_reconciliation": navigation_reconciliation,
        "publication_policy": "第三方页面只产生私有线索；不会生成官方岗位、不会填写官方链接、不会进入学生端。",
    }


def enrich_persisted_candidate_leads(
    scanner: DiscoveryScanner,
    database: Any,
    *,
    sources: Iterable[dict[str, Any]],
    source_records: list[dict[str, Any]],
    max_total: int = 120,
) -> dict[str, int]:
    """Retry unresolved private leads in bounded batches.

    Discovery scans historically enriched only newly discovered links. That
    left older rows permanently stuck at ``candidate`` even though their
    source page was still reachable. This repair pass revisits only registered
    discovery hosts, applies a 24-hour per-lead cooldown, and can advance a
    row only to the private ``official_url_found`` stage.
    """
    source_by_id = {
        str(source.get("id")): source
        for source in sources
        if str(source.get("id") or "").strip()
    }
    candidates = database.list_candidate_leads(limit=5000)
    counts = {"scanned": 0, "enriched": 0, "resolved": 0, "unavailable": 0, "skipped": 0}
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    for row in candidates:
        if counts["scanned"] >= max(1, int(max_total)):
            break
        if str(row.get("verification_status") or "candidate") not in {"candidate", "need_review"}:
            counts["skipped"] += 1
            continue
        source = source_by_id.get(str(row.get("discovery_source_id") or ""))
        if source is None:
            counts["skipped"] += 1
            continue
        config = source.get("scan_config") if isinstance(source.get("scan_config"), dict) else {}
        allowed_hosts = {
            str(item).strip().lower().rstrip(".")
            for item in (config.get("allowed_hosts") or source.get("allowed_hosts") or [])
            if str(item).strip()
        }
        if not allowed_hosts:
            counts["skipped"] += 1
            continue
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        if _has_phase6_discovery_capture(metadata):
            counts["skipped"] += 1
            continue
        prior = metadata.get("official_recovery") if isinstance(metadata, dict) else None
        checked_at = prior.get("checked_at") if isinstance(prior, dict) else None
        if checked_at:
            try:
                checked = datetime.fromisoformat(str(checked_at).replace("Z", "+00:00"))
                if checked.tzinfo is not None and (now - checked.astimezone(now.tzinfo)).total_seconds() < 24 * 3600:
                    counts["skipped"] += 1
                    continue
            except ValueError:
                pass
        counts["scanned"] += 1
        lead = {
            "lead_url": row.get("lead_url"),
            "title": row.get("title"),
            "employer_hint": row.get("employer_hint"),
            "location_hint": row.get("location_hint"),
        }
        try:
            recovery = scanner.enrich_lead(lead, allowed_hosts=allowed_hosts)
            resolved = resolve_official_url_candidates(recovery, source_records)
            recovery = dict(recovery)
            if resolved:
                recovery["resolved_official_url_candidates"] = resolved
            next_metadata = dict(metadata)
            next_metadata["official_recovery"] = recovery
            changes: dict[str, Any] = {"metadata": next_metadata}
            winner = next(
                (
                    item
                    for item in resolved
                    if item.get("candidate_quality") == "job_detail_or_notice"
                    and item.get("domain_status") == "registered_source_match"
                    and item.get("official_source_id")
                ),
                None,
            )
            if winner and not str(row.get("official_url") or "").strip():
                changes.update(
                    {
                        "official_url": winner["url"],
                        "official_source_id": winner.get("official_source_id") or None,
                        "official_domain_status": winner["domain_status"],
                        "verification_status": "official_url_found",
                        "verification_note": "已从已保存第三方线索重试解析出官方 URL 候选；来源主体和内容仍待核验。",
                    }
                )
                counts["resolved"] += 1
            database.update_candidate_lead(int(row["id"]), changes)
            counts["enriched"] += 1
        except (DiscoveryAccessLimited, DiscoveryScanError, requests.RequestException, ValueError) as error:
            next_metadata = dict(metadata)
            attempt = {
                "status": "unavailable",
                "checked_url": str(row.get("lead_url") or ""),
                "checked_at": now.isoformat(),
                "detail": str(error)[:400],
            }
            if _has_successful_official_recovery(next_metadata):
                next_metadata["official_recovery_last_attempt"] = attempt
            else:
                next_metadata["official_recovery"] = attempt
            database.update_candidate_lead(int(row["id"]), {"metadata": next_metadata})
            counts["unavailable"] += 1
    return counts


def extract_discovery_evidence(
    body: str,
    base_url: str,
    *,
    allowed_hosts: set[str],
    official_hosts: set[str] | None = None,
) -> dict[str, Any]:
    """Extract bounded, non-authoritative recovery evidence from an article."""
    # Discovery pages may be a plain-text URL list rather than HTML.  Parse
    # that input as markup intentionally and keep BeautifulSoup's locator
    # heuristic from turning a valid scan into a noisy test warning.
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=MarkupResemblesLocatorWarning)
        soup = BeautifulSoup(str(body)[:2_000_000], "html.parser")
    title = re.sub(r"\s+", " ", soup.title.get_text(" ", strip=True)) if soup.title else ""
    attribution = ""
    for text_node in soup.find_all(string=re.compile("文章来源")):
        attribution_match = re.search(
            r"文章来源\s*[:：]\s*(.{2,160})",
            str(text_node),
        )
        if attribution_match:
            attribution = re.sub(r"\s+", " ", attribution_match.group(1)).strip()
            break

    attachments: list[dict[str, str]] = []
    official_candidates: list[dict[str, str]] = []
    seen_attachments: set[str] = set()
    seen_official: set[str] = set()
    attachment_pattern = re.compile(r"\.(?:xlsx?|docx?|pdf|zip)(?:$|[?#])", re.IGNORECASE)
    # Keep recovery links narrow: a generic "招聘" label is common on ads and
    # training pages, so it is deliberately not enough to classify a link as a
    # likely official destination.  These hints only create private candidates.
    official_text_pattern = re.compile(
        r"原文|官网|公告|报名通知|人民政府|政府|研究所|大学|学院|自然资源|地质|集团|公司|研究院|勘查",
        re.IGNORECASE,
    )
    registered_official_hosts = {
        str(host).strip().lower().rstrip(".")
        for host in (official_hosts or set())
        if str(host).strip()
    }
    # Some aggregators wrap an official destination in a transparent tracking
    # URL. Unwrap only known wrappers and only their URL-valued query fields;
    # the resulting URL still passes the registered-host gate below.
    redirect_wrapper_hosts = {"link.wtturl.cn"}
    redirect_query_keys = ("target", "url", "redirect", "redirect_url", "dest", "destination", "to")

    def unwrap_redirect_wrapper(value: str) -> str:
        current = value
        for _ in range(2):
            parsed = urlparse(current)
            host = (parsed.hostname or "").lower().rstrip(".")
            if host not in redirect_wrapper_hosts:
                break
            query = parse_qs(parsed.query, keep_blank_values=False)
            destination = next(
                (query[key][0] for key in redirect_query_keys if query.get(key)),
                "",
            )
            if not destination:
                break
            current = normalize_recovered_url(
                unquote(html.unescape(destination).replace("\\/", "/"))
            )
        return current

    def consider_link(href: str, label: str) -> None:
        """Record one private recovery link after the same host checks."""
        # Reposts frequently HTML-escape query separators or JSON-escape
        # slashes. Decode only presentation layers; the URL still has to pass
        # the registered first-party host gate below.
        href = normalize_recovered_url(
            unquote(html.unescape(str(href or "").strip()).replace("\\/", "/"))
        )
        href = unwrap_redirect_wrapper(href)
        # Official notices often print a bare institutional domain in prose
        # (for example ``www.example.edu.cn``) instead of turning it into an
        # anchor. It is safe to promote only bare hosts that are already in the
        # first-party registry; arbitrary text never reaches this branch.
        if not re.match(r"^https?://", href, re.IGNORECASE):
            href = "https://" + href.lstrip("/")
        parsed = urlparse(href)
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme not in {"http", "https"} or not host:
            return
        label = re.sub(r"\s+", " ", str(label or "")).strip()
        if is_non_recruitment_official_host(host) or re.search(
            r"备案|公安备案|ICP备案|经营性网站备案", label, re.IGNORECASE
        ):
            return
        if attachment_pattern.search(href):
            if href not in seen_attachments and len(attachments) < 30:
                attachments.append({"url": href, "label": label[:200]})
                seen_attachments.add(href)
            return
        if _host_allowed(href, allowed_hosts):
            return
        host_looks_official = (
            host.endswith(".gov.cn")
            or host.endswith(".edu.cn")
            or any(
                host == root or host.endswith("." + root)
                for root in registered_official_hosts
            )
        )
        if (host_looks_official or official_text_pattern.search(label)) and href not in seen_official:
            if len(official_candidates) < 30:
                official_candidates.append({"url": href, "label": label[:200], "host": host})
                seen_official.add(href)

    for anchor in soup.find_all("a", href=True):
        href = urljoin(
            base_url,
            html.unescape(str(anchor.get("href") or "")).replace("\\/", "/"),
        ).split("#", 1)[0]
        label = re.sub(r"\s+", " ", anchor.get_text(" ", strip=True)).strip()
        consider_link(href, label)

    # Many reposts place the source URL in plain text instead of an anchor.
    # Treat it as a private candidate only; it still must match a registered
    # first-party host (or an explicitly official .gov.cn/.edu.cn host) before
    # any later verifier can use it.
    plain_url_pattern = re.compile(r"https?://[^\s<>\"'，。；！？）】》]+", re.IGNORECASE)
    normalized_body = html.unescape(str(body)).replace("\\/", "/")
    for raw_url in plain_url_pattern.findall(
        soup.get_text(" ", strip=True) + " " + normalized_body
    ):
        consider_link(raw_url.rstrip(".,;:!?)]}"), "正文明文或嵌入 URL")

    # JSON and redirect wrappers often percent-encode the complete official
    # destination (``url=https%3A%2F%2F...``). Keep this extraction private;
    # ``consider_link`` applies the same host and filing-link filters.
    encoded_url_pattern = re.compile(
        r"https?%3a%2f%2f[^\s<>\"'，。；！？）】》]+", re.IGNORECASE
    )
    for encoded_url in encoded_url_pattern.findall(str(body)):
        consider_link(encoded_url.rstrip(".,;:!?)]}"), "嵌入或编码 URL")

    # A first-party domain is frequently written without a scheme in the
    # article body. Search only the explicitly registered official hosts and
    # keep the result private until the official verifier fetches it.
    if registered_official_hosts:
        registered_host_pattern = re.compile(
            r"(?<![@A-Za-z0-9.-])(?:https?://)?(?:"
            + "|".join(
                re.escape(host)
                for host in sorted(registered_official_hosts, key=len, reverse=True)
            )
            + r")(?:/[^\s<>\"'，。；！？）】》]*)?",
            re.IGNORECASE,
        )
        for match in registered_host_pattern.finditer(normalized_body):
            consider_link(match.group(0).rstrip(".,;:!?)]}"), "正文登记的官方域名")
    return {
        "status": "fetched",
        "parser_version": "discovery-evidence-v2",
        "checked_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "checked_url": base_url,
        "page_title": title[:240],
        "source_attribution": attribution[:160],
        "attachment_candidates": attachments,
        "official_link_candidates": official_candidates,
        "note": "第三方页面提取结果仅为私有恢复线索；附件、来源主体和链接必须经过官方证据复核后才能进入岗位流程。",
    }


def _bounded_int(value: Any, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return minimum
    return max(minimum, min(maximum, number))


def _has_phase6_discovery_capture(metadata: dict[str, Any]) -> bool:
    return isinstance(metadata.get("phase6_discovery_capture"), dict)


def _has_successful_official_recovery(metadata: dict[str, Any]) -> bool:
    recovery = metadata.get("official_recovery")
    return isinstance(recovery, dict) and str(recovery.get("status") or "") == "fetched"


def _include_patterns(value: Any) -> tuple[str, ...]:
    if value is None:
        return DiscoveryScanner.DEFAULT_INCLUDE_PATTERNS
    if not isinstance(value, list):
        return DiscoveryScanner.DEFAULT_INCLUDE_PATTERNS
    return tuple(str(item).strip() for item in value if str(item).strip()) or DiscoveryScanner.DEFAULT_INCLUDE_PATTERNS


def _exclude_patterns(value: Any) -> tuple[str, ...]:
    """Return only explicitly configured exclusions.

    Include terms have useful defaults for discovery pages.  Applying those
    same defaults as exclusions when ``exclude_patterns`` is absent would
    reject every relevant link, so an omitted/invalid exclusion list is empty.
    """
    if not isinstance(value, list):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def _url_patterns(value: Any) -> tuple[re.Pattern[str], ...]:
    """Compile an optional source-owned allowlist for discovery article URLs.

    A pattern is intentionally opt-in. Existing channels without a stable
    notice-path grammar keep their current behavior, while a channel such as
    Zhonggong can exclude its navigation and course links before a private
    candidate is created.
    """

    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("lead_url_patterns must be a list of regular expressions")
    compiled: list[re.Pattern[str]] = []
    for raw in value:
        pattern = str(raw).strip()
        if not pattern:
            continue
        try:
            compiled.append(re.compile(pattern, re.IGNORECASE))
        except re.error as error:
            raise ValueError(f"invalid lead_url_patterns expression: {pattern}") from error
    return tuple(compiled)


def _optional_text_patterns(value: Any, *, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list of regular expressions")
    patterns: list[str] = []
    for raw in value:
        pattern = str(raw).strip()
        if not pattern:
            continue
        try:
            re.compile(pattern, re.IGNORECASE)
        except re.error as error:
            raise ValueError(f"invalid {field} expression: {pattern}") from error
        patterns.append(pattern)
    return tuple(patterns)


def _matches_url_patterns(url: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    return any(pattern.search(url) is not None for pattern in patterns)


def _matches(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def _same_or_subdomain(url: str, root_host: str) -> bool:
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    root = str(root_host or "").lower().rstrip(".")
    return bool(host and root and (host == root or host.endswith("." + root)))


def _host_allowed(url: str, allowed_hosts: set[str]) -> bool:
    """Allow only registered hosts and their subdomains.

    Discovery sources may legitimately expose separate first-party subdomains
    (for example ``sydw.huatu.com`` alongside ``www.huatu.com``).  The
    explicit host set keeps that expansion auditable and prevents a page from
    turning the crawler into an arbitrary external-link scanner.
    """
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    return bool(
        host
        and any(host == root or host.endswith("." + root) for root in allowed_hosts)
    )


def _registered_official_hosts(source_records: Iterable[dict[str, Any]]) -> set[str]:
    """Collect first-party hosts without treating them as enabled sources.

    This helper intentionally reads metadata only.  Disabled, candidate and
    manual fallback records are included so a third-party clue can be moved to
    the private ``official_url_found`` stage, while the normal publication and
    source-health gates still decide whether it can be fetched or shown.
    """

    hosts: set[str] = set()
    for source in source_records:
        if not isinstance(source, dict):
            continue
        values: list[Any] = [
            source.get("homepage_url"),
            source.get("listing_url"),
            source.get("application_url"),
        ]
        config = source.get("config")
        if isinstance(config, dict):
            values.extend(config.get("allowed_hosts") or [])
            values.extend(
                config.get(key)
                for key in ("official_evidence_url", "browser_url")
            )
        for value in values:
            raw_value = str(value or "").strip()
            parsed = urlparse(raw_value)
            # ``allowed_hosts`` is intentionally stored as bare host names,
            # while URL fields include a scheme.
            if not parsed.hostname:
                parsed = urlparse("//" + raw_value.lstrip("/"))
            host = (parsed.hostname or "").lower().rstrip(".")
            if host:
                hosts.add(host)
    return hosts


def _response_text(response: Any) -> str:
    content = getattr(response, "content", None)
    if isinstance(content, bytes):
        encoding = getattr(response, "encoding", None)
        # Requests defaults many Chinese pages to ISO-8859-1 when the server
        # omits a charset.  Prefer an explicit HTML declaration, then the
        # detector, before falling back to UTF-8; otherwise Chinese anchor
        # text becomes mojibake and no discovery pattern can match.
        if not encoding or str(encoding).lower().replace("_", "-") in {
            "iso-8859-1",
            "latin-1",
            "ascii",
        }:
            head = content[:16_384]
            declared = re.search(
                rb"charset\s*=\s*[\"']?\s*([A-Za-z0-9._-]+)",
                head,
                flags=re.IGNORECASE,
            )
            encoding = (
                declared.group(1).decode("ascii", errors="ignore")
                if declared
                else getattr(response, "apparent_encoding", None)
            )
        encoding = encoding or "utf-8"
        return content[:1_000_000].decode(str(encoding), errors="replace")
    return str(getattr(response, "text", "") or "")[:1_000_000]
