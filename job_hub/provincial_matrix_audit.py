"""Read-only audit for the 31-province, five-role source matrix.

The target matrix, validation ledger and runtime database answer different
questions.  This module joins them without promoting a candidate URL or
turning an unavailable source into a no-jobs result.  It is intentionally
side-effect free so operators can run it before every source expansion.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from job_hub.source_targets import REQUIRED_ROLES, load_source_targets, role_label
from job_hub.source_validation import (
    load_source_validation_registry,
    source_validation_records,
)
from job_hub.sources import load_source_registries


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SOURCE_REGISTRY_PATH = PROJECT_ROOT / "data" / "sources.json"
PROVINCIAL_SOURCE_REGISTRY_PATH = PROJECT_ROOT / "data" / "provincial_sources.json"

EVIDENCE_STAGES = frozenset(
    {"adapter_fixture_verified", "server_health_and_adapter_verified"}
)
HEALTHY_STATUS = "source_active"


def _static_sources() -> list[dict[str, Any]]:
    return load_source_registries(
        [str(SOURCE_REGISTRY_PATH), str(PROVINCIAL_SOURCE_REGISTRY_PATH)]
    )


def _target_source_id(target: dict[str, Any]) -> str | None:
    state = str(target.get("state") or "")
    if state == "verified":
        value = target.get("source_id")
    elif state == "candidate":
        value = target.get("candidate_source_id")
    else:
        value = None
    return str(value).strip() if value else None


def _validation_by_slot(
    registry: dict[str, Any],
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    result: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for record in source_validation_records(registry):
        key = (str(record["province"]), str(record["role"]))
        result.setdefault(key, []).append(record)
    return result


def _best_validation(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not records:
        return None
    rank = {
        "server_health_and_adapter_verified": 3,
        "adapter_fixture_verified": 2,
        "entry_checked_no_recruitment_sample": 1,
    }
    return max(records, key=lambda item: rank.get(str(item.get("validation_stage")), 0))


def _evidence_state(record: dict[str, Any] | None) -> str:
    if not record:
        return "not_recorded"
    stage = str(record.get("validation_stage") or "")
    sample = record.get("sample")
    if stage in EVIDENCE_STAGES and isinstance(sample, dict) and record.get("fixture_path"):
        return "sample_and_fixture_verified"
    if stage in EVIDENCE_STAGES and isinstance(sample, dict):
        return "sample_verified_without_fixture"
    return "entry_only"


def _field_evidence(record: dict[str, Any] | None) -> dict[str, Any]:
    sample = record.get("sample") if isinstance(record, dict) else None
    fields = sample.get("field_evidence") if isinstance(sample, dict) else None
    fields = fields if isinstance(fields, dict) else {}
    required = (
        "publisher",
        "published_date",
        "recruitment_scope",
        "application_or_deadline",
        "attachment_or_position_table",
    )
    present = [field for field in required if str(fields.get(field) or "").strip()]
    return {
        "required": list(required),
        "present": present,
        "missing": [field for field in required if field not in present],
        "complete": len(present) == len(required),
    }


def build_provincial_matrix_audit(
    *,
    matrix: dict[str, Any] | None = None,
    validation_registry: dict[str, Any] | None = None,
    source_records: Iterable[dict[str, Any]] | None = None,
    health_records: Iterable[dict[str, Any]] | None = None,
    latest_runs: Iterable[dict[str, Any]] | None = None,
    provinces: Iterable[str] | None = None,
    roles: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Join declared targets, evidence and runtime state without side effects."""
    payload = matrix or load_source_targets()
    materialized_sources = list(source_records) if source_records is not None else None
    registry = validation_registry or load_source_validation_registry(
        target_matrix=payload,
        source_records=materialized_sources,
    )
    sources = materialized_sources if materialized_sources is not None else _static_sources()
    source_by_id = {str(item.get("id")): item for item in sources if item.get("id")}
    health_by_id = {
        str(item.get("source_id")): item
        for item in (health_records or [])
        if item.get("source_id")
    }
    runs_by_id = {
        str(item.get("source_id")): item
        for item in (latest_runs or [])
        if item.get("source_id")
    }
    validation_by_slot = _validation_by_slot(registry)
    province_filter = {str(value).strip() for value in provinces or () if str(value).strip()}
    role_filter = {str(value).strip() for value in roles or () if str(value).strip()}
    unknown_roles = role_filter.difference(REQUIRED_ROLES)
    if unknown_roles:
        raise ValueError(f"Unknown provincial source role(s): {sorted(unknown_roles)}")

    rows: list[dict[str, Any]] = []
    state_counts: Counter[str] = Counter()
    evidence_counts: Counter[str] = Counter()
    for province, role_map in sorted((payload.get("province_targets") or {}).items()):
        if province_filter and province not in province_filter:
            continue
        role_map = role_map if isinstance(role_map, dict) else {}
        for role in REQUIRED_ROLES:
            if role_filter and role not in role_filter:
                continue
            target = role_map.get(role)
            target = target if isinstance(target, dict) else {"state": "unlocated"}
            target_state = str(target.get("state") or "unlocated")
            source_id = _target_source_id(target)
            source = source_by_id.get(source_id or "")
            records = validation_by_slot.get((str(province), role), [])
            validation = _best_validation(records)
            evidence_state = _evidence_state(validation)
            health = health_by_id.get(source_id or "")
            run = runs_by_id.get(source_id or "")
            blocked_reasons: list[str] = []
            if target_state != "verified":
                blocked_reasons.append(f"target_state:{target_state}")
            if not source_id:
                blocked_reasons.append("no_bound_source")
            elif source is None:
                blocked_reasons.append("source_not_registered")
            elif not bool(source.get("enabled")):
                blocked_reasons.append("source_disabled")
            if evidence_state == "not_recorded":
                blocked_reasons.append("validation_not_recorded")
            elif evidence_state == "entry_only":
                blocked_reasons.append("no_current_recruitment_sample")
            elif validation and not _field_evidence(validation)["complete"]:
                blocked_reasons.append("sample_field_evidence_incomplete")
            if validation and not validation.get("backup_entry_urls"):
                blocked_reasons.append("backup_entry_missing")
            if health is None:
                blocked_reasons.append("runtime_health_unchecked")
            elif str(health.get("status") or "") != HEALTHY_STATUS:
                blocked_reasons.append(f"runtime_health:{health.get('status')}")
            if run is None:
                blocked_reasons.append("latest_crawl_unchecked")
            elif str(run.get("status") or "") != "finished":
                blocked_reasons.append(f"latest_crawl:{run.get('status')}")
            evidence_counts[evidence_state] += 1
            state_counts[target_state] += 1
            rows.append(
                {
                    "province": str(province),
                    "role": role,
                    "role_label": role_label(role),
                    "target_state": target_state,
                    "source_id": source_id,
                    "source_name": source.get("name") if source else None,
                    "source_registered": source is not None,
                    "source_enabled": bool(source.get("enabled")) if source else False,
                    "validation_record_id": validation.get("id") if validation else None,
                    "validation_stage": validation.get("validation_stage") if validation else None,
                    "evidence_state": evidence_state,
                    "official_entry_url": (
                        validation.get("official_entry_url") if validation else target.get("official_entry_url")
                    ),
                    "sample_url": (
                        (validation.get("sample") or {}).get("official_url")
                        if validation else None
                    ),
                    "fixture_path": validation.get("fixture_path") if validation else None,
                    "field_evidence": _field_evidence(validation),
                    "backup_entry_count": len(validation.get("backup_entry_urls") or []) if validation else 0,
                    "source_health_status": health.get("status") if health else None,
                    "source_health_checked_at": health.get("checked_at") if health else None,
                    "latest_crawl_status": run.get("status") if run else None,
                    "latest_crawl_finished_at": run.get("finished_at") if run else None,
                    "ready_for_review": target_state == "verified" and evidence_state == "sample_and_fixture_verified",
                    "ready_for_activation": not blocked_reasons,
                    "blocked_reasons": blocked_reasons,
                }
            )

    ready_review = sum(1 for row in rows if row["ready_for_review"])
    ready_activation = sum(1 for row in rows if row["ready_for_activation"])
    return {
        "version": 1,
        "target_count": len(rows),
        "province_count": len({row["province"] for row in rows}),
        "role_count": len({row["role"] for row in rows}),
        "state_counts": dict(sorted(state_counts.items())),
        "evidence_state_counts": dict(sorted(evidence_counts.items())),
        "ready_for_review_count": ready_review,
        "ready_for_activation_count": ready_activation,
        "rows": rows,
        "publication_policy": (
            "这是管理员只读审计，不发布岗位、不启用来源，也不把 unlocated、candidate、"
            "访问故障或无匹配解释为无岗位。只有岗位级官方证据通过现有学生端门禁后才可发布。"
        ),
    }


__all__ = ["build_provincial_matrix_audit", "EVIDENCE_STAGES"]
