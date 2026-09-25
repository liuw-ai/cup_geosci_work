"""中国石油单位矩阵与官方岗位快照审计。

单位注册表来自 ``domestic_source_expansion_queue.json``，岗位数据来自经
管理员核验的官方快照。二者必须分别审计：登记了官方入口不等于已经有岗位，
访问受限也不等于没有岗位。
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


PROJECT_ROOT = Path(__file__).resolve().parent.parent
QUEUE_PATH = PROJECT_ROOT / "data" / "domestic_source_expansion_queue.json"
DEFAULT_SNAPSHOT_PATH = PROJECT_ROOT / "data" / "verified" / "cnpc-geoscience-20260924.json"
CNPC_HOSTS = {"zhaopin.cnpc.com.cn", "www.cnpc.com.cn", "cnpc.com.cn", "www.bgp.com.cn", "bgp.com.cn", "rip.ed.cnpc.com.cn", "cpl.cnpc.com.cn"}
QUEUE_STATUSES = {
    "official_job_sample_verified",
    "official_identity_only",
    "scan_success_no_match",
    "access_limited",
    "manual_review_required",
}
REQUIRED_UNIT_FIELDS = (
    "id",
    "organization",
    "parent_organization",
    "organization_role",
    "official_url",
    "backup_urls",
    "channel_type",
    "status",
)
REQUIRED_JOB_FIELDS = (
    "external_id",
    "title",
    "employer",
    "location",
    "qualification_text",
    "source_url",
    "official_evidence_url",
)
TARGET_MAJOR_TERMS = (
    "资源勘查工程",
    "地质学",
    "地质工程",
    "地质资源与地质工程",
)


class CnpcMatrixError(ValueError):
    """Raised when the CNPC matrix or snapshot violates its contract."""


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CnpcMatrixError(f"无法读取 JSON: {path}") from error


def _is_official_url(value: Any) -> bool:
    parsed = urlparse(str(value or ""))
    host = (parsed.hostname or "").lower().rstrip(".")
    return parsed.scheme in {"http", "https"} and (
        host in CNPC_HOSTS or any(host.endswith("." + domain) for domain in CNPC_HOSTS)
    )


def load_cnpc_unit_matrix(path: Path | None = None) -> list[dict[str, Any]]:
    """Load the CNPC subset of the official expansion queue."""
    payload = _read_json(path or QUEUE_PATH)
    records = payload.get("records") if isinstance(payload, dict) else None
    if not isinstance(records, list):
        raise CnpcMatrixError("国内来源队列缺少 records 数组")
    units: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in records:
        if not isinstance(record, dict) or record.get("system") != "中国石油":
            continue
        missing = [field for field in REQUIRED_UNIT_FIELDS if field not in record]
        if missing:
            raise CnpcMatrixError(
                f"中石油单位 {record.get('id', '<unknown>')} 缺少字段: {', '.join(missing)}"
            )
        unit_id = str(record["id"]).strip()
        if not unit_id or unit_id in seen:
            raise CnpcMatrixError(f"中石油单位 ID 重复或为空: {unit_id!r}")
        if str(record["status"]) not in QUEUE_STATUSES:
            raise CnpcMatrixError(f"中石油单位状态无效: {record['status']!r}")
        if not _is_official_url(record["official_url"]):
            raise CnpcMatrixError(f"中石油单位官方入口不是允许的官方域名: {unit_id}")
        backups = record["backup_urls"]
        if not isinstance(backups, list) or not backups or not all(
            _is_official_url(url) for url in backups
        ):
            raise CnpcMatrixError(f"中石油单位缺少有效备用官方入口: {unit_id}")
        seen.add(unit_id)
        units.append(dict(record))
    if not units:
        raise CnpcMatrixError("中石油单位矩阵为空")
    return units


def load_cnpc_snapshot(path: Path | None = None) -> list[dict[str, Any]]:
    """Load the administrator-verified CNPC job rows."""
    payload = _read_json(path or DEFAULT_SNAPSHOT_PATH)
    rows = payload if isinstance(payload, list) else payload.get("jobs")
    if not isinstance(rows, list):
        raise CnpcMatrixError("中石油岗位快照必须是数组或包含 jobs 数组的对象")
    return [dict(row) for row in rows if isinstance(row, dict)]


def _normalized(value: Any) -> str:
    return "".join(str(value or "").split()).replace("股份有限公司", "").replace("有限责任公司", "")


def _unit_for_employer(employer: str, units: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidate = _normalized(employer)
    if not candidate:
        return None
    matches = []
    for unit in units:
        organization = _normalized(unit["organization"])
        if candidate == organization or candidate in organization or organization in candidate:
            matches.append(unit)
    return max(matches, key=lambda unit: len(_normalized(unit["organization"]))) if matches else None


def audit_cnpc_snapshot(
    rows: list[dict[str, Any]],
    units: list[dict[str, Any]],
) -> dict[str, Any]:
    """Audit field evidence and bind every snapshot row to a registered unit."""
    unit_by_id = {str(unit["id"]): unit for unit in units}
    jobs_by_unit: Counter[str] = Counter()
    explicit_by_unit: Counter[str] = Counter()
    missing_fields: list[dict[str, Any]] = []
    unmapped_jobs: list[dict[str, Any]] = []
    invalid_evidence: list[dict[str, Any]] = []
    for row in rows:
        external_id = str(row.get("external_id") or "<unknown>")
        missing = [field for field in REQUIRED_JOB_FIELDS if not str(row.get(field) or "").strip()]
        if missing:
            missing_fields.append({"external_id": external_id, "fields": missing})
        unit = _unit_for_employer(str(row.get("employer") or ""), units)
        if unit is None:
            unmapped_jobs.append({"external_id": external_id, "employer": row.get("employer")})
            continue
        unit_id = str(unit["id"])
        jobs_by_unit[unit_id] += 1
        qualification = str(row.get("qualification_text") or "")
        if any(term in qualification for term in TARGET_MAJOR_TERMS):
            explicit_by_unit[unit_id] += 1
        for field in ("source_url", "official_evidence_url"):
            if not _is_official_url(row.get(field)):
                invalid_evidence.append({"external_id": external_id, "field": field, "value": row.get(field)})
    return {
        "row_count": len(rows),
        "mapped_row_count": sum(jobs_by_unit.values()),
        "explicit_match_count": sum(explicit_by_unit.values()),
        "missing_field_rows": missing_fields,
        "invalid_evidence": invalid_evidence,
        "unmapped_jobs": unmapped_jobs,
        "jobs_by_unit": dict(sorted(jobs_by_unit.items())),
        "explicit_matches_by_unit": dict(sorted(explicit_by_unit.items())),
        "all_rows_bound": not unmapped_jobs and not missing_fields and not invalid_evidence,
        "unit_ids": sorted(unit_by_id),
    }


def cnpc_matrix_summary(
    units: list[dict[str, Any]],
    audit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return stable metrics used by CLI, admin API and phase reports."""
    audit = audit or {}
    return {
        "unit_count": len(units),
        "status_counts": dict(sorted(Counter(str(unit["status"]) for unit in units).items())),
        "role_counts": dict(sorted(Counter(str(unit["organization_role"]) for unit in units).items())),
        "units_with_source_binding": sum(1 for unit in units if unit.get("source_id")),
        "units_with_sample_job": sum(1 for unit in units if unit.get("status") == "official_job_sample_verified"),
        "backup_entry_rate": round(sum(bool(unit.get("backup_urls")) for unit in units) / len(units), 4),
        "snapshot_rows": int(audit.get("row_count", 0)),
        "snapshot_rows_bound": int(audit.get("mapped_row_count", 0)),
        "explicit_major_matches": int(audit.get("explicit_match_count", 0)),
        "snapshot_contract_passed": bool(audit.get("all_rows_bound", False)),
    }


def cnpc_matrix_rows(
    units: list[dict[str, Any]],
    audit: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    audit = audit or {}
    jobs_by_unit = audit.get("jobs_by_unit", {})
    explicit_by_unit = audit.get("explicit_matches_by_unit", {})
    return [
        {
            "id": unit["id"],
            "organization": unit["organization"],
            "parent_organization": unit["parent_organization"],
            "organization_role": unit["organization_role"],
            "official_url": unit["official_url"],
            "backup_urls": list(unit["backup_urls"]),
            "channel_type": unit["channel_type"],
            "status": unit["status"],
            "source_id": unit.get("source_id"),
            "sample_announcement_url": unit.get("sample_announcement_url"),
            "job_count": int(jobs_by_unit.get(unit["id"], 0)),
            "explicit_match_count": int(explicit_by_unit.get(unit["id"], 0)),
            "next_action": unit.get("next_action", ""),
        }
        for unit in units
    ]


def build_cnpc_matrix_report(
    *,
    matrix_path: Path | None = None,
    snapshot_path: Path | None = None,
) -> dict[str, Any]:
    units = load_cnpc_unit_matrix(matrix_path)
    rows = load_cnpc_snapshot(snapshot_path)
    audit = audit_cnpc_snapshot(rows, units)
    return {
        "matrix_path": str(matrix_path or QUEUE_PATH),
        "snapshot_path": str(snapshot_path or DEFAULT_SNAPSHOT_PATH),
        "summary": cnpc_matrix_summary(units, audit),
        "audit": audit,
        "items": cnpc_matrix_rows(units, audit),
    }
