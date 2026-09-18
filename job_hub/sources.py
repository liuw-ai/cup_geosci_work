from __future__ import annotations

import base64
import binascii
import json
import re
import time
import xml.etree.ElementTree as ET
import zlib
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

from job_hub.config import Settings
from job_hub.matching import (
    clean_text,
    extract_deadline,
    extract_published_date,
    extract_major_tags,
    looks_like_recruitment,
    normalize_url,
)


USER_AGENT = (
    "CUPB-Geoscience-Employment-Information-Service/1.0 "
    "(official-public-source-crawler; contact: site-administrator)"
)


class SourceCollectionError(RuntimeError):
    pass


class SourceSkipped(RuntimeError):
    pass


@dataclass(frozen=True)
class RawPosting:
    title: str
    employer: str
    source_url: str
    application_url: str | None
    text: str
    summary: str
    published_date: str | None
    deadline_date: str | None
    location: str | None
    external_id: str | None = None
    match_text: str | None = None


class OfficialSourceCollector:
    """Fetches only explicitly configured, public and robots-permitted sources."""

    def __init__(
        self,
        settings: Settings,
        session: requests.Session | None = None,
    ) -> None:
        self.settings = settings
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml,"
                "application/json;q=0.9,*/*;q=0.5",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
            }
        )
        self._robots_cache: dict[str, RobotFileParser] = {}

    def collect(self, source: dict[str, Any]) -> list[RawPosting]:
        source_type = source["source_type"]
        if source_type == "manual":
            return []
        if source_type == "rss":
            return self._collect_rss(source)
        if source_type == "json":
            return self._collect_json(source)
        if source_type == "cupb_career":
            return self._collect_cupb_career(source)
        if source_type == "cas_job_board":
            return self._collect_cas_job_board(source)
        if source_type == "successfactors_search":
            return self._collect_successfactors_search(source)
        if source_type in {"html_notice", "landing_page"}:
            return self._collect_html_notice(source)
        raise SourceCollectionError(f"Unsupported source type: {source_type}")

    def _collect_html_notice(self, source: dict[str, Any]) -> list[RawPosting]:
        config = source["config"]
        listing_urls = config.get("listing_urls") or [source["homepage_url"]]
        links: list[tuple[str, str]] = []
        seen_links: set[str] = set()
        item_limit = self._item_limit(source)
        for listing_url in listing_urls:
            response = self._get(listing_url, source)
            soup = BeautifulSoup(response.text, "html.parser")
            for title_hint, detail_url in self._notice_links(
                soup, response.url, source
            ):
                if detail_url in seen_links:
                    continue
                seen_links.add(detail_url)
                links.append((title_hint, detail_url))
                if len(links) >= item_limit:
                    break
            if len(links) >= item_limit:
                break

        postings: list[RawPosting] = []
        for title_hint, detail_url in links:
            try:
                detail_response = self._get(detail_url, source)
                posting = self._extract_html_detail(
                    detail_response.text,
                    detail_response.url,
                    source,
                    title_hint,
                )
                if posting:
                    postings.append(posting)
            except SourceSkipped:
                continue
            except (requests.RequestException, SourceCollectionError):
                continue
            self._wait(source)
        return postings

    def _collect_cupb_career(self, source: dict[str, Any]) -> list[RawPosting]:
        """Collect only concrete CUPB vacancy and recruitment-announcement pages."""
        config = source["config"]
        listing_urls = config.get("listing_urls") or [source["homepage_url"]]
        detail_patterns = config.get(
            "detail_path_patterns",
            [r"/(?:campus|job)/view/", r"/news/view/.+tag/xwzp"],
        )
        item_limit = self._item_limit(source)
        candidates: list[tuple[str, str]] = []
        seen: set[str] = set()

        for listing_url in listing_urls:
            response = self._get(listing_url, source)
            soup = BeautifulSoup(response.text, "html.parser")
            for anchor in soup.find_all("a"):
                href = anchor.get("href")
                title = clean_text(
                    anchor.get("title") or anchor.get_text(" ", strip=True)
                )
                if not href or not title:
                    continue
                detail_url = normalize_url(urljoin(response.url, href))
                parsed = urlparse(detail_url)
                path_and_query = f"{parsed.path}?{parsed.query}"
                if parsed.hostname != "career.cup.edu.cn":
                    continue
                if not any(
                    re.search(pattern, path_and_query, re.IGNORECASE)
                    for pattern in detail_patterns
                ):
                    continue
                if not self._accept_candidate(title, source):
                    continue
                if detail_url in seen:
                    continue
                seen.add(detail_url)
                candidates.append((title, detail_url))
                if len(candidates) >= item_limit:
                    break
            if len(candidates) >= item_limit:
                break

        postings: list[RawPosting] = []
        for title_hint, detail_url in candidates:
            try:
                response = self._get(detail_url, source)
                posting = self._extract_cupb_detail(
                    response.text,
                    response.url,
                    source,
                    title_hint,
                )
                if posting:
                    postings.append(posting)
            except SourceSkipped:
                continue
            except (requests.RequestException, SourceCollectionError):
                continue
            self._wait(source)
        return postings

    def _collect_cas_job_board(self, source: dict[str, Any]) -> list[RawPosting]:
        """Collect public job cards from the Chinese Academy of Sciences job board."""
        response = self._get(source["homepage_url"], source)
        soup = BeautifulSoup(response.text, "html.parser")
        selector = source["config"].get(
            "job_link_selector",
            "a[href*='toRecruitment.do'][href*='postInfo.id']",
        )
        item_limit = self._item_limit(source)
        candidates: list[tuple[str, str, str, str | None]] = []
        seen: set[str] = set()

        for anchor in soup.select(selector):
            href = anchor.get("href")
            title = clean_text(anchor.get("title") or anchor.get_text(" ", strip=True))
            if not href or not title:
                continue
            detail_url = normalize_url(urljoin(response.url, href))
            if detail_url in seen:
                continue
            seen.add(detail_url)
            row = anchor.find_parent("tr")
            cells = row.find_all("td", recursive=False) if row else []
            employer = (
                clean_text(cells[1].get_text(" ", strip=True))
                if len(cells) >= 2
                else source["publisher"]
            )
            location = (
                clean_text(cells[2].get_text(" ", strip=True))
                if len(cells) >= 3
                else None
            )
            candidates.append((title, detail_url, employer, location))
            if len(candidates) >= item_limit:
                break

        postings: list[RawPosting] = []
        for title, detail_url, employer, location in candidates:
            try:
                detail_response = self._get(detail_url, source)
                posting = self._extract_cas_job_detail(
                    detail_response.text,
                    detail_response.url,
                    title,
                    employer,
                    location,
                )
                postings.append(posting)
            except SourceSkipped:
                continue
            except (requests.RequestException, SourceCollectionError):
                continue
            self._wait(source)
        return postings

    def _collect_successfactors_search(
        self, source: dict[str, Any]
    ) -> list[RawPosting]:
        """Collect keyword-filtered public results from a SuccessFactors job board."""
        config = source["config"]
        search_url = config.get("search_url", source["homepage_url"])
        locale = config.get("locale", "en_US")
        queries = config.get("queries", [])
        if not queries:
            raise SourceCollectionError("successfactors_search requires configured queries")
        item_limit = self._item_limit(source)
        postings: list[RawPosting] = []
        seen: set[str] = set()

        for query in queries:
            params = urlencode({"q": query, "locale": locale})
            separator = "&" if "?" in search_url else "?"
            response = self._get(f"{search_url}{separator}{params}", source)
            soup = BeautifulSoup(response.text, "html.parser")
            for row in soup.select("tr.data-row"):
                anchor = row.select_one("a.jobTitle-link[href*='/job/']")
                if anchor is None:
                    continue
                title = clean_text(anchor.get_text(" ", strip=True))
                detail_url = normalize_url(urljoin(response.url, anchor["href"]))
                if not title or detail_url in seen:
                    continue
                seen.add(detail_url)
                location_node = row.select_one(
                    ".colLocation .jobLocation, .jobLocation"
                )
                location = clean_text(
                    location_node.get_text(" ", strip=True) if location_node else ""
                ) or None
                row_text = clean_text(row.get_text(" ", strip=True))
                postings.append(
                    RawPosting(
                        title=title,
                        employer=source["publisher"],
                        source_url=detail_url,
                        application_url=detail_url,
                        text=(
                            f"{title} {source['publisher']} {location or ''} "
                            f"{row_text} Search topic: {query}"
                        ),
                        match_text=f"{title} {row_text}",
                        summary=(
                            f"{source['publisher']} 官方招聘岗位"
                            f"{'，地点：' + location if location else ''}"
                        ),
                        published_date=extract_published_date(row_text),
                        deadline_date=extract_deadline(row_text),
                        location=location,
                        external_id=self._external_id_from_url(detail_url),
                    )
                )
                if len(postings) >= item_limit:
                    return postings
            self._wait(source)
        return postings

    def _collect_rss(self, source: dict[str, Any]) -> list[RawPosting]:
        response = self._get(source["homepage_url"], source)
        try:
            root = ET.fromstring(response.content)
        except ET.ParseError as error:
            raise SourceCollectionError(f"Invalid RSS XML: {error}") from error

        config = source["config"]
        entries = list(root.findall(".//item"))
        if not entries:
            entries = list(root.findall(".//{http://www.w3.org/2005/Atom}entry"))
        postings: list[RawPosting] = []
        for entry in entries[: self.settings.max_source_items]:
            title = clean_text(
                self._xml_text(entry, "title")
                or self._xml_text(entry, "{http://www.w3.org/2005/Atom}title")
            )
            link = self._rss_link(entry)
            description = clean_text(
                self._xml_text(entry, "description")
                or self._xml_text(entry, "content")
                or self._xml_text(entry, "{http://www.w3.org/2005/Atom}summary")
                or self._xml_text(entry, "{http://www.w3.org/2005/Atom}content")
            )
            combined = f"{title} {description}"
            if not title or not link:
                continue
            if not config.get("accept_all_entries") and not self._accept_candidate(
                combined, source
            ):
                continue
            postings.append(
                RawPosting(
                    title=title,
                    employer=config.get("employer_hint", source["publisher"]),
                    source_url=normalize_url(urljoin(response.url, link)),
                    application_url=None,
                    text=combined,
                    summary=description[:420],
                    published_date=extract_published_date(
                        self._xml_text(entry, "pubDate")
                        or self._xml_text(entry, "published")
                        or self._xml_text(entry, "updated")
                    ),
                    deadline_date=extract_deadline(combined),
                    location=config.get("location_hint"),
                    external_id=self._xml_text(entry, "guid") or None,
                )
            )
        return postings

    def _collect_json(self, source: dict[str, Any]) -> list[RawPosting]:
        response = self._get(source["homepage_url"], source)
        try:
            body = response.json()
        except ValueError as error:
            raise SourceCollectionError(f"Invalid JSON feed: {error}") from error
        config = source["config"]
        items = self._nested_value(body, config.get("items_path", "items"))
        if not isinstance(items, list):
            raise SourceCollectionError("Configured JSON items_path did not resolve to a list")
        postings: list[RawPosting] = []
        for item in items[: self.settings.max_source_items]:
            if not isinstance(item, dict):
                continue
            title = clean_text(str(self._nested_value(item, config.get("title_key", "title")) or ""))
            link_value = self._nested_value(item, config.get("url_key", "url"))
            if not title or not link_value:
                continue
            link = normalize_url(urljoin(response.url, str(link_value)))
            body_text = clean_text(
                str(self._nested_value(item, config.get("description_key", "description")) or "")
            )
            combined = f"{title} {body_text}"
            if not config.get("accept_all_entries") and not self._accept_candidate(
                combined, source
            ):
                continue
            postings.append(
                RawPosting(
                    title=title,
                    employer=clean_text(
                        str(
                            self._nested_value(
                                item,
                                config.get("employer_key", "employer"),
                            )
                            or config.get("employer_hint", source["publisher"])
                        )
                    ),
                    source_url=link,
                    application_url=normalize_url(
                        urljoin(
                            response.url,
                            str(
                                self._nested_value(
                                    item, config.get("application_url_key", "application_url")
                                )
                                or link
                            ),
                        )
                    ),
                    text=combined,
                    summary=body_text[:420],
                    published_date=extract_published_date(
                        str(
                            self._nested_value(
                                item, config.get("published_date_key", "published_at")
                            )
                            or ""
                        )
                    ),
                    deadline_date=extract_deadline(combined),
                    location=clean_text(
                        str(
                            self._nested_value(item, config.get("location_key", "location"))
                            or config.get("location_hint", "")
                        )
                    )
                    or None,
                    external_id=str(
                        self._nested_value(item, config.get("id_key", "id")) or ""
                    )
                    or None,
                )
            )
        return postings

    def _notice_links(
        self,
        soup: BeautifulSoup,
        base_url: str,
        source: dict[str, Any],
    ) -> list[tuple[str, str]]:
        config = source["config"]
        selector = config.get("listing_selector")
        anchors = soup.select(selector) if selector else soup.find_all("a")
        allowed_hosts = set(config.get("allowed_hosts", []))
        base_parts = urlparse(base_url)
        allowed_hosts.add(base_parts.netloc.lower())
        if base_parts.hostname:
            allowed_hosts.add(base_parts.hostname.lower())
        include_patterns = config.get("include_patterns", [])
        detail_path_patterns = config.get("detail_path_patterns", [])
        exclude_patterns = config.get("exclude_patterns", [])
        links: list[tuple[str, str]] = []
        seen: set[str] = set()
        for anchor in anchors:
            href = anchor.get("href")
            title = clean_text(anchor.get("title") or anchor.get_text(" ", strip=True))
            if not href or not title:
                continue
            detail_url = normalize_url(urljoin(base_url, href))
            parsed = urlparse(detail_url)
            if parsed.scheme not in {"http", "https"}:
                continue
            if (
                parsed.netloc.lower() not in allowed_hosts
                and (parsed.hostname or "").lower() not in allowed_hosts
            ):
                continue
            if re.search(r"\.(?:pdf|docx?|xlsx?|zip|rar)$", parsed.path, re.IGNORECASE):
                continue
            candidate = f"{title} {detail_url}"
            if any(
                re.search(pattern, candidate, re.IGNORECASE)
                for pattern in exclude_patterns
            ):
                continue
            if not self._accept_candidate(candidate, source):
                continue
            if include_patterns and not any(
                re.search(pattern, candidate, re.IGNORECASE) for pattern in include_patterns
            ):
                continue
            path_and_query = f"{parsed.path}?{parsed.query}"
            if detail_path_patterns and not any(
                re.search(pattern, path_and_query, re.IGNORECASE)
                for pattern in detail_path_patterns
            ):
                continue
            if detail_url in seen:
                continue
            seen.add(detail_url)
            links.append((title, detail_url))
            if len(links) >= self._item_limit(source):
                break
        return links

    def _extract_html_detail(
        self,
        document: str,
        source_url: str,
        source: dict[str, Any],
        title_hint: str,
    ) -> RawPosting | None:
        soup = BeautifulSoup(document, "html.parser")
        config = source["config"]
        title_node = soup.select_one(config.get("title_selector", "h1"))
        title = clean_text(title_node.get_text(" ", strip=True) if title_node else "")
        if not title:
            title = clean_text(soup.title.get_text(" ", strip=True) if soup.title else title_hint)
        content_selector = config.get("content_selector")
        content_node = soup.select_one(content_selector) if content_selector else None
        if not content_node:
            content_node = soup.find("article") or soup.find("main") or soup.body
        if content_node:
            for selector in config.get("remove_selectors", []):
                for node in content_node.select(selector):
                    node.decompose()
        body_text = clean_text(
            content_node.get_text(" ", strip=True) if content_node else document
        )
        combined = f"{title} {body_text}"
        if not self._accept_candidate(combined, source):
            return None
        application_url = self._find_application_url(soup, source_url)
        published_date = self._published_date_from_meta(soup) or extract_published_date(
            body_text
        )
        employer_selector = config.get("employer_selector")
        employer_node = (
            soup.select_one(employer_selector) if employer_selector else None
        )
        employer = clean_text(
            employer_node.get_text(" ", strip=True) if employer_node else ""
        ) or config.get("employer_hint", source["publisher"])
        return RawPosting(
            title=title,
            employer=employer,
            source_url=normalize_url(source_url),
            application_url=application_url,
            text=combined,
            summary=body_text[:420],
            published_date=published_date,
            deadline_date=extract_deadline(body_text),
            location=config.get("location_hint"),
        )

    def _extract_cupb_detail(
        self,
        document: str,
        source_url: str,
        source: dict[str, Any],
        title_hint: str,
    ) -> RawPosting | None:
        soup = BeautifulSoup(document, "html.parser")
        title_node = soup.select_one(
            ".details-title h5, .title-message h5, h1, h2"
        )
        title = clean_text(
            title_node.get_text(" ", strip=True) if title_node else title_hint
        )
        embedded_html = self._decode_cupb_embedded_content(document)
        content_soup = BeautifulSoup(embedded_html or document, "html.parser")
        content_node = content_soup.select_one(
            ".aContent, .zp-details, .common-view, main, article"
        ) or content_soup
        if content_node:
            for selector in (
                ".common-view-tips",
                ".operation",
                ".share",
                ".footer",
                "script",
                "style",
            ):
                for node in content_node.select(selector):
                    node.decompose()
        body_text = clean_text(content_node.get_text(" ", strip=True))
        metadata_node = soup.select_one(".zp-details, .common-view")
        metadata_text = clean_text(
            metadata_node.get_text(" ", strip=True) if metadata_node else ""
        )
        # Header text contains site-wide anti-fraud and employment-guidance copy.
        # Keep it for dates, but do not let those unrelated words reject a genuine
        # job notice through source-level exclusion patterns.
        combined = clean_text(f"{title} {body_text}")
        if not self._accept_candidate(combined, source):
            return None
        employer_node = soup.select_one(".title-message .name")
        employer = clean_text(
            employer_node.get_text(" ", strip=True) if employer_node else ""
        ) or self._employer_from_title(title, source["publisher"])
        fields = self._cupb_table_fields(content_soup)
        matching_fields = " ".join(
            value
            for key, value in fields.items()
            if key in {"岗位", "专业范围", "面向对象", "学历要求"}
        )
        # CUPB notices often start with a long employer introduction. When their
        # structured job table is present, use it as matching evidence so a unit's
        # industry description cannot masquerade as a candidate's qualification.
        match_text = clean_text(f"{title} {matching_fields}") or combined
        summary_fields = [
            f"{key}：{value}"
            for key, value in fields.items()
            if key in {"岗位", "专业范围", "面向对象", "工作地点"}
        ]
        return RawPosting(
            title=title,
            employer=employer,
            source_url=normalize_url(source_url),
            application_url=(
                self._find_application_url(content_soup, source_url)
                or self._find_application_url(soup, source_url)
            ),
            text=combined,
            summary=clean_text("；".join(summary_fields))[:420] or body_text[:420],
            published_date=(
                extract_published_date(metadata_text)
                or extract_published_date(body_text)
            ),
            deadline_date=extract_deadline(body_text),
            location=fields.get("工作地点"),
            external_id=self._external_id_from_url(source_url),
            match_text=match_text,
        )

    @staticmethod
    def _decode_cupb_embedded_content(document: str) -> str | None:
        """Decode public UEditor content embedded by the CUPB announcement page.

        Some public detail pages ship their article body in a zlib-compressed,
        Base64-encoded JavaScript literal. This decodes only data already returned
        by that page; it does not use a login, private endpoint, or browser bypass.
        """
        match = re.search(
            r"""Base64\.decode\s*\(\s*unzip\s*\(\s*[\"'](?P<payload>[^\"']+)[\"']""",
            document,
        )
        if not match:
            return None
        try:
            compressed = base64.b64decode(match.group("payload"), validate=True)
            packed_text = zlib.decompress(compressed).decode("utf-8")
            _, encoded_html = packed_text.split(None, 1)
            html = base64.b64decode(encoded_html, validate=True).decode("utf-8")
        except (
            ValueError,
            UnicodeDecodeError,
            binascii.Error,
            zlib.error,
        ):
            return None
        # The page's helper leaves a short view marker before the HTML fragment.
        return re.sub(r"^view\d+d\s*", "", html).strip() or None

    @staticmethod
    def _cupb_table_fields(soup: BeautifulSoup) -> dict[str, str]:
        """Read label/value rows from public CUPB recruitment-announcement tables."""
        aliases = {
            "岗位": "岗位",
            "岗位需求": "岗位",
            "职位": "岗位",
            "专业范围": "专业范围",
            "专业要求": "专业范围",
            "需求专业": "专业范围",
            "面向对象": "面向对象",
            "学历要求": "学历要求",
            "工作地点": "工作地点",
        }
        fields: dict[str, str] = {}
        for row in soup.select("table tr"):
            cells = [clean_text(cell.get_text(" ", strip=True)) for cell in row.select("th, td")]
            cells = [cell for cell in cells if cell]
            if len(cells) < 2:
                continue
            label = aliases.get(cells[0].rstrip("：:"))
            if not label:
                continue
            value = clean_text(" ".join(cells[1:]))
            if value:
                fields[label] = value
        return fields

    def _extract_cas_job_detail(
        self,
        document: str,
        source_url: str,
        title_hint: str,
        employer: str,
        location_hint: str | None,
    ) -> RawPosting:
        soup = BeautifulSoup(document, "html.parser")
        page_text = clean_text(soup.get_text(" ", strip=True))
        title_node = soup.select_one("h1, h2, .post_title, .content_title")
        candidate_title = clean_text(
            title_node.get_text(" ", strip=True) if title_node else ""
        )
        generic_titles = {
            "岗位描述",
            "岗位职责",
            "应聘条件",
            "招聘岗位",
            "联系方式",
        }
        title = (
            candidate_title
            if candidate_title and candidate_title not in generic_titles
            else title_hint
        )
        if title not in page_text:
            page_text = f"{title} {page_text}"
        location = self._label_value(
            page_text,
            "工作地点",
            (
                "工作经验",
                "学历要求",
                "招聘人数",
                "薪资水平",
                "发布时间",
                "截止时间",
                "学科领域",
                "专业描述",
                "岗位描述",
            ),
        ) or location_hint
        discipline = self._label_value(
            page_text,
            "学科领域",
            ("专业描述", "专业技术等级", "招聘类型", "岗位描述", "岗位职责"),
        )
        professional_requirements = self._label_value(
            page_text,
            "专业描述",
            (
                "专业技术等级",
                "招聘类型",
                "岗位描述",
                "岗位职责",
                "应聘条件",
            ),
        )
        summary = self._cas_summary(page_text, title)
        return RawPosting(
            title=title,
            employer=employer,
            source_url=normalize_url(source_url),
            application_url=normalize_url(source_url),
            text=page_text,
            summary=summary,
            published_date=extract_published_date(page_text),
            deadline_date=extract_deadline(page_text),
            location=location,
            external_id=self._external_id_from_url(source_url),
            match_text=clean_text(
                f"{title} {discipline or ''} {professional_requirements or ''}"
            ),
        )

    def _get(self, url: str, source: dict[str, Any]) -> requests.Response:
        normalized = normalize_url(url)
        if not self._robots_allowed(normalized):
            raise SourceSkipped(f"robots.txt does not permit collection: {normalized}")
        try:
            response = self.session.get(
                normalized,
                timeout=self.settings.request_timeout_seconds,
                allow_redirects=True,
            )
            response.raise_for_status()
            if (
                not response.encoding
                or response.encoding.lower() in {"iso-8859-1", "ascii"}
            ) and response.apparent_encoding:
                response.encoding = response.apparent_encoding
            return response
        except requests.RequestException as error:
            raise SourceCollectionError(f"Request failed for {normalized}: {error}") from error

    def _robots_allowed(self, url: str) -> bool:
        parsed = urlparse(url)
        root = f"{parsed.scheme}://{parsed.netloc}"
        parser = self._robots_cache.get(root)
        if parser is None:
            parser = RobotFileParser()
            robots_url = urljoin(root, "/robots.txt")
            try:
                response = self.session.get(
                    robots_url,
                    timeout=min(self.settings.request_timeout_seconds, 10),
                    allow_redirects=True,
                )
                if response.status_code == 404:
                    parser.parse([])
                elif response.ok:
                    parser.parse(response.text.splitlines())
                else:
                    raise SourceSkipped(
                        f"Unable to verify robots.txt for {root}: HTTP {response.status_code}"
                    )
            except requests.RequestException as error:
                raise SourceSkipped(
                    f"Unable to verify robots.txt for {root}: {error}"
                ) from error
            self._robots_cache[root] = parser
        return parser.can_fetch(USER_AGENT, url)

    @staticmethod
    def _accept_candidate(value: str, source: dict[str, Any]) -> bool:
        config = source["config"]
        if config.get("accept_all_entries"):
            return True
        normalized = clean_text(value)
        exclude_patterns = config.get("exclude_patterns", [])
        if any(
            re.search(pattern, normalized, re.IGNORECASE)
            for pattern in exclude_patterns
        ):
            return False
        has_recruitment_word = looks_like_recruitment(normalized)
        has_major_match = bool(extract_major_tags(normalized))
        if config.get("require_recruitment_word") and not has_recruitment_word:
            return False
        if config.get("require_major_match") and not has_major_match:
            return False
        include_keywords = config.get("include_keywords", [])
        if include_keywords and not any(
            keyword.lower() in normalized.lower() for keyword in include_keywords
        ):
            return False
        return has_recruitment_word or has_major_match

    def _item_limit(self, source: dict[str, Any]) -> int:
        value = source["config"].get("max_items", self.settings.max_source_items)
        try:
            return max(1, min(int(value), self.settings.max_source_items))
        except (TypeError, ValueError):
            return self.settings.max_source_items

    @staticmethod
    def _external_id_from_url(url: str) -> str:
        parsed = urlparse(url)
        return f"{parsed.path}?{parsed.query}".rstrip("?")

    def _wait(self, source: dict[str, Any]) -> None:
        try:
            seconds = float(
                source["config"].get("request_interval_seconds", 0.75)
            )
        except (TypeError, ValueError):
            seconds = 0.75
        if seconds > 0:
            time.sleep(min(seconds, 5.0))

    @staticmethod
    def _employer_from_title(title: str, fallback: str) -> str:
        markers = (
            "校园招聘",
            "社会招聘",
            "公开招聘",
            "招聘公告",
            "招聘简章",
            "招聘启事",
            "招聘",
            "招录",
            "招考",
            "人才引进",
            "202",
        )
        positions = [title.find(marker) for marker in markers if title.find(marker) > 1]
        if not positions:
            return fallback
        candidate = title[: min(positions)].strip(" -|丨：:，,。.")
        return candidate if len(candidate) >= 3 else fallback

    @staticmethod
    def _label_value(
        text: str,
        label: str,
        end_labels: tuple[str, ...],
    ) -> str | None:
        match = re.search(
            rf"{re.escape(label)}\s*[:：]\s*(.+?)(?="
            + "|".join(re.escape(item) + r"\s*[:：]" for item in end_labels)
            + r"|$)",
            text,
        )
        if not match:
            return None
        value = clean_text(match.group(1))
        return value[:200] or None

    @staticmethod
    def _cas_summary(page_text: str, title: str) -> str:
        fields = []
        for label in ("所在部门", "学历要求", "工作地点", "招聘人数", "截止时间"):
            match = re.search(
                rf"{re.escape(label)}\s*[:：]\s*(.+?)(?="
                r"(?:工作经验|学历要求|工作地点|招聘人数|薪资水平|发布时间|"
                r"截止时间|学科领域|专业描述|招聘类型|岗位描述)\s*[:：]|$)",
                page_text,
            )
            if match:
                fields.append(f"{label}：{clean_text(match.group(1))}")
        return clean_text("；".join(fields))[:420] or title

    @staticmethod
    def _find_application_url(soup: BeautifulSoup, base_url: str) -> str | None:
        for anchor in soup.find_all("a"):
            label = clean_text(anchor.get_text(" ", strip=True) or anchor.get("title"))
            href = anchor.get("href")
            context_node = anchor.find_parent("p") or anchor.parent
            context = clean_text(
                context_node.get_text(" ", strip=True) if context_node else label
            )
            if href and any(
                word in f"{label} {context}"
                for word in (
                    "报名",
                    "申请",
                    "投递",
                    "网申",
                    "应聘",
                    "招聘平台",
                    "招聘网",
                )
            ):
                return normalize_url(urljoin(base_url, href))
        return None

    @staticmethod
    def _published_date_from_meta(soup: BeautifulSoup) -> str | None:
        for selector in (
            "meta[property='article:published_time']",
            "meta[name='publishdate']",
            "meta[name='date']",
            "time",
        ):
            element = soup.select_one(selector)
            if not element:
                continue
            value = element.get("content") or element.get("datetime") or element.get_text()
            parsed = extract_published_date(value)
            if parsed:
                return parsed
        return None

    @staticmethod
    def _xml_text(entry: ET.Element, tag: str) -> str:
        element = entry.find(tag)
        return element.text if element is not None and element.text else ""

    @staticmethod
    def _rss_link(entry: ET.Element) -> str:
        link = OfficialSourceCollector._xml_text(entry, "link")
        if link:
            return link
        atom_link = entry.find("{http://www.w3.org/2005/Atom}link")
        if atom_link is not None:
            return atom_link.get("href", "")
        return ""

    @staticmethod
    def _nested_value(value: Any, path: str) -> Any:
        current = value
        for part in path.split("."):
            if not part:
                continue
            if not isinstance(current, dict):
                return None
            current = current.get(part)
        return current


def load_source_registry(path: str) -> list[dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except OSError as error:
        raise SourceCollectionError(f"Cannot read source registry: {error}") from error
    if not isinstance(payload, list):
        raise SourceCollectionError("The source registry must be a JSON list")
    required = {
        "id",
        "name",
        "publisher",
        "homepage_url",
        "source_type",
        "category",
        "source_tier",
    }
    for source in payload:
        missing = required.difference(source)
        if missing:
            raise SourceCollectionError(
                f"Source {source.get('id', '<unknown>')} is missing: {', '.join(sorted(missing))}"
            )
        source.setdefault("config", {})
        source.setdefault("enabled", True)
    return payload
