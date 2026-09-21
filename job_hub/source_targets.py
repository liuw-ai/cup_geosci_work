"""Versioned source-expansion targets for the 31 provincial network."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from job_hub.contracts import PROVINCIAL_SOURCE_TARGET_ROLES

REQUIRED_ROLES = PROVINCIAL_SOURCE_TARGET_ROLES

ROLE_LABELS = {
    "human_resources_or_exam": "人社/考试",
    "natural_resources": "自然资源",
    "geology_bureau_or_institute": "地质局/地质院",
    "public_institution_recruitment": "事业单位",
    "civil_service": "公务员",
}


def role_label(role: str) -> str:
    """Return a student-facing label while retaining stable machine role IDs."""
    return ROLE_LABELS.get(role, role)


def default_target_path() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "source_targets.json"


def load_source_targets(path: Path | None = None) -> dict[str, Any]:
    """Load and validate the source-target matrix.

    Targets are deliberately separate from the active source registry.  A
    candidate URL can be tracked, reviewed, and assigned a role without being
    crawled or being counted as an active official source.
    """
    target_path = path or default_target_path()
    if not target_path.exists():
        return {"required_roles": list(REQUIRED_ROLES), "province_targets": {}}
    try:
        payload = json.loads(target_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Invalid source target matrix: {target_path}") from error
    if not isinstance(payload, dict):
        raise ValueError("source_targets.json must contain an object")
    province_targets = payload.get("province_targets")
    if not isinstance(province_targets, dict):
        raise ValueError("source_targets.json requires province_targets")
    required_roles = tuple(payload.get("required_roles") or REQUIRED_ROLES)
    unknown_roles = set(required_roles).difference(REQUIRED_ROLES)
    if unknown_roles:
        raise ValueError(f"Unknown source target roles: {sorted(unknown_roles)}")
    for province, role_map in province_targets.items():
        if not isinstance(role_map, dict):
            raise ValueError(f"Target roles for {province} must be an object")
        for role, target in role_map.items():
            if role not in required_roles:
                raise ValueError(f"Unknown target role {role} for {province}")
            if not isinstance(target, dict):
                raise ValueError(f"Target {province}/{role} must be an object")
            state = str(target.get("state") or "").strip()
            if state not in {"verified", "candidate", "unlocated", "blocked"}:
                raise ValueError(f"Invalid target state {state!r} for {province}/{role}")
            if state == "verified" and not target.get("source_id"):
                raise ValueError(
                    f"Verified target {province}/{role} must bind source_id"
                )
            candidate_source_id = target.get("candidate_source_id")
            if candidate_source_id is not None:
                if state != "candidate":
                    raise ValueError(
                        f"candidate_source_id is only valid for candidate target "
                        f"{province}/{role}"
                    )
                if not isinstance(candidate_source_id, str) or not candidate_source_id.strip():
                    raise ValueError(
                        f"candidate_source_id for {province}/{role} must be non-empty text"
                    )
    payload["required_roles"] = list(required_roles)
    return payload


def target_matrix_summary(
    matrix: dict[str, Any],
    *,
    source_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Return counts used by coverage reports without probing candidate URLs."""
    source_ids = source_ids or set()
    required_roles = tuple(matrix.get("required_roles") or REQUIRED_ROLES)
    provinces: list[dict[str, Any]] = []
    totals = {"targets": 0, "verified": 0, "candidate": 0, "unlocated": 0, "blocked": 0}
    for province, role_map in sorted(
        (matrix.get("province_targets") or {}).items()
    ):
        role_states: dict[str, str] = {}
        role_source_ids: dict[str, str] = {}
        missing_roles: list[str] = []
        bound_verified = 0
        for role in required_roles:
            target = role_map.get(role) if isinstance(role_map, dict) else None
            state = str(target.get("state") or "unlocated") if isinstance(target, dict) else "unlocated"
            source_id = str(target.get("source_id") or "").strip()
            if state == "verified" and source_id not in source_ids:
                state = "candidate"
            role_states[role] = state
            if state == "verified":
                role_source_ids[role] = source_id
            totals["targets"] += 1
            totals[state] = totals.get(state, 0) + 1
            if state == "verified":
                bound_verified += 1
            else:
                missing_roles.append(role)
        provinces.append(
            {
                "province": province,
                "role_states": role_states,
                "role_source_ids": role_source_ids,
                "verified_targets": bound_verified,
                "target_count": len(required_roles),
                "missing_target_roles": missing_roles,
                "missing_target_role_labels": [
                    role_label(role) for role in missing_roles
                ],
            }
        )
    return {
        "required_roles": list(required_roles),
        "role_labels": {role: role_label(role) for role in required_roles},
        "province_count": len(provinces),
        "totals": totals,
        "provinces": provinces,
    }
