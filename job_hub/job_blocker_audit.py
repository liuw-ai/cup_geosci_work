"""Read-only, reproducible audit of the job-to-student publication funnel.

The report intentionally keeps the persisted publication state separate from a
fresh evaluation with today's business date.  A row can be retained for
history while its deadline has passed, and a rule reindex can change the
derived publication state without changing the underlying official record.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
from typing import Any, Iterable
from job_hub.phase3_policy import current_asia_shanghai_date
from job_hub.profiles import (
    PUBLIC_STUDENT_PUBLICATION_STATUSES,
    PublicationDecision,
    evaluate_student_publication,
)


CURRENT_LIFECYCLE_STATUSES = frozenset({"open", "expired"})


def _deadline_state(job: dict[str, Any], as_of: str) -> str:
    deadline = str(job.get("deadline_date") or "").strip()
    if deadline:
        try:
            return "deadline_current" if date.fromisoformat(deadline) >= date.fromisoformat(as_of) else "deadline_expired"
        except ValueError:
            return "deadline_invalid"
    expires = str(job.get("no_deadline_expires_at") or "").strip()
    if expires:
        try:
            # Stored timestamps are UTC ISO values.  The date portion is
            # sufficient because the lifecycle contract compares the local
            # business date supplied to this report.
            expiry_date = date.fromisoformat(expires[:10])
            return "undated_current" if expiry_date >= date.fromisoformat(as_of) else "undated_expired"
        except ValueError:
            return "undated_invalid"
    return "undated_no_expiry"


def _is_current_record(job: dict[str, Any], as_of: str) -> bool:
    return (
        str(job.get("status") or "") in CURRENT_LIFECYCLE_STATUSES
        and _deadline_state(job, as_of)
        in {"deadline_current", "undated_current", "undated_no_expiry"}
    )


def _blocker_code(decision: PublicationDecision) -> str:
    reason = str(decision.reason or "")
    label = str(decision.label or "")
    if decision.status in PUBLIC_STUDENT_PUBLICATION_STATUSES:
        return "public_gate_passed"
    if "专业不匹配" in label or "石油工程方向不匹配" in label:
        return "major_not_in_scope"
    if "非中国大陆" in label:
        return "non_mainland_location"
    if "社会招聘" in label or "成熟人才" in label:
        return "non_campus_recruitment"
    if "实习" in label or "见习" in label:
        return "internship_or_trainee"
    if "工作经验" in label or "需工作经验" in label:
        return "work_experience_required"
    if "采购" in label or "非地学业务" in label:
        return "non_geoscience_role"
    if "不限专业" in label:
        return "unrestricted_major_not_public_role"
    if decision.status == "pending_evidence":
        # Use the decision label first.  Generic evidence explanations often
        # mention several missing fields (for example "专业、学历字段定位"),
        # and searching the full reason used to misclassify role-direction or
        # postdoctoral review rows as degree blockers.
        if label == "岗位类型需核验":
            return "special_role_type_needs_review"
        if label == "待核岗位方向":
            return "major_or_role_evidence_missing"
        if label == "待补岗位级证据":
            if "专业" in reason or "岗位方向" in reason:
                return "major_or_role_evidence_missing"
            return "job_level_evidence_missing"
        if "地点" in label:
            return "location_evidence_missing"
        if "学历" in label:
            return "degree_evidence_missing"
        if "专业" in label:
            return "major_or_role_evidence_missing"
        return "job_level_evidence_missing"
    if decision.status == "out_of_scope":
        return "other_out_of_scope"
    return "other_blocked"


def _sample(job: dict[str, Any], decision: PublicationDecision, as_of: str) -> dict[str, Any]:
    return {
        "id": job.get("id"),
        "title": job.get("title"),
        "employer": job.get("employer"),
        "source_id": job.get("source_id"),
        "source_name": job.get("source_name"),
        "location": job.get("location"),
        "deadline_date": job.get("deadline_date"),
        "lifecycle_status": job.get("status"),
        "persisted_publication_status": job.get("publication_status"),
        "recomputed_status": decision.status,
        "decision_label": decision.label,
        "decision_reason": decision.reason,
        "blocker_code": _blocker_code(decision),
        "deadline_state": _deadline_state(job, as_of),
        "official_url": job.get("official_evidence_url") or job.get("source_url"),
    }


def build_job_blocker_audit(
    jobs: Iterable[dict[str, Any]],
    *,
    as_of: str | None = None,
    sample_limit: int = 5,
    canonical_student_visible_count: int | None = None,
) -> dict[str, Any]:
    """Build a non-mutating funnel report from normalized job dictionaries."""

    target_date = as_of or current_asia_shanghai_date()
    date.fromisoformat(target_date)
    limit = max(1, min(int(sample_limit), 20))
    all_jobs = [dict(job) for job in jobs]
    current_jobs = [job for job in all_jobs if _is_current_record(job, target_date)]

    persisted_status = Counter(str(job.get("publication_status") or "unknown") for job in all_jobs)
    persisted_lifecycle = Counter(str(job.get("status") or "unknown") for job in all_jobs)
    current_persisted_status = Counter(
        str(job.get("publication_status") or "unknown") for job in current_jobs
    )
    deadline_states = Counter(_deadline_state(job, target_date) for job in all_jobs)
    decision_counts: Counter[tuple[str, str, str]] = Counter()
    blocker_counts: Counter[str] = Counter()
    source_counts: Counter[tuple[str, str]] = Counter()
    samples: dict[str, list[dict[str, Any]]] = defaultdict(list)
    mismatches: list[dict[str, Any]] = []
    current_public_keys: set[str] = set()
    mismatch_count = 0
    current_mismatch_count = 0

    for job in all_jobs:
        decision = evaluate_student_publication(job)
        mismatch = str(job.get("publication_status") or "") != decision.status
        if mismatch:
            mismatch_count += 1
            if _is_current_record(job, target_date):
                current_mismatch_count += 1
            if len(mismatches) < limit * 20:
                mismatches.append(_sample(job, decision, target_date))
        if not _is_current_record(job, target_date):
            continue
        blocker = _blocker_code(decision)
        decision_counts[(decision.status, decision.label, decision.reason)] += 1
        blocker_counts[blocker] += 1
        source_counts[(blocker, str(job.get("source_id") or "unknown"))] += 1
        if len(samples[blocker]) < limit:
            samples[blocker].append(_sample(job, decision, target_date))
        if decision.status in PUBLIC_STUDENT_PUBLICATION_STATUSES:
            detail_key = str(job.get("official_detail_key") or "").strip()
            current_public_keys.add(detail_key or f"job:{job.get('id')}")

    decision_rows = [
        {
            "status": key[0],
            "label": key[1],
            "reason": key[2],
            "count": value,
        }
        for key, value in decision_counts.most_common()
    ]
    source_rows = [
        {"blocker_code": key[0], "source_id": key[1], "count": value}
        for key, value in source_counts.most_common()
    ]
    return {
        "report": "job_blocker_audit",
        "as_of_date": target_date,
        "read_only": True,
        "total_jobs": len(all_jobs),
        "lifecycle_counts": dict(sorted(persisted_lifecycle.items())),
        "persisted_publication_status_counts": dict(sorted(persisted_status.items())),
        "current_record_count": len(current_jobs),
        "current_persisted_publication_status_counts": dict(sorted(current_persisted_status.items())),
        "deadline_state_counts": dict(sorted(deadline_states.items())),
        # This is an eligibility projection, not the database read path.  The
        # latter can still differ until the worker/reindex persists the same
        # rule result, so expose both values when the caller has it.
        "current_recomputed_eligible_deduplicated_count": len(current_public_keys),
        "canonical_student_visible_count": canonical_student_visible_count,
        "current_blocker_counts_recomputed": dict(sorted(blocker_counts.items())),
        "current_decision_counts_recomputed": decision_rows,
        "current_blocker_by_source": source_rows,
        "persisted_vs_recomputed_mismatch_count": mismatch_count,
        "current_persisted_vs_recomputed_mismatch_count": current_mismatch_count,
        "mismatch_samples": mismatches,
        "samples_by_blocker": dict(sorted(samples.items())),
        "policy": (
            "本报告只读；第三方线索、待补证据和不在范围岗位不会因审计而公开。"
            "当前公开数按截止日期和官方 detail key 去重估算。"
        ),
    }


__all__ = ["build_job_blocker_audit"]
