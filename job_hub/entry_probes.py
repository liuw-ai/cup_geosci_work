"""Read-only probing of official national-energy entry points.

This module is deliberately separate from vacancy collection. It checks
robots, redirects and public HTML structure, then records why an entry is
usable, dynamic, blocked or unavailable. A successful page probe never creates
a job and never means that matching vacancies exist.
"""

from __future__ import annotations

import json
import re
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from job_hub.contracts import (
    ContractValidationError,
    validate_national_entry_probe_run,
    validate_national_entry_targets,
)
from job_hub.sources import USER_AGENT, load_source_registries


PROJECT_ROOT = Path(__file__).resolve().parent.parent
TARGETS_PATH = PROJECT_ROOT / "data" / "national_entry_targets.json"
RUN_PATH = PROJECT_ROOT / "data" / "national_entry_probe_runs.json"
SOURCE_REGISTRY_PATH = PROJECT_ROOT / "data" / "sources.json"
PROVINCIAL_SOURCE_REGISTRY_PATH = PROJECT_ROOT / "data" / "provincial_sources.json"

TRANSIENT_HTTP_STATUS = frozenset({408, 429, 500, 502, 503, 504})
ACCESS_POLICY_STATUS = frozenset({401, 403, 412, 429})
RECRUITMENT_HINTS = (
    "招聘",
    "人才",
    "校招",
    "校园",
    "职位",
    "公告",
    "招录",
    "招考",
    "career",
    "careers",
    "job",
    "jobs",
    "recruit",
)


class ProbeRequestError(requests.RequestException):
    """Transport failure carrying the number of attempts already made."""

    def __init__(self, original: requests.RequestException, retries_used: int) -> None:
        super().__init__(str(original))
        self.original = original
        self.retries_used = retries_used


def _registered_source_ids() -> set[str]:
    paths = [SOURCE_REGISTRY_PATH]
    if PROVINCIAL_SOURCE_REGISTRY_PATH.exists():
        paths.append(PROVINCIAL_SOURCE_REGISTRY_PATH)
    return {
        str(item["id"])
        for item in load_source_registries([str(path) for path in paths])
        if item.get("id")
    }


def load_national_entry_targets(
    path: Path | None = None,
    *,
    source_ids: set[str] | None = None,
) -> dict[str, Any]:
    target_path = path or TARGETS_PATH
    try:
        payload = json.loads(target_path.read_text(encoding="utf-8"))
        return validate_national_entry_targets(
            payload,
            source_ids=_registered_source_ids() if source_ids is None else source_ids,
        )
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Cannot read national entry targets: {target_path}") from error
    except ContractValidationError as error:
        raise ValueError(f"national_entry_targets.json is invalid: {error}") from error


def load_national_entry_probe_run(
    path: Path | None = None,
    *,
    source_ids: set[str] | None = None,
) -> dict[str, Any]:
    run_path = path or RUN_PATH
    try:
        payload = json.loads(run_path.read_text(encoding="utf-8"))
        return validate_national_entry_probe_run(
            payload,
            source_ids=_registered_source_ids() if source_ids is None else source_ids,
        )
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Cannot read national entry probe run: {run_path}") from error
    except ContractValidationError as error:
        raise ValueError(f"national_entry_probe_runs.json is invalid: {error}") from error


def national_entry_probe_summary(run: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = run or load_national_entry_probe_run()
    systems = payload["systems"]
    attempts = payload["attempts"]
    classifications = Counter(str(item["classification"]) for item in attempts)
    statuses = Counter(str(item["status"]) for item in systems)
    return {
        "observed_on": payload["observed_on"],
        "environment": payload["environment"],
        "system_count": len(systems),
        "attempt_count": len(attempts),
        "systems_with_recruitment_links": sum(
            bool(item["recruitment_links"]) for item in systems
        ),
        "status_counts": dict(sorted(statuses.items())),
        "classification_counts": dict(sorted(classifications.items())),
        "scope_note": (
            "入口探测只记录公开页面和访问策略；不采集岗位，不代表存在或不存在匹配岗位。"
        ),
    }


class PublicEntryProbeRunner:
    """Probe registered public entries with bounded, compliant retries."""

    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        timeout_seconds: int = 10,
        retries: int = 1,
        backoff_seconds: float = 0.25,
    ) -> None:
        if timeout_seconds < 1:
            raise ValueError("timeout_seconds must be positive")
        if retries < 0 or retries > 4:
            raise ValueError("retries must be between 0 and 4")
        if backoff_seconds < 0:
            raise ValueError("backoff_seconds must be non-negative")
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.5",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
            }
        )
        self.timeout_seconds = timeout_seconds
        self.retries = retries
        self.backoff_seconds = backoff_seconds

    def run(
        self,
        targets: dict[str, Any],
        *,
        system_id: str | None = None,
        observed_on: str | None = None,
        environment: str = "local-network",
    ) -> dict[str, Any]:
        selected = [
            item
            for item in targets["systems"]
            if not system_id or item["system_id"] == system_id
        ]
        if not selected:
            raise ValueError(f"Unknown national entry system: {system_id}")
        date_value = observed_on or datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
        attempts: list[dict[str, Any]] = []
        systems: list[dict[str, Any]] = []
        for system in selected:
            system_attempts = [
                self._probe_entry(system, entry) for entry in system["entries"]
            ]
            attempts.extend(system_attempts)
            systems.append(self._summarize_system(system, system_attempts))
        payload = {
            "version": 1,
            "observed_on": date_value,
            "environment": environment,
            "description": (
                "只读探测结果。入口可访问、动态、受限或故障均不等于岗位数量；"
                "岗位必须经过独立的官方原文采集和证据审计。"
            ),
            "systems": systems,
            "attempts": attempts,
        }
        return validate_national_entry_probe_run(
            payload,
            source_ids={str(item["source_id"]) for item in selected},
        )

    def _probe_entry(
        self,
        system: dict[str, Any],
        entry: dict[str, Any],
    ) -> dict[str, Any]:
        url = str(entry["url"])
        parsed = urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        base = {
            "system_id": system["system_id"],
            "source_id": system["source_id"],
            "entry_id": entry["entry_id"],
            "url": url,
            "robots_url": robots_url,
            "robots_http_status": None,
            "robots_allowed": None,
            "http_status": None,
            "final_url": None,
            "content_type": "",
            "title": "",
            "classification": "source_unavailable",
            "discovered_links": [],
            "recruitment_links": [],
            "retries": 0,
            "error_class": "",
            "error_detail": "",
            "text_excerpt": "",
        }
        try:
            robots, robot_retries = self._request(robots_url)
            base["retries"] += robot_retries
            base["robots_http_status"] = int(robots.status_code)
            if robots.status_code == 404:
                allowed = True
            elif 200 <= robots.status_code < 300:
                parser = RobotFileParser()
                parser.parse(self._response_text(robots).splitlines())
                allowed = parser.can_fetch(USER_AGENT, url)
            elif robots.status_code in ACCESS_POLICY_STATUS:
                base["classification"] = "access_policy_block"
                base["error_class"] = f"http_{robots.status_code}"
                base["error_detail"] = "robots.txt access policy prevented entry verification"
                return base
            else:
                base["classification"] = "source_unavailable"
                base["error_class"] = f"http_{robots.status_code}"
                base["error_detail"] = "robots.txt could not be verified"
                return base
            base["robots_allowed"] = bool(allowed)
            if not allowed:
                base["classification"] = "robots_blocked"
                base["error_class"] = "robots_disallow"
                base["error_detail"] = "robots.txt disallows the registered public entry"
                return base
        except requests.RequestException as error:
            base["retries"] += getattr(error, "retries_used", 0)
            base["classification"] = "source_unavailable"
            base["error_class"] = self._error_class(error)
            base["error_detail"] = self._error_detail(error)
            return base

        try:
            response, page_retries = self._request(url)
            base["retries"] += page_retries
        except requests.RequestException as error:
            base["retries"] += getattr(error, "retries_used", 0)
            base["classification"] = "source_unavailable"
            base["error_class"] = self._error_class(error)
            base["error_detail"] = self._error_detail(error)
            return base

        base["http_status"] = int(response.status_code)
        base["final_url"] = str(getattr(response, "url", "") or url)
        base["content_type"] = str(response.headers.get("Content-Type", ""))
        if response.status_code in ACCESS_POLICY_STATUS:
            base["classification"] = "access_policy_block"
            base["error_class"] = f"http_{response.status_code}"
            base["error_detail"] = "public entry returned an access-policy response"
            return base
        if response.status_code == 404:
            base["classification"] = "soft_not_found"
            base["error_class"] = "http_404"
            base["error_detail"] = "registered entry returned HTTP 404"
            return base
        if response.status_code < 200 or response.status_code >= 400:
            base["classification"] = "source_unavailable"
            base["error_class"] = f"http_{response.status_code}"
            base["error_detail"] = "registered entry returned a non-success response"
            return base

        final_host = (urlparse(base["final_url"]).hostname or "").lower()
        allowed_hosts = {str(host).lower() for host in entry["official_hosts"]}
        if final_host and final_host not in allowed_hosts:
            base["classification"] = "redirected_outside_entry"
            base["error_class"] = "redirect_host_mismatch"
            base["error_detail"] = f"redirected to unregistered host: {final_host}"
            return base

        body = self._response_text(response)
        base["text_excerpt"] = re.sub(r"\s+", " ", body[:400]).strip()
        content_type = base["content_type"].lower()
        if "html" not in content_type and not body.lstrip().startswith("<"):
            base["classification"] = "non_html"
            base["error_class"] = "unexpected_content_type"
            base["error_detail"] = "entry did not return an HTML document"
            return base

        soup = BeautifulSoup(body, "html.parser")
        base["title"] = re.sub(
            r"\s+",
            " ",
            soup.title.get_text(" ", strip=True) if soup.title else "",
        ).strip()[:240]
        discovered, recruitment = self._links(
            soup,
            base["final_url"],
            allowed_hosts,
            system["recruitment_keywords"],
            int(system["max_links"]),
        )
        base["discovered_links"] = discovered
        base["recruitment_links"] = recruitment
        visible_text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).strip()
        script_count = len(soup.find_all("script"))
        if len(visible_text) < 120 and script_count > 0 and not discovered:
            base["classification"] = "accessible_dynamic_shell"
            base["error_class"] = "dynamic_html_shell"
            base["error_detail"] = "HTML is accessible but contains no stable public links"
        else:
            base["classification"] = "accessible_html"
            base["error_class"] = ""
            base["error_detail"] = (
                "HTML is accessible; vacancy fields still require a dedicated adapter."
            )
        return base

    def _request(self, url: str) -> tuple[requests.Response, int]:
        retries_used = 0
        while True:
            try:
                response = self.session.get(
                    url,
                    timeout=self.timeout_seconds,
                    allow_redirects=True,
                )
                if (
                    response.status_code in TRANSIENT_HTTP_STATUS
                    and retries_used < self.retries
                ):
                    retries_used += 1
                    self._backoff(retries_used)
                    continue
                return response, retries_used
            except requests.RequestException as error:
                if retries_used >= self.retries:
                    raise ProbeRequestError(
                        error,
                        retries_used,
                    ) from error
                retries_used += 1
                self._backoff(retries_used)

    def _backoff(self, retry_number: int) -> None:
        if self.backoff_seconds:
            time.sleep(self.backoff_seconds * (2 ** (retry_number - 1)))

    @staticmethod
    def _response_text(response: requests.Response) -> str:
        content = getattr(response, "content", None)
        if isinstance(content, bytes):
            encoding = getattr(response, "encoding", None)
            if not encoding or str(encoding).lower() in {"iso-8859-1", "ascii"}:
                encoding = getattr(response, "apparent_encoding", None) or "utf-8"
            return content[:500_000].decode(
                encoding,
                errors="replace",
            )
        return str(getattr(response, "text", "") or "")[:500_000]

    @staticmethod
    def _error_class(error: requests.RequestException) -> str:
        if isinstance(error, ProbeRequestError):
            error = error.original
        if isinstance(error, requests.exceptions.SSLError):
            return "tls_or_policy_block"
        if isinstance(error, requests.exceptions.Timeout):
            return "timeout"
        if isinstance(error, requests.exceptions.ConnectionError):
            return "network_error"
        return type(error).__name__.lower()

    @staticmethod
    def _error_detail(error: requests.RequestException) -> str:
        if isinstance(error, ProbeRequestError):
            error = error.original
        return re.sub(r"\s+", " ", str(error)).strip()[:400]

    @staticmethod
    def _links(
        soup: BeautifulSoup,
        base_url: str,
        official_hosts: set[str],
        keywords: list[str],
        max_links: int,
    ) -> tuple[list[str], list[str]]:
        discovered: list[str] = []
        recruitment: list[str] = []
        seen: set[str] = set()
        for anchor in soup.find_all("a", href=True):
            resolved = urljoin(base_url, str(anchor.get("href")))
            parsed = urlparse(resolved)
            host = (parsed.hostname or "").lower()
            if parsed.scheme not in {"http", "https"} or host not in official_hosts:
                continue
            normalized = resolved.split("#", 1)[0]
            if normalized in seen:
                continue
            seen.add(normalized)
            label = re.sub(
                r"\s+",
                " ",
                f"{anchor.get_text(' ', strip=True)} {normalized}",
            ).strip()
            discovered.append(normalized)
            if any(
                re.search(str(keyword), label, re.IGNORECASE)
                for keyword in [*keywords, *RECRUITMENT_HINTS]
            ):
                recruitment.append(normalized)
            if len(discovered) >= max_links:
                break
        return discovered, recruitment

    @staticmethod
    def _summarize_system(
        system: dict[str, Any],
        attempts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        accessible = [
            item
            for item in attempts
            if item["classification"]
            in {"accessible_html", "accessible_dynamic_shell"}
        ]
        recruitment_links = sorted(
            {
                link
                for item in attempts
                for link in item["recruitment_links"]
            }
        )
        if accessible:
            status = "accessible_structure_unverified"
            conclusion = "structure_needs_adapter"
            usable = next(
                (
                    item["final_url"] or item["url"]
                    for item in accessible
                    if item["final_url"] or item["url"]
                ),
                None,
            )
            note = "至少一个官方入口可访问，但岗位字段尚未完成专用适配器验证。"
        elif any(
            item["classification"] in {"access_policy_block", "robots_blocked"}
            for item in attempts
        ):
            status = "access_limited"
            conclusion = "source_unavailable"
            usable = None
            note = "入口或 robots 受到访问策略限制，不能解释为无岗位。"
        else:
            status = "source_unavailable"
            conclusion = "source_unavailable"
            usable = None
            note = "当前探测未取得可解析的官方入口，不能解释为无岗位。"
        return {
            "system_id": system["system_id"],
            "source_id": system["source_id"],
            "status": status,
            "scan_conclusion": conclusion,
            "attempt_count": len(attempts),
            "usable_entry_url": usable,
            "recruitment_links": recruitment_links,
            "note": note,
        }
