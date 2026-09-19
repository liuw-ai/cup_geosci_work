"""Operational coverage metrics for the official employment-source network."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
import re
from typing import Any

from job_hub.db import Database, utc_now
from job_hub.employers import CATEGORY_ORDER
from job_hub.locations import PROVINCES
from job_hub.simulation import simulate_cohort
from job_hub.source_targets import load_source_targets, target_matrix_summary


HEALTHY_SOURCE_STATUSES = {"source_active"}
UNHEALTHY_SOURCE_STATUSES = {
    "source_degraded",
    "source_blocked",
    "source_error",
}

REQUIRED_PROVINCIAL_SOURCE_ROLES = (
    "human_resources_or_exam",
    "natural_resources",
    "geology_bureau_or_institute",
    "public_institution_recruitment",
    "civil_service",
)


def build_coverage_report(
    database: Database,
    *,
    snapshot_date: str | None = None,
) -> dict[str, Any]:
    """Measure quality and distribution, not just the total job count.

    The report is intentionally based on publicly published job rows and source
    health records.  Candidate leads and any third-party discovery pages are
    never included.
    """
    if snapshot_date is not None:
        try:
            # Snapshot dates deliberately use the deployment's local business day.
            # Callers with a configured timezone pass that value explicitly.
            date.fromisoformat(snapshot_date)
        except (TypeError, ValueError) as error:
            raise ValueError("snapshot_date must use ISO YYYY-MM-DD format") from error

    sources = database.list_sources()
    source_by_id = {source["id"]: source for source in sources}
    health_by_id = {
        item["source_id"]: item for item in database.list_source_health()
    }
    latest_runs_by_id = {
        item["source_id"]: item for item in database.list_latest_crawl_runs()
    }
    jobs, _ = database.list_jobs(page_size=None)
    enabled_source_ids = {
        str(source["id"]) for source in sources if source.get("enabled")
    }

    jobs_by_source = Counter(str(job.get("source_id") or "unknown") for job in jobs)
    jobs_by_category = Counter(str(job.get("category") or "未分类") for job in jobs)
    jobs_by_province = Counter(str(job.get("province") or "未注明") for job in jobs)
    sources_by_category: dict[str, Counter[str]] = defaultdict(Counter)
    for job in jobs:
        sources_by_category[str(job.get("category") or "未分类")][
            str(job.get("source_id") or "unknown")
        ] += 1
    total_jobs = len(jobs)
    source_concentration = _concentration(jobs_by_source, total_jobs)
    source_concentration["top_name"] = (
        source_by_id.get(str(source_concentration.get("top_key")), {}).get("name")
        if source_concentration.get("top_key")
        else None
    )
    category_concentration = _concentration(jobs_by_category, total_jobs)

    field_completeness = {
        field: _completion(total_jobs, jobs, field)
        for field in (
            "employer",
            "source_url",
            "official_evidence_url",
            "location",
            "country_or_region",
            "province",
            "city",
            "degree_levels",
            "major_tags",
            "deadline_date",
        )
    }
    deadline_quality = _deadline_quality(jobs, source_by_id)
    location_quality = _location_quality(jobs)
    target_matrix = target_matrix_summary(
        load_source_targets(),
        source_ids=set(source_by_id),
    )
    province_coverage = [
        _province_coverage(
            province,
            sources=sources,
            health_by_id=health_by_id,
            latest_runs_by_id=latest_runs_by_id,
            jobs_by_province=jobs_by_province,
            source_by_id=source_by_id,
            target_matrix=target_matrix,
        )
        for province in PROVINCES
    ]
    simulation = simulate_cohort(jobs)
    profile_match_quality = {
        "cohort_summary": simulation["summary"],
        "explicit_match_rate": _ratio(
            simulation["summary"].get("explicit_job_profile_matches", 0),
            simulation["summary"].get("explicit_job_profile_matches", 0)
            + simulation["summary"].get("review_job_profile_matches", 0),
        ),
        "students_with_explicit_match_rate": _ratio(
            simulation["summary"].get("students_with_explicit_match", 0),
            simulation.get("cohort_size", 0),
        ),
        "profiles": [
            {
                "profile_id": item["profile"]["id"],
                "label": item["profile"]["label"],
                "explicit_matches": item["explicit_matches"],
                "review_matches": item["review_matches"],
                "category_coverage": item["category_coverage"],
            }
            for item in simulation["profiles"]
        ],
    }
    coverage_snapshots = database.list_coverage_snapshots(limit=3)
    profile_match_quality["trend"] = _profile_match_trend(
        profile_match_quality["cohort_summary"],
        coverage_snapshots,
        snapshot_date=snapshot_date,
    )
    health_distribution = Counter(
        str(health_by_id.get(source["id"], {}).get("status", "unknown"))
        for source in sources
    )
    enabled_latest_runs = {
        source_id: run
        for source_id, run in latest_runs_by_id.items()
        if source_id in enabled_source_ids
    }
    scan_distribution = Counter(_scan_outcome(run) for run in enabled_latest_runs.values())
    successful_scan_sources = sum(
        1
        for source_id, run in enabled_latest_runs.items()
        if run["status"] == "finished"
        and health_by_id.get(source_id, {}).get("status") in HEALTHY_SOURCE_STATUSES
    )
    sources_with_open_matches = sum(
        1
        for source_id, run in enabled_latest_runs.items()
        if run["status"] == "finished"
        and health_by_id.get(source_id, {}).get("status") in HEALTHY_SOURCE_STATUSES
        and int(run.get("open_matching_count") or 0) > 0
    )

    return {
        "generated_at": utc_now(),
        "scope": "公开学生端已发布官方岗位，不含内部候选线索",
        "open_jobs": total_jobs,
        "source_health": {
            "registered_sources": len(sources),
            "enabled_sources": sum(1 for source in sources if source["enabled"]),
            "distribution": dict(sorted(health_distribution.items())),
            "sources_without_health_record": sorted(
                source["id"] for source in sources if source["id"] not in health_by_id
            ),
            "enabled_sources_without_health_record": sorted(
                source["id"]
                for source in sources
                if source["enabled"] and source["id"] not in health_by_id
            ),
            "unhealthy_sources": [
                {
                    "id": source["id"],
                    "name": source["name"],
                    "status": health_by_id.get(source["id"], {}).get("status", "unknown"),
                    "detail": health_by_id.get(source["id"], {}).get("detail", ""),
                }
                for source in sources
                if health_by_id.get(source["id"], {}).get("status", "unknown")
                in UNHEALTHY_SOURCE_STATUSES
            ],
        },
        "source_resilience": _source_resilience(
            sources,
            health_by_id=health_by_id,
            latest_runs_by_id=latest_runs_by_id,
        ),
        "scan_quality": {
            "sources_with_latest_run": len(enabled_latest_runs),
            "successful_scan_sources": successful_scan_sources,
            "sources_with_open_matches": sources_with_open_matches,
            "distribution": dict(sorted(scan_distribution.items())),
        },
        "province_coverage": province_coverage,
        "source_target_matrix": target_matrix,
        "field_completeness": field_completeness,
        "location_quality": location_quality,
        "deadline_quality": deadline_quality,
        "quality_gate": _quality_gate(
            field_completeness=field_completeness,
            deadline_quality=deadline_quality,
            location_quality=location_quality,
            simulation=simulation,
            province_coverage=province_coverage,
            source_concentration=source_concentration,
            category_concentration=category_concentration,
        ),
        "job_distribution": {
            "by_province": dict(sorted(jobs_by_province.items())),
            "by_country_or_region": dict(
                Counter(
                    str(job.get("country_or_region") or "未注明") for job in jobs
                ).most_common()
            ),
            "by_category": dict(jobs_by_category.most_common()),
            "by_source": dict(jobs_by_source.most_common()),
            "source_concentration": source_concentration,
            "category_concentration": category_concentration,
            "category_source_concentration": {
                category: _category_source_metric(
                    category,
                    jobs_by_category=jobs_by_category,
                    sources_by_category=sources_by_category,
                    source_by_id=source_by_id,
                )
                for category in CATEGORY_ORDER
            },
        },
        "profile_match_quality": profile_match_quality,
        "trend_since_last_snapshot": _coverage_snapshot_trend(
            open_jobs=total_jobs,
            profile_summary=profile_match_quality["cohort_summary"],
            scan_quality={
                "successful_scan_sources": successful_scan_sources,
                "sources_with_open_matches": sources_with_open_matches,
            },
            source_concentration=source_concentration,
            category_concentration=category_concentration,
            snapshots=coverage_snapshots,
            snapshot_date=snapshot_date,
        ),
    }


def _province_coverage(
    province: str,
    *,
    sources: list[dict[str, Any]],
    health_by_id: dict[str, dict[str, Any]],
    latest_runs_by_id: dict[str, dict[str, Any]],
    jobs_by_province: Counter[str],
    source_by_id: dict[str, dict[str, Any]],
    target_matrix: dict[str, Any],
) -> dict[str, Any]:
    local_sources = [
        source
        for source in sources
        if source.get("config", {}).get("province") == province
    ]
    enabled_local_sources = [source for source in local_sources if source["enabled"]]
    statuses = Counter(
        str(health_by_id.get(source["id"], {}).get("status", "unknown"))
        for source in local_sources
    )
    fallback_ids = sorted(
        {
            fallback_id
            for source in local_sources
            for fallback_id in source.get("config", {}).get("fallback_source_ids", [])
            if fallback_id in source_by_id
        }
    )
    accessible = sum(
        1
        for source in enabled_local_sources
        if health_by_id.get(source["id"], {}).get("status")
        in HEALTHY_SOURCE_STATUSES
    )
    completed_scans = [
        source
        for source in enabled_local_sources
        if latest_runs_by_id.get(source["id"], {}).get("status") == "finished"
    ]
    active_sources = [
        source
        for source in enabled_local_sources
        if _source_has_usable_scan(source, health_by_id, latest_runs_by_id)
    ]
    matching_sources = [
        source for source in active_sources
        if int(latest_runs_by_id[source["id"]].get("open_matching_count") or 0) > 0
    ]
    no_match_sources = [
        source for source in active_sources
        if int(latest_runs_by_id[source["id"]].get("open_matching_count") or 0) == 0
    ]
    unready_source_ids = [
        source["id"]
        for source in enabled_local_sources
        if not _source_has_usable_scan(source, health_by_id, latest_runs_by_id)
    ]
    fallback_sources = [
        source_by_id[fallback_id]
        for fallback_id in fallback_ids
        if fallback_id in source_by_id
    ]
    fallback_active = [
        source
        for source in fallback_sources
        if _source_has_usable_scan(source, health_by_id, latest_runs_by_id)
    ]
    fallback_open_matches = sum(
        1
        for source in fallback_active
        if int(latest_runs_by_id.get(source["id"], {}).get("open_matching_count") or 0)
        > 0
    )
    registered_roles = sorted(
        {
            str(source.get("config", {}).get("source_role"))
            for source in local_sources
            if source.get("config", {}).get("source_role")
        }
    )
    target_active_sources = max(
        [int(source.get("config", {}).get("target_active_sources", 3)) for source in local_sources]
        or [3]
    )
    has_unhealthy_source = any(
        health_by_id.get(source["id"], {}).get("status") in UNHEALTHY_SOURCE_STATUSES
        for source in enabled_local_sources
    )
    target_row = next(
        (
            item
            for item in target_matrix.get("provinces", [])
            if item.get("province") == province
        ),
        {
            "role_states": {},
            "role_source_ids": {},
            "verified_targets": 0,
            "target_count": 0,
            "missing_target_roles": list(REQUIRED_PROVINCIAL_SOURCE_ROLES),
        },
    )
    all_enabled_sources_scanned = bool(enabled_local_sources) and not unready_source_ids
    all_target_roles_verified = not target_row.get("missing_target_roles")
    target_role_source_ids = target_row.get("role_source_ids", {})
    target_roles_without_usable_scan = [
        role
        for role in REQUIRED_PROVINCIAL_SOURCE_ROLES
        if role in target_role_source_ids
        and not _source_has_usable_scan(
            source_by_id.get(str(target_role_source_ids[role]), {}),
            health_by_id,
            latest_runs_by_id,
        )
    ]
    all_verified_target_roles_usable = bool(
        all_target_roles_verified and not target_roles_without_usable_scan
    )
    no_match_is_verified = bool(
        all_enabled_sources_scanned
        and all_target_roles_verified
        and all_verified_target_roles_usable
        and not matching_sources
    )
    if not local_sources:
        scan_interpretation = "尚未登记该省官方来源，不能把零岗位解释为无招聘"
    elif matching_sources:
        scan_interpretation = "已完成当地官方来源扫描，当前存在可公开的匹配岗位"
    elif has_unhealthy_source:
        scan_interpretation = "至少一个当地来源当前访问异常，不能把零岗位解释为无招聘"
    elif unready_source_ids:
        scan_interpretation = "至少一个已启用当地来源未完成可用扫描，不能把零岗位解释为无招聘"
    elif not all_target_roles_verified:
        scan_interpretation = "五类官方入口尚未全部核验，不能把零岗位解释为无招聘"
    elif target_roles_without_usable_scan:
        scan_interpretation = "五类官方入口虽已核验，但至少一个未完成可用扫描，不能把零岗位解释为无招聘"
    elif jobs_by_province[province]:
        scan_interpretation = "当前有来自官方平台的该省岗位；当地来源覆盖仍需继续核验"
    elif fallback_open_matches:
        scan_interpretation = "当地来源暂无匹配，但全国级官方备用来源有开放岗位；不代表当地没有招聘"
    elif no_match_is_verified:
        scan_interpretation = "五类官方入口均已完成可用扫描，本次未发现匹配岗位"
    elif no_match_sources:
        scan_interpretation = "已扫描来源本次无匹配，但五类官方入口尚未全部核验，不能称该省无岗位"
    else:
        scan_interpretation = "公开入口可访问不等于已扫描；当前不能把零岗位解释为无招聘"
    return {
        "province": province,
        "registered_official_sources": len(local_sources),
        "enabled_sources": len(enabled_local_sources),
        "active_official_sources": len(active_sources),
        "target_active_official_sources": target_active_sources,
        "accessible_entry_sources": accessible,
        "accessible_or_scanned_sources": len(
            {
                source["id"]
                for source in enabled_local_sources
                if health_by_id.get(source["id"], {}).get("status")
                in HEALTHY_SOURCE_STATUSES
            }
            | {source["id"] for source in completed_scans}
        ),
        "completed_scan_sources": len(completed_scans),
        "sources_with_open_matches": len(matching_sources),
        "no_matching_opening_sources": len(no_match_sources),
        "no_match_is_verified": no_match_is_verified,
        "all_enabled_sources_scanned": all_enabled_sources_scanned,
        "all_target_roles_verified": all_target_roles_verified,
        "all_verified_target_roles_usable": all_verified_target_roles_usable,
        "unready_source_ids": unready_source_ids,
        "target_roles_without_usable_scan": target_roles_without_usable_scan,
        "has_unhealthy_enabled_source": has_unhealthy_source,
        "fallback_source_ids": fallback_ids,
        "fallback_sources_registered": len(fallback_ids),
        "fallback_active_sources": len(fallback_active),
        "fallback_sources_with_open_matches": fallback_open_matches,
        "effective_active_sources": len(active_sources) + len(fallback_active),
        "health_statuses": dict(sorted(statuses.items())),
        "registered_source_roles": registered_roles,
        "missing_source_roles": [
            role for role in REQUIRED_PROVINCIAL_SOURCE_ROLES if role not in registered_roles
        ],
        "target_role_states": target_row.get("role_states", {}),
        "verified_target_sources": target_row.get("verified_targets", 0),
        "target_source_count": target_row.get("target_count", 0),
        "missing_target_roles": target_row.get("missing_target_roles", []),
        "published_open_jobs": jobs_by_province[province],
        "scan_interpretation": scan_interpretation,
    }


def _source_has_usable_scan(
    source: dict[str, Any],
    health_by_id: dict[str, dict[str, Any]],
    latest_runs_by_id: dict[str, dict[str, Any]],
) -> bool:
    """A usable source must be enabled, healthy, and have a completed latest run."""
    return bool(
        source.get("enabled")
        and health_by_id.get(source["id"], {}).get("status") in HEALTHY_SOURCE_STATUSES
        and latest_runs_by_id.get(source["id"], {}).get("status") == "finished"
    )


def _source_resilience(
    sources: list[dict[str, Any]],
    *,
    health_by_id: dict[str, dict[str, Any]],
    latest_runs_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Separate declared backup routes from fallback sources proven usable today."""
    source_by_id = {str(source["id"]): source for source in sources}
    without_backup: list[dict[str, str]] = []
    details: list[dict[str, Any]] = []
    with_backup = 0
    with_usable_fallback_source = 0
    for source in sources:
        config = source.get("config", {})
        listing_urls = config.get("listing_urls") or []
        fallback_entries = config.get("fallback_entry_urls") or []
        fallback_sources = sorted(
            {
                str(source_id)
                for source_id in config.get("fallback_source_ids", [])
                if str(source_id) and str(source_id) != str(source["id"])
            }
        )
        has_backup = bool(fallback_entries or fallback_sources or len(listing_urls) > 1)
        usable_fallback_ids = [
            source_id
            for source_id in fallback_sources
            if source_id in source_by_id
            and _source_has_usable_scan(
                source_by_id[source_id], health_by_id, latest_runs_by_id
            )
        ]
        details.append(
            {
                "id": source["id"],
                "name": source["name"],
                "has_backup": has_backup,
                "backup_entry_count": len(fallback_entries) + max(len(listing_urls) - 1, 0),
                "backup_source_count": len(fallback_sources),
                "usable_fallback_source_ids": usable_fallback_ids,
            }
        )
        if has_backup:
            with_backup += 1
        else:
            without_backup.append({"id": source["id"], "name": source["name"]})
        if usable_fallback_ids:
            with_usable_fallback_source += 1
    return {
        "registered_sources": len(sources),
        "sources_with_backup": with_backup,
        "coverage_rate": round((with_backup / len(sources)) if sources else 0.0, 4),
        "sources_with_usable_fallback_source": with_usable_fallback_source,
        "usable_fallback_source_coverage_rate": round(
            (with_usable_fallback_source / len(sources)) if sources else 0.0,
            4,
        ),
        "sources_without_backup": without_backup,
        "details": details,
    }


def _scan_outcome(run: dict[str, Any]) -> str:
    if run["status"] != "finished":
        return str(run["status"])
    return (
        "successful_with_open_matches"
        if int(run.get("open_matching_count") or 0) > 0
        else "successful_without_open_matches"
    )


def _completion(total: int, jobs: list[dict[str, Any]], field: str) -> dict[str, int | float]:
    if field in {"degree_levels", "major_tags"}:
        complete = sum(1 for job in jobs if job.get(field))
    elif field == "location":
        complete = sum(
            1
            for job in jobs
            if str(job.get("location") or "").strip()
            or str(job.get("province") or "").strip()
            or str(job.get("country_or_region") or "").strip()
        )
    else:
        complete = sum(1 for job in jobs if str(job.get(field) or "").strip())
    return {
        "complete": complete,
        "total": total,
        "rate": round((complete / total) if total else 0.0, 4),
    }


def _concentration(counts: Counter[str], total: int) -> dict[str, Any]:
    if not total or not counts:
        return {"top_key": None, "top_count": 0, "top_share": 0.0, "risk": "no_data"}
    key, count = counts.most_common(1)[0]
    share = count / total
    return {
        "top_key": key,
        "top_count": count,
        "top_share": round(share, 4),
        "risk": "high" if share >= 0.5 else "watch" if share >= 0.3 else "balanced",
    }


def _category_source_metric(
    category: str,
    *,
    jobs_by_category: Counter[str],
    sources_by_category: dict[str, Counter[str]],
    source_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Expose per-category source concentration with a readable source name."""
    counts = sources_by_category.get(category, Counter())
    metric = _concentration(counts, int(jobs_by_category.get(category, 0)))
    metric["source_count"] = len(counts)
    metric["top_name"] = (
        source_by_id.get(str(metric.get("top_key")), {}).get("name")
        if metric.get("top_key")
        else None
    )
    return metric


def _ratio(numerator: int | float, denominator: int | float) -> float:
    return round((numerator / denominator) if denominator else 0.0, 4)


def _previous_snapshot(
    snapshots: list[dict[str, Any]],
    *,
    snapshot_date: str | None,
) -> dict[str, Any] | None:
    """Find the last *different-day* observation when recording today's metric.

    A daily snapshot is replaceable because later syncs can correct the same
    day's measurement.  Excluding the current business date keeps the UI's
    "versus previous record" indicator meaningful after that replacement.
    """
    for snapshot in snapshots:
        if snapshot_date and snapshot.get("snapshot_date") == snapshot_date:
            continue
        return snapshot
    return None


def _profile_match_trend(
    summary: dict[str, Any],
    snapshots: list[dict[str, Any]],
    *,
    snapshot_date: str | None,
) -> dict[str, Any]:
    baseline = _previous_snapshot(snapshots, snapshot_date=snapshot_date)
    current = {
        "explicit_job_profile_matches": int(
            summary.get("explicit_job_profile_matches", 0)
        ),
        "review_job_profile_matches": int(summary.get("review_job_profile_matches", 0)),
        "students_with_explicit_match": int(
            summary.get("students_with_explicit_match", 0)
        ),
    }
    if baseline is None:
        return {
            "available": False,
            "baseline_snapshot_date": None,
            "message": "尚无上一日质量快照，下一次记录后可比较变化。",
        }
    return {
        "available": True,
        "baseline_snapshot_date": baseline["snapshot_date"],
        **{
            f"{field}_change": value - int(baseline.get(field, 0))
            for field, value in current.items()
        },
    }


def _coverage_snapshot_trend(
    *,
    open_jobs: int,
    profile_summary: dict[str, Any],
    scan_quality: dict[str, Any],
    source_concentration: dict[str, Any],
    category_concentration: dict[str, Any],
    snapshots: list[dict[str, Any]],
    snapshot_date: str | None,
) -> dict[str, Any]:
    """Compare high-signal coverage metrics with the preceding daily snapshot."""
    baseline = _previous_snapshot(snapshots, snapshot_date=snapshot_date)
    if baseline is None:
        return {
            "available": False,
            "baseline_snapshot_date": None,
            "message": "尚无上一日质量快照，无法计算变化。",
        }
    return {
        "available": True,
        "baseline_snapshot_date": baseline["snapshot_date"],
        "open_jobs_change": open_jobs - int(baseline.get("open_jobs", 0)),
        "explicit_job_profile_matches_change": int(
            profile_summary.get("explicit_job_profile_matches", 0)
        ) - int(baseline.get("explicit_job_profile_matches", 0)),
        "review_job_profile_matches_change": int(
            profile_summary.get("review_job_profile_matches", 0)
        ) - int(baseline.get("review_job_profile_matches", 0)),
        "students_with_explicit_match_change": int(
            profile_summary.get("students_with_explicit_match", 0)
        ) - int(baseline.get("students_with_explicit_match", 0)),
        "successful_scan_sources_change": int(
            scan_quality.get("successful_scan_sources", 0)
        ) - int(baseline.get("successful_scan_sources", 0)),
        "sources_with_open_matches_change": int(
            scan_quality.get("sources_with_open_matches", 0)
        ) - int(baseline.get("sources_with_open_matches", 0)),
        "source_top_share_change": round(
            float(source_concentration.get("top_share", 0))
            - float(baseline.get("source_top_share", 0)),
            4,
        ),
        "category_top_share_change": round(
            float(category_concentration.get("top_share", 0))
            - float(baseline.get("category_top_share", 0)),
            4,
        ),
    }


def _location_quality(jobs: list[dict[str, Any]]) -> dict[str, Any]:
    raw = sum(1 for job in jobs if str(job.get("location") or "").strip())
    normalized = sum(
        1
        for job in jobs
        if str(job.get("province") or "").strip()
        or str(job.get("country_or_region") or "").strip()
    )
    domestic = sum(1 for job in jobs if str(job.get("province") or "").strip())
    overseas = sum(
        1
        for job in jobs
        if job.get("country_or_region") and not job.get("province")
    )
    return {
        "raw_location": {"complete": raw, "total": len(jobs), "rate": _ratio(raw, len(jobs))},
        "normalized_region": {
            "complete": normalized,
            "total": len(jobs),
            "rate": _ratio(normalized, len(jobs)),
        },
        "mainland_province": {
            "complete": domestic,
            "total": len(jobs),
            "rate": _ratio(domestic, len(jobs)),
        },
        "overseas_or_hong_kong_macao_taiwan": overseas,
    }


def _deadline_quality(
    jobs: list[dict[str, Any]], source_by_id: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    explicit = 0
    policy_known = 0
    unstated = 0
    for job in jobs:
        if str(job.get("deadline_date") or "").strip():
            explicit += 1
            continue
        source = source_by_id.get(str(job.get("source_id") or ""), {})
        policy = str(source.get("config", {}).get("deadline_policy") or "").strip()
        text = f"{job.get('summary', '')} {job.get('description', '')}".lower()
        if policy or re.search(
            r"open\s+until\s+filled|rolling\s+basis|ongoing|招满即止|长期招聘|长期有效",
            text,
            re.IGNORECASE,
        ):
            policy_known += 1
        else:
            unstated += 1
    total = len(jobs)
    return {
        "explicit": {"complete": explicit, "total": total, "rate": _ratio(explicit, total)},
        "source_policy_or_open_ended": {
            "complete": policy_known,
            "total": total,
            "rate": _ratio(policy_known, total),
        },
        "known_or_policy": {
            "complete": explicit + policy_known,
            "total": total,
            "rate": _ratio(explicit + policy_known, total),
        },
        "unstated": unstated,
    }


def _quality_gate(
    *,
    field_completeness: dict[str, dict[str, Any]],
    deadline_quality: dict[str, Any],
    location_quality: dict[str, Any],
    simulation: dict[str, Any],
    province_coverage: list[dict[str, Any]],
    source_concentration: dict[str, Any],
    category_concentration: dict[str, Any],
) -> dict[str, Any]:
    checks = {
        "official_evidence_url_complete": field_completeness["official_evidence_url"]["rate"] == 1.0,
        "major_tags_at_least_90_percent": field_completeness["major_tags"]["rate"] >= 0.9,
        "normalized_location_at_least_90_percent": location_quality["normalized_region"]["rate"] >= 0.9,
        "deadline_explicit_or_policy_at_least_80_percent": deadline_quality["known_or_policy"]["rate"] >= 0.8,
        "explicit_profile_match_exists": simulation["summary"].get("explicit_job_profile_matches", 0) > 0,
        "source_concentration_below_50_percent": source_concentration.get("top_share", 0) < 0.5,
        "category_concentration_below_70_percent": category_concentration.get("top_share", 0) < 0.7,
        "no_province_marked_verified_during_source_failure": all(
            not item.get("no_match_is_verified") or not item.get("health_statuses", {}).get("source_error")
            for item in province_coverage
        ),
    }
    failed = [name for name, passed in checks.items() if not passed]
    return {
        "status": "pass" if not failed else "needs_attention",
        "checks": checks,
        "failed_checks": failed,
        "note": "质量门槛用于指导扩源，不会因未达标而伪造或隐藏官方岗位。",
    }
