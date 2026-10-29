"""Publish already verified discovery leads during the normal worker cycle.

Third-party sources remain discovery-only.  This module only handles the
final conversion for leads that an operator has already moved to
``official_content_verified``.  It deliberately reuses the same normalization
and student publication gate as the administrator action, so a worker cycle
cannot turn a candidate or an unverified URL into a public job.
"""

from __future__ import annotations

from typing import Any

from job_hub.contracts import is_http_url
from job_hub.pipeline import JobPipeline, RawPosting
from job_hub.profiles import PUBLIC_STUDENT_PUBLICATION_STATUSES


def normalize_verified_candidate_lead(
    database: Any,
    pipeline: JobPipeline,
    lead: dict[str, Any],
) -> dict[str, Any]:
    """Build a normalized public job from one already verified lead.

    Raises ``ValueError`` with an operator-readable reason when any final
    publication requirement is missing.  The function performs no database
    writes, which makes it safe for both the admin action and the worker.
    """

    if str(lead.get("verification_status") or "") != "official_content_verified":
        raise ValueError("Only official-content-verified leads can be published.")
    official_url = str(lead.get("official_url") or "").strip()
    if not is_http_url(official_url):
        raise ValueError("Lead has no valid official_url.")
    if str(lead.get("official_domain_status") or "") not in {
        "registered_source_match",
        "manual_review_approved",
    }:
        raise ValueError(
            "Lead needs a registered-source match or explicit manual-domain approval."
        )

    metadata = lead.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    employer = str(metadata.get("employer") or lead.get("employer_hint") or "").strip()
    if not employer:
        raise ValueError("Verified lead needs an employer before publication.")

    source_id = str(
        metadata.get("source_id")
        or lead.get("official_source_id")
        or "official-manual-import"
    ).strip()
    source = database.get_source(source_id)
    if source is None:
        raise ValueError("Configured source_id is not registered.")
    if not source.get("enabled"):
        raise ValueError("Configured source_id is disabled; publication is paused.")

    field_evidence = metadata.get("field_evidence")
    if not isinstance(field_evidence, dict):
        field_evidence = {}
    else:
        field_evidence = dict(field_evidence)
    if field_evidence:
        field_evidence.setdefault("evidence_scope", "admin_verified_official_record")
        field_evidence.setdefault(
            "岗位", str(metadata.get("title") or lead.get("title") or "").strip()
        )

    raw = RawPosting(
        title=str(metadata.get("title") or lead.get("title") or "").strip(),
        employer=employer,
        source_url=official_url,
        application_url=(
            str(
                lead.get("official_application_url")
                or metadata.get("application_url")
                or ""
            ).strip()
            or None
        ),
        text=str(metadata.get("description") or lead.get("title") or "").strip(),
        summary=str(metadata.get("summary") or "").strip(),
        published_date=str(
            metadata.get("published_date") or lead.get("published_date") or ""
        ).strip()
        or None,
        deadline_date=str(metadata.get("deadline_date") or "").strip() or None,
        location=str(
            metadata.get("location") or lead.get("location_hint") or ""
        ).strip()
        or None,
        external_id=f"verified-lead-{int(lead['id'])}",
        official_evidence_url=official_url,
        match_text=str(metadata.get("match_text") or "").strip() or None,
        qualification_text=(
            str(metadata.get("qualification_text") or "").strip() or None
        ),
        field_evidence=field_evidence or None,
    )
    normalized = pipeline.normalize_posting(raw, source)
    if normalized.get("publication_status") not in PUBLIC_STUDENT_PUBLICATION_STATUSES:
        basis = normalized.get("publication_basis") or {}
        raise ValueError(
            "student publication gate rejected the automatically verified row: "
            + str(
                basis.get("reason")
                or normalized.get("publication_status")
                or "unknown"
            )
        )
    return normalized


def publish_verified_candidate_lead(
    database: Any,
    pipeline: JobPipeline,
    lead_id: int,
) -> tuple[int, str, dict[str, Any]]:
    """Convert one verified lead and link it to its public job record."""

    lead = database.get_candidate_lead(int(lead_id))
    if lead is None:
        raise ValueError("Candidate lead was not found.")
    normalized = normalize_verified_candidate_lead(database, pipeline, lead)
    job_id, outcome = database.save_job(normalized)
    published = database.mark_candidate_lead_published(int(lead_id), int(job_id))
    if published is None:  # pragma: no cover - the lead was read immediately above
        raise ValueError("Candidate lead was not found.")
    return int(job_id), str(outcome), published


def auto_publish_verified_candidate_leads(
    database: Any,
    pipeline: JobPipeline,
    *,
    limit: int = 5000,
) -> dict[str, Any]:
    """Publish only rows already marked ``official_content_verified``.

    A blocked row is retained in the private queue with its reason.  The
    worker continues processing the remaining rows, so one incomplete review
    cannot prevent other verified official rows from reaching students.
    """

    result: dict[str, Any] = {
        "candidates_checked": 0,
        "published": 0,
        "unchanged": 0,
        "blocked": 0,
        "items": [],
        "publication_gate": "official_content_verified_and_student_eligible",
    }
    leads = database.list_candidate_leads(
        "official_content_verified", limit=max(1, min(int(limit), 5000))
    )
    for lead in leads:
        result["candidates_checked"] += 1
        try:
            job_id, outcome, published = publish_verified_candidate_lead(
                database, pipeline, int(lead["id"])
            )
        except (TypeError, ValueError, KeyError) as error:
            result["blocked"] += 1
            result["items"].append(
                {
                    "lead_id": int(lead.get("id") or 0),
                    "status": "blocked",
                    "reason": str(error),
                }
            )
            continue
        result["unchanged" if outcome != "created" else "published"] += 1
        result["items"].append(
            {
                "lead_id": int(lead["id"]),
                "job_id": job_id,
                "status": "published",
                "outcome": outcome,
                "published_lead_id": int(published["id"]),
            }
        )
    return result


__all__ = [
    "auto_publish_verified_candidate_leads",
    "normalize_verified_candidate_lead",
    "publish_verified_candidate_lead",
]
