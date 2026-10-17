"""Bounded daily revalidation for published government position ledgers.

The reviewed JSON ledger is the publication contract, but it is not itself a
live source. This module rechecks only the explicit official notice and
attachment URLs already present in that ledger. It never discovers new URLs,
logs in, or uses a discovery/aggregation site as student-facing evidence.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any, Iterable
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

from job_hub.config import Settings
from job_hub.transport import (
    RequestPolicy,
    create_session,
    looks_like_non_robots_document,
    request_exception_types,
)


VERIFICATION_STATUSES = frozenset(
    {
        "verified",
        "scheduled",
        "source_unavailable",
        "withdrawn",
        "not_configured",
        "manual_confirmation_required",
    }
)
MANUAL_EVIDENCE_RECHECK_MODE = "manual_only"
AUTOMATIC_AFTER_OPENING_MODE = "automatic_after_opening"
GOVERNMENT_REVALIDATION_USER_AGENT = "CUP-Geosci-OfficialEvidence/1.0"
_EXPLICIT_CANCELLATION_PATTERNS = (
    re.compile(r"本(?:公告|次(?:公开)?招聘(?:公告)?).{0,40}(?:已)?(?:取消|作废|终止|停止|撤销)"),
    re.compile(r"(?:取消|作废|终止|停止|撤销).{0,40}本(?:公告|次(?:公开)?招聘(?:公告)?)"),
)
_ERROR_PAGE_MARKERS = ("页面不存在", "您访问的页面不存在", "not found", "404")
_ATTACHMENT_SUFFIXES = frozenset({".pdf", ".xls", ".xlsx", ".doc", ".docx", ".csv"})


def revalidate_government_sources(
    registry: dict[str, Any],
    sources_by_id: dict[str, dict[str, Any]],
    settings: Settings,
    *,
    now: datetime | None = None,
    session: Any | None = None,
) -> list[dict[str, Any]]:
    """Check registered official evidence for each source with open rows.

    A request failure means only that the source could not be revalidated. It
    is deliberately reported as ``source_unavailable`` rather than being
    interpreted as an empty vacancy list. A source is marked ``withdrawn``
    only when its own reachable notice explicitly contains a cancellation
    statement.
    """
    checked_at = _utc_timestamp(now)
    policy = RequestPolicy(
        session or create_session(
            settings.http_transport_mode,
            headers={"User-Agent": GOVERNMENT_REVALIDATION_USER_AGENT},
            client=settings.http_client,
        ),
        timeout=settings.request_timeout_seconds,
        retries=settings.http_retry_attempts,
        backoff_seconds=settings.http_backoff_seconds,
        max_backoff_seconds=settings.http_max_backoff_seconds,
        jitter_seconds=settings.http_jitter_seconds,
        transport_mode=settings.http_transport_mode,
    )
    robots_cache: dict[str, RobotFileParser | str] = {}
    records_by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in registry.get("records", []):
        if str(record.get("record_status") or "") == "verified_open":
            records_by_source[str(record.get("source_id") or "")].append(record)

    # A failed recheck withdraws the reviewed rows. Do not let that make an
    # automatic-after-opening source permanently invisible: its configured
    # first-party notice/attachment URLs remain the recovery evidence. This
    # path is limited to sources that declare an official attachment, so
    # ordinary sources without reviewed rows are not probed.
    source_ids = set(records_by_source)
    for source_id, source in sources_by_id.items():
        config = source.get("config") if isinstance(source.get("config"), dict) else {}
        if (
            config.get("government_evidence_recheck")
            and str(config.get("government_evidence_recheck_mode") or "").strip()
            == AUTOMATIC_AFTER_OPENING_MODE
            and str(config.get("official_attachment_url") or "").strip()
        ):
            source_ids.add(str(source_id))

    results: list[dict[str, Any]] = []
    for source_id in sorted(source_ids):
        records = records_by_source.get(source_id, [])
        source = sources_by_id.get(source_id)
        if source is None:
            results.append(
                _result(source_id, "not_configured", checked_at, "source is not registered")
            )
            continue
        config = source.get("config") if isinstance(source.get("config"), dict) else {}
        if not config.get("government_evidence_recheck"):
            results.append(
                _result(
                    source_id,
                    "not_configured",
                    checked_at,
                    "source has not opted in to controlled government evidence recheck",
                )
            )
            continue
        if (
            str(config.get("government_evidence_recheck_mode") or "").strip()
            == AUTOMATIC_AFTER_OPENING_MODE
        ):
            opening_at = _source_opening_at(registry, source_id)
            if opening_at is None:
                results.append(
                    _result(
                        source_id,
                        "source_unavailable",
                        checked_at,
                        "automatic evidence recheck requires a valid source_opening_at; "
                        "no official request was made",
                    )
                )
                continue
            if checked_at < _utc_timestamp(opening_at):
                results.append(
                    _result(
                        source_id,
                        "scheduled",
                        checked_at,
                        f"official application window opens at {opening_at.isoformat()}",
                    )
                )
                continue
        if requires_manual_government_evidence_confirmation(source):
            # Some official attachment hosts explicitly prohibit automated
            # retrieval. An administrator must inspect the official notice and
            # attachment, then renew the existing verification explicitly.
            results.append(
                _result(
                    source_id,
                    "manual_confirmation_required",
                    checked_at,
                    "official evidence is configured for administrator confirmation; "
                    "no automated request was made",
                )
            )
            continue

        urls = _official_urls(records)
        if not urls:
            urls = _configured_official_urls(source)
        current_availability_url = _configured_availability_url(config)
        if current_availability_url and current_availability_url not in urls:
            urls.append(current_availability_url)
        allowed_hosts = {
            str(host).strip().lower()
            for key in ("allowed_hosts", "attachment_allowed_hosts")
            for host in config.get(key, [])
            if str(host).strip()
        }
        failures: list[str] = []
        cancellations: list[str] = []
        fingerprints: list[str] = []
        availability_match: str | None = None
        for url in urls:
            host = (urlparse(url).hostname or "").lower()
            if host not in allowed_hosts:
                failures.append(f"{url}: evidence host is outside this source allowlist")
                continue
            robots_outcome = _robots_permits(policy, url, robots_cache)
            if robots_outcome["status"] != "verified":
                failures.append(f"{url}: {robots_outcome['detail']}")
                continue
            is_availability_url = url == current_availability_url
            outcome = _check_url(
                policy,
                url,
                _url_is_attachment(url),
                capture_text=is_availability_url,
                full_attachment=(
                    _url_is_attachment(url)
                    and str(config.get("government_evidence_recheck_mode") or "").strip()
                    == AUTOMATIC_AFTER_OPENING_MODE
                ),
                expected_sha256=(
                    str(config.get("expected_attachment_sha256") or "").strip().lower()
                    if _url_is_attachment(url)
                    else ""
                ),
                max_attachment_bytes=int(
                    config.get("max_attachment_bytes")
                    or getattr(settings, "attachment_max_bytes", 25_000_000)
                ),
            )
            if outcome["status"] == "withdrawn":
                cancellations.append(f"{url}: {outcome['detail']}")
            elif outcome["status"] != "verified":
                failures.append(f"{url}: {outcome['detail']}")
            elif outcome.get("fingerprint"):
                fingerprints.append(str(outcome["fingerprint"]))
                if is_availability_url:
                    availability_match = _availability_match(config, str(outcome.get("text") or ""))

        if cancellations:
            results.append(
                _result(source_id, "withdrawn", checked_at, "; ".join(cancellations), fingerprints)
            )
        elif failures:
            results.append(
                _result(
                    source_id,
                    "source_unavailable",
                    checked_at,
                    "; ".join(failures),
                    fingerprints,
                )
            )
        else:
            result = _result(
                source_id,
                "verified",
                checked_at,
                f"revalidated {len(urls)} official evidence URL(s)",
                fingerprints,
            )
            if availability_match:
                result["availability_confirmed_at"] = checked_at
                result["availability_confirmation_detail"] = (
                    "官方当前名额状态页匹配开放标记：" + availability_match
                )
            results.append(result)
    return results


def _robots_permits(
    policy: RequestPolicy,
    url: str,
    cache: dict[str, RobotFileParser | str],
) -> dict[str, str]:
    """Require a verified robots policy before refreshing official evidence.

    A reviewed URL is not an exemption from a source's current access policy.
    A missing robots file follows the project-wide collector policy; all other
    policy, transport, or HTTP errors leave the source unrefreshed.
    """
    parsed = urlparse(url)
    root = f"{parsed.scheme}://{parsed.netloc}"
    cached = cache.get(root)
    if isinstance(cached, str):
        return {"status": "source_unavailable", "detail": cached}
    if isinstance(cached, RobotFileParser):
        if cached.can_fetch(GOVERNMENT_REVALIDATION_USER_AGENT, url):
            return {"status": "verified", "detail": "robots.txt permits access"}
        return {
            "status": "source_unavailable",
            "detail": "robots.txt does not permit evidence recheck",
        }

    robots_url = f"{root}/robots.txt"
    try:
        response = policy.request("GET", robots_url)
    except request_exception_types() as error:
        detail = f"unable to verify robots.txt: {error.__class__.__name__}"
        cache[root] = detail
        return {"status": "source_unavailable", "detail": detail}

    try:
        status_code = int(getattr(response, "status_code", 0) or 0)
        if status_code == 404:
            parser = RobotFileParser()
            parser.parse([])
            cache[root] = parser
            return {
                "status": "verified",
                "detail": "robots.txt not found; access allowed by policy",
            }
        if not 200 <= status_code < 400:
            detail = f"unable to verify robots.txt: HTTP {status_code}"
            cache[root] = detail
            return {"status": "source_unavailable", "detail": detail}
        content = bytes(getattr(response, "content", b"") or b"")
        text = content.decode("utf-8", errors="replace")
        if looks_like_non_robots_document(text):
            detail = "unable to verify robots.txt: received an HTML error document"
            cache[root] = detail
            return {"status": "source_unavailable", "detail": detail}
        parser = RobotFileParser()
        parser.parse(text.splitlines())
        cache[root] = parser
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            close()

    if parser.can_fetch(GOVERNMENT_REVALIDATION_USER_AGENT, url):
        return {"status": "verified", "detail": "robots.txt permits access"}
    return {
        "status": "source_unavailable",
        "detail": "robots.txt does not permit evidence recheck",
    }


def requires_manual_government_evidence_confirmation(source: dict[str, Any]) -> bool:
    """Return whether an official source forbids automated evidence retrieval."""
    config = source.get("config") if isinstance(source.get("config"), dict) else {}
    return str(config.get("government_evidence_recheck_mode") or "").strip() == (
        MANUAL_EVIDENCE_RECHECK_MODE
    )


def requires_preopening_evidence_confirmation(source: dict[str, Any]) -> bool:
    """Return whether future public rows need an explicit verification event.

    ``automatic_after_opening`` sources deliberately do not make HTTP requests
    before their official opening instant.  Their reviewed ledger is therefore
    planning evidence only until a successful verification event exists.  This
    helper is kept separate from ``requires_manual...`` because the latter
    describes an operational prohibition and is also used by the worker's
    revalidation path.
    """
    config = source.get("config") if isinstance(source.get("config"), dict) else {}
    mode = str(config.get("government_evidence_recheck_mode") or "").strip()
    return mode in {MANUAL_EVIDENCE_RECHECK_MODE, AUTOMATIC_AFTER_OPENING_MODE}


def _official_urls(records: Iterable[dict[str, Any]]) -> list[str]:
    urls: list[str] = []
    for record in records:
        for field in ("official_notice_url", "official_attachment_url"):
            value = str(record.get(field) or "").strip()
            if value and value not in urls:
                urls.append(value)
    return urls


def _configured_official_urls(source: dict[str, Any]) -> list[str]:
    """Return first-party evidence URLs for a source with no live rows.

    This is a recovery-only fallback. The notice URL is explicit when
    configured; otherwise the source homepage is accepted only alongside a
    configured official attachment, which prevents generic landing pages from
    becoming evidence by accident.
    """

    config = source.get("config") if isinstance(source.get("config"), dict) else {}
    urls: list[str] = []
    notice = str(config.get("official_notice_url") or "").strip()
    if not notice:
        notice = str(source.get("homepage_url") or "").strip()
    attachment = str(config.get("official_attachment_url") or "").strip()
    for value in (notice, attachment):
        if value and value not in urls:
            urls.append(value)
    return urls


def _url_is_attachment(url: str) -> bool:
    return PurePosixPath(urlparse(url).path).suffix.lower() in _ATTACHMENT_SUFFIXES


def _configured_availability_url(config: dict[str, Any]) -> str:
    """Return an explicitly configured official current-status URL.

    A status page is opt-in. Static notices and attachments never renew a
    rolling ``open_until_filled`` vacancy by themselves.
    """

    value = str(config.get("current_availability_url") or "").strip()
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return value


def _availability_match(config: dict[str, Any], text: str) -> str | None:
    """Match an explicit open marker while rejecting configured close markers."""

    raw_open_patterns = config.get("availability_open_patterns", [])
    raw_closed_patterns = config.get("availability_closed_patterns", [])
    if not isinstance(raw_open_patterns, (list, tuple)):
        raw_open_patterns = []
    if not isinstance(raw_closed_patterns, (list, tuple)):
        raw_closed_patterns = []
    open_patterns = [
        str(pattern).strip()
        for pattern in raw_open_patterns
        if str(pattern).strip()
    ]
    closed_patterns = [
        str(pattern).strip()
        for pattern in raw_closed_patterns
        if str(pattern).strip()
    ]
    if not open_patterns or not text.strip():
        return None
    compact = re.sub(r"\s+", " ", text)
    if any(re.search(pattern, compact, re.IGNORECASE) for pattern in closed_patterns):
        return None
    for pattern in open_patterns:
        if re.search(pattern, compact, re.IGNORECASE):
            return pattern
    return None


def _check_url(
    policy: RequestPolicy,
    url: str,
    is_attachment: bool,
    *,
    capture_text: bool = False,
    full_attachment: bool = False,
    expected_sha256: str = "",
    max_attachment_bytes: int = 25_000_000,
) -> dict[str, str]:
    # A short range read validates that a public attachment still exists without
    # re-downloading the same multi-megabyte XLS/PDF in every worker cycle.
    # Full hash verification is different: the server must be allowed to return
    # the entire configured file, otherwise a compliant Range response can
    # silently truncate a valid attachment before its SHA-256 is calculated.
    byte_limit = (
        max(1, int(max_attachment_bytes))
        if full_attachment
        else (8_192 if is_attachment else 262_144)
    )
    try:
        response = policy.request(
            "GET",
            url,
            headers={"Range": f"bytes=0-{byte_limit - 1}"},
            stream=True,
        )
    except request_exception_types() as error:
        return {"status": "source_unavailable", "detail": error.__class__.__name__}
    try:
        status_code = int(getattr(response, "status_code", 0) or 0)
        if not 200 <= status_code < 400:
            return {
                "status": "source_unavailable",
                "detail": f"HTTP {status_code}",
            }
        chunks: list[bytes] = []
        digest = hashlib.sha256()
        total_bytes = 0
        remaining = byte_limit
        for chunk in response.iter_content(chunk_size=4096):
            if not chunk:
                continue
            if full_attachment:
                total_bytes += len(chunk)
                if total_bytes > max(1, max_attachment_bytes):
                    return {
                        "status": "source_unavailable",
                        "detail": "official attachment exceeds configured size limit",
                    }
                digest.update(chunk)
                chunks.append(chunk)
            else:
                chunks.append(chunk[:remaining])
                remaining -= len(chunks[-1])
                if remaining <= 0:
                    break
        content = b"".join(chunks)
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            close()
    # A server sometimes returns an HTTP-200 CMS error page for a deleted
    # article. This is a source failure, except for an explicit recruitment
    # cancellation published by the official publisher.
    text = content.decode("utf-8", errors="ignore")
    if _states_this_notice_is_withdrawn(text):
        return {"status": "withdrawn", "detail": "official notice states cancellation"}
    if not is_attachment and any(marker in text.lower() for marker in _ERROR_PAGE_MARKERS):
        return {"status": "source_unavailable", "detail": "official page returned an error document"}
    fingerprint = digest.hexdigest() if full_attachment else hashlib.sha256(content).hexdigest()
    if full_attachment and expected_sha256:
        if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
            return {"status": "source_unavailable", "detail": "configured attachment hash is invalid"}
        if fingerprint != expected_sha256:
            return {
                "status": "source_unavailable",
                "detail": "official attachment hash does not match the reviewed evidence",
            }
    result = {
        "status": "verified",
        "detail": f"HTTP {status_code}",
        "fingerprint": fingerprint,
    }
    if capture_text:
        result["text"] = text[:262_144]
    return result


def _source_opening_at(registry: dict[str, Any], source_id: str) -> datetime | None:
    """Resolve a source opening instant for automatic revalidation."""

    raw = str((registry.get("source_opening_at") or {}).get(source_id) or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _states_this_notice_is_withdrawn(text: str) -> bool:
    """Require an explicit statement about this notice or recruitment run.

    Official pages often contain rules such as "if a position is cancelled".
    Those rules, sidebar links, or historical notices cannot withdraw the
    current source.  A source is removed only when the page explicitly says
    this announcement or this recruitment run has been cancelled.
    """
    compact = re.sub(r"\s+", " ", text)
    return any(pattern.search(compact) for pattern in _EXPLICIT_CANCELLATION_PATTERNS)


def _result(
    source_id: str,
    status: str,
    checked_at: str,
    detail: str,
    fingerprints: Iterable[str] = (),
) -> dict[str, Any]:
    assert status in VERIFICATION_STATUSES
    digest = hashlib.sha256("\n".join(sorted(fingerprints)).encode("ascii")).hexdigest()
    return {
        "source_id": source_id,
        "status": status,
        "checked_at": checked_at,
        "detail": detail[:2000],
        "evidence_fingerprint": digest,
    }


def _utc_timestamp(value: datetime | None) -> str:
    target = value or datetime.now(timezone.utc)
    if target.tzinfo is None:
        target = target.replace(tzinfo=timezone.utc)
    return target.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
