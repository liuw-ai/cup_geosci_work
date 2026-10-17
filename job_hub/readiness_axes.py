"""Separate reliability from domestic-coverage readiness.

The student service has two different questions to answer:

* Are the rows currently published trustworthy and maintained correctly?
* Is the domestic corpus broad enough to serve the whole college?

Combining those questions into one score makes a small, high-quality corpus
look further complete than it is.  This module intentionally keeps them as
two auditable axes and exposes explicit hard gates for the latter claim.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Iterable

from job_hub.locations import MAINLAND_COUNTRY_LABELS, PROVINCES
from job_hub.employers import employment_segment
from job_hub.profiles import PUBLIC_STUDENT_PUBLICATION_STATUSES


PUBLIC_STATUSES = PUBLIC_STUDENT_PUBLICATION_STATUSES
DEFAULT_RELIABILITY_READY_SCORE = 90.0
DEFAULT_DETAIL_EVIDENCE_RATE = 0.95
DEFAULT_EXPLICIT_MATCH_RATE = 0.95
DEFAULT_REFRESH_RATE = 0.90
DEFAULT_MAX_SOURCE_SHARE = 0.25
DEFAULT_MAX_TOP_FIVE_SOURCE_SHARE = 0.75
DEFAULT_MAX_EMPLOYER_SHARE = 0.15


def is_public_job(job: dict[str, Any]) -> bool:
    """Return whether a row belongs to the current student-facing corpus."""

    return (
        str(job.get("status") or "open") == "open"
        and str(job.get("publication_status") or "") in PUBLIC_STATUSES
    )


def is_domestic_job(job: dict[str, Any]) -> bool:
    """Identify mainland jobs without guessing from an employer name.

    Location normalization normally writes both ``province`` and
    ``country_or_region``.  The province fallback keeps older verified rows
    measurable while avoiding a false domestic classification for an empty
    location.
    """

    country = str(job.get("country_or_region") or "").strip()
    province = str(job.get("province") or "").strip()
    return country in MAINLAND_COUNTRY_LABELS or province in PROVINCES


def build_dual_axis_readiness(
    jobs: Iterable[dict[str, Any]],
    *,
    reliability_scorecard: dict[str, Any],
    domestic_expansion_targets: dict[str, Any],
    quality_gate: dict[str, Any],
) -> dict[str, Any]:
    """Build non-blended reliability and domestic-coverage measurements.

    ``reliability_scorecard`` remains the existing evidence/lifecycle metric.
    The coverage axis uses only current mainland jobs against the explicit
    500-row recruitment-season plan.  A full-college claim is allowed only if every hard gate
    passes; neither axis can compensate for the other.
    """

    public_jobs = [job for job in jobs if is_public_job(job)]
    domestic_jobs = [job for job in public_jobs if is_domestic_job(job)]
    target = max(int(domestic_expansion_targets.get("target_total") or 0), 1)
    source_counts = Counter(_source_name(job) for job in domestic_jobs)
    employer_counts = Counter(_employer_name(job) for job in domestic_jobs)
    industry_counts = Counter(employment_segment(job) for job in domestic_jobs)
    provinces = {
        str(job.get("province") or "").strip()
        for job in domestic_jobs
        if str(job.get("province") or "").strip() in PROVINCES
    }
    domestic_total = len(domestic_jobs)
    three_oil_count = int(industry_counts.get("three_oil", 0))
    three_oil_share = three_oil_count / domestic_total if domestic_total else 0.0
    three_oil_target_min = int(target * 0.70)
    three_oil_target_max = int(target * 0.75)
    other_count = max(0, domestic_total - three_oil_count)
    top_source_share = _top_share(source_counts, domestic_total)
    top_five_source_share = _top_n_share(source_counts, domestic_total, 5)
    top_employer_share = _top_share(employer_counts, domestic_total)

    components = reliability_scorecard.get("components", {})
    reliability_score = float(reliability_scorecard.get("score") or 0)
    detail_rate = float(
        components.get("official_detail_evidence", {}).get("rate") or 0
    )
    explicit_rate = float(
        components.get("explicit_major_degree_match", {}).get("rate") or 0
    )
    refresh_rate = float(
        components.get("consecutive_refresh", {}).get("rate") or 0
    )
    lifecycle_rate = float(
        components.get("lifecycle_quality", {}).get("rate") or 0
    )

    checks = {
        "domestic_effective_job_target": domestic_total >= target,
        "all_segment_source_gates": bool(
            domestic_expansion_targets.get("all_segment_source_gates")
        ),
        "all_segment_unit_gates": bool(
            domestic_expansion_targets.get("all_segment_unit_gates")
        ),
        "all_segment_province_gates": bool(
            domestic_expansion_targets.get("all_segment_province_gates")
        ),
        "source_share_at_most_25_percent": top_source_share <= DEFAULT_MAX_SOURCE_SHARE,
        "top_five_source_share_at_most_75_percent": (
            top_five_source_share <= DEFAULT_MAX_TOP_FIVE_SOURCE_SHARE
        ),
        "employer_share_at_most_15_percent": (
            top_employer_share <= DEFAULT_MAX_EMPLOYER_SHARE
        ),
        "three_oil_share_at_most_75_percent": three_oil_share <= 0.75,
        "reliability_score_at_least_90": (
            reliability_score >= DEFAULT_RELIABILITY_READY_SCORE
        ),
        "official_detail_evidence_at_least_95_percent": (
            detail_rate >= DEFAULT_DETAIL_EVIDENCE_RATE
        ),
        "explicit_major_degree_match_at_least_95_percent": (
            explicit_rate >= DEFAULT_EXPLICIT_MATCH_RATE
        ),
        "two_consecutive_refresh_at_least_90_percent": (
            refresh_rate >= DEFAULT_REFRESH_RATE
        ),
        "lifecycle_quality_pass": lifecycle_rate == 1.0,
        "existing_quality_gate_pass": quality_gate.get("status") == "pass",
    }
    failed_checks = [key for key, passed in checks.items() if not passed]
    return {
        "version": 1,
        "coverage_axis": {
            "label": "国内有效岗位覆盖",
            "domestic_effective_jobs": domestic_total,
            "all_public_effective_jobs": len(public_jobs),
            "overseas_or_unclassified_public_jobs": len(public_jobs) - domestic_total,
            "target_jobs": target,
            "completion_rate": round(min(domestic_total / target, 1.0), 4),
            "completion_score": round(min(domestic_total / target, 1.0) * 100, 2),
            "gap_jobs": max(0, target - domestic_total),
            "independent_sources": len(source_counts),
            "employers_or_units": len(employer_counts),
            "provinces_with_jobs": len(provinces),
            "top_source_share": round(top_source_share, 4),
            "top_five_source_share": round(top_five_source_share, 4),
            "top_employer_share": round(top_employer_share, 4),
            "three_oil_job_count": three_oil_count,
            "three_oil_share": round(three_oil_share, 4),
            "other_job_count": other_count,
            "other_share": round(
                (other_count / domestic_total)
                if domestic_total
                else 0.0,
                4,
            ),
            "three_oil_target_band": {"min_share": 0.70, "max_share": 0.75},
            "three_oil_target_counts": {
                "min": three_oil_target_min,
                "max": three_oil_target_max,
                "additional_to_min": max(0, three_oil_target_min - three_oil_count),
            },
            "other_target_counts": {
                "min": target - three_oil_target_max,
                "max": target - three_oil_target_min,
                "additional_to_min": max(0, target - three_oil_target_max - other_count),
            },
            "additional_jobs_to_complete_target": {
                "at_70_percent_three_oil": {
                    "three_oil": max(0, three_oil_target_min - three_oil_count),
                    "other": max(0, target - three_oil_target_min - other_count),
                    "total": max(0, three_oil_target_min - three_oil_count)
                    + max(0, target - three_oil_target_min - other_count),
                },
                "at_75_percent_three_oil": {
                    "three_oil": max(0, three_oil_target_max - three_oil_count),
                    "other": max(0, target - three_oil_target_max - other_count),
                    "total": max(0, three_oil_target_max - three_oil_count)
                    + max(0, target - three_oil_target_max - other_count),
                },
            },
            "three_oil_share_within_target_band": bool(
                domestic_total and 0.70 <= three_oil_share <= 0.75
            ),
            "scope_note": (
                "只统计当前学生端公开且地点归一化为中国大陆省份或中国大陆的岗位；"
                "海外、历史、待复核和访问失败记录不计入招聘季 500 条目标。"
            ),
        },
        "reliability_axis": {
            "label": "官方证据与持续运行可靠性",
            "score": round(reliability_score, 2),
            "max_score": int(reliability_scorecard.get("max_score") or 100),
            "official_detail_evidence_rate": round(detail_rate, 4),
            "explicit_major_degree_match_rate": round(explicit_rate, 4),
            "two_consecutive_refresh_rate": round(refresh_rate, 4),
            "lifecycle_quality_rate": round(lifecycle_rate, 4),
            "scope_note": "沿用既有证据、专业匹配、刷新、字段和生命周期评分，不代表岗位覆盖规模。",
        },
        "college_data_readiness": {
            "status": "ready" if not failed_checks else "not_ready",
            "checks": checks,
            "failed_checks": failed_checks,
            "note": (
                "全院数据就绪是硬门禁，不以两个轴的加权平均代替。"
                "公网域名、HTTPS 和服务器发布状态属于独立部署门禁。"
            ),
        },
    }


def _source_name(job: dict[str, Any]) -> str:
    return str(job.get("source_id") or "unknown").strip() or "unknown"


def _employer_name(job: dict[str, Any]) -> str:
    return (
        str(job.get("employer") or job.get("canonical_employer_name") or "unknown")
        .strip()
        or "unknown"
    )


def _top_share(counts: Counter[str], total: int) -> float:
    return (max(counts.values()) / total) if counts and total else 0.0


def _top_n_share(counts: Counter[str], total: int, n: int) -> float:
    return (
        sum(count for _, count in counts.most_common(max(1, n))) / total
        if counts and total
        else 0.0
    )


__all__ = ["build_dual_axis_readiness", "is_domestic_job", "is_public_job"]
