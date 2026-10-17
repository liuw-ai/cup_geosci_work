"""Stable identities for direct, official job-detail pages.

The crawler intentionally keeps every source observation for audit.  A public
portal can, however, expose the very same job-detail page through an employer
sub-site and through a platform-wide search.  This module supplies a narrow
cross-source identity only when the official detail URL itself proves it.
Titles, employers and locations are deliberately not used: they are too weak
to merge two genuinely different vacancies.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qs, urlparse


IGUOPIN_DETAIL_HOSTS = frozenset({"iguopin.com", "www.iguopin.com"})
_DETAIL_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{6,128}$")
_EVIDENCE_DETAIL_URL_KEYS = (
    "官方详情链接",
    "岗位详情链接",
    "职位详情链接",
    "detail_url",
)


def iguopin_detail_key(value: object) -> str | None:
    """Return the global 国聘 job identity for one concrete official URL.

    The platform's detail ``id`` is accepted only from the public global
    detail host and only on ``/job/detail``.  This is intentionally narrower
    than a generic URL normalizer, so an employer landing page, an API URL or
    a similarly named query parameter can never suppress a real job.
    """

    url = str(value or "").strip()
    if not url:
        return None
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if host not in IGUOPIN_DETAIL_HOSTS:
        return None
    if parsed.path.rstrip("/") != "/job/detail":
        return None
    detail_id = str(parse_qs(parsed.query).get("id", [""])[0]).strip()
    if not _DETAIL_ID_PATTERN.fullmatch(detail_id):
        return None
    return f"iguopin-job:{detail_id}"


def official_detail_key(
    *,
    source_url: object = None,
    application_url: object = None,
    official_evidence_url: object = None,
    field_evidence: Mapping[str, Any] | None = None,
) -> str | None:
    """Derive a cross-source key only from explicit official detail evidence."""

    candidates: list[object] = [source_url, application_url]
    if isinstance(field_evidence, Mapping):
        candidates.extend(field_evidence.get(key) for key in _EVIDENCE_DETAIL_URL_KEYS)
    candidates.append(official_evidence_url)
    for candidate in candidates:
        key = iguopin_detail_key(candidate)
        if key is not None:
            return key
    return None


def official_detail_key_from_row(row: Mapping[str, Any]) -> str | None:
    """Read a database row safely while backfilling an additive migration."""

    evidence = row.get("field_evidence")
    if not isinstance(evidence, Mapping):
        evidence = None
    return official_detail_key(
        source_url=row.get("source_url"),
        application_url=row.get("application_url"),
        official_evidence_url=row.get("official_evidence_url"),
        field_evidence=evidence,
    )


__all__ = [
    "IGUOPIN_DETAIL_HOSTS",
    "iguopin_detail_key",
    "official_detail_key",
    "official_detail_key_from_row",
]
