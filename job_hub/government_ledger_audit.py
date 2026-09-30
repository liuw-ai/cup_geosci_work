"""Read-only consistency checks across government recruitment ledgers.

The artifact manifest, position registry, expansion queue and configured
source registries represent different stages of one recruitment fact.  They
must agree before an operator treats a source state as actionable.  This
module deliberately performs no network, database or publication work.
"""

from __future__ import annotations

from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from job_hub.domestic_expansion import load_domestic_expansion_queue
from job_hub.government_artifacts import load_government_artifact_manifest
from job_hub.government_positions import load_position_registry
from job_hub.sources import load_source_registries


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SOURCE_REGISTRY_PATH = PROJECT_ROOT / "data" / "sources.json"
PROVINCIAL_SOURCE_REGISTRY_PATH = PROJECT_ROOT / "data" / "provincial_sources.json"

# A source that has verified position rows cannot simultaneously be described
# as inaccessible or as a successful scan with no matching positions.  This
# says nothing about whether its evidence is still fresh enough to publish.
CONTRADICTORY_QUEUE_STATUSES = frozenset({"access_limited", "scan_success_no_match"})
VERIFIED_POSITION_STATUSES = frozenset({"verified_open", "verified_closed"})


def _default_source_records() -> list[dict[str, Any]]:
    return load_source_registries(
        [str(SOURCE_REGISTRY_PATH), str(PROVINCIAL_SOURCE_REGISTRY_PATH)]
    )


def _issue(code: str, message: str, **context: Any) -> dict[str, Any]:
    return {
        "code": code,
        "severity": "error",
        "message": message,
        **context,
    }


def _registry_external_ids(registry: dict[str, Any]) -> set[str]:
    """Return the stable public external identifiers for all ledger rows."""
    return {
        (
            f"government-position:{record['id']}:{record['position_code']}"
        )
        for record in registry.get("records", [])
    }


def _registry_evidence_summary(
    registry: dict[str, Any],
    *,
    today: date,
    max_age_hours: float,
) -> dict[str, Any]:
    as_of = date.fromisoformat(str(registry["as_of"]))
    age_hours = max(0, (today - as_of).days * 24)
    records = list(registry.get("records", []))
    verified_records = [
        record
        for record in records
        if str(record.get("record_status") or "") in VERIFIED_POSITION_STATUSES
    ]
    verified_open = [
        record
        for record in verified_records
        if str(record.get("record_status") or "") == "verified_open"
    ]
    if not verified_records:
        state = "no_verified_position_records"
        interpretation = (
            "岗位台账尚无已核验职位行；这不能解释为该官方来源或全国市场没有岗位。"
        )
    elif age_hours > max_age_hours:
        state = "verified_positions_stale"
        interpretation = (
            "岗位级官方证据存在，但台账已超过新鲜度窗口；重新核验前不得将这些记录"
            "计入当前在招岗位。"
        )
    else:
        state = "verified_positions_current"
        interpretation = (
            "岗位级官方证据处于新鲜度窗口内；仍须由现有专业、学历、报名窗口和"
            "发布门禁决定是否向学生端公开。"
        )
    return {
        "as_of": as_of.isoformat(),
        "today": today.isoformat(),
        "age_hours": age_hours,
        "max_age_hours": max_age_hours,
        "state": state,
        "verified_position_records": len(verified_records),
        "verified_open_position_records": len(verified_open),
        "sources_with_verified_positions": len(
            {str(record.get("source_id") or "") for record in verified_records}
        ),
        "interpretation": interpretation,
    }


def build_government_ledger_consistency_audit(
    *,
    queue: dict[str, Any] | None = None,
    manifest: dict[str, Any] | None = None,
    registry: dict[str, Any] | None = None,
    source_records: Iterable[dict[str, Any]] | None = None,
    today: str | None = None,
    max_age_hours: float = 48,
) -> dict[str, Any]:
    """Join government evidence ledgers and report contradictions.

    The result is intentionally diagnostic instead of a publishing decision.
    A fresh and internally consistent registry may still have no currently
    open, student-eligible positions; conversely, a stale registry contains
    evidence but cannot be used as a current vacancy count.
    """
    if max_age_hours < 0:
        raise ValueError("max_age_hours must be non-negative")
    target_date = date.fromisoformat(today) if today else date.today()
    queue_payload = (
        queue if queue is not None else load_domestic_expansion_queue()
    )
    manifest_payload = (
        manifest if manifest is not None else load_government_artifact_manifest()
    )
    registry_payload = (
        registry if registry is not None else load_position_registry()
    )
    sources = (
        list(source_records)
        if source_records is not None
        else _default_source_records()
    )

    queue_records = list(queue_payload.get("records", []))
    artifact_records = list(manifest_payload.get("artifacts", []))
    position_records = list(registry_payload.get("records", []))
    registered_source_ids = {
        str(source.get("id") or "").strip()
        for source in sources
        if str(source.get("id") or "").strip()
    }
    verified_by_source: dict[str, list[dict[str, Any]]] = {}
    for record in position_records:
        if str(record.get("record_status") or "") not in VERIFIED_POSITION_STATUSES:
            continue
        source_id = str(record.get("source_id") or "").strip()
        if source_id:
            verified_by_source.setdefault(source_id, []).append(record)

    artifact_by_id = {
        str(item.get("id") or "").strip(): item
        for item in artifact_records
        if str(item.get("id") or "").strip()
    }
    referenced_artifact_ids: set[str] = set()
    known_position_ids = _registry_external_ids(registry_payload)
    errors: list[dict[str, Any]] = []

    def check_source_reference(owner: str, record_id: str, source_id: str) -> None:
        if source_id and source_id not in registered_source_ids:
            errors.append(
                _issue(
                    "unregistered_source_reference",
                    f"{owner} record references source_id not found in configured source registries.",
                    record_id=record_id,
                    source_id=source_id,
                )
            )

    for item in queue_records:
        queue_id = str(item.get("id") or "").strip()
        source_id = str(item.get("source_id") or "").strip()
        status = str(item.get("status") or "").strip()
        check_source_reference("Expansion queue", queue_id, source_id)
        if status in CONTRADICTORY_QUEUE_STATUSES and verified_by_source.get(source_id):
            errors.append(
                _issue(
                    "queue_status_conflicts_with_verified_registry_records",
                    "Queue status conflicts with verified government position records for the same source.",
                    queue_record_id=queue_id,
                    source_id=source_id,
                    queue_status=status,
                    verified_position_ids=sorted(
                        str(record["id"]) for record in verified_by_source[source_id]
                    ),
                )
            )
        for sample_id in item.get("sample_job_ids") or []:
            sample = str(sample_id or "").strip()
            if sample.startswith("government-position:") and sample not in known_position_ids:
                errors.append(
                    _issue(
                        "queue_government_position_sample_missing",
                        "Queue sample_job_id does not resolve to a government position registry row.",
                        queue_record_id=queue_id,
                        sample_job_id=sample,
                    )
                )
        if status == "current_non_student_eligible":
            artifact_id = str(item.get("related_artifact_id") or "").strip()
            referenced_artifact_ids.add(artifact_id)
            artifact = artifact_by_id.get(artifact_id)
            if artifact is None:
                errors.append(
                    _issue(
                        "non_student_queue_artifact_reference_missing",
                        "Excluded queue record must reference a real manifest artifact.",
                        queue_record_id=queue_id,
                        related_artifact_id=artifact_id or None,
                    )
                )
            else:
                artifact_source_id = str(artifact.get("source_id") or "").strip()
                if artifact_source_id != source_id:
                    errors.append(
                        _issue(
                            "non_student_queue_artifact_source_mismatch",
                            "Excluded queue record and linked artifact must have the same source_id.",
                            queue_record_id=queue_id,
                            related_artifact_id=artifact_id,
                            queue_source_id=source_id,
                            artifact_source_id=artifact_source_id,
                        )
                    )
                if artifact.get("status") != "current_non_student_eligible":
                    errors.append(
                        _issue(
                            "non_student_queue_artifact_status_mismatch",
                            "Excluded queue record must link to an artifact with the same excluded lifecycle status.",
                            queue_record_id=queue_id,
                            related_artifact_id=artifact_id,
                            artifact_status=artifact.get("status"),
                        )
                    )

    for artifact in artifact_records:
        artifact_id = str(artifact.get("id") or "").strip()
        source_id = str(artifact.get("source_id") or "").strip()
        check_source_reference("Artifact manifest", artifact_id, source_id)
        if (
            artifact.get("status") == "current_non_student_eligible"
            and artifact_id not in referenced_artifact_ids
        ):
            errors.append(
                _issue(
                    "excluded_artifact_has_no_queue_link",
                    "Current excluded artifact has no linked expansion-queue scope decision.",
                    artifact_id=artifact_id,
                    source_id=source_id,
                )
            )

    for source_id in verified_by_source:
        check_source_reference("Government position registry", source_id, source_id)

    evidence = _registry_evidence_summary(
        registry_payload,
        today=target_date,
        max_age_hours=float(max_age_hours),
    )
    return {
        "version": 1,
        "ok": not errors,
        "today": target_date.isoformat(),
        "error_count": len(errors),
        "errors": errors,
        "queue": {
            "as_of": queue_payload.get("as_of"),
            "records": len(queue_records),
            "by_status": dict(
                sorted(Counter(str(item.get("status") or "unknown") for item in queue_records).items())
            ),
        },
        "artifact_manifest": {
            "as_of": manifest_payload.get("as_of"),
            "artifacts": len(artifact_records),
            "by_status": dict(
                sorted(Counter(str(item.get("status") or "unknown") for item in artifact_records).items())
            ),
        },
        "registry_evidence": evidence,
        "configured_source_count": len(registered_source_ids),
        "publication_policy": (
            "这是只读一致性审计：通过不代表有新增岗位，失败也不代表无岗位。"
            "学生端仍只能发布具备当前官方岗位级证据、专业和学历匹配、地点、人数、"
            "报名截止日期且通过既有发布门禁的记录。"
        ),
    }


__all__ = [
    "CONTRADICTORY_QUEUE_STATUSES",
    "build_government_ledger_consistency_audit",
]
