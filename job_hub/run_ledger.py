"""Auditable source-run conclusions built on top of ``crawl_runs``.

The collector status (``finished``/``failed``/``skipped``) is an implementation
detail.  Operators and the student-facing publication gate need a stable
conclusion that distinguishes an empty successful scan from an unavailable or
access-restricted source.  This module keeps that vocabulary in one place and
never turns a failed run into a no-match result.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime
from typing import Any, Iterable
from zoneinfo import ZoneInfo


RUN_OUTCOMES = frozenset(
    {
        "running",
        "success_with_matches",
        "success_without_matches",
        "source_unavailable",
        "access_limited",
        "parse_failed",
        "manual_review_required",
        "not_run",
        "expired",
        "unknown",
    }
)

_ACCESS_MARKERS = (
    "robots",
    "http 401",
    "http 403",
    "http 407",
    "http 412",
    "http 451",
    "captcha",
    "access restricted",
    "access policy",
    "not permit",
    "permission",
)
_TRANSPORT_MARKERS = (
    "tls",
    "eof",
    "timeout",
    "timed out",
    "connection",
    "dns",
    "name resolution",
    "source unavailable",
    "unavailable",
)
_REVIEW_MARKERS = (
    "manual review",
    "manual_review",
    "待复核",
    "人工复核",
    "needs_review",
)
_PARSE_MARKERS = (
    "parse",
    "parser",
    "json",
    "html",
    "extract",
    "unsupported source",
)


def classify_run_outcome(
    status: str,
    *,
    error: str | None = None,
    open_matching_count: int = 0,
    discovered_count: int = 0,
    manual_review_count: int = 0,
) -> str:
    """Map an operational run to a publication-safe conclusion.

    ``success_without_matches`` is emitted only for a completed scan.  A
    blocked, unavailable or parser-failed source therefore cannot be silently
    interpreted as having no suitable vacancies.
    """

    normalized_status = str(status or "unknown").strip().lower()
    if normalized_status == "running":
        return "running"
    if normalized_status == "finished":
        if manual_review_count > 0 and int(open_matching_count or 0) == 0:
            return "manual_review_required"
        return (
            "success_with_matches"
            if int(open_matching_count or 0) > 0
            else "success_without_matches"
        )
    text = str(error or "").strip().lower()
    if any(marker in text for marker in _REVIEW_MARKERS):
        return "manual_review_required"
    if any(marker in text for marker in _ACCESS_MARKERS):
        return "access_limited"
    if any(marker in text for marker in _TRANSPORT_MARKERS):
        return "source_unavailable"
    if any(marker in text for marker in _PARSE_MARKERS):
        return "parse_failed"
    if normalized_status in {"skipped", "interrupted"}:
        return "source_unavailable"
    if normalized_status == "failed":
        return "parse_failed"
    return "unknown"


def _local_date(value: str | None, timezone: str) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo("UTC"))
    return parsed.astimezone(ZoneInfo(timezone)).date().isoformat()


def build_run_ledger(
    runs: Iterable[dict[str, Any]],
    *,
    sources: Iterable[dict[str, Any]] = (),
    report_date: date | None = None,
    timezone: str = "Asia/Shanghai",
) -> dict[str, Any]:
    """Return an auditable, JSON-ready run ledger and aggregate counters."""

    source_list = list(sources)
    source_names = {
        str(item.get("id")): str(item.get("name") or item.get("id") or "")
        for item in source_list
    }
    rows: list[dict[str, Any]] = []
    for run in runs:
        started_local_date = _local_date(run.get("started_at"), timezone)
        if report_date is not None and started_local_date != report_date.isoformat():
            continue
        stored_outcome = str(run.get("outcome") or "").strip()
        # ``unknown`` is the additive-migration default for old runs. Treat it
        # as missing context and infer conservatively from the legacy status,
        # otherwise a historical successful scan would remain permanently
        # indistinguishable from an actually unknown run.
        outcome = stored_outcome if stored_outcome in RUN_OUTCOMES - {"unknown"} else classify_run_outcome(
            str(run.get("status") or "unknown"),
            error=run.get("error_message"),
            open_matching_count=int(run.get("open_matching_count") or 0),
            discovered_count=int(run.get("discovered_count") or 0),
            manual_review_count=int(run.get("manual_review_count") or 0),
        )
        if outcome not in RUN_OUTCOMES:
            outcome = "unknown"
        rows.append(
            {
                "run_id": int(run["id"]),
                "source_id": run.get("source_id"),
                "source_name": source_names.get(str(run.get("source_id")), str(run.get("source_id") or "")),
                "started_at": run.get("started_at"),
                "finished_at": run.get("finished_at"),
                "status": run.get("status"),
                "outcome": outcome,
                "discovered_count": int(run.get("discovered_count") or 0),
                "open_matching_count": int(run.get("open_matching_count") or 0),
                "inserted_count": int(run.get("inserted_count") or 0),
                "updated_count": int(run.get("updated_count") or 0),
                "attempts": int(run.get("attempts") or 0),
                "retryable_failures": int(run.get("retryable_failures") or 0),
                "transport_mode": run.get("transport_mode") or "environment",
                "evidence_complete_count": int(run.get("evidence_complete_count") or 0),
                "manual_review_count": int(run.get("manual_review_count") or 0),
                "attachment_success_count": int(run.get("attachment_success_count") or 0),
                "error_message": run.get("error_message"),
            }
        )
    represented_source_ids = {
        str(row.get("source_id") or "")
        for row in rows
        if row.get("source_id")
    }
    if report_date is not None:
        for source in source_list:
            source_id = str(source.get("id") or "").strip()
            if not source_id or not source.get("enabled", True) or source_id in represented_source_ids:
                continue
            rows.append(
                {
                    "run_id": None,
                    "source_id": source_id,
                    "source_name": source_names.get(source_id, source_id),
                    "started_at": None,
                    "finished_at": None,
                    "status": "not_run",
                    "outcome": "not_run",
                    "discovered_count": 0,
                    "open_matching_count": 0,
                    "inserted_count": 0,
                    "updated_count": 0,
                    "attempts": 0,
                    "retryable_failures": 0,
                    "transport_mode": None,
                    "evidence_complete_count": 0,
                    "manual_review_count": 0,
                    "attachment_success_count": 0,
                    "error_message": "当日没有完成采集运行，不能解释为无岗位。",
                }
            )
    counts = Counter(str(row["outcome"]) for row in rows)
    return {
        "report_date": report_date.isoformat() if report_date else None,
        "runs": rows,
        "summary": {
            "run_count": len(rows),
            "success_with_matches": counts["success_with_matches"],
            "success_without_matches": counts["success_without_matches"],
            "source_unavailable": counts["source_unavailable"],
            "access_limited": counts["access_limited"],
            "parse_failed": counts["parse_failed"],
            "manual_review_required": counts["manual_review_required"],
            "not_run": counts["not_run"],
            "running": counts["running"],
            "expired": counts["expired"],
            "unknown": counts["unknown"],
            "open_matching_count": sum(int(row["open_matching_count"]) for row in rows),
            "evidence_complete_count": sum(int(row["evidence_complete_count"]) for row in rows),
            "attachment_success_count": sum(int(row["attachment_success_count"]) for row in rows),
        },
        "interpretation": (
            "仅 success_with_matches/success_without_matches 表示完成扫描；"
            "source_unavailable、access_limited、parse_failed、running 和 not_run 均不能解释为无岗位。"
        ),
    }
