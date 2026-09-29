"""Read-only triage for provincial official recruitment entry candidates.

The provincial five-role matrix intentionally contains URLs that have been
identified as official but have not yet earned crawler admission.  This module
turns that backlog into a repeatable server-side diagnostic: it verifies the
registered entry's robots policy and availability, without discovering jobs,
following aggregation links, enabling a source, or modifying the database.

An accessible entry is only a candidate for the next manual step.  It still
needs a current official recruitment notice, a row-level position table (or
official detail), field validation, and a dedicated source configuration
before it can contribute jobs to students.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from job_hub.config import Settings
from job_hub.source_targets import REQUIRED_ROLES, load_source_targets, role_label
from job_hub.sources import SourceHealthProbe, SourceHealthResult
from job_hub.transport import transport_metadata


PROBEABLE_STATES = frozenset({"candidate", "blocked"})


def provincial_probe_targets(
    matrix: dict[str, Any] | None = None,
    *,
    provinces: Iterable[str] | None = None,
    roles: Iterable[str] | None = None,
    states: Iterable[str] | None = None,
) -> list[dict[str, str]]:
    """Return only explicit official entries eligible for a harmless probe."""
    payload = matrix or load_source_targets()
    selected_provinces = {str(value).strip() for value in provinces or () if str(value).strip()}
    selected_roles = {str(value).strip() for value in roles or () if str(value).strip()}
    selected_states = {
        str(value).strip() for value in (states or PROBEABLE_STATES) if str(value).strip()
    }
    unknown_roles = selected_roles.difference(REQUIRED_ROLES)
    if unknown_roles:
        raise ValueError(f"Unknown provincial source role(s): {sorted(unknown_roles)}")
    unknown_states = selected_states.difference(PROBEABLE_STATES)
    if unknown_states:
        raise ValueError(
            "Provincial entry probing only accepts candidate/blocked states: "
            f"{sorted(unknown_states)}"
        )

    targets: list[dict[str, str]] = []
    for province, role_map in sorted((payload.get("province_targets") or {}).items()):
        if selected_provinces and province not in selected_provinces:
            continue
        if not isinstance(role_map, dict):
            continue
        for role in REQUIRED_ROLES:
            if selected_roles and role not in selected_roles:
                continue
            target = role_map.get(role)
            if not isinstance(target, dict):
                continue
            state = str(target.get("state") or "").strip()
            entry_url = str(target.get("official_entry_url") or "").strip()
            if state not in selected_states or not entry_url:
                continue
            targets.append(
                {
                    "province": str(province),
                    "role": role,
                    "role_label": role_label(role),
                    "target_state": state,
                    "official_entry_url": entry_url,
                }
            )
    return targets


def run_provincial_entry_probe(
    settings: Settings,
    *,
    matrix: dict[str, Any] | None = None,
    provinces: Iterable[str] | None = None,
    roles: Iterable[str] | None = None,
    states: Iterable[str] | None = None,
    probe: Callable[[dict[str, Any]], SourceHealthResult] | None = None,
    checked_at: datetime | None = None,
) -> dict[str, Any]:
    """Probe a bounded set of candidate entries without changing source state.

    ``probe`` is injectable for tests.  The production default is the same
    robots-aware health probe used for registered sources, but candidates are
    represented transiently and never persisted as enabled source records.
    """
    targets = provincial_probe_targets(
        matrix,
        provinces=provinces,
        roles=roles,
        states=states,
    )
    health_probe = probe or SourceHealthProbe(settings).check
    results: list[dict[str, Any]] = []
    for target in targets:
        source = {
            "id": f"provincial-probe:{target['province']}:{target['role']}",
            "source_type": "html_notice",
            "homepage_url": target["official_entry_url"],
            "config": {
                "healthcheck_url": target["official_entry_url"],
                "require_path_stability": True,
            },
        }
        outcome = health_probe(source)
        classification = _classification(outcome.status)
        results.append(
            {
                **target,
                "classification": classification,
                "source_health_status": outcome.status,
                "status_code": outcome.status_code,
                "detail": outcome.detail,
                "checks": outcome.checks,
                "next_action": _next_action(classification),
            }
        )
    moment = checked_at or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    counts = Counter(str(item["classification"]) for item in results)
    return {
        "version": 1,
        "checked_at": moment.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        **transport_metadata(settings.http_transport_mode),
        "requested_provinces": sorted({str(value) for value in provinces or ()}) or None,
        "requested_roles": sorted({str(value) for value in roles or ()}) or None,
        "target_count": len(results),
        "classification_counts": dict(sorted(counts.items())),
        "results": results,
        "publication_policy": (
            "本报告只核验官方入口和 robots/访问状态；可访问不等于存在岗位。"
            "不得据此启用来源、下载附件、发布岗位或声称该省无岗位。"
        ),
    }


def _classification(status: str) -> str:
    if status == "source_active":
        return "entry_accessible"
    if status == "source_blocked":
        return "access_limited"
    return "source_unavailable"


def _next_action(classification: str) -> str:
    if classification == "entry_accessible":
        return "人工定位当前招聘公告与官方职位表；字段和回归样例通过后才可注册来源。"
    if classification == "access_limited":
        return "保留访问受限证据；不得标记为无岗位，等待官方公开附件或人工核验入口。"
    return "记录来源故障并保留备用入口；不启用采集器，不得标记为无岗位。"


__all__ = [
    "PROBEABLE_STATES",
    "provincial_probe_targets",
    "run_provincial_entry_probe",
]
