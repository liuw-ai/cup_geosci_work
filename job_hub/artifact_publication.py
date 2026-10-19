"""Unattended publication of independently verified official attachments.

The attachment queue is intentionally private by default.  This module is the
small, explicit bridge from the automatic government-artifact verifier to the
normal job pipeline.  A candidate is considered here only when the verifier
from the same worker cycle says that its current artifact is publication-ready.
No broad candidate status scan can publish a row by accident.
"""

from __future__ import annotations

from typing import Any

from job_hub.attachments import build_attachment_field_evidence
from job_hub.pipeline import JobPipeline
from job_hub.profiles import PUBLIC_STUDENT_PUBLICATION_STATUSES
from job_hub.sources import RawPosting


AUTO_PUBLICATION_VERSION = "government-artifact-auto-publication-v1"


def auto_publish_verified_artifact_candidates(
    database: Any,
    pipeline: JobPipeline,
    verification_report: dict[str, Any],
) -> dict[str, Any]:
    """Publish only candidates authorized by this cycle's verifier report.

    The verifier has already matched each row to the reviewed official ledger,
    checked its artifact hash and lifecycle, and rejected rolling vacancies
    without a fresh availability confirmation.  This function adds the normal
    student publication gate and records any failure without replacing the
    candidate's private evidence.
    """

    authorized: dict[tuple[str, str], set[str]] = {}
    for item in verification_report.get("items", []):
        if not bool(item.get("publication_ready")):
            continue
        candidate_ids = {
            str(value).strip()
            for value in item.get("matched_candidate_ids") or []
            if str(value).strip()
        }
        if not candidate_ids:
            continue
        key = (str(item.get("source_id") or ""), str(item.get("artifact_url") or ""))
        authorized.setdefault(key, set()).update(candidate_ids)
    result: dict[str, Any] = {
        "version": AUTO_PUBLICATION_VERSION,
        "authorized_artifacts": len(authorized),
        "candidates_checked": 0,
        "published": 0,
        "unchanged": 0,
        "blocked": 0,
        "withdrawn": 0,
        "reviewed": 0,
        "restored": 0,
        "refreshed": 0,
        "items": [],
    }
    if verification_report.get("status") != "failed":
        _reconcile_published_dynamic_candidates(database, pipeline, authorized, result)
    if not authorized:
        return result

    for candidate in database.iter_artifact_job_candidates(
        review_status="official_content_verified"
    ):
        key = (
            str(candidate.get("source_id") or ""),
            str(candidate.get("artifact_url") or ""),
        )
        if str(candidate.get("id") or "") not in authorized.get(key, set()):
            continue
        result["candidates_checked"] += 1
        try:
            job_id, outcome, published = _publish_one(database, pipeline, candidate)
        except ValueError as error:
            result["blocked"] += 1
            result["items"].append(
                {
                    "candidate_id": int(candidate["id"]),
                    "status": "blocked",
                    "reason": str(error),
                }
            )
            continue
        if outcome == "created":
            result["published"] += 1
        else:
            result["unchanged"] += 1
        result["items"].append(
            {
                "candidate_id": int(candidate["id"]),
                "job_id": int(job_id),
                "status": "published",
                "outcome": outcome,
                "candidate": published,
            }
        )
    return result


def _reconcile_published_dynamic_candidates(
    database: Any,
    pipeline: JobPipeline,
    authorized: dict[tuple[str, str], set[str]],
    result: dict[str, Any],
) -> None:
    artifacts: dict[int, dict[str, Any] | None] = {}
    for candidate in database.iter_artifact_job_candidates(review_status="published"):
        artifact_id = int(candidate["artifact_id"])
        if artifact_id not in artifacts:
            artifacts[artifact_id] = database.get_source_artifact(artifact_id)
        artifact = artifacts[artifact_id]
        metadata = artifact.get("metadata") if isinstance(artifact, dict) else None
        if not isinstance(metadata, dict) or not metadata.get("discovery_only"):
            continue
        if metadata.get("government_artifact_id"):
            continue
        key = (str(candidate["source_id"]), str(candidate["artifact_url"]))
        approved = str(candidate["id"]) in authorized.get(key, set())
        if approved:
            try:
                normalized = _normalized_candidate(pipeline, database, candidate)
                outcome = database.refresh_published_artifact_job(
                    int(candidate["id"]), normalized
                )
            except ValueError as error:
                approved = False
                if database.mark_published_artifact_review_state(
                    int(candidate["id"]),
                    availability_status="degraded",
                    reason="parser_error",
                    review_note=f"动态附件岗位本轮规范化失败，保留原岗位并待复核：{error}",
                    evidence_url=str(candidate.get("official_page_url") or "") or None,
                ):
                    result["reviewed"] += 1
                continue
            else:
                if outcome in {"restored", "refreshed"}:
                    result[outcome] += 1
                else:
                    result["blocked"] += 1
                    result["items"].append(
                        {
                            "candidate_id": int(candidate["id"]),
                            "status": "blocked",
                            "reason": "published artifact job cannot be reopened by this evidence cycle",
                        }
                    )
                continue
        if not approved and database.mark_published_artifact_review_state(
            int(candidate["id"]),
            availability_status="stale_pending_review",
            reason="temporary_absence",
            review_note="动态官方附件岗位本轮未出现；保留原岗位，不因一次扫描缺失撤回，等待下一轮官方复核。",
            evidence_url=str(candidate.get("official_page_url") or "") or None,
        ):
            result["reviewed"] += 1


def _publish_one(
    database: Any,
    pipeline: JobPipeline,
    candidate: dict[str, Any],
) -> tuple[int, str, dict[str, Any]]:
    normalized = _normalized_candidate(pipeline, database, candidate)
    attachment_evidence = {
        "artifact_id": candidate["artifact_id"],
        "evidence_type": "attachment",
        "field_name": "job_record",
        "evidence_url": str(candidate["artifact_url"]),
        "locator": f"{candidate['row_sheet_name']}!{candidate['row_number']}",
        "excerpt": str(candidate.get("row_text") or "")[:2000],
        "verification_status": "verified",
        "metadata": {
            "candidate_id": int(candidate["id"]),
            "content_sha256": candidate.get("artifact_content_sha256"),
            "parser_version": candidate.get("artifact_parser_version"),
            "verification_path": AUTO_PUBLICATION_VERSION,
        },
    }
    return database.publish_artifact_job_candidate(
        int(candidate["id"]), normalized, attachment_evidence
    )


def _normalized_candidate(
    pipeline: JobPipeline, database: Any, candidate: dict[str, Any]
) -> dict[str, Any]:
    source = database.get_source(str(candidate["source_id"]))
    if source is None or not source.get("enabled"):
        raise ValueError("candidate source is no longer enabled")
    evidence = candidate.get("field_evidence") or {}
    if not isinstance(evidence, dict):
        evidence = {}
    nested = evidence.get("fields")
    if not isinstance(nested, dict):
        nested = {}
    field_evidence = build_attachment_field_evidence(
        title=str(candidate["title"]),
        major=str(evidence.get("专业范围") or nested.get("major") or "") or None,
        degree=str(evidence.get("学历要求") or nested.get("degree") or "") or None,
        location=str(candidate.get("location") or "").strip() or None,
        artifact=candidate,
        row={
            "sheet_name": candidate.get("row_sheet_name"),
            "row_number": candidate.get("row_number"),
        },
        row_text=str(candidate.get("row_text") or ""),
        extra_evidence=evidence,
    )
    location_basis = str(evidence.get("工作地点依据") or "").strip()
    if location_basis:
        field_evidence["工作地点依据"] = location_basis
    raw = RawPosting(
        title=str(candidate["title"]),
        employer=str(candidate["employer"]),
        source_url=str(candidate["official_page_url"]),
        application_url=candidate.get("application_url"),
        text=str(candidate.get("description") or candidate["title"]),
        summary=str(candidate.get("summary") or ""),
        published_date=candidate.get("published_date"),
        deadline_date=candidate.get("deadline_date"),
        location=candidate.get("location"),
        external_id=f"artifact-candidate-{int(candidate['id'])}",
        official_evidence_url=str(candidate["official_page_url"]),
        field_evidence=field_evidence,
    )
    normalized = pipeline.normalize_posting(raw, source)
    if normalized.get("publication_status") not in PUBLIC_STUDENT_PUBLICATION_STATUSES:
        basis = normalized.get("publication_basis") or {}
        raise ValueError(
            "student publication gate rejected the automatically verified row: "
            + str(basis.get("reason") or normalized.get("publication_status") or "unknown")
        )
    return normalized
