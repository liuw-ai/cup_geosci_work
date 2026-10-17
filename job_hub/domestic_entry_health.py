"""Read-only health checks for unregistered official expansion candidates.

An ``official_identity_only`` queue record has an official organisation and
entry URL, but no verified vacancy sample.  It must not be registered as a
collector merely to make a health probe possible.  This module converts those
queue records into ephemeral landing-page probe inputs and reports the result
without writing jobs, sources, or source-health rows.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Iterable
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from job_hub.config import Settings
from job_hub.sources import SourceHealthProbe, SourceHealthResult


IDENTITY_ONLY_STATUS = "official_identity_only"


def identity_only_entry_source(record: dict[str, Any]) -> dict[str, Any]:
    """Create the minimum transient source shape accepted by the health probe."""
    official_url = str(record["official_url"]).strip()
    host = (urlparse(official_url).hostname or "").lower()
    if not host:
        raise ValueError(f"{record['id']}: official_url has no hostname")
    return {
        # This is intentionally not a registered ``sources`` row.  The prefix
        # makes accidental persistence into source_health easy to spot.
        "id": f"candidate:{record['id']}",
        "name": str(record.get("organization") or record["id"]),
        "publisher": str(record.get("organization") or record["id"]),
        "homepage_url": official_url,
        "source_type": "landing_page",
        "category": str(record.get("system") or "官方扩源候选"),
        "source_tier": "A",
        "enabled": False,
        "config": {
            "allowed_hosts": [host],
            "healthcheck_url": official_url,
            "require_path_stability": True,
        },
    }


def build_identity_only_entry_health_report(
    queue: dict[str, Any],
    *,
    check: Callable[[dict[str, Any]], SourceHealthResult],
    record_ids: Iterable[str] | None = None,
    observed_at: str | None = None,
) -> dict[str, Any]:
    """Probe only identity-confirmed candidate entries without publication.

    ``check`` is injected so the report can be tested without network access.
    The queue itself remains the owner of discovery status; this report only
    records temporary reachability evidence for the next discovery action.
    """
    requested = {str(item).strip() for item in record_ids or () if str(item).strip()}
    candidates = [
        record
        for record in queue["records"]
        if str(record.get("status") or "") == IDENTITY_ONLY_STATUS
    ]
    candidate_ids = {str(record["id"]) for record in candidates}
    unknown = sorted(requested - candidate_ids)
    if unknown:
        raise ValueError(
            "record_id is not an official_identity_only expansion candidate: "
            + ", ".join(unknown)
        )
    selected = [record for record in candidates if not requested or record["id"] in requested]
    if not selected:
        raise ValueError("no official_identity_only expansion candidates selected")

    rows: list[dict[str, Any]] = []
    for record in selected:
        result = check(identity_only_entry_source(record))
        reachable = bool(result.successful)
        rows.append(
            {
                "candidate_id": record["id"],
                "system": record.get("system"),
                "organization": record.get("organization"),
                "official_url": record["official_url"],
                "entry_status": result.status,
                "entry_status_code": result.status_code,
                "entry_reachable": reachable,
                "detail": result.detail,
                "checks": result.checks,
                "publication_status": "not_registered",
                "next_gate": (
                    "locate_current_official_job_sample"
                    if reachable
                    else "retry_from_approved_environment"
                ),
            }
        )

    observed = observed_at or datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
    return {
        "scope": "official_identity_only_entry_health",
        "observed_at": observed,
        "candidate_count": len(rows),
        "reachable_count": sum(1 for row in rows if row["entry_reachable"]),
        "unreachable_or_blocked_count": sum(
            1 for row in rows if not row["entry_reachable"]
        ),
        "items": rows,
        "publication_policy": (
            "本报告只检查队列中的官方入口和 robots.txt；不采集岗位、不注册来源、"
            "不写入学生端。入口失败不能解释为无岗位，入口成功也不能解释为存在可发布岗位。"
        ),
    }


def run_identity_only_entry_health(
    settings: Settings,
    queue: dict[str, Any],
    *,
    record_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Run compliant entry checks using the application's normal HTTP policy."""
    probe = SourceHealthProbe(settings)
    return build_identity_only_entry_health_report(
        queue,
        check=probe.check,
        record_ids=record_ids,
    )
