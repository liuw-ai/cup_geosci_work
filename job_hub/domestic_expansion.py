"""Domestic official-source expansion queue.

The queue is deliberately separate from ``sources.json``.  A queue record is
an auditable next action for a unit or source family; it is not permission to
fetch a page and it never creates a public job.  This keeps source coverage
work visible without turning an unverified URL into a student-facing claim.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from job_hub.contracts import is_http_url
from job_hub.config import PROJECT_ROOT


QUEUE_PATH = PROJECT_ROOT / "data" / "domestic_source_expansion_queue.json"
QUEUE_STATUSES = frozenset(
    {
        "official_job_sample_verified",
        "official_identity_only",
        "scan_success_no_match",
        "access_limited",
        "manual_review_required",
    }
)


def load_domestic_expansion_queue(
    path: Path | str = QUEUE_PATH,
) -> dict[str, Any]:
    """Load and validate the versioned queue without performing network I/O."""
    queue_path = Path(path)
    with queue_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("Domestic expansion queue must be a JSON object")
    records = payload.get("records")
    if not isinstance(records, list) or not records:
        raise ValueError("Domestic expansion queue records must be a non-empty list")
    seen_ids: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("Each domestic expansion record must be an object")
        record_id = str(record.get("id") or "").strip()
        if not record_id or record_id in seen_ids:
            raise ValueError("Domestic expansion record ids must be unique and non-empty")
        official_url = str(record.get("official_url") or "").strip()
        if not is_http_url(official_url):
            raise ValueError(f"{record_id}: official_url must be an HTTP(S) URL")
        status = str(record.get("status") or "").strip()
        if status not in QUEUE_STATUSES:
            raise ValueError(f"{record_id}: unsupported queue status {status!r}")
        backup_urls = record.get("backup_urls", [])
        if not isinstance(backup_urls, list) or not backup_urls:
            raise ValueError(f"{record_id}: at least one backup URL is required")
        for backup_url in backup_urls:
            if not is_http_url(str(backup_url).strip()):
                raise ValueError(f"{record_id}: backup_urls must contain HTTP(S) URLs")
        source_id = record.get("source_id")
        if source_id is not None and not str(source_id).strip():
            raise ValueError(f"{record_id}: source_id cannot be blank")
        sample_url = record.get("sample_announcement_url")
        if sample_url is not None and not is_http_url(str(sample_url).strip()):
            raise ValueError(
                f"{record_id}: sample_announcement_url must be an HTTP(S) URL"
            )
        if status == "official_job_sample_verified":
            if not source_id:
                raise ValueError(
                    f"{record_id}: verified sample requires a registered source_id"
                )
            if not sample_url:
                raise ValueError(
                    f"{record_id}: verified sample requires sample_announcement_url"
                )
            sample_job_ids = record.get("sample_job_ids")
            if not isinstance(sample_job_ids, list) or not sample_job_ids:
                raise ValueError(
                    f"{record_id}: verified sample requires sample_job_ids"
                )
            if not str(record.get("field_validation") or "").strip():
                raise ValueError(
                    f"{record_id}: verified sample requires field_validation"
                )
        if status == "scan_success_no_match":
            if not str(record.get("observed_on") or "").strip():
                raise ValueError(
                    f"{record_id}: scan_success_no_match requires observed_on"
                )
            if record.get("scan_conclusion") != "scan_success_no_match":
                raise ValueError(
                    f"{record_id}: scan_success_no_match requires matching scan_conclusion"
                )
        normalized_record = {
            **record,
            "id": record_id,
            "official_url": official_url,
            "backup_urls": [str(url).strip() for url in backup_urls],
            "source_id": str(source_id).strip() if source_id is not None else None,
            "sample_announcement_url": (
                str(sample_url).strip() if sample_url is not None else None
            ),
            "official_host": (urlparse(official_url).hostname or "").lower(),
        }
        seen_ids.add(record_id)
        normalized.append(normalized_record)
    return {**payload, "records": normalized}


def domestic_expansion_rows(
    queue: dict[str, Any],
    *,
    system: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """Return queue rows filtered for administrator review."""
    if status is not None and status not in QUEUE_STATUSES:
        raise ValueError(f"unsupported queue status: {status}")
    rows = []
    for record in queue["records"]:
        if system and record.get("system") != system:
            continue
        if status and record.get("status") != status:
            continue
        rows.append(dict(record))
    return rows


def domestic_expansion_summary(queue: dict[str, Any]) -> dict[str, Any]:
    """Summarize expansion readiness without counting queue rows as jobs."""
    records = list(queue["records"])
    by_status = Counter(str(item.get("status") or "unknown") for item in records)
    by_system = Counter(str(item.get("system") or "unknown") for item in records)
    with_source = sum(1 for item in records if item.get("source_id"))
    verified = by_status.get("official_job_sample_verified", 0)
    return {
        "queue_version": queue.get("version"),
        "as_of": queue.get("as_of"),
        "record_count": len(records),
        "records_with_registered_source": with_source,
        "records_with_backup": sum(1 for item in records if item.get("backup_urls")),
        "backup_rate": round(
            sum(1 for item in records if item.get("backup_urls")) / len(records), 4
        )
        if records
        else 0.0,
        "official_job_sample_verified": verified,
        "scan_success_no_match": by_status.get("scan_success_no_match", 0),
        "by_status": dict(sorted(by_status.items())),
        "by_system": dict(sorted(by_system.items())),
        "scope_note": (
            "队列是扩源任务台账，不是岗位数据；只有来源通过官方原文、"
            "岗位级专业/学历证据和发布审计后，才会进入学生端。"
        ),
    }
