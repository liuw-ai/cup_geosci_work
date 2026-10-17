"""Deterministic audit of open pending-evidence geoscience records.

The report is deliberately diagnostic. It never changes publication status and
never treats a title keyword or a source count as eligibility evidence.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any, Iterable

from job_hub.matching import qualification_evidence_text
from job_hub.profiles import (
    evaluate_student_publication,
    student_blocking_work_experience_requirement,
)


GEO_SIGNAL_TERMS = (
    "地质",
    "地学",
    "资源勘查",
    "地球科学",
    "地球物理",
    "水工环",
    "物探",
    "矿产",
    "岩土",
    "勘查",
)

# These are the exact or reviewed taxonomy terms. They are only used to label
# a candidate for follow-up; publication still depends on the normal gate.
EXACT_MAJOR_TERMS = (
    "资源勘查工程",
    "地质资源与地质工程",
    "地质工程",
    "地质学",
    "地球科学",
    "矿产普查与勘探",
    "矿产勘查",
    "勘查技术与工程",
    "地球探测与信息技术",
)

PROFESSIONAL_FIELD_KEYS = frozenset(
    {
        "专业范围",
        "专业要求",
        "major",
        "major requirement",
        "专业",
        # Keep the diagnostic extractor aligned with
        # matching.qualification_evidence_text. Some official detail pages
        # put the major condition in a labelled requirements block instead of
        # a dedicated major field.
        "岗位要求",
        "任职要求",
        "资格条件",
        "应聘条件",
        "job requirements",
        "job requirement",
        "qualifications",
    }
)


def _json_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return []
        return _json_list(parsed)
    return []


def _professional_text(job: dict[str, Any]) -> str:
    evidence = job.get("field_evidence")
    if not isinstance(evidence, dict):
        return ""
    values: list[str] = []
    for key, value in evidence.items():
        if str(key).casefold() in {item.casefold() for item in PROFESSIONAL_FIELD_KEYS}:
            values.append(str(value or ""))
        elif key == "fields" and isinstance(value, dict):
            values.extend(
                str(nested or "")
                for nested_key, nested in value.items()
                if str(nested_key).casefold()
                in {item.casefold() for item in PROFESSIONAL_FIELD_KEYS}
            )
    return qualification_evidence_text({"专业范围": " ".join(values)})


def _signal_text(job: dict[str, Any]) -> str:
    evidence = job.get("field_evidence")
    professional = _professional_text(job)
    tags = " ".join(_json_list(job.get("major_tags")))
    if not tags:
        tags = " ".join(_json_list(job.get("major_tags_json")))
    return " ".join(
        str(value or "")
        for value in (job.get("title"), tags, professional, evidence.get("岗位") if isinstance(evidence, dict) else "")
    )


def _detail_key(job: dict[str, Any]) -> str:
    official_key = str(job.get("official_detail_key") or "").strip()
    if official_key:
        return official_key
    # An attachment/notice URL can legitimately contain several rows. Bind
    # the fallback key to the displayed role and location so different jobs,
    # locations, or subsidiaries are never collapsed by the audit.
    for key in ("official_evidence_url", "source_url"):
        value = str(job.get(key) or "").strip()
        if value:
            title = str(job.get("title") or "").strip()
            location = str(job.get("location") or "").strip()
            employer = str(job.get("employer") or "").strip()
            return "|".join((value, title, location, employer))
    return f"job:{job.get('id')}"


def _primary_blocker_code(*, label: str, reason: str) -> str:
    """Map the public gate's stable outcome to a machine-readable blocker.

    The human-readable reason remains in the report.  This code is solely an
    audit label: it must never be treated as a reason to change publication
    state or to infer facts absent from the official record.
    """

    if label == "待补岗位地点":
        return "mainland_location_unverified"
    if "学历层次" in reason:
        return "degree_level_unrecognized"
    if "学历要求" in reason:
        return "degree_evidence_missing"
    if "相关专业" in reason:
        return "related_major_only"
    if "专业要求" in reason:
        return "major_evidence_missing"
    if "当前岗位对应" in reason:
        return "job_level_binding_missing"
    return "other_pending_evidence"


def _supplemental_blocker_codes(job: dict[str, Any]) -> tuple[list[str], str | None]:
    """Expose independent blockers that an earlier gate may mask.

    In particular, the publication gate must check mainland location before
    eligibility.  For audit purposes a missing location should not conceal a
    captured multi-year work-experience condition.  The returned information
    is diagnostic only and does not simulate a publishable job.
    """

    codes: list[str] = []
    experience = student_blocking_work_experience_requirement(job)
    if experience:
        codes.append("requires_work_experience")
    return codes, experience


def build_pending_evidence_audit(
    jobs: Iterable[dict[str, Any]],
    *,
    open_only: bool = True,
) -> dict[str, Any]:
    """Build a repeatable, non-mutating pending-evidence audit."""

    pending: list[dict[str, Any]] = []
    for original in jobs:
        job = dict(original)
        if str(job.get("publication_status") or "") != "pending_evidence":
            continue
        if open_only and str(job.get("status") or "") != "open":
            continue
        pending.append(job)

    candidates: list[dict[str, Any]] = []
    detail_groups: dict[str, list[dict[str, Any]]] = {}
    source_counts: Counter[str] = Counter()
    decision_counts: Counter[str] = Counter()
    exact_count = 0
    for job in pending:
        signal_text = _signal_text(job)
        geo_signal = any(term in signal_text for term in GEO_SIGNAL_TERMS)
        if not geo_signal:
            continue
        professional_text = _professional_text(job)
        exact_terms = [term for term in EXACT_MAJOR_TERMS if term in professional_text]
        decision = evaluate_student_publication(job)
        source_name = str(job.get("source_name") or job.get("source_id") or "unknown")
        source_counts[source_name] += 1
        decision_counts[decision.label] += 1
        exact_count += bool(exact_terms)
        detail_key = _detail_key(job)
        detail_groups.setdefault(detail_key, []).append(job)
        blocker_codes = [
            _primary_blocker_code(
                label=str(decision.label), reason=str(decision.reason)
            )
        ]
        supplemental_codes, blocking_experience = _supplemental_blocker_codes(job)
        blocker_codes.extend(
            code for code in supplemental_codes if code not in blocker_codes
        )
        candidates.append(
            {
                "id": job.get("id"),
                "source_id": job.get("source_id"),
                "source_name": source_name,
                "title": job.get("title"),
                "employer": job.get("employer"),
                "location": job.get("location"),
                "deadline_date": job.get("deadline_date"),
                "detail_key": detail_key,
                "exact_major_terms": exact_terms,
                "decision_label": decision.label,
                "decision_reason": decision.reason,
                "blocker_codes": blocker_codes,
                "blocking_experience_requirement": blocking_experience,
            }
        )

    duplicate_groups = {
        key: [item.get("id") for item in values]
        for key, values in detail_groups.items()
        if len(values) > 1
    }
    duplicate_group_items = [
        (key, values) for key, values in detail_groups.items() if len(values) > 1
    ]
    same_source_duplicate_group_count = sum(
        1
        for _key, values in duplicate_group_items
        if len({str(item.get("source_id") or "") for item in values}) == 1
    )
    cross_source_duplicate_group_count = sum(
        1
        for _key, values in duplicate_group_items
        if len({str(item.get("source_id") or "") for item in values}) > 1
    )
    location_split_duplicate_group_count = sum(
        1
        for key, _values in duplicate_group_items
        if "::location:" in str(key)
    )
    blocker_code_counts: Counter[str] = Counter()
    exact_detail_keys: set[str] = set()
    for candidate in candidates:
        blocker_code_counts.update(candidate["blocker_codes"])
        if candidate["exact_major_terms"]:
            exact_detail_keys.add(str(candidate["detail_key"]))
        same_detail_ids = [
            item.get("id")
            for item in detail_groups[candidate["detail_key"]]
            if item.get("id") != candidate["id"]
        ]
        candidate["same_detail_record_ids"] = same_detail_ids
        candidate["has_same_detail_record"] = bool(same_detail_ids)
    candidates.sort(key=lambda item: (str(item.get("source_name") or ""), int(item.get("id") or 0)))
    return {
        "scope": "open_pending_evidence" if open_only else "pending_evidence",
        "pending_count": len(pending),
        "geoscience_signal_count": len(candidates),
        "explicit_taxonomy_signal_count": int(exact_count),
        "source_counts": dict(sorted(source_counts.items())),
        "decision_label_counts": dict(sorted(decision_counts.items())),
        "blocker_code_counts": dict(sorted(blocker_code_counts.items())),
        "duplicate_detail_group_count": len(duplicate_groups),
        "duplicate_detail_groups": duplicate_groups,
        "duplicate_record_count": sum(len(values) for _key, values in duplicate_group_items),
        "same_source_duplicate_group_count": same_source_duplicate_group_count,
        "cross_source_duplicate_group_count": cross_source_duplicate_group_count,
        "location_split_duplicate_group_count": location_split_duplicate_group_count,
        "explicit_taxonomy_unique_detail_count": len(exact_detail_keys),
        "candidates": candidates,
        "publication_mutated": False,
    }


__all__ = ["build_pending_evidence_audit"]
