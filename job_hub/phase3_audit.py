"""Evidence-driven Phase 3 backfill and historical withdrawal audit.

This module is intentionally deterministic and offline.  It reads the job,
source-health and event tables, derives the independent Phase 3 columns, and
can optionally persist the result.  A dry run is the default; callers must
explicitly opt into ``apply`` after copying the database.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

from job_hub.db import Database, utc_now
from job_hub.phase3_policy import (
    SOURCE_UNAVAILABLE_REASONS,
    WITHDRAWAL_REASONS,
    classify_legacy_withdrawal_reason,
    deadline_is_expired,
    layer_for_legacy_status,
    no_deadline_expiry,
    normalize_trust_grade,
    source_failure_state,
)


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _has_basic_major_evidence(job: dict[str, Any]) -> bool:
    evidence = _json_object(job.get("field_evidence_json"))
    nested = evidence.get("fields") if isinstance(evidence.get("fields"), dict) else {}
    major = " ".join(
        str(value or "").strip()
        for value in (
            evidence.get("专业范围"),
            evidence.get("专业要求"),
            nested.get("major"),
            job.get("major_tags_json"),
        )
    )
    if not major:
        return False
    return not any(
        token in major.lower()
        for token in ("石油工程", "采矿工程", "化工", "机械", "电气", "计算机")
    )


def _latest_withdrawal_events(connection: sqlite3.Connection) -> dict[int, dict[str, Any]]:
    latest: dict[int, dict[str, Any]] = {}
    rows = connection.execute(
        """
        SELECT job_id, payload_json, occurred_at
        FROM job_events
        WHERE event_type = 'withdrawn'
        ORDER BY id ASC
        """
    ).fetchall()
    for row in rows:
        payload = _json_object(row["payload_json"])
        latest[int(row["job_id"])] = {
            "reason": str(
                payload.get("reason")
                or payload.get("withdrawal_reason")
                or payload.get("detail")
                or ""
            ).strip(),
            "payload": payload,
            "occurred_at": row["occurred_at"],
        }
    return latest


def _source_failure(source: dict[str, Any] | None, health: dict[str, Any] | None) -> tuple[str, str]:
    if not source:
        return "degraded", "unknown"
    if not int(source.get("enabled") or 0):
        return "source_unavailable", "source_disabled"
    status = str((health or {}).get("status") or "").strip().lower()
    if status in {"", "ok", "healthy", "success", "verified", "available", "source_active"}:
        return "available", ""
    if status in {"source_degraded", "degraded"}:
        return "degraded", "page_structure_changed"
    if status in {"source_blocked", "blocked"}:
        return "source_unavailable", "unknown"
    reason = status if status in SOURCE_UNAVAILABLE_REASONS else "unknown"
    return source_failure_state(reason), reason
    return "available", ""


def derive_phase3_update(
    job: dict[str, Any],
    *,
    source: dict[str, Any] | None,
    source_health: dict[str, Any] | None,
    withdrawal_event: dict[str, Any] | None,
    as_of: date,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Derive one row update and one withdrawal-audit record."""

    basis = _json_object(job.get("publication_basis_json"))
    basic_major = _has_basic_major_evidence(job)
    layer = layer_for_legacy_status(
        job.get("publication_status"), basis, has_basic_major_evidence=basic_major
    )
    source_failure_status, failure_reason = _source_failure(source, source_health)
    official_evidence_url = str(
        job.get("official_evidence_url") or job.get("source_url") or ""
    ).strip()
    official_application_url = str(
        job.get("official_application_url") or job.get("application_url") or ""
    ).strip() or None
    verified_at = str(job.get("last_official_verified_at") or "").strip()
    if not verified_at and official_evidence_url.startswith(("http://", "https://")):
        verified_at = str(job.get("last_seen_at") or "").strip() or None
    expiry = str(job.get("no_deadline_expires_at") or "").strip() or no_deadline_expiry(
        str(job.get("published_date") or "") or None, verified_at
    )
    trust = normalize_trust_grade(
        (source or {}).get("trust_grade") or (source or {}).get("source_tier"),
        default="D",
    )
    if source_failure_status != "available" and layer != "not_public" and verified_at:
        layer = "source_unavailable"
    if source_failure_status == "available" and layer == "source_unavailable":
        previous = str(basis.get("publication_layer_before_unavailable") or "").strip()
        if previous in {"exact_match", "broad_geoscience_match", "incomplete_but_basic_match"}:
            layer = previous

    backfill_basis = {
        **basis,
        "phase3_backfill": {
            "method": "evidence_driven_local_backfill_v1",
            "source_id": job.get("source_id"),
            "official_evidence_url_present": bool(official_evidence_url),
            "basic_major_evidence": basic_major,
            "as_of": as_of.isoformat(),
        },
    }
    update = {
        "trust_grade": trust,
        "source_availability_status": source_failure_status,
        "source_unavailable_reason": failure_reason,
        "publication_layer": layer,
        "official_notice_url": job.get("official_notice_url") or official_evidence_url or None,
        "official_application_url": official_application_url,
        "last_official_verified_at": verified_at,
        "no_deadline_expires_at": expiry,
        "publication_basis_json": json.dumps(backfill_basis, ensure_ascii=False),
    }

    stored_reason = str(job.get("withdrawal_reason") or "").strip()
    event_reason = str((withdrawal_event or {}).get("reason") or "").strip()
    raw_reason = stored_reason or event_reason
    classification = (
        classify_legacy_withdrawal_reason(raw_reason)
        if str(job.get("status") or "") == "withdrawn" or raw_reason
        else "not_withdrawn"
    )
    deadline_expired = deadline_is_expired(
        str(job.get("deadline_date") or "") or None,
        expiry,
        as_of,
    )
    if str(job.get("status") or "") == "withdrawn" and deadline_expired:
        classification = "deadline_passed"
    eligible = classification in WITHDRAWAL_REASONS
    recommended_status = "withdrawn" if eligible else "open"
    if classification == "legacy_source_failure" and str(job.get("status") or "") == "withdrawn":
        update["status"] = "open"
        update["withdrawal_reason"] = None
        if layer != "not_public" and verified_at:
            update["publication_layer"] = "source_unavailable"
            update["source_availability_status"] = (
                source_failure_status if source_failure_status != "available" else "stale_pending_review"
            )
    elif eligible:
        update["withdrawal_reason"] = classification
    audit = {
        "job_id": int(job["id"]),
        "legacy_reason": raw_reason,
        "classification": classification,
        "eligible_withdrawal": int(eligible),
        "recommended_status": recommended_status,
        "evidence": {
            "event": withdrawal_event or {},
            "deadline_date": job.get("deadline_date"),
            "no_deadline_expires_at": expiry,
            "official_evidence_url": official_evidence_url,
        },
    }
    return update, audit


def audit_and_backfill(
    database_path: str,
    *,
    as_of: date,
    apply: bool = False,
) -> dict[str, Any]:
    """Audit all rows and optionally apply the deterministic backfill."""

    path = Path(database_path)
    if apply:
        database = Database(path)
        database.initialize()
        connection_context = database.connect()
    else:
        # A dry run must not create a WAL file, run migrations, or alter the
        # official runtime database merely to inspect it.
        uri = f"{path.resolve().as_uri()}?mode=ro"
        connection_context = sqlite3.connect(uri, uri=True)
        connection_context.row_factory = sqlite3.Row
    with connection_context as connection:
        sources = {
            str(row["id"]): dict(row)
            for row in connection.execute("SELECT * FROM sources").fetchall()
        }
        health = {
            str(row["source_id"]): dict(row)
            for row in connection.execute("SELECT * FROM source_health").fetchall()
        }
        events = _latest_withdrawal_events(connection)
        jobs = [dict(row) for row in connection.execute("SELECT * FROM jobs ORDER BY id").fetchall()]

    updates: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
    counts: Counter[str] = Counter()
    withdrawn_counts: Counter[str] = Counter()
    jobs_by_id = {int(row["id"]): row for row in jobs}
    for job in jobs:
        update, audit = derive_phase3_update(
            job,
            source=sources.get(str(job.get("source_id") or "")),
            source_health=health.get(str(job.get("source_id") or "")),
            withdrawal_event=events.get(int(job["id"])),
            as_of=as_of,
        )
        updates.append((int(job["id"]), update, audit))
        counts[str(audit["classification"])] += 1
        if str(job.get("status") or "") == "withdrawn":
            withdrawn_counts[str(audit["classification"])] += 1

    if apply:
        now = utc_now()
        writable = Database(path)
        with writable.transaction() as connection:
            # Preserve the reason and successor for historical superseded
            # rows.  Older releases only recorded this relation in the event
            # payload, so materialize it into indexed columns during the same
            # local Phase 3 migration.  An unlinked snapshot remains an
            # administrator-review record; it is never made public here.
            superseded_events = connection.execute(
                """
                SELECT job_id, payload_json, occurred_at
                FROM job_events
                WHERE event_type = 'superseded'
                ORDER BY id ASC
                """
            ).fetchall()
            for event in superseded_events:
                payload = _json_object(event["payload_json"])
                successor_id = payload.get("winner_job_id")
                reason = "duplicate_same_source" if successor_id else "source_transition"
                if not successor_id and payload.get("successor_source_id"):
                    external_id = str(payload.get("external_id") or "").strip()
                    if external_id:
                        successor = connection.execute(
                            """
                            SELECT id FROM jobs
                            WHERE source_id = ? AND external_id = ? AND status = 'open'
                            ORDER BY updated_at DESC, id DESC LIMIT 1
                            """,
                            (str(payload["successor_source_id"]), external_id),
                        ).fetchone()
                        successor_id = int(successor["id"]) if successor else None
                connection.execute(
                    """
                    UPDATE jobs
                    SET superseded_successor_job_id = COALESCE(?, superseded_successor_job_id),
                        superseded_reason = CASE
                            WHEN superseded_reason = '' THEN ? ELSE superseded_reason END,
                        superseded_at = COALESCE(superseded_at, ?)
                    WHERE id = ? AND status = 'superseded'
                    """,
                    (successor_id, reason, event["occurred_at"] or now, int(event["job_id"])),
                )
            for job_id, update, audit in updates:
                assignments = [f"{key} = ?" for key in update]
                values = [update[key] for key in update]
                values.append(job_id)
                connection.execute(
                    f"UPDATE jobs SET {', '.join(assignments)}, updated_at = ? WHERE id = ?",
                    [*values[:-1], now, values[-1]],
                )
                connection.execute(
                    """
                    INSERT INTO phase3_withdrawal_audits
                        (job_id, legacy_reason, classification, eligible_withdrawal,
                         recommended_status, evidence_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(job_id) DO UPDATE SET
                        legacy_reason=excluded.legacy_reason,
                        classification=excluded.classification,
                        eligible_withdrawal=excluded.eligible_withdrawal,
                        recommended_status=excluded.recommended_status,
                        evidence_json=excluded.evidence_json,
                        updated_at=excluded.updated_at
                    """,
                    (
                        audit["job_id"],
                        audit["legacy_reason"],
                        audit["classification"],
                        audit["eligible_withdrawal"],
                        audit["recommended_status"],
                        json.dumps(audit["evidence"], ensure_ascii=False),
                        now,
                        now,
                    ),
                )

    return {
        "database": str(database_path),
        "as_of": as_of.isoformat(),
        "apply": apply,
        "job_count": len(jobs),
        "withdrawn_input_count": sum(1 for row in jobs if row.get("status") == "withdrawn"),
        "classification_counts": dict(counts),
        "withdrawn_classification_counts": dict(withdrawn_counts),
        "recommended_restore_count": sum(
            1
            for job_id, update, audit in updates
            if audit["recommended_status"] == "open"
            and jobs_by_id[job_id].get("status") == "withdrawn"
        ),
        "updated_count": len(updates) if apply else 0,
        "audit_count": len(updates) if apply else 0,
        "preview": [audit for _, _, audit in updates[:25]],
    }


__all__ = ["audit_and_backfill", "derive_phase3_update"]
