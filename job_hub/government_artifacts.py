"""Versioned manifest for official provincial position-table artifacts.

The manifest is intentionally separate from ``sources.json``.  A source is
an entry point; an artifact is one specific notice attachment with its own
deadline and server-download state.  Registering an artifact never downloads
it or publishes rows.  The existing controlled attachment processor performs
the download, hash, extraction and review steps afterwards.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST_PATH = PROJECT_ROOT / "data" / "government_artifact_manifest.json"
ARTIFACT_STATUSES = frozenset(
    {
        "server_download_pending",
        "manual_verified",
        "historical_closed",
        "source_unavailable",
        "current_non_student_eligible",
    }
)
DEADLINE_POLICIES = frozenset({"fixed_date", "open_until_filled"})
EVIDENCE_MODES = frozenset({"registry_bound", "row_self_contained"})

# Manifest state is a control-plane decision, not a descriptive note. Only
# artifacts explicitly marked for server processing may enter the automated
# download queue.
MANIFEST_PROCESSING_POLICIES = {
    "server_download_pending": "automatic",
    "manual_verified": "manual_only",
    "historical_closed": "historical_closed",
    "source_unavailable": "source_unavailable",
    "current_non_student_eligible": "student_scope_excluded",
}
AUTOMATIC_MANIFEST_PROCESSING_POLICIES = frozenset({"automatic"})
# A manifest entry represents a controlled evidence lifecycle.  The static
# control plane can authorize pre-built ledger rows only when explicitly marked
# as manually verified.  Automatically processed files receive a separate,
# process-local runtime projection after their database evidence is complete.
MANIFEST_PUBLICATION_READY_STATUSES = frozenset({"manual_verified"})
RUNTIME_PROJECTION_VERSION = "government-artifact-runtime-projection-v1"
RUNTIME_CANDIDATE_READY_STATUSES = frozenset(
    {"official_content_verified", "published"}
)


class GovernmentArtifactContractError(ValueError):
    """Raised when an official artifact manifest is incomplete."""


def _text(value: Any, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise GovernmentArtifactContractError(f"{field} must be non-empty")
    return result


def _url(value: Any, field: str) -> str:
    result = _text(value, field)
    parsed = urlparse(result)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise GovernmentArtifactContractError(f"{field} must be an HTTP(S) URL")
    return result


def _date(value: Any, field: str, *, allow_empty: bool = False) -> str:
    result = str(value or "").strip()
    if not result and allow_empty:
        return ""
    if not result:
        raise GovernmentArtifactContractError(f"{field} must be non-empty")
    try:
        date.fromisoformat(result)
    except ValueError as error:
        raise GovernmentArtifactContractError(f"{field} must use YYYY-MM-DD") from error
    return result


def load_government_artifact_manifest(
    path: Path | str | None = None,
) -> dict[str, Any]:
    manifest_path = Path(path or DEFAULT_MANIFEST_PATH)
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise GovernmentArtifactContractError(f"Cannot read {manifest_path}") from error
    if not isinstance(payload, dict):
        raise GovernmentArtifactContractError("government artifact manifest must be an object")
    version = payload.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise GovernmentArtifactContractError("manifest version must be a positive integer")
    as_of = _date(payload.get("as_of"), "manifest.as_of")
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list):
        raise GovernmentArtifactContractError("manifest.artifacts must be a list")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    required = (
        "id",
        "source_id",
        "province",
        "position_type",
        "notice_url",
        "attachment_url",
        "artifact_kind",
        "deadline_date",
        "deadline_policy",
        "observed_on",
        "status",
    )
    for index, value in enumerate(artifacts):
        context = f"artifacts[{index}]"
        if not isinstance(value, dict):
            raise GovernmentArtifactContractError(f"{context} must be an object")
        missing = [field for field in required if field not in value]
        if missing:
            raise GovernmentArtifactContractError(f"{context} is missing: {', '.join(missing)}")
        item = dict(value)
        for field in ("id", "source_id", "province", "position_type", "artifact_kind"):
            item[field] = _text(item[field], f"{context}.{field}")
        item["notice_url"] = _url(item["notice_url"], f"{context}.notice_url")
        item["attachment_url"] = _url(item["attachment_url"], f"{context}.attachment_url")
        item["deadline_date"] = _date(
            item["deadline_date"], f"{context}.deadline_date", allow_empty=True
        )
        item["deadline_policy"] = _text(item["deadline_policy"], f"{context}.deadline_policy")
        if item["deadline_policy"] not in DEADLINE_POLICIES:
            raise GovernmentArtifactContractError(
                f"{context}.deadline_policy must be one of {sorted(DEADLINE_POLICIES)}"
            )
        if item["deadline_policy"] == "fixed_date" and not item["deadline_date"]:
            raise GovernmentArtifactContractError(f"{context} fixed_date artifacts require deadline_date")
        if item["deadline_policy"] == "open_until_filled" and item["deadline_date"]:
            raise GovernmentArtifactContractError(
                f"{context} open_until_filled artifacts must not invent a fixed deadline_date"
            )
        item["observed_on"] = _date(item["observed_on"], f"{context}.observed_on")
        item["status"] = _text(item["status"], f"{context}.status")
        if item["status"] not in ARTIFACT_STATUSES:
            raise GovernmentArtifactContractError(f"{context}.status is unsupported")
        item["evidence_mode"] = str(item.get("evidence_mode") or "registry_bound").strip()
        if item["evidence_mode"] not in EVIDENCE_MODES:
            raise GovernmentArtifactContractError(
                f"{context}.evidence_mode must be one of {sorted(EVIDENCE_MODES)}"
            )
        # Runtime publication markers are process-local projections.  Never
        # accept them from the JSON control plane, otherwise editing the
        # manifest could bypass the database extraction and row-evidence gate.
        item.pop("runtime_publication_ready", None)
        item.pop("runtime_projection", None)
        if item["id"] in seen:
            raise GovernmentArtifactContractError(f"duplicate artifact id: {item['id']}")
        seen.add(item["id"])
        item["note"] = str(item.get("note") or "").strip()
        normalized.append(item)
    return {
        "version": version,
        "as_of": as_of,
        "description": str(payload.get("description") or "").strip(),
        "artifacts": normalized,
    }


def government_artifact_processing_policy(metadata: dict[str, Any] | None) -> str | None:
    """Return the non-bypassable processing policy for a manifest artifact.

    Non-manifest attachment discoveries return ``None`` and continue through
    the ordinary controlled attachment workflow. An old or malformed manifest
    row is deliberately manual-only instead of becoming an unexpected
    automated download.
    """
    metadata = metadata or {}
    if not metadata.get("government_artifact_id"):
        return None
    status = str(metadata.get("manifest_status") or "").strip()
    return MANIFEST_PROCESSING_POLICIES.get(status, "manual_only")


def government_artifact_processing_block_reason(
    metadata: dict[str, Any] | None,
) -> str | None:
    """Explain why a manifest-backed artifact cannot be auto-processed."""
    policy = government_artifact_processing_policy(metadata)
    if policy in (None, "automatic"):
        return None
    reasons = {
        "manual_only": (
            "Government manifest requires manual evidence confirmation; "
            "automatic attachment download is disabled"
        ),
        "historical_closed": (
            "Government manifest marks this attachment as historical and closed; "
            "automatic processing is disabled"
        ),
        "source_unavailable": (
            "Government manifest records this official attachment as unavailable; "
            "automatic retry is disabled"
        ),
        "student_scope_excluded": (
            "Government manifest marks this current recruitment as outside the "
            "student-facing eligibility scope; automatic processing is disabled"
        ),
    }
    return reasons.get(policy, "Government manifest disables automatic attachment processing")


def government_artifact_publication_block_reason(
    attachment_url: str,
    manifest: dict[str, Any] | None,
) -> str | None:
    """Return why a known manifest attachment cannot authorize publication.

    The artifact manifest is deliberately the source of truth for files that
    have entered the controlled attachment workflow.  A reviewed government
    ledger may contain a row derived from such a file, but it must not bypass
    the file lifecycle simply because a short HTTP evidence recheck succeeds.
    URLs absent from the manifest are left to the ordinary reviewed-ledger
    gate; this preserves direct official-detail records that have no attached
    position table.
    """
    if manifest is None:
        return None
    target = str(attachment_url or "").strip()
    if not target:
        return None
    matches = [
        item
        for item in manifest.get("artifacts", [])
        if str(item.get("attachment_url") or "").strip() == target
    ]
    if not matches:
        return None
    statuses = {str(item.get("status") or "").strip() for item in matches}
    runtime_projection_active = (
        manifest.get("runtime_projection_version") == RUNTIME_PROJECTION_VERSION
    )
    if all(
        str(item.get("status") or "").strip() in MANIFEST_PUBLICATION_READY_STATUSES
        or (runtime_projection_active and bool(item.get("runtime_publication_ready")))
        for item in matches
    ):
        return None
    status_text = "、".join(sorted(status for status in statuses if status)) or "unknown"
    return (
        "官方附件尚未完成受控下载、哈希和逐行复核，"
        f"当前清单状态为 {status_text}。"
    )


def project_government_artifact_manifest(
    database: Any,
    manifest: dict[str, Any] | None,
    *,
    registry: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Overlay verified database facts onto a static artifact manifest.

    The JSON manifest is the declaration/control plane: ``server_download_pending``
    remains the intended processing policy and is never rewritten on disk.  A
    processed attachment can, however, acquire a runtime publication marker
    after the database proves all of the following: extraction completed,
    content has a valid SHA-256, the automatic verifier found an exact current
    registry match, and every matched candidate has reached a verified private
    review state.  The marker is an in-memory projection consumed by the
    publication gate and is therefore lost safely if the database is missing
    or the next verification fails.
    """
    if manifest is None:
        return None
    projected = dict(manifest)
    projected_items: list[dict[str, Any]] = []
    artifacts = list(database.iter_source_artifacts()) if database is not None else []
    by_key = {
        (
            str(item.get("source_id") or "").strip(),
            str(item.get("artifact_url") or "").strip(),
        ): item
        for item in artifacts
    }
    for declared in manifest.get("artifacts") or []:
        item = dict(declared)
        if str(item.get("status") or "").strip() == "server_download_pending":
            key = (
                str(item.get("source_id") or "").strip(),
                str(item.get("attachment_url") or "").strip(),
            )
            artifact = by_key.get(key)
            ready, evidence = _runtime_artifact_publication_state(
                database,
                artifact,
                declared=item,
                registry=registry,
            )
            if ready:
                item["runtime_publication_ready"] = True
                item["runtime_projection"] = evidence
        projected_items.append(item)
    projected["artifacts"] = projected_items
    projected["runtime_projection_version"] = RUNTIME_PROJECTION_VERSION
    return projected


def _runtime_artifact_publication_state(
    database: Any,
    artifact: dict[str, Any] | None,
    *,
    declared: dict[str, Any] | None = None,
    registry: dict[str, Any] | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Evaluate database-only facts needed to supersede a pending declaration."""
    base = {
        "version": RUNTIME_PROJECTION_VERSION,
        "publication_ready": False,
    }
    if artifact is None:
        return False, base | {"reason": "artifact_not_registered"}
    if str(artifact.get("extraction_status") or "") != "extracted":
        return False, base | {
            "reason": f"extraction_status:{artifact.get('extraction_status') or 'unknown'}"
        }
    digest = str(artifact.get("content_sha256") or "").strip().lower()
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        return False, base | {"reason": "content_sha256_missing_or_invalid"}
    metadata = artifact.get("metadata") or {}
    verification = metadata.get("government_auto_verification")
    if not isinstance(verification, dict):
        return False, base | {"reason": "government_auto_verification_missing"}
    if verification.get("row_evidence_verified") is not True:
        return False, base | {"reason": "row_evidence_not_verified"}
    if verification.get("publication_ready") is not True:
        return False, base | {
            "reason": "government_auto_verification_not_publication_ready"
        }
    evidence_mode = str((declared or {}).get("evidence_mode") or "registry_bound").strip()
    if evidence_mode == "row_self_contained":
        matched_row_ids = {
            str(value).strip()
            for value in (verification.get("matched_candidate_ids") or [])
            if str(value).strip()
        }
        if not matched_row_ids:
            return False, base | {"reason": "matched_row_candidates_missing"}
        try:
            candidates = list(database.iter_artifact_job_candidates(artifact_id=int(artifact["id"])))
        except (KeyError, TypeError, ValueError):
            candidates = []
        ready_ids = {
            str(candidate.get("id") or "")
            for candidate in candidates
            if str(candidate.get("review_status") or "") in RUNTIME_CANDIDATE_READY_STATUSES
        }
        if not matched_row_ids.issubset(ready_ids):
            return False, base | {
                "reason": "matched_row_candidates_not_verified",
                "matched_rows": len(matched_row_ids),
                "verified_rows": len(matched_row_ids & ready_ids),
            }
        return True, base | {
            "publication_ready": True,
            "extraction_status": "extracted",
            "content_sha256": digest,
            "row_evidence_verified": True,
            "evidence_mode": evidence_mode,
            "matched_rows": len(matched_row_ids),
            "verified_candidates": len(matched_row_ids),
            "government_auto_verification_checked_at": str(
                verification.get("checked_at") or ""
            ),
        }
    matched_ids = {
        str(value).strip()
        for value in (verification.get("matched_record_ids") or [])
        if str(value).strip()
    }
    blocked_records = [
        item
        for item in (verification.get("blocked_records") or [])
        if isinstance(item, dict)
    ]
    blocked_ids = {
        str(value).strip()
        for value in (verification.get("blocked_record_ids") or [])
        if str(value).strip()
    }
    # A partial table result is safe only when every current ledger row is
    # accounted for: matched rows have verified candidates, while blocked rows
    # carry an exact raw-row locator and an explicit student-gate reason. A
    # silently missing record must continue to fail closed.
    if blocked_records and blocked_ids != {
        str(item.get("record_id") or "").strip()
        for item in blocked_records
        if str(item.get("record_id") or "").strip()
    }:
        return False, base | {
            "reason": "blocked_registry_records_malformed",
            "blocked_records": len(blocked_records),
        }
    if not matched_ids:
        return False, base | {"reason": "matched_registry_records_missing"}
    if registry is not None:
        expected_ids = {
            str(record.get("id") or "").strip()
            for record in registry.get("records") or []
            if str(record.get("source_id") or "").strip()
            == str((declared or {}).get("source_id") or "").strip()
            and str(record.get("official_attachment_url") or "").strip()
            == str((declared or {}).get("attachment_url") or "").strip()
            and str(record.get("record_status") or "") == "verified_open"
            and str(record.get("match_status") or "")
            in {"explicit_match", "unrestricted_match"}
        }
        accounted_ids = matched_ids | blocked_ids
        if not expected_ids or accounted_ids != expected_ids:
            return False, base | {
                "reason": "matched_registry_records_changed_or_unaccounted",
                "matched_registry_records": len(matched_ids),
                "blocked_registry_records": len(blocked_ids),
                "current_registry_records": len(expected_ids),
            }
        for item in blocked_records:
            if not str(item.get("row_locator") or "").strip() or not str(
                item.get("reason") or ""
            ).strip():
                return False, base | {
                    "reason": "blocked_registry_record_evidence_missing",
                    "record_id": str(item.get("record_id") or ""),
                }
    matched_candidate_ids = {
        str(value).strip()
        for value in (verification.get("matched_candidate_ids") or [])
        if str(value).strip()
    }
    if len(matched_candidate_ids) != len(matched_ids):
        return False, base | {
            "reason": "matched_registry_candidate_ids_missing_or_duplicated",
            "matched_registry_records": len(matched_ids),
            "matched_candidates": len(matched_candidate_ids),
        }
    try:
        candidates = list(database.iter_artifact_job_candidates(artifact_id=int(artifact["id"])))
    except (KeyError, TypeError, ValueError):
        candidates = []
    ready_ids = {
        str(candidate.get("id") or "")
        for candidate in candidates
        if str(candidate.get("review_status") or "") in RUNTIME_CANDIDATE_READY_STATUSES
    }
    if not matched_candidate_ids.issubset(ready_ids):
        return False, base | {
            "reason": "matched_candidates_not_verified",
            "matched_registry_records": len(matched_ids),
            "verified_candidates": len(matched_candidate_ids & ready_ids),
        }
    return True, base | {
        "publication_ready": True,
        "extraction_status": "extracted",
        "content_sha256": digest,
        "row_evidence_verified": True,
        "matched_registry_records": len(matched_ids),
        "blocked_registry_records": len(blocked_ids),
        "verified_candidates": len(matched_candidate_ids),
        "government_auto_verification_checked_at": str(
            verification.get("checked_at") or ""
        ),
    }


def register_government_artifacts(database: Any, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Register manifest rows in the private attachment ledger only."""
    registered: list[dict[str, Any]] = []
    for item in manifest["artifacts"]:
        processing_policy = MANIFEST_PROCESSING_POLICIES[item["status"]]
        registered.append(
            database.upsert_source_artifact(
                {
                    "source_id": item["source_id"],
                    "parent_url": item["notice_url"],
                    "artifact_url": item["attachment_url"],
                    "artifact_kind": item["artifact_kind"],
                    "media_type": None,
                    "extraction_status": (
                        "registered"
                        if processing_policy in AUTOMATIC_MANIFEST_PROCESSING_POLICIES
                        else "skipped"
                    ),
                    "metadata": {
                        "government_artifact_id": item["id"],
                        "position_type": item["position_type"],
                        "province": item["province"],
                        "deadline_date": item["deadline_date"],
                        "deadline_policy": item["deadline_policy"],
                        "observed_on": item["observed_on"],
                        "manifest_status": item["status"],
                        "manifest_processing_policy": processing_policy,
                        "evidence_mode": str(item.get("evidence_mode") or "registry_bound").strip(),
                        "note": item["note"],
                    },
                }
            )
        )
    return registered


def government_artifact_refresh_summary(
    database: Any,
    *,
    manifest: dict[str, Any] | None = None,
    today: str | None = None,
    manifest_error: str | None = None,
) -> dict[str, Any]:
    """Return an operational summary for the daily report and worker logs.

    The summary distinguishes an unavailable source or a private review queue
    from a verified absence of jobs. It counts only artifacts registered through
    the versioned manifest; public job rows remain subject to the normal gate.
    """
    target = date.fromisoformat(today) if today else date.today()
    declared = list((manifest or {}).get("artifacts") or [])
    artifacts = []
    for item in database.iter_source_artifacts():
        metadata = item.get("metadata") or {}
        if metadata.get("government_artifact_id"):
            artifacts.append(item)
    extraction_counts: dict[str, int] = {}
    manifest_counts: dict[str, int] = {}
    policy_counts: dict[str, int] = {}
    current = historical = 0
    for artifact in artifacts:
        extraction = str(artifact.get("extraction_status") or "unknown")
        extraction_counts[extraction] = extraction_counts.get(extraction, 0) + 1
        metadata = artifact.get("metadata") or {}
        status = str(metadata.get("manifest_status") or "unknown")
        manifest_counts[status] = manifest_counts.get(status, 0) + 1
        processing_policy = government_artifact_processing_policy(metadata) or "not_manifest_backed"
        policy_counts[processing_policy] = policy_counts.get(processing_policy, 0) + 1
        deadline = str(metadata.get("deadline_date") or "").strip()
        policy = str(metadata.get("deadline_policy") or "fixed_date").strip()
        if policy == "open_until_filled" and not deadline:
            current += 1
            continue
        if deadline:
            try:
                if date.fromisoformat(deadline) >= target:
                    current += 1
                else:
                    historical += 1
            except ValueError:
                pass
    candidates = database.iter_artifact_job_candidates()
    candidate_counts: dict[str, int] = {}
    for candidate in candidates:
        status = str(candidate.get("review_status") or "unknown")
        candidate_counts[status] = candidate_counts.get(status, 0) + 1
    # A policy-driven ``skipped`` status is intentional, not a source failure.
    source_failures = manifest_counts.get("source_unavailable", 0) + (1 if manifest_error else 0)
    for artifact in artifacts:
        extraction = str(artifact.get("extraction_status") or "unknown")
        processing_policy = government_artifact_processing_policy(artifact.get("metadata") or {})
        if extraction == "failed" or (extraction == "skipped" and processing_policy == "automatic"):
            source_failures += 1
    return {
        "as_of": (manifest or {}).get("as_of"),
        "declared_artifacts": len(declared),
        "registered_artifacts": len(artifacts),
        "current_deadline_artifacts": current,
        "historical_deadline_artifacts": historical,
        "extraction_status": dict(sorted(extraction_counts.items())),
        "manifest_status": dict(sorted(manifest_counts.items())),
        "processing_policy": dict(sorted(policy_counts.items())),
        "candidate_review_status": dict(sorted(candidate_counts.items())),
        "source_failures_or_unavailable": source_failures,
        "manifest_error": manifest_error,
        "pending_manual_review": candidate_counts.get("needs_review", 0),
        "interpretation": (
            "存在来源故障或待人工复核记录，不能把未形成岗位行解释为无岗位。"
            if source_failures or candidate_counts.get("needs_review", 0) or manifest_error
            else "已登记附件均已完成当前处理；公开岗位仍以岗位级证据门禁为准。"
        ),
    }
