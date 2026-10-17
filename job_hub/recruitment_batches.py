"""Auditable recruitment-batch ledger for source expansion.

The project collects annual campaigns from many official entrances.  A source
is too small a unit for planning a campaign: one campaign may span several
subsidiaries, attachments and browser captures.  This module provides a
read-only projection over the existing source, crawl-run and public-job data.
It never promotes a candidate and never treats a blocked or partial run as a
successful batch.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

from job_hub.config import PROJECT_ROOT
from job_hub.readiness_axes import is_domestic_job, is_public_job


BATCH_REGISTRY_PATH = PROJECT_ROOT / "data" / "recruitment_batches.json"
REQUIRED_FIELDS = ("official_evidence_url", "major", "degree", "location", "deadline")
VALID_FAMILIES = {"three_oil", "public_institution", "civil_service", "other"}
VALID_STATUSES = {"planned", "in_progress", "blocked", "verified"}


def load_recruitment_batch_registry(
    path: Path | str = BATCH_REGISTRY_PATH,
) -> dict[str, Any]:
    """Load and validate the versioned batch registry."""

    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read recruitment batch registry: {path}") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("batches"), list):
        raise ValueError("recruitment batch registry requires a batches list")
    version = int(payload.get("version") or 0)
    if version != 1:
        raise ValueError("unsupported recruitment batch registry version")
    seen: set[str] = set()
    for batch in payload["batches"]:
        if not isinstance(batch, dict):
            raise ValueError("each recruitment batch must be an object")
        batch_id = str(batch.get("id") or "").strip()
        if not batch_id or batch_id in seen:
            raise ValueError("recruitment batch ids must be unique and non-empty")
        if str(batch.get("family") or "") not in VALID_FAMILIES:
            raise ValueError(f"{batch_id}: unsupported family")
        if str(batch.get("status") or "") not in VALID_STATUSES:
            raise ValueError(f"{batch_id}: unsupported status")
        source_ids = batch.get("source_ids")
        if not isinstance(source_ids, list) or not source_ids or any(
            not str(item).strip() for item in source_ids
        ):
            raise ValueError(f"{batch_id}: source_ids must be a non-empty list")
        if len({str(item).strip() for item in source_ids}) != len(source_ids):
            raise ValueError(f"{batch_id}: source_ids must be unique")
        entry_urls = batch.get("official_entry_urls")
        if not isinstance(entry_urls, list) or not entry_urls:
            raise ValueError(f"{batch_id}: official_entry_urls are required")
        for url in entry_urls:
            parsed = urlparse(str(url).strip())
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError(f"{batch_id}: official_entry_urls must be HTTP(S)")
        target = int(batch.get("target_jobs") or 0)
        if target <= 0:
            raise ValueError(f"{batch_id}: target_jobs must be positive")
        required_fields = batch.get("required_fields")
        if not isinstance(required_fields, list) or not required_fields:
            raise ValueError(f"{batch_id}: required_fields are required")
        unknown = set(str(item) for item in required_fields) - set(REQUIRED_FIELDS)
        if unknown:
            raise ValueError(f"{batch_id}: unsupported required fields: {sorted(unknown)}")
        seen.add(batch_id)
    return payload


def build_recruitment_batch_report(
    *,
    batches: Iterable[dict[str, Any]],
    sources: Iterable[dict[str, Any]],
    jobs: Iterable[dict[str, Any]],
    crawl_runs: Iterable[dict[str, Any]],
    health_by_id: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Project progress for each batch without changing publication state."""

    source_rows = {str(item.get("id") or "").strip(): dict(item) for item in sources}
    health_by_id = health_by_id or {}
    public_domestic_jobs = [
        dict(job) for job in jobs if is_public_job(job) and is_domestic_job(job)
    ]
    jobs_by_source: dict[str, list[dict[str, Any]]] = {}
    for job in public_domestic_jobs:
        jobs_by_source.setdefault(str(job.get("source_id") or "").strip(), []).append(job)
    runs_by_source: dict[str, list[dict[str, Any]]] = {}
    for run in crawl_runs:
        source_id = str(run.get("source_id") or "").strip()
        if source_id:
            runs_by_source.setdefault(source_id, []).append(dict(run))
    for runs in runs_by_source.values():
        runs.sort(key=lambda item: int(item.get("id") or 0), reverse=True)

    batch_list = [dict(batch) for batch in batches]
    rows: list[dict[str, Any]] = []
    batch_source_ids = {
        str(source_id).strip()
        for raw_batch in batch_list
        for source_id in raw_batch.get("source_ids", [])
        if str(source_id).strip()
    }
    # ``jobs`` is expected to come from the canonical dated student-public
    # query. Keep a separate assignment view so the aggregate does not make
    # rows from newly registered/unassigned sources look like missing jobs.
    assigned_jobs = [
        job for job in public_domestic_jobs
        if str(job.get("source_id") or "").strip() in batch_source_ids
    ]
    unassigned_jobs = [
        job for job in public_domestic_jobs
        if str(job.get("source_id") or "").strip() not in batch_source_ids
    ]
    for raw_batch in batch_list:
        batch = dict(raw_batch)
        batch_id = str(batch["id"])
        source_ids = [str(item).strip() for item in batch["source_ids"]]
        registered = [source_id for source_id in source_ids if source_id in source_rows]
        missing = [source_id for source_id in source_ids if source_id not in source_rows]
        # A dynamic source and its verified snapshot can overlap during an
        # atomic replacement.  Count one official detail once, otherwise the
        # batch ledger can claim progress that the student corpus does not
        # actually contain.
        batch_jobs = _deduplicate_jobs(
            job
            for source_id in source_ids
            for job in jobs_by_source.get(source_id, [])
        )
        employers = {
            str(job.get("canonical_employer_name") or job.get("employer") or "").strip()
            for job in batch_jobs
            if str(job.get("canonical_employer_name") or job.get("employer") or "").strip()
        }
        provinces = {
            str(job.get("province") or "").strip()
            for job in batch_jobs
            if str(job.get("province") or "").strip()
        }
        latest_finished = 0
        consecutive_successes = 0
        access_limited = 0
        for source_id in source_ids:
            runs = runs_by_source.get(source_id, [])
            if runs and runs[0].get("status") == "finished":
                latest_finished += 1
            if len(runs) >= 2 and all(item.get("status") == "finished" for item in runs[:2]):
                consecutive_successes += 1
            health_status = str(health_by_id.get(source_id, {}).get("status") or "")
            if health_status in {"source_blocked", "access_limited", "robots_blocked"} or (
                source_rows.get(source_id, {}).get("enabled") and any(
                token in str(source_rows.get(source_id, {}).get("config", {}).get("disabled_reason") or "").lower()
                for token in ("access", "robots", "tls", "blocked")
                )
            ):
                access_limited += 1
        evidence_total = sum(
            1 for job in batch_jobs if _evidence_complete(job, batch.get("required_fields", REQUIRED_FIELDS))
        )
        evidence_rate = evidence_total / len(batch_jobs) if batch_jobs else 0.0
        minimum_refresh_rate = float(batch.get("minimum_refresh_rate") or 0.9)
        refresh_rate = consecutive_successes / len(registered) if registered else 0.0
        gates = {
            "official_entries_registered": bool(batch.get("official_entry_urls")),
            "all_sources_registered": not missing,
            "evidence_rate_at_least_95_percent": evidence_rate >= 0.95,
            "two_consecutive_refresh_rate_met": refresh_rate >= minimum_refresh_rate,
            "has_current_public_jobs": bool(batch_jobs),
        }
        failed = [name for name, passed in gates.items() if not passed]
        rows.append(
            {
                "id": batch_id,
                "label": batch.get("label", batch_id),
                "family": batch.get("family"),
                "status": batch.get("status"),
                "priority": batch.get("priority", "normal"),
                "target_jobs": int(batch["target_jobs"]),
                "current_public_domestic_jobs": len(batch_jobs),
                "gap_jobs": max(0, int(batch["target_jobs"]) - len(batch_jobs)),
                "source_ids": source_ids,
                "registered_source_ids": registered,
                "missing_source_ids": missing,
                "official_entry_count": len(batch.get("official_entry_urls", [])),
                "latest_finished_source_count": latest_finished,
                "two_consecutive_refresh_source_count": consecutive_successes,
                "two_consecutive_refresh_rate": round(refresh_rate, 4),
                "access_limited_source_count": access_limited,
                "evidence_complete_jobs": evidence_total,
                "evidence_rate": round(evidence_rate, 4),
                "unit_count": len(employers),
                "province_count": len(provinces),
                "gates": gates,
                "failed_gates": failed,
                "next_action": _next_action(failed, batch),
            }
        )
    rows.sort(key=lambda item: (item["status"] == "verified", item["priority"], item["id"]))
    return {
        "version": 1,
        "scope": "current_public_domestic_jobs_only",
        "target_jobs": sum(int(item["target_jobs"]) for item in rows),
        "current_public_domestic_jobs": sum(int(item["current_public_domestic_jobs"]) for item in rows),
        "batches": rows,
        "ready_batches": sum(1 for item in rows if not item["failed_gates"]),
        "blocked_or_incomplete_batches": sum(1 for item in rows if item["failed_gates"]),
        "all_current_public_domestic_jobs": len(public_domestic_jobs),
        "assigned_current_public_domestic_jobs": len(_deduplicate_jobs(assigned_jobs)),
        "unassigned_current_public_domestic_jobs": len(_deduplicate_jobs(unassigned_jobs)),
        "batch_row_overlap_deduplicated": max(
            0,
            sum(int(item["current_public_domestic_jobs"]) for item in rows)
            - len(_deduplicate_jobs(assigned_jobs)),
        ),
        "note": "批次台账只解释扩容进度；候选、待复核、访问受限和过期记录不会进入 current_public_domestic_jobs。",
    }


def _evidence_complete(job: dict[str, Any], required_fields: Iterable[str]) -> bool:
    for field in required_fields:
        if not _required_field_present(job, field):
            return False
    evidence = job.get("field_evidence")
    if not isinstance(evidence, dict):
        return False
    scope = str(evidence.get("evidence_scope") or "").lower()
    return any(token in scope for token in ("detail", "job", "table_row", "official_html"))


_CANONICAL_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    # The batch registry deliberately uses human-readable field names.  Job
    # rows use the database contract names, so the projection must translate
    # them instead of treating every production row as evidence-incomplete.
    "official_evidence_url": ("official_evidence_url", "source_url"),
    "major": ("major", "major_tags"),
    "degree": ("degree", "degree_levels"),
    # A normalized province is not a substitute for a job-level work
    # location. Keep this gate strict so a location-less row cannot pass by
    # inheriting geography from a source or employer.
    "location": ("location",),
    "deadline": ("deadline", "deadline_date"),
}


def _required_field_present(job: dict[str, Any], field: str) -> bool:
    """Check a batch field against both registry and database field names."""

    keys = _CANONICAL_FIELD_ALIASES.get(str(field), (str(field),))
    for key in keys:
        value = job.get(key)
        if isinstance(value, (list, tuple, set)):
            if any(str(item).strip() for item in value):
                return True
        elif isinstance(value, dict):
            if any(str(item).strip() for item in value.values()):
                return True
        elif str(value or "").strip():
            return True
    return False


def _deduplicate_jobs(jobs: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate overlap between a live source and its verified snapshot.

    ``fingerprint`` includes ``source_id`` by design, so it cannot identify
    the same official job across a replacement source.  Prefer the stable
    official detail key, then the canonical detail/evidence URL, then the
    external ID plus employer.  Rows without any stable identity are kept;
    silently dropping them would hide an evidence defect rather than fix it.
    """

    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for raw in jobs:
        job = dict(raw)
        detail_key = str(job.get("official_detail_key") or "").strip()
        source_url = str(job.get("source_url") or "").strip()
        external_id = str(job.get("external_id") or "").strip()
        employer = str(job.get("canonical_employer_name") or job.get("employer") or "").strip()
        if detail_key:
            key = ("detail_key", detail_key)
        elif external_id and employer:
            key = ("external_id", f"{external_id}|{employer}")
        elif source_url:
            key = ("source_url", source_url)
        else:
            key = ("row", str(len(result)))
        if key in seen:
            continue
        seen.add(key)
        result.append(job)
    return result


def _next_action(failed: list[str], batch: dict[str, Any]) -> str:
    if "all_sources_registered" in failed:
        return "register_missing_official_sources_before_capture"
    if "two_consecutive_refresh_rate_met" in failed:
        return "run_two_complete_low_rate_captures_and_keep_partial_runs_private"
    if "evidence_rate_at_least_95_percent" in failed:
        return "resolve_job_level_detail_or_attachment_evidence_before_activation"
    if "has_current_public_jobs" in failed:
        return "complete_first_compliant_scan_or_record_access_limited_state"
    return "continue_scheduled_refresh_and_lifecycle_cleanup"


__all__ = [
    "BATCH_REGISTRY_PATH",
    "build_recruitment_batch_report",
    "load_recruitment_batch_registry",
]
