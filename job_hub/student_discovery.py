"""Student discovery read layer.

The ordinary student read path is intentionally conservative: it only reads
rows whose persisted publication gate has already been reindexed.  This module
provides a separate, read-only projection for current official vacancies that
are useful to students but still need a field-level check.  It never promotes
third-party text, expired rows, or non-domestic vacancies into a public job.
"""

from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Any, Iterable

from job_hub.locations import MAINLAND_COUNTRY_LABELS
from job_hub.profiles import PublicationDecision, evaluate_student_publication


DISCOVERY_SCOPE = "student_discovery"
DISCOVERY_PENDING_STATUS = "official_incomplete"
DISCOVERY_PUBLIC_STATUS = "official_eligible"


def _as_of(value: str) -> date:
    return date.fromisoformat(str(value).strip())


def is_current_job(job: dict[str, Any], as_of_date: str) -> bool:
    """Apply the same dated lifecycle rule as the public read path."""

    if str(job.get("status") or "") not in {"open", "expired"}:
        return False
    as_of = _as_of(as_of_date)
    deadline = str(job.get("deadline_date") or "").strip()
    if deadline:
        try:
            return date.fromisoformat(deadline) >= as_of
        except ValueError:
            return False
    expiry = str(job.get("no_deadline_expires_at") or "").strip()
    if not expiry:
        return True
    try:
        return date.fromisoformat(expiry[:10]) >= as_of
    except ValueError:
        return False


def has_official_evidence(job: dict[str, Any]) -> bool:
    """Require a registered source and an official detail/evidence URL."""

    if not str(job.get("source_id") or "").strip():
        return False
    if str(job.get("verification_status") or "") != "published_official":
        return False
    return bool(
        str(job.get("official_evidence_url") or "").strip()
        or str(job.get("source_url") or "").strip()
    )


def is_domestic_job(job: dict[str, Any]) -> bool:
    country = str(job.get("country_or_region") or "").strip()
    # ``境内外混合`` is not a domestic guarantee, but it is still a valid
    # official discovery record when the notice does not bind one location.
    # The card/detail warning makes students verify the actual work region.
    return not country or country in MAINLAND_COUNTRY_LABELS or country in {
        "境内",
        "境内外混合",
    }


def is_explicit_international_job(job: dict[str, Any]) -> bool:
    country = str(job.get("country_or_region") or "").strip()
    return bool(country and not is_domestic_job(job))


def discovery_layer(decision: PublicationDecision) -> str:
    """Map the policy decision to the stable student-facing layer labels."""

    if decision.major_match_mode in {
        "exact_four_names",
        "public_named_or_code",
    }:
        return "exact_match"
    if decision.major_match_mode in {
        "broad_geoscience",
        "public_unrestricted_rule",
        "extended_unrestricted_rule",
    }:
        return "broad_geoscience_match"
    return "incomplete_but_basic_match"


def classify_discovery_job(
    job: dict[str, Any],
    *,
    as_of_date: str,
) -> tuple[PublicationDecision, str] | None:
    """Return the read-layer decision for one official current row.

    ``pending_evidence`` is deliberately included here as an information
    discovery record.  It is not presented as confirmed eligibility; the UI
    exposes a missing-field checklist and directs students to the official
    notice.  This is the explicit policy requested for official rows whose
    degree, location, deadline, or row-level qualification is not fully stated.
    """

    if not is_current_job(job, as_of_date):
        return None
    if not has_official_evidence(job):
        return None
    international = is_explicit_international_job(job)
    evaluation_job = dict(job)
    if international:
        # The core policy intentionally rejects international rows.  Discovery
        # is an explicitly labelled expansion, so evaluate the professional
        # evidence against a domestic placeholder and restore the region label
        # below without changing the persisted job or the strict read path.
        evaluation_job["country_or_region"] = "中国大陆"
    decision = evaluate_student_publication(
        evaluation_job,
        include_extended_opportunities=True,
    )
    if not decision.is_public and decision.status != "pending_evidence":
        return None
    if international and decision.is_public:
        decision = PublicationDecision(
            status=decision.status,
            label="海外拓展机会",
            reason=decision.reason + "；该岗位工作地点在中国大陆以外，请自行核验签证、工作许可与报名资格。",
            matched_profile_ids=decision.matched_profile_ids,
            public_scope="international",
            major_match_mode=decision.major_match_mode,
        )
    return decision, discovery_layer(decision)


def _dedupe_key(job: dict[str, Any]) -> str:
    return str(job.get("official_detail_key") or f"__job:{job.get('id')}")


def artifact_candidate_to_job(candidate: dict[str, Any]) -> dict[str, Any]:
    """Project an independently verified attachment row into the read layer.

    The projection uses a negative id and never calls ``save_job``.  It is
    therefore impossible for a private attachment candidate to become a
    normal application record merely by being viewed.
    """

    evidence = candidate.get("field_evidence")
    evidence = dict(evidence) if isinstance(evidence, dict) else {}
    code = str(evidence.get("职位代码") or evidence.get("岗位代码") or "").strip()
    page_url = str(candidate.get("official_page_url") or "").strip()
    source_id = str(candidate.get("source_id") or "").strip()
    return {
        "id": -abs(int(candidate["id"])),
        "source_id": source_id,
        "source_name": f"{source_id}官方附件",
        "source_tier": "A",
        "source_url": page_url,
        "official_evidence_url": page_url,
        "official_detail_key": f"artifact:{source_id}:{page_url}:{code or candidate.get('id')}",
        "official_table_url": str(candidate.get("artifact_url") or "").strip(),
        "application_url": candidate.get("application_url"),
        "title": str(candidate.get("title") or "官方附件岗位").strip(),
        "employer": str(candidate.get("employer") or "官方招聘单位待核").strip(),
        "category": "事业单位与人才引进",
        "location": str(candidate.get("location") or "").strip(),
        "country_or_region": "中国大陆",
        "verification_status": "published_official",
        "status": "open",
        "deadline_date": candidate.get("deadline_date"),
        "no_deadline_expires_at": None,
        "degree_levels": list(candidate.get("degree_levels") or []),
        "major_tags": list(candidate.get("major_tags") or []),
        "field_evidence": evidence,
        "summary": str(candidate.get("summary") or "").strip(),
        "description": str(candidate.get("description") or "").strip(),
        "relevance_score": int(candidate.get("relevance_score") or 0),
        "relevance_band": "强相关",
        "official_detail_precedence": 1,
        "category_label": "事业单位与人才引进",
        "employer_type": "地质调查与自然资源事业单位",
        "affiliation": "自然资源与地质调查体系",
        "employment_path": "自然资源与地勘",
        "opportunity_scope": "中国境内岗位",
        "role_direction": "地质与资源评价",
        "official_link_url": page_url,
        "official_link_label": "打开官方公告核验",
        "publication_status_label": "官方附件已核验，开放状态待复核",
        "updated_at": str(candidate.get("updated_at") or ""),
        "first_seen_at": str(candidate.get("created_at") or ""),
        "last_seen_at": str(candidate.get("updated_at") or ""),
        "discovery_virtual": True,
        "discovery_candidate_id": int(candidate["id"]),
    }


def _selection_rank(job: dict[str, Any], decision: PublicationDecision) -> tuple[int, int, str, int]:
    # Confirmed policy decisions win over incomplete rows for the same detail;
    # lower source precedence is preferred, then the newest row and id.
    return (
        0 if decision.is_public else 1,
        int(job.get("official_detail_precedence") or 100),
        str(job.get("updated_at") or ""),
        -int(job.get("id") or 0),
    )


def build_student_discovery_jobs(
    jobs: Iterable[dict[str, Any]],
    *,
    as_of_date: str,
    category: str | None = None,
    degree: str | None = None,
    province: str | None = None,
    relevance_band: str | None = None,
    query: str | None = None,
    publication_layer: str | None = None,
    artifact_candidates: Iterable[dict[str, Any]] = (),
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Build one canonical student discovery row per official detail page."""

    selected: dict[str, tuple[dict[str, Any], PublicationDecision, str]] = {}
    candidate_rows = list(jobs) + [
        artifact_candidate_to_job(item) for item in artifact_candidates
    ]
    for job in candidate_rows:
        classified = classify_discovery_job(job, as_of_date=as_of_date)
        if classified is None:
            continue
        decision, layer = classified
        if category and str(job.get("category") or "") != category:
            continue
        if degree and degree not in (job.get("degree_levels") or []):
            continue
        if province and province not in {
            str(job.get("province") or ""),
            *(job.get("provinces") or []),
        }:
            continue
        if relevance_band and str(job.get("relevance_band") or "") != relevance_band:
            continue
        if publication_layer and layer != publication_layer:
            continue
        if query:
            haystack = " ".join(
                str(job.get(key) or "")
                for key in (
                    "title",
                    "employer",
                    "canonical_employer_name",
                    "location",
                    "description",
                    "major_tags",
                    "field_evidence",
                )
            ).lower()
            if str(query).strip().lower() not in haystack:
                continue
        key = _dedupe_key(job)
        current = selected.get(key)
        if current is None or _selection_rank(job, decision) < _selection_rank(
            current[0], current[1]
        ):
            selected[key] = (job, decision, layer)

    records: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for job, decision, layer in selected.values():
        annotated = dict(job)
        incomplete_warning = decision.label in {
            "官方信息不完整",
            "待核岗位方向",
            "岗位类型需核验",
        } or str(job.get("country_or_region") or "").strip() == "境内外混合"
        annotated["discovery_status"] = (
            DISCOVERY_PENDING_STATUS
            if incomplete_warning or not decision.is_public
            else DISCOVERY_PUBLIC_STATUS
        )
        annotated["discovery_layer"] = layer
        annotated["discovery_label"] = (
            decision.label if annotated["discovery_status"] == DISCOVERY_PUBLIC_STATUS else "官方信息不完整，需核验"
        )
        annotated["discovery_reason"] = decision.reason
        annotated["discovery_missing_fields"] = discovery_missing_fields(job)
        annotated["discovery_decision"] = decision.as_dict()
        annotated["discovery_region"] = (
            "海外拓展" if decision.public_scope == "international" else "中国境内/地点待核"
        )
        if annotated.get("discovery_virtual"):
            annotated["discovery_status"] = DISCOVERY_PENDING_STATUS
            annotated["discovery_label"] = "官方附件已核验，开放状态待复核"
            annotated["discovery_reason"] = (
                "官方职位表已逐行核验并保留附件证据，但当前 jobs 表尚未完成正式发布；"
                "请打开官方公告确认是否仍在招。"
            )
        records.append(annotated)
        counts[annotated["discovery_status"]] += 1
        counts[f"layer:{layer}"] += 1

    records.sort(
        key=lambda item: (
            0 if item["discovery_status"] == DISCOVERY_PUBLIC_STATUS else 1,
            -int(item.get("relevance_score") or 0),
            item.get("deadline_date") or "9999-12-31",
            str(item.get("updated_at") or ""),
            -int(item.get("id") or 0),
        )
    )
    return records, dict(counts)


def discovery_missing_fields(job: dict[str, Any]) -> list[str]:
    """Return only fields absent from the official row, not inferred values."""

    evidence = job.get("field_evidence")
    evidence = evidence if isinstance(evidence, dict) else {}
    nested = evidence.get("fields")
    nested = nested if isinstance(nested, dict) else {}

    def has_value(*keys: str) -> bool:
        return any(str(evidence.get(k) or nested.get(k) or "").strip() for k in keys)

    missing: list[str] = []
    if not has_value("专业范围", "专业要求", "major"):
        missing.append("专业要求")
    if not has_value("学历要求", "学历", "degree", "面向对象") and not job.get(
        "degree_levels"
    ):
        missing.append("学历要求")
    if not str(job.get("location") or "").strip() and not has_value(
        "工作地点", "官方工作地点", "location"
    ):
        missing.append("工作地点")
    if not str(job.get("deadline_date") or "").strip() and not has_value(
        "截止日期", "报名截止", "报名截止时间", "截止时间", "deadline"
    ):
        missing.append("截止日期")
    return missing


__all__ = [
    "DISCOVERY_SCOPE",
    "DISCOVERY_PENDING_STATUS",
    "DISCOVERY_PUBLIC_STATUS",
    "build_student_discovery_jobs",
    "classify_discovery_job",
    "discovery_missing_fields",
    "has_official_evidence",
    "is_current_job",
]
