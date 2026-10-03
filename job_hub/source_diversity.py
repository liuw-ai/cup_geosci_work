"""Source-diversity gates for the 1,000-row expansion programme.

The effective-job target is not met by repeatedly importing another snapshot
from the same portal.  This module turns that rule into an auditable batch
plan.  It only reads already published rows and source metadata; it never
promotes a candidate or changes a job's lifecycle state.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any, Iterable


PUBLIC_STATUSES = frozenset({"student_eligible", "unrestricted_eligible"})
DEFAULT_MAX_SOURCE_SHARE = 0.25
DEFAULT_MAX_EMPLOYER_SHARE = 0.15
DEFAULT_MAX_BATCH_SIZE = 50


def build_source_diversity_plan(
    jobs: Iterable[dict[str, Any]],
    *,
    sources: Iterable[dict[str, Any]] = (),
    target_plan: dict[str, Any] | None = None,
    target_total: int = 1_000,
    max_source_share: float = DEFAULT_MAX_SOURCE_SHARE,
    max_employer_share: float = DEFAULT_MAX_EMPLOYER_SHARE,
    max_batch_size: int = DEFAULT_MAX_BATCH_SIZE,
) -> dict[str, Any]:
    """Build the next source-expansion batch without touching database rows.

    ``jobs`` must be the same public query used by coverage.  A source can be
    recommended only when it is registered, and a source marked as enabled is
    still not a guarantee that its network request will work.  The plan
    therefore reports an explicit capture gate for every planned source.
    """

    if target_total <= 0:
        raise ValueError("target_total must be positive")
    if not 0 < max_source_share <= 1:
        raise ValueError("max_source_share must be in (0, 1]")
    if not 0 < max_employer_share <= 1:
        raise ValueError("max_employer_share must be in (0, 1]")
    if max_batch_size <= 0:
        raise ValueError("max_batch_size must be positive")

    public_jobs = [
        job
        for job in jobs
        if str(job.get("status") or "open") == "open"
        and str(job.get("publication_status") or "") in PUBLIC_STATUSES
    ]
    total = len(public_jobs)
    source_counts = Counter(_value(job, "source_id", "unknown") for job in public_jobs)
    employer_counts = Counter(
        _value(job, "canonical_employer_name", "")
        or _value(job, "employer", "unknown")
        for job in public_jobs
    )
    province_counts = Counter(
        _value(job, "province", "未注明") for job in public_jobs
    )
    source_records = {
        str(item.get("id") or "").strip(): item
        for item in sources
        if str(item.get("id") or "").strip()
    }

    planned_segments = list((target_plan or {}).get("segments") or [])
    planned: dict[str, dict[str, Any]] = {}
    for segment in planned_segments:
        segment_id = str(segment.get("id") or "").strip()
        if not segment_id:
            continue
        for raw_source_id in segment.get("source_ids") or []:
            source_id = str(raw_source_id or "").strip()
            if not source_id:
                continue
            item = planned.setdefault(
                source_id,
                {
                    "source_id": source_id,
                    "segments": [],
                    "target_jobs": 0,
                },
            )
            item["segments"].append(segment_id)
            item["target_jobs"] += int(segment.get("target_jobs") or 0)

    top_source = source_counts.most_common(1)[0] if source_counts else (None, 0)
    top_employer = employer_counts.most_common(1)[0] if employer_counts else (None, 0)
    top_source_share = _share(top_source[1], total)
    top_employer_share = _share(top_employer[1], total)
    target_gap = max(0, int(target_total) - total)

    source_rows: list[dict[str, Any]] = []
    for source_id, item in sorted(planned.items()):
        current = int(source_counts.get(source_id, 0))
        record = source_records.get(source_id, {})
        enabled = bool(record.get("enabled"))
        source_share = _share(current, total)
        if current and source_share > max_source_share:
            gate = "hold_dominant_source"
            reason = (
                f"当前占比 {source_share:.2%} 高于 {max_source_share:.0%}，"
                "先接入独立来源和单位，再增加该来源。"
            )
        elif not record:
            gate = "register_official_source"
            reason = "目标计划中的 source_id 尚未在来源注册表中登记，不能直接抓取。"
        elif not enabled:
            gate = "reactivate_or_build_adapter"
            reason = "来源已登记但当前停用；先完成专用适配器、访问策略和完整捕获门禁。"
        else:
            gate = "capture_and_verify"
            reason = "来源已登记且启用；需完成岗位级详情、字段和连续刷新验收。"
        source_rows.append(
            {
                **item,
                "current_jobs": current,
                "current_share": round(source_share, 4),
                "enabled": enabled,
                "gate": gate,
                "reason": reason,
                "recommended_batch_size": min(max_batch_size, target_gap),
            }
        )

    # Sources with no current rows are deliberately ordered before sources
    # already carrying a large share.  This makes each accepted batch improve
    # breadth instead of merely increasing the headline count.
    source_rows.sort(
        key=lambda row: (
            0 if row["current_jobs"] == 0 and row["gate"] == "capture_and_verify" else 1,
            0 if row["gate"] == "capture_and_verify" else 1,
            row["current_jobs"],
            row["source_id"],
        )
    )

    independent_sources_needed = max(0, 5 - len(source_counts))
    if total and top_source_share > max_source_share:
        concentration_status = "high"
    elif total and top_source_share > max_source_share * 0.8:
        concentration_status = "watch"
    else:
        concentration_status = "controlled"

    return {
        "target_total": int(target_total),
        "current_effective_jobs": total,
        "gap_jobs": target_gap,
        "target_rate": round(min(total / target_total, 1.0), 4),
        "unique_sources": len(source_counts),
        "unique_employers": len(employer_counts),
        "unique_provinces": len(
            {key for key in province_counts if key and key != "未注明"}
        ),
        "top_source": {
            "id": top_source[0],
            "count": top_source[1],
            "share": round(top_source_share, 4),
        },
        "top_employer": {
            "name": top_employer[0],
            "count": top_employer[1],
            "share": round(top_employer_share, 4),
        },
        "concentration_status": concentration_status,
        "max_source_share": max_source_share,
        "max_employer_share": max_employer_share,
        "independent_sources_needed_for_next_gate": independent_sources_needed,
        "planned_source_count": len(source_rows),
        "source_batches": source_rows,
        "next_batch_rule": (
            f"每批最多 {max_batch_size} 条；优先 gate=capture_and_verify 且当前为 0 条的独立来源。"
            "任何批次都必须逐岗位通过官方详情/公告、专业、学历、地点和期限门禁。"
        ),
        "scope_note": (
            "这是扩容排程和集中度门禁，不是岗位数据；不会把待复核、历史或访问失败记录计入有效岗位。"
        ),
    }


def _value(job: dict[str, Any], key: str, default: str) -> str:
    value = str(job.get(key) or "").strip()
    return value or default


def _share(count: int, total: int) -> float:
    return count / total if total else 0.0


__all__ = ["build_source_diversity_plan"]
