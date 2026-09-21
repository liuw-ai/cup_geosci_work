"""Private discovery-source registry and lead-funnel helpers.

Discovery channels are useful for finding notices that an official crawler has
not reached yet.  They are deliberately kept outside ``data/sources.json`` and
cannot make a job public by themselves.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from job_hub.contracts import ContractValidationError, validate_discovery_source_registry


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DISCOVERY_SOURCE_REGISTRY_PATH = PROJECT_ROOT / "data" / "discovery_sources.json"


def load_discovery_source_registry(
    path: Path = DISCOVERY_SOURCE_REGISTRY_PATH,
) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Cannot read discovery source registry: {error}") from error
    try:
        return validate_discovery_source_registry(payload)
    except ContractValidationError as error:
        raise ValueError(f"Invalid discovery source registry: {error}") from error


def discovery_source_summary(
    registry: dict[str, Any],
    *,
    lead_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return counts that explain discovery coverage without exposing leads."""
    sources = list(registry.get("sources", []))
    rows = lead_rows or []
    status_names = (
        "candidate",
        "official_url_found",
        "official_content_verified",
        "need_review",
        "published",
        "rejected",
        "expired",
    )
    by_source: dict[str, dict[str, int]] = {
        str(source["id"]): {
            "leads": 0,
            **{status: 0 for status in status_names},
        }
        for source in sources
    }
    unassigned = 0
    for lead in rows:
        source_id = str(lead.get("discovery_source_id") or "").strip()
        if source_id not in by_source:
            unassigned += 1
            continue
        bucket = by_source[source_id]
        bucket["leads"] += 1
        status = str(lead.get("verification_status") or "")
        if status in bucket:
            bucket[status] += 1
    return {
        "registered_sources": len(sources),
        "active_sources": sum(source.get("status") in {"registered", "checked"} for source in sources),
        "access_limited_sources": sum(source.get("status") == "access_limited" for source in sources),
        "lead_count": len(rows),
        "unassigned_lead_count": unassigned,
        "sources": [
            {
                "id": source["id"],
                "name": source["name"],
                "source_type": source["source_type"],
                "status": source["status"],
                "last_checked_on": source["last_checked_on"],
                "lead_counts": by_source[str(source["id"])],
            }
            for source in sources
        ],
    }


def discovery_source_rows(registry: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the private registry rows for an administrator."""
    return [dict(source) for source in registry.get("sources", [])]


def normalize_lead_url(url: str) -> str:
    """Normalize tracking noise for deduplication, while retaining the source URL."""
    parsed = urlsplit(str(url).strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return str(url).strip()
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_")
        and key.lower() not in {"from", "source", "share", "spm"}
    ]
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    return urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            path,
            urlencode(sorted(query)),
            "",
        )
    )


def lead_fingerprint(
    lead_url: str,
    *,
    title: str = "",
    employer_hint: str = "",
) -> str:
    """Create a stable URL key for idempotent private lead ingestion.

    Titles on aggregators are often edited after first publication.  The
    normalized article URL is the durable identity; title and employer remain
    useful fields on the first captured row, but must not create duplicates.
    """
    del title, employer_hint
    return hashlib.sha256(normalize_lead_url(lead_url).encode("utf-8")).hexdigest()


def official_domain_assessment(
    official_url: str,
    source_records: list[dict[str, Any]],
    *,
    official_source_id: str | None = None,
) -> dict[str, Any]:
    """Assess an official URL against registered source hosts without network I/O."""
    parsed = urlsplit(str(official_url).strip())
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        return {"status": "unverified", "host": "", "source_id": official_source_id}
    source = (
        next(
            (item for item in source_records if str(item.get("id")) == str(official_source_id)),
            None,
        )
        if official_source_id
        else None
    )
    if source is not None:
        allowed = {
            str(value).lower().rstrip(".")
            for value in source.get("config", {}).get("allowed_hosts", [])
        }
        homepage = urlsplit(str(source.get("homepage_url") or "")).hostname
        if homepage:
            allowed.add(homepage.lower().rstrip("."))
        matches = any(host == item or host.endswith("." + item) for item in allowed)
        return {
            "status": "registered_source_match" if matches else "source_domain_mismatch",
            "host": host,
            "source_id": official_source_id,
        }
    return {
        "status": "manual_review_required",
        "host": host,
        "source_id": official_source_id,
    }


def discovery_funnel(
    lead_rows: list[dict[str, Any]],
    registry: dict[str, Any],
) -> dict[str, Any]:
    """Calculate an auditable lead funnel; no count is presented as a job count."""
    statuses = {
        "candidate": 0,
        "official_url_found": 0,
        "official_content_verified": 0,
        "need_review": 0,
        "published": 0,
        "rejected": 0,
        "expired": 0,
    }
    domains: dict[str, int] = {}
    fingerprints: set[str] = set()
    attributed = 0
    for row in lead_rows:
        status = str(row.get("verification_status") or "")
        if status in statuses:
            statuses[status] += 1
        domain_status = str(row.get("official_domain_status") or "unverified")
        domains[domain_status] = domains.get(domain_status, 0) + 1
        fingerprint = str(row.get("lead_fingerprint") or "").strip()
        if fingerprint:
            fingerprints.add(fingerprint)
        if str(row.get("discovery_source_id") or "").strip():
            attributed += 1
    total = len(lead_rows)

    def rate(value: int, denominator: int = total) -> float:
        return round(value / denominator, 4) if denominator else 0.0

    return {
        "lead_total": total,
        "unique_lead_fingerprints": len(fingerprints),
        "duplicate_lead_rows": max(0, total - len(fingerprints)),
        "status_counts": statuses,
        "domain_status_counts": domains,
        "attributed_leads": attributed,
        "unattributed_leads": total - attributed,
        "rates": {
            "official_url_found": rate(
                statuses["official_url_found"]
                + statuses["official_content_verified"]
                + statuses["published"]
            ),
            "official_content_verified": rate(
                statuses["official_content_verified"] + statuses["published"]
            ),
            "published": rate(statuses["published"]),
            "source_attribution": rate(attributed),
        },
        "registered_discovery_sources": len(registry.get("sources", [])),
        "policy": "线索统计不等于公开岗位统计；第三方原文不会进入学生端。",
    }
