"""Auditable progress against the 1,000-current-job expansion target."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from job_hub.config import PROJECT_ROOT
from job_hub.readiness_axes import is_domestic_job


TARGET_PLAN_PATH = PROJECT_ROOT / "data" / "effective_job_target_plan.json"


def load_effective_job_target_plan(
    path: Path | str = TARGET_PLAN_PATH,
) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("effective job target plan must be an object")
    target_total = int(payload.get("target_total") or 0)
    segments = payload.get("segments")
    if target_total <= 0 or not isinstance(segments, list) or not segments:
        raise ValueError("target plan requires a positive target_total and segments")
    seen: set[str] = set()
    total = 0
    for segment in segments:
        if not isinstance(segment, dict):
            raise ValueError("each target segment must be an object")
        segment_id = str(segment.get("id") or "").strip()
        if not segment_id or segment_id in seen:
            raise ValueError("target segment ids must be unique and non-empty")
        target = int(segment.get("target_jobs") or 0)
        if target <= 0:
            raise ValueError(f"{segment_id}: target_jobs must be positive")
        for key in ("source_ids", "categories"):
            if not isinstance(segment.get(key), list) or not segment[key]:
                raise ValueError(f"{segment_id}: {key} must be a non-empty list")
        seen.add(segment_id)
        total += target
    if total != target_total:
        raise ValueError(f"segment targets total {total}, expected {target_total}")
    return payload


def build_expansion_target_report(
    jobs: list[dict[str, Any]],
    *,
    plan: dict[str, Any] | None = None,
    registered_source_ids: set[str] | None = None,
    include_non_domestic: bool = False,
) -> dict[str, Any]:
    """Measure current jobs against the domestic 1,000-job target.

    The target plan is explicitly for mainland opportunities.  Keeping that
    filter here, rather than trusting every caller to pre-filter ``jobs``,
    prevents an overseas role from silently reducing the reported domestic
    gap.  The only exception is the separately labelled all-public diagnostic
    report, which opts in through ``include_non_domestic``.
    """

    target_plan = plan or load_effective_job_target_plan()
    registered_source_ids = {
        str(item).strip()
        for item in (registered_source_ids or set())
        if str(item).strip()
    }
    planned_source_ids = {
        str(source_id).strip()
        for segment in target_plan["segments"]
        for source_id in segment["source_ids"]
        if str(source_id).strip()
    }
    public_jobs = [
        job
        for job in jobs
        if str(job.get("status") or "open") == "open"
        and str(job.get("publication_status") or "")
        in {"student_eligible", "unrestricted_eligible"}
    ]
    current_jobs = (
        public_jobs
        if include_non_domestic
        else [job for job in public_jobs if is_domestic_job(job)]
    )
    rows: list[dict[str, Any]] = []
    remaining_jobs = list(current_jobs)
    for segment in target_plan["segments"]:
        source_ids = {str(item) for item in segment["source_ids"]}
        categories = {str(item) for item in segment["categories"]}
        source_matched = [
            job for job in remaining_jobs if str(job.get("source_id") or "") in source_ids
        ]
        # A category is only a discovery hint. It cannot make a job count
        # toward a segment whose registered official source has not produced
        # that row yet. The previous ``source_matched or category`` fallback
        # made an absent CNPC source look like it had jobs merely because
        # other oil-and-gas rows shared the same broad category.
        matched = source_matched
        category_candidates = [
            job
            for job in remaining_jobs
            if str(job.get("category") or "") in categories
            and id(job) not in {id(item) for item in source_matched}
        ]
        matched_ids = {id(job) for job in matched}
        remaining_jobs = [job for job in remaining_jobs if id(job) not in matched_ids]
        source_count = len({str(job.get("source_id") or "") for job in matched})
        unit_count = len(
            {
                str(
                    job.get("canonical_employer_name")
                    or job.get("employer")
                    or ""
                ).strip()
                for job in matched
                if str(job.get("canonical_employer_name") or job.get("employer") or "").strip()
            }
        )
        province_count = len(
            {
                str(job.get("province") or "").strip()
                for job in matched
                if str(job.get("province") or "").strip()
                and str(job.get("province") or "").strip() != "未注明"
            }
        )
        target = int(segment["target_jobs"])
        rows.append(
            {
                "id": segment["id"],
                "label": segment.get("label", segment["id"]),
                "target_jobs": target,
                "current_jobs": len(matched),
                "gap_jobs": max(0, target - len(matched)),
                "job_rate": round(min(len(matched) / target, 1.0), 4),
                "source_count": source_count,
                "min_independent_sources": int(segment.get("min_independent_sources") or 0),
                "source_gate": source_count >= int(segment.get("min_independent_sources") or 0),
                "assignment_mode": "registered_source_ids_only",
                "category_candidates_not_counted": len(category_candidates),
                "unit_count": unit_count,
                "min_units": int(segment.get("min_units") or 0),
                "unit_gate": unit_count >= int(segment.get("min_units") or 0),
                "province_count": province_count,
                "min_provinces": int(segment.get("min_provinces") or 0),
                "province_gate": province_count >= int(segment.get("min_provinces") or 0),
            }
        )
    top_sources = Counter(str(job.get("source_id") or "unknown") for job in current_jobs)
    employers = Counter(
        str(job.get("canonical_employer_name") or job.get("employer") or "unknown").strip()
        or "unknown"
        for job in current_jobs
    )
    provinces = {
        str(job.get("province") or "").strip()
        for job in current_jobs
        if str(job.get("province") or "").strip()
        and str(job.get("province") or "").strip() != "未注明"
    }
    total_target = int(target_plan["target_total"])
    total_current = len(current_jobs)
    return {
        "plan_version": target_plan.get("version"),
        "as_of": target_plan.get("as_of"),
        "target_total": total_target,
        "current_effective_jobs": total_current,
        "gap_jobs": max(0, total_target - total_current),
        "target_rate": round(min(total_current / total_target, 1.0), 4),
        "scope": "all_public" if include_non_domestic else "domestic_mainland",
        "excluded_non_domestic_public_jobs": (
            0 if include_non_domestic else len(public_jobs) - total_current
        ),
        "all_segment_source_gates": all(row["source_gate"] for row in rows),
        "all_segment_unit_gates": all(row["unit_gate"] for row in rows),
        "all_segment_province_gates": all(row["province_gate"] for row in rows),
        "source_top_share": round(
            (max(top_sources.values()) / total_current) if total_current else 0.0,
            4,
        ),
        "unique_sources": len(top_sources),
        "unique_employers": len(employers),
        "unique_provinces": len(provinces),
        "planned_source_ids": sorted(planned_source_ids),
        "unregistered_planned_source_ids": sorted(
            planned_source_ids - registered_source_ids
        )
        if registered_source_ids
        else [],
        "registered_source_ids_checked": bool(registered_source_ids),
        "employer_top_share": round(
            (max(employers.values()) / total_current) if total_current else 0.0,
            4,
        ),
        "employer_top_5_share": round(
            (
                sum(value for _, value in employers.most_common(5)) / total_current
                if total_current
                else 0.0
            ),
            4,
        ),
        "concentration_note": (
            "来源集中度同时按 source_id 和单位统计；source_top_share 不能替代单位级审计。"
        ),
        "segments": rows,
        "scope_note": (
            "这是全体当前公开岗位的补充诊断，不替代国内 1000 条目标。"
            if include_non_domestic
            else "这是中国大陆当前有效岗位扩容台账；海外、地点未归属中国大陆、待复核、过期和历史岗位不计入 current_effective_jobs。"
        ),
    }


__all__ = [
    "TARGET_PLAN_PATH",
    "build_expansion_target_report",
    "load_effective_job_target_plan",
]
