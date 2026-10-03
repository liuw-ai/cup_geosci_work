"""Reproducible scorecard for the student-facing employment service.

The score is an engineering readiness measure, not a promise that a student is
eligible to apply.  It deliberately uses only rows already visible to students
and records the dimensions that can be improved by source work.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any


SCORE_VERSION = "1.1"
DEFAULT_EFFECTIVE_JOB_TARGET = 1_000
_WEIGHTS = {
    "current_effective_jobs": 20,
    "official_detail_evidence": 15,
    "explicit_major_degree_match": 15,
    "source_diversity": 15,
    "consecutive_refresh": 15,
    "core_field_completeness": 10,
    "province_coverage": 5,
    "lifecycle_quality": 5,
}


def build_scorecard(
    jobs: list[dict[str, Any]],
    *,
    sources: list[dict[str, Any]],
    health_by_id: dict[str, dict[str, Any]],
    crawl_runs: list[dict[str, Any]],
    field_completeness: dict[str, dict[str, Any]],
    deadline_quality: dict[str, Any],
    location_quality: dict[str, Any],
    quality_gate: dict[str, Any],
    province_coverage: list[dict[str, Any]],
    effective_job_target: int = DEFAULT_EFFECTIVE_JOB_TARGET,
) -> dict[str, Any]:
    """Return an auditable 100-point score from current public evidence.

    ``jobs`` must be the result of the public read query.  Passing historical
    or pending-review rows here would inflate the score and is intentionally
    impossible for the coverage caller.
    """
    effective_count = len(jobs)
    source_ids = {str(job.get("source_id") or "") for job in jobs if job.get("source_id")}
    source_count = len(source_ids)
    explicit_match_count = sum(
        1 for job in jobs if job.get("publication_status") == "student_eligible"
    )
    detail_count = sum(
        1
        for job in jobs
        if str(job.get("official_evidence_url") or "").strip()
        and _detail_evidence_present(job)
    )

    enabled_ids = {
        str(source["id"])
        for source in sources
        if source.get("enabled")
    }
    healthy_ids = {
        source_id
        for source_id, health in health_by_id.items()
        if source_id in enabled_ids and health.get("status") == "source_active"
    }
    runs_by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for run in crawl_runs:
        source_id = str(run.get("source_id") or "")
        if source_id in enabled_ids:
            runs_by_source[source_id].append(run)
    consecutive_ids = {
        source_id
        for source_id, runs in runs_by_source.items()
        if source_id in healthy_ids
        and _has_two_consecutive_successes(runs)
    }

    source_top_share = _source_top_share(jobs)
    source_top_5_share = _source_top_n_share(jobs, 5)
    employer_top_share = _employer_top_share(jobs)
    diversity_base = min(source_count / 12.0, 1.0)
    # The target plan treats 25% per source, 75% for the top five sources and
    # 15% per employer as concentration ceilings.  The old score only applied
    # a loose 35% single-source penalty, so a 13-source corpus could receive
    # full diversity points while 89% of its rows still came from five feeds.
    # Use the geometric mean so one healthy dimension cannot hide a collapsed
    # source or employer distribution.
    balance_factors = (
        _ceiling_factor(source_top_share, 0.25),
        _ceiling_factor(source_top_5_share, 0.75),
        _ceiling_factor(employer_top_share, 0.15),
    )
    balance = (balance_factors[0] * balance_factors[1] * balance_factors[2]) ** (1 / 3)
    source_diversity_rate = round(diversity_base * balance, 4)
    refresh_rate = (
        len(consecutive_ids) / len(enabled_ids)
        if enabled_ids
        else 0.0
    )
    core_field_rate = _core_field_rate(
        field_completeness=field_completeness,
        deadline_quality=deadline_quality,
        location_quality=location_quality,
    )
    province_count = sum(
        1
        for item in province_coverage
        if int(item.get("published_open_jobs") or 0) > 0
    )
    province_rate = min(province_count / 31.0, 1.0)
    lifecycle_rate = 1.0 if quality_gate.get("status") == "pass" else 0.0

    target = max(int(effective_job_target), 1)
    component_rates = {
        "current_effective_jobs": min(effective_count / target, 1.0),
        "official_detail_evidence": detail_count / effective_count if effective_count else 0.0,
        "explicit_major_degree_match": explicit_match_count / effective_count if effective_count else 0.0,
        "source_diversity": source_diversity_rate,
        "consecutive_refresh": min(refresh_rate, 1.0),
        "core_field_completeness": core_field_rate,
        "province_coverage": province_rate,
        "lifecycle_quality": lifecycle_rate,
    }
    component_scores = {
        key: round(_WEIGHTS[key] * rate, 2)
        for key, rate in component_rates.items()
    }
    score = round(sum(component_scores.values()), 2)
    return {
        "version": SCORE_VERSION,
        "score": score,
        "max_score": 100,
        "components": {
            key: {
                "weight": _WEIGHTS[key],
                "rate": round(component_rates[key], 4),
                "score": component_scores[key],
            }
            for key in _WEIGHTS
        },
        "evidence": {
            "effective_job_count": effective_count,
            "effective_job_target": target,
            "official_detail_evidence_count": detail_count,
            "explicit_major_degree_match_count": explicit_match_count,
            "visible_source_count": source_count,
            "enabled_source_count": len(enabled_ids),
            "two_consecutive_refresh_source_count": len(consecutive_ids),
            "two_consecutive_refresh_source_ids": sorted(consecutive_ids),
            "source_top_share": round(source_top_share, 4),
            "source_top_5_share": round(source_top_5_share, 4),
            "employer_top_share": round(employer_top_share, 4),
            "source_balance_factors": {
                "top_source": round(balance_factors[0], 4),
                "top_five_sources": round(balance_factors[1], 4),
                "top_employer": round(balance_factors[2], 4),
            },
            "province_count_with_open_jobs": province_count,
            "core_field_rate": round(core_field_rate, 4),
        },
        "interpretation": (
            "该分数只衡量当前学生端数据和生产证据的完整度，不代表岗位数量上限、"
            "录用概率或个人报名资格；历史、待复核和已过期岗位不计分。"
        ),
    }


def _detail_evidence_present(job: dict[str, Any]) -> bool:
    evidence = job.get("field_evidence")
    if not isinstance(evidence, dict):
        return False
    scope = str(evidence.get("evidence_scope") or "").lower()
    return any(token in scope for token in ("detail", "job", "table_row", "official_html"))


def _source_top_share(jobs: list[dict[str, Any]]) -> float:
    return _source_top_n_share(jobs, 1)


def _source_top_n_share(jobs: list[dict[str, Any]], n: int) -> float:
    counts: dict[str, int] = defaultdict(int)
    for job in jobs:
        counts[str(job.get("source_id") or "unknown")] += 1
    if not counts:
        return 0.0
    return sum(count for _, count in sorted(counts.items(), key=lambda item: item[1], reverse=True)[: max(1, n)]) / len(jobs)


def _employer_top_share(jobs: list[dict[str, Any]]) -> float:
    counts: dict[str, int] = defaultdict(int)
    for job in jobs:
        employer = str(
            job.get("canonical_employer_name")
            or job.get("employer")
            or "unknown"
        ).strip() or "unknown"
        counts[employer] += 1
    return max(counts.values()) / len(jobs) if counts else 0.0


def _ceiling_factor(value: float, ceiling: float) -> float:
    """Return 1 below a concentration ceiling, otherwise a bounded penalty."""

    if value <= 0:
        return 1.0
    return min(1.0, ceiling / value)


def _has_two_consecutive_successes(runs: list[dict[str, Any]]) -> bool:
    ordered = sorted(runs, key=lambda item: int(item.get("id") or 0), reverse=True)
    return len(ordered) >= 2 and all(item.get("status") == "finished" for item in ordered[:2])


def _core_field_rate(
    *,
    field_completeness: dict[str, dict[str, Any]],
    deadline_quality: dict[str, Any],
    location_quality: dict[str, Any],
) -> float:
    rates = [
        float(field_completeness.get("major_tags", {}).get("rate", 0.0)),
        float(field_completeness.get("degree_levels", {}).get("rate", 0.0)),
        float(location_quality.get("normalized_region", {}).get("rate", 0.0)),
        float(deadline_quality.get("known_or_policy", {}).get("rate", 0.0)),
    ]
    return sum(rates) / len(rates) if rates else 0.0
