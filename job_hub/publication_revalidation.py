"""Re-apply the student publication gate to persisted job rows.

The publication policy is code, so changing it must also repair rows written by
an older release.  This module is deliberately downgrade-only: an operator can
withdraw a row that no longer passes, but a stale/pending row is never promoted
without a fresh official crawl.
"""

from __future__ import annotations

from typing import Any

from job_hub.profiles import PUBLIC_STUDENT_PUBLICATION_STATUSES, evaluate_student_publication


def revalidate_persisted_publication_gates(
    database: Any,
    *,
    apply: bool = False,
    limit: int = 50_000,
) -> dict[str, Any]:
    """Audit persisted public rows and optionally downgrade invalid ones."""

    jobs, _ = database.list_jobs(
        page_size=None,
        only_open=False,
        student_visible=False,
    )
    jobs = jobs[: max(0, min(int(limit), 50_000))]
    checked = changed = unchanged = 0
    status_counts: dict[str, int] = {}
    changes: list[dict[str, Any]] = []
    for job in jobs:
        current_status = str(job.get("publication_status") or "pending_evidence")
        if current_status not in PUBLIC_STUDENT_PUBLICATION_STATUSES:
            continue
        checked += 1
        current_basis = job.get("publication_basis")
        if not isinstance(current_basis, dict):
            current_basis = {}
        extended_opportunity = bool(current_basis.get("extended_opportunity"))
        desired_basis = dict(current_basis)
        decision = evaluate_student_publication(
            job,
            include_extended_opportunities=extended_opportunity,
        )
        status_counts[decision.status] = status_counts.get(decision.status, 0) + 1
        desired_basis.update(decision.as_dict())
        desired_basis["revalidated_by"] = "publication_gate_v1"
        desired_basis["revalidation_applied"] = bool(apply)
        status_changed = current_status != decision.status
        mode_changed = str(current_basis.get("major_match_mode") or "") != decision.major_match_mode
        if not status_changed and not mode_changed:
            unchanged += 1
            continue
        item = {
            "job_id": int(job["id"]),
            "title": str(job.get("title") or ""),
            "employer": str(job.get("employer") or ""),
            "before_status": current_status,
            "after_status": decision.status,
            "before_major_match_mode": str(current_basis.get("major_match_mode") or ""),
            "after_major_match_mode": decision.major_match_mode,
            "reason": decision.reason,
        }
        changes.append(item)
        if not apply:
            continue
        database.update_derived_job_fields(
            int(job["id"]),
            category=str(job.get("category") or ""),
            degree_levels=list(job.get("degree_levels") or []),
            major_tags=list(job.get("major_tags") or []),
            relevance_score=int(job.get("relevance_score") or 0),
            relevance_band=str(job.get("relevance_band") or "拓展机会"),
            status=str(job.get("status") or "open"),
            publication_status=decision.status,
            publication_basis=desired_basis,
        )
        changed += 1
    return {
        "version": 1,
        "apply": bool(apply),
        "checked_public_rows": checked,
        "changed_rows": changed,
        "unchanged_rows": unchanged,
        "decision_status_counts": dict(sorted(status_counts.items())),
        "changes": changes,
        "policy": (
            "只复核已经持久化为学生端公开的岗位；apply=false 仅报告，"
            "apply=true 只会按最新门禁降级或修正门禁证据，不会提升待核验/历史岗位。"
        ),
    }


__all__ = ["revalidate_persisted_publication_gates"]
