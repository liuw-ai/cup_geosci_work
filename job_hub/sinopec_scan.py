"""Unit-level scan contracts for the Sinopec campus SPA.

The browser worker is intentionally separate from publication.  It can use
this module to turn a verified enterprise manifest into deterministic detail
routes, then validate the resulting capture before the normal job pipeline is
allowed to run.  A listed unit is never treated as a successful empty scan.
"""

from __future__ import annotations

from typing import Any

from job_hub.sinopec import (
    SINOPEC_COMPLETED_SCAN_STATUSES,
    SinopecCaptureError,
    _external_id_enterprise_id,
    _job_enterprise_id,
)


class SinopecScanContractError(SinopecCaptureError):
    """Raised when a unit-level scan cannot be audited safely."""


def build_sinopec_scan_plan(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the ordered 132-unit detail plan for a browser worker.

    Candidate units are marked ``required_detail=True``.  Non-candidates stay
    in the plan as well so the operator can prove that all listed units were
    visited and distinguish an unscanned unit from a unit with no match.
    """

    enterprises = payload.get("enterprises")
    if not isinstance(enterprises, list):
        raise SinopecScanContractError("enterprises must be a list")
    plan: list[dict[str, Any]] = []
    seen: set[str] = set()
    for sequence, item in enumerate(enterprises, start=1):
        if not isinstance(item, dict):
            raise SinopecScanContractError(f"enterprise {sequence} must be an object")
        enterprise_id = str(item.get("id") or "").strip()
        if not enterprise_id or enterprise_id in seen:
            raise SinopecScanContractError(
                "enterprise ids must be unique and non-empty"
            )
        seen.add(enterprise_id)
        status = str(item.get("scan_status") or "").strip()
        plan.append(
            {
                "sequence": sequence,
                "enterprise_id": enterprise_id,
                "name": str(item.get("name") or "").strip(),
                "detail_url": str(item.get("detail_url") or "").strip(),
                "candidate": bool(item.get("candidate_by_keyword", False)),
                "required_detail": bool(item.get("candidate_by_keyword", False)),
                "current_status": status,
            }
        )
    return plan


def validate_sinopec_scan_result(
    payload: dict[str, Any],
    *,
    require_candidate_completion: bool = False,
) -> dict[str, Any]:
    """Audit unit coverage and row-to-detail binding for one capture.

    ``require_candidate_completion`` is the production promotion gate.  It is
    opt-in so an in-progress browser capture can still be inspected and saved
    as a private diagnostic artifact.
    """

    plan = build_sinopec_scan_plan(payload)
    expected_total = int(payload.get("enterprise_total") or 0)
    candidate_total = int(payload.get("candidate_enterprise_total") or 0)
    if len(plan) != expected_total:
        raise SinopecScanContractError(
            f"enterprise manifest has {len(plan)} rows; expected {expected_total}"
        )
    candidate_units = [item for item in plan if item["candidate"]]
    if len(candidate_units) != candidate_total:
        raise SinopecScanContractError(
            f"candidate manifest has {len(candidate_units)} rows; expected {candidate_total}"
        )

    unit_ids = {item["enterprise_id"] for item in plan}
    rows = payload.get("jobs")
    if not isinstance(rows, list):
        raise SinopecScanContractError("jobs must be a list")
    unknown_rows: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            raise SinopecScanContractError("every job row must be an object")
        enterprise_id = _job_enterprise_id(row)
        external_enterprise_id = _external_id_enterprise_id(row.get("external_id"))
        if (
            not enterprise_id
            or enterprise_id not in unit_ids
            or (
                external_enterprise_id is not None
                and external_enterprise_id != enterprise_id
            )
        ):
            unknown_rows.append(str(row.get("external_id") or "<unknown>"))
    if unknown_rows:
        raise SinopecScanContractError(
            "job rows reference unknown Sinopec units: " + ", ".join(unknown_rows[:5])
        )

    status_counts: dict[str, int] = {}
    candidate_status_counts: dict[str, int] = {}
    incomplete_candidates: list[str] = []
    access_limited: list[str] = []
    parse_failed: list[str] = []
    for item in plan:
        status = item["current_status"]
        status_counts[status] = status_counts.get(status, 0) + 1
        if item["candidate"]:
            candidate_status_counts[status] = candidate_status_counts.get(status, 0) + 1
            metrics = next(
                (
                    enterprise.get("scan_metrics") or {}
                    for enterprise in payload["enterprises"]
                    if str(enterprise.get("id")) == item["enterprise_id"]
                ),
                {},
            )
            complete = (
                status in SINOPEC_COMPLETED_SCAN_STATUSES
                and bool(metrics.get("pagination_complete"))
                and int(metrics.get("failed_jobs") or 0) == 0
            )
            if not complete:
                incomplete_candidates.append(item["enterprise_id"])
            if status == "access_limited":
                access_limited.append(item["enterprise_id"])
            if status == "parse_failed":
                parse_failed.append(item["enterprise_id"])

    if require_candidate_completion and incomplete_candidates:
        raise SinopecScanContractError(
            "candidate units are not fully scanned: "
            + ", ".join(incomplete_candidates[:10])
        )

    return {
        "enterprise_total": expected_total,
        "candidate_enterprise_total": candidate_total,
        "enterprise_count": len(plan),
        "candidate_count": len(candidate_units),
        "job_rows": len(rows),
        "status_counts": dict(sorted(status_counts.items())),
        "candidate_status_counts": dict(sorted(candidate_status_counts.items())),
        "candidate_complete_count": candidate_total - len(incomplete_candidates),
        "candidate_incomplete_count": len(incomplete_candidates),
        "candidate_incomplete_ids": incomplete_candidates,
        "access_limited_candidate_ids": access_limited,
        "parse_failed_candidate_ids": parse_failed,
        "rows_bound_to_known_units": len(rows) - len(unknown_rows),
        "promotion_ready": not incomplete_candidates and not unknown_rows,
    }
