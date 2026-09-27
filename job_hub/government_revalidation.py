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

from job_hub.config import Settings
from job_hub.transport import RequestPolicy, create_session, request_exception_types


VERIFICATION_STATUSES = frozenset(
    {"verified", "source_unavailable", "withdrawn", "not_configured"}
)
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
            headers={"User-Agent": "CUP-Geosci-OfficialEvidence/1.0"},
            client=settings.http_client,
        ),
        timeout=settings.request_timeout_seconds,
        retries=settings.http_retry_attempts,
        backoff_seconds=settings.http_backoff_seconds,
        max_backoff_seconds=settings.http_max_backoff_seconds,
        jitter_seconds=settings.http_jitter_seconds,
        transport_mode=settings.http_transport_mode,
    )
    records_by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in registry.get("records", []):
        if str(record.get("record_status") or "") == "verified_open":
            records_by_source[str(record.get("source_id") or "")].append(record)

    results: list[dict[str, Any]] = []
    for source_id, records in sorted(records_by_source.items()):
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

        urls = _official_urls(records)
        allowed_hosts = {
            str(host).strip().lower()
            for key in ("allowed_hosts", "attachment_allowed_hosts")
            for host in config.get(key, [])
            if str(host).strip()
        }
        failures: list[str] = []
        cancellations: list[str] = []
        fingerprints: list[str] = []
        for url in urls:
            host = (urlparse(url).hostname or "").lower()
            if host not in allowed_hosts:
                failures.append(f"{url}: evidence host is outside this source allowlist")
                continue
            outcome = _check_url(policy, url, _url_is_attachment(url))
            if outcome["status"] == "withdrawn":
                cancellations.append(f"{url}: {outcome['detail']}")
            elif outcome["status"] != "verified":
                failures.append(f"{url}: {outcome['detail']}")
            elif outcome.get("fingerprint"):
                fingerprints.append(str(outcome["fingerprint"]))

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
            results.append(
                _result(
                    source_id,
                    "verified",
                    checked_at,
                    f"revalidated {len(urls)} official evidence URL(s)",
                    fingerprints,
                )
            )
    return results


def _official_urls(records: Iterable[dict[str, Any]]) -> list[str]:
    urls: list[str] = []
    for record in records:
        for field in ("official_notice_url", "official_attachment_url"):
            value = str(record.get(field) or "").strip()
            if value and value not in urls:
                urls.append(value)
    return urls


def _url_is_attachment(url: str) -> bool:
    return PurePosixPath(urlparse(url).path).suffix.lower() in _ATTACHMENT_SUFFIXES


def _check_url(policy: RequestPolicy, url: str, is_attachment: bool) -> dict[str, str]:
    # A short range read validates that a public attachment still exists without
    # re-downloading the same multi-megabyte XLS/PDF in every worker cycle.
    byte_limit = 8_192 if is_attachment else 262_144
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
        remaining = byte_limit
        for chunk in response.iter_content(chunk_size=4096):
            if not chunk:
                continue
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
    return {
        "status": "verified",
        "detail": f"HTTP {status_code}",
        "fingerprint": hashlib.sha256(content).hexdigest(),
    }


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
) -> dict[str, str]:
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
