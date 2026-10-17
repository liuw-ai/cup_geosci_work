"""Read-only audit for official ``专业不限`` vacancy records.

An unrestricted major requirement is useful for public-sector recruitment, but
it is not a general-purpose relevance rule.  This report makes the existing
publication decision visible to operators and proves that ordinary enterprise
records remain private.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Iterable

from job_hub.employers import resolve_employer
from job_hub.matching import qualification_evidence_text
from job_hub.profiles import (
    PUBLICATION_UNRESTRICTED_ELIGIBLE,
    UNRESTRICTED_MAJOR_MARKERS,
    evaluate_student_publication,
)


def _qualification_text(job: dict[str, Any]) -> str:
    return qualification_evidence_text(job.get("field_evidence"))


def _is_unrestricted_requirement(text: str) -> bool:
    lowered = text.casefold()
    return any(str(marker).casefold() in lowered for marker in UNRESTRICTED_MAJOR_MARKERS)


def build_unrestricted_role_audit(
    jobs: Iterable[dict[str, Any]],
    *,
    open_only: bool = True,
) -> dict[str, Any]:
    """Build a deterministic, non-mutating audit of unrestricted-major rows."""

    records: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()
    category_counts: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()
    decision_counts: Counter[str] = Counter()

    for original in jobs:
        job = dict(original)
        if open_only and str(job.get("status") or "") != "open":
            continue
        qualification = _qualification_text(job)
        if not _is_unrestricted_requirement(qualification):
            continue

        decision = evaluate_student_publication(job)
        employer_identity = resolve_employer(str(job.get("employer") or ""))
        publication_status = str(job.get("publication_status") or "")
        category = str(job.get("category") or "")
        source_id = str(job.get("source_id") or "")
        status_counts[publication_status or "missing"] += 1
        category_counts[category or "missing"] += 1
        source_counts[source_id or "missing"] += 1
        decision_counts[decision.label] += 1
        records.append(
            {
                "id": job.get("id"),
                "source_id": source_id,
                "source_name": job.get("source_name"),
                "title": job.get("title"),
                "employer": job.get("employer"),
                "canonical_employer_name": (
                    employer_identity.get("canonical_employer_name")
                    if employer_identity
                    else None
                ),
                "employer_type": (
                    employer_identity.get("employer_type") if employer_identity else None
                ),
                "category": category,
                "location": job.get("location"),
                "deadline_date": job.get("deadline_date"),
                "government_type": (
                    (job.get("field_evidence") or {}).get("政府岗位类型")
                    if isinstance(job.get("field_evidence"), dict)
                    else None
                ),
                "publication_status": publication_status,
                "decision_label": decision.label,
                "decision_reason": decision.reason,
                "matched_profile_ids": list(decision.matched_profile_ids),
            }
        )

    records.sort(key=lambda item: (str(item.get("source_id") or ""), int(item.get("id") or 0)))
    return {
        "scope": "open_unrestricted_major" if open_only else "unrestricted_major",
        "open_only": open_only,
        "unrestricted_count": len(records),
        "publicly_eligible_count": sum(
            1
            for item in records
            if item["publication_status"] == PUBLICATION_UNRESTRICTED_ELIGIBLE
        ),
        "private_or_blocked_count": sum(
            1
            for item in records
            if item["publication_status"] != PUBLICATION_UNRESTRICTED_ELIGIBLE
        ),
        "publication_status_counts": dict(sorted(status_counts.items())),
        "category_counts": dict(sorted(category_counts.items())),
        "source_counts": dict(sorted(source_counts.items())),
        "decision_label_counts": dict(sorted(decision_counts.items())),
        "records": records,
        "publication_mutated": False,
        "policy": (
            "专业不限仅可在事业单位、公务员、博士后或经官方身份核验的高校/科研院所岗位中公开；"
            "普通企业、金融、教育、机械、电气和管理岗位继续保持私有。"
        ),
    }


__all__ = ["build_unrestricted_role_audit"]
