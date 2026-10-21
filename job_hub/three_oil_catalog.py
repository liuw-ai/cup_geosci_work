"""Validated scope catalog for the three oil groups.

This catalog is deliberately separate from ``organization_registry.json``.
It records the units that must be covered, while the organization registry
records only official entrances that have been individually verified. A unit
in this catalog is therefore not automatically crawlable or publishable.
"""

from __future__ import annotations

import json
import hashlib
import socket
from collections import Counter
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
import re
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit

from job_hub.contracts import ContractValidationError


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = PROJECT_ROOT / "data" / "three_oil_organization_catalog.json"
PHASE4_CNOOC_UNIT_EVIDENCE_PATH = (
    PROJECT_ROOT
    / "docs"
    / "current"
    / "phase4-cnooc-unit-display-evidence-2026-10-19.json"
)
GROUP_IDS = frozenset({"cnpc", "sinopec", "cnooc"})
FORBIDDEN_GROUP_IDS = frozenset({"pipechina"})
OFFICIAL_THREE_OIL_IDENTITY_SOURCE_IDS = frozenset(
    {
        "cnpc-career",
        "cnpc-career-browser",
        "cnpc-logging-campus",
        "sinopec-career",
        "sinopec-2027-geoscience-snapshot",
        "sinopec-pepris-recruitment",
        "cnooc-career-browser",
        "cosl-career",
    }
)
# Keep source ownership separate from the source allow-list.  An official
# detail row naming a three-oil unit is only identity evidence when the source
# itself belongs to the same group; otherwise a stale or cross-group parser
# record could incorrectly certify the unit.
OFFICIAL_THREE_OIL_SOURCE_GROUPS = {
    "cnpc-career": "cnpc",
    "cnpc-career-browser": "cnpc",
    "cnpc-logging-campus": "cnpc",
    "sinopec-career": "sinopec",
    "sinopec-2027-geoscience-snapshot": "sinopec",
    "sinopec-pepris-recruitment": "sinopec",
    "cnooc-career-browser": "cnooc",
    "cosl-career": "cnooc",
}
FALLBACK_CHANNEL_REQUIREMENTS = (
    {
        "channel_type": "official_announcement",
        "coverage_key": "official_announcement",
        "evidence_required": (
            "单位官网或集团官方公告中明确的年度校招/应届招聘正文，"
            "并能定位岗位级专业、学历、地点和截止日期。"
        ),
    },
    {
        "channel_type": "official_attachment",
        "coverage_key": "official_attachment",
        "evidence_required": (
            "官方公告页面直接链接的 XLS/XLSX/PDF 等附件，保存下载地址、"
            "SHA-256、文件时间和逐行字段定位。"
        ),
    },
    {
        "channel_type": "authorized_platform",
        "coverage_key": "authorized_platform",
        "evidence_required": (
            "单位官方公告明确授权的第三方招聘平台或子站；必须保存授权原文 URL、"
            "授权对象、授权时间范围和平台岗位详情 URL。"
        ),
    },
)


def _sha256_file(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""
# ``role`` is retained in the JSON for backwards-compatible imports, but the
# public/admin contract uses these neutral, inspectable organization roles.
# They describe what the unit does, not an assumed legal "party A/B" relation.
CANONICAL_UNIT_ROLES = frozenset(
    {
        "group",
        "operating_company",
        "regional_branch",
        "research_institute",
        "internal_service_unit",
        "external_service_provider",
        "refining_company",
        "sales_company",
        "financial_support_unit",
        "international_business_unit",
        "other_affiliate",
    }
)
LEGACY_ROLE_MAP = {
    "upstream_operator": "operating_company",
    "research_institute": "research_institute",
    "internal_technical_service": "internal_service_unit",
    "group": "group",
}
UNIT_ROLES = frozenset({*CANONICAL_UNIT_ROLES, *LEGACY_ROLE_MAP})


def load_three_oil_catalog(path: Path | None = None) -> dict[str, Any]:
    catalog_path = path or CATALOG_PATH
    try:
        payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Invalid three-oil organization catalog: {catalog_path}") from error
    try:
        return validate_three_oil_catalog(payload)
    except ContractValidationError as error:
        raise ValueError(f"three_oil_organization_catalog.json is invalid: {error}") from error


@lru_cache(maxsize=4)
def _cached_catalog(path: str) -> dict[str, Any]:
    return load_three_oil_catalog(Path(path))


def validate_three_oil_catalog(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ContractValidationError("three-oil catalog must be an object")
    version = payload.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ContractValidationError("three-oil catalog version must be a positive integer")
    groups = payload.get("groups")
    if not isinstance(groups, list) or not groups:
        raise ContractValidationError("three-oil catalog groups must be a non-empty list")

    normalized_groups: list[dict[str, Any]] = []
    seen_groups: set[str] = set()
    seen_units: set[str] = set()
    for index, group_value in enumerate(groups):
        if not isinstance(group_value, dict):
            raise ContractValidationError(f"three-oil group {index} must be an object")
        group = dict(group_value)
        group_id = str(group.get("id") or "").strip()
        if group_id not in GROUP_IDS:
            raise ContractValidationError(f"unsupported three-oil group id: {group_id}")
        if group_id in seen_groups:
            raise ContractValidationError(f"duplicate three-oil group id: {group_id}")
        seen_groups.add(group_id)
        for field in ("name", "official_identity_url", "units"):
            if field not in group:
                raise ContractValidationError(f"three-oil group {group_id} missing {field}")
        if not isinstance(group["units"], list) or not group["units"]:
            raise ContractValidationError(f"three-oil group {group_id} units must be non-empty")

        units: list[dict[str, Any]] = []
        for unit_index, unit_value in enumerate(group["units"]):
            if not isinstance(unit_value, dict):
                raise ContractValidationError(
                    f"three-oil unit {group_id}[{unit_index}] must be an object"
                )
            unit = dict(unit_value)
            unit_id = str(unit.get("id") or "").strip()
            unit_name = str(unit.get("name") or "").strip()
            role = str(unit.get("role") or "").strip()
            if not unit_id or not unit_name:
                raise ContractValidationError(
                    f"three-oil unit {group_id}[{unit_index}] requires id and name"
                )
            if unit_id in seen_units:
                raise ContractValidationError(f"duplicate three-oil unit id: {unit_id}")
            if unit_id in FORBIDDEN_GROUP_IDS:
                raise ContractValidationError(
                    f"forbidden non-three-oil group appears in catalog: {unit_id}"
                )
            if role not in UNIT_ROLES:
                raise ContractValidationError(
                    f"unsupported three-oil role for {unit_id}: {role}"
                )
            canonical_role = LEGACY_ROLE_MAP.get(role, role)
            if canonical_role == "group" and unit_id != group_id:
                raise ContractValidationError(
                    f"group role {unit_id} must use its catalog group id {group_id}"
                )
            seen_units.add(unit_id)
            unit["id"] = unit_id
            unit["name"] = unit_name
            unit["role"] = role
            unit["organization_role"] = canonical_role
            unit["group_id"] = group_id
            unit["name_evidence_status"] = str(
                unit.get("name_evidence_status") or "official_roster_registered_pending_legal_cross_check"
            ).strip()
            unit["catalog_status"] = str(
                unit.get("catalog_status") or "group_roster_catalogued"
            ).strip()
            units.append(unit)
        group["id"] = group_id
        group["name"] = str(group["name"]).strip()
        group["official_identity_url"] = str(group["official_identity_url"]).strip()
        group["units"] = units
        normalized_groups.append(group)

    if set(seen_groups) != GROUP_IDS:
        missing = sorted(GROUP_IDS - seen_groups)
        raise ContractValidationError(
            f"three-oil catalog must contain CNPC, Sinopec and CNOOC; missing {missing}"
        )
    normalized = dict(payload)
    normalized["version"] = version
    normalized["groups"] = normalized_groups
    normalized["exclusions"] = list(payload.get("exclusions") or [])
    return normalized


def three_oil_units(
    catalog: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], ...]:
    payload = catalog or _cached_catalog(str(CATALOG_PATH))
    rows: list[dict[str, Any]] = []
    for group in payload["groups"]:
        for unit in group["units"]:
            rows.append(
                {
                    **unit,
                    "organization_role": unit.get("organization_role")
                    or LEGACY_ROLE_MAP.get(unit["role"], unit["role"]),
                    "parent_id": None if unit["id"] == group["id"] else group["id"],
                    "parent_name": group["name"],
                    "group_name": group["name"],
                    "group_official_identity_url": group["official_identity_url"],
                }
            )
    return tuple(rows)


def three_oil_catalog_summary(
    catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = catalog or _cached_catalog(str(CATALOG_PATH))
    units = three_oil_units(payload)
    return {
        "group_count": len(payload["groups"]),
        "unit_count": len(units),
        "units_by_group": {
            group["id"]: len(group["units"]) for group in payload["groups"]
        },
        "roles": {
            role: sum(1 for unit in units if unit["organization_role"] == role)
            for role in sorted(CANONICAL_UNIT_ROLES)
        },
        "excluded_group_ids": sorted(
            str(item.get("id"))
            for item in payload.get("exclusions", [])
            if isinstance(item, dict) and item.get("id")
        ),
    }


def build_phase4_external_identity_evidence(
    catalog: dict[str, Any] | None = None,
    *,
    evidence_path: Path = PHASE4_CNOOC_UNIT_EVIDENCE_PATH,
) -> dict[str, dict[str, Any]]:
    """Map exact official employer records to formal catalog units.

    The CNOOC group portal evidence contains exact employer display names and
    official job application URLs for 16 units.  It proves an official portal
    employer record, not an independent unit recruitment website or a legal
    registration extract.  Keep this evidence separate from the catalog so
    abbreviated or ambiguous names remain pending instead of being guessed.
    """
    if not evidence_path.is_file():
        return {}
    try:
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    observed_units = evidence.get("units")
    if not isinstance(observed_units, list):
        return {}

    def normalized(value: Any) -> str:
        return "".join(str(value or "").lower().split())

    catalog_units = {
        normalized(unit["name"]): unit
        for unit in three_oil_units(catalog or _cached_catalog(str(CATALOG_PATH)))
    }
    try:
        display_evidence_relpath = evidence_path.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        display_evidence_relpath = str(evidence_path)
    full_job_evidence_relpath = str(
        evidence.get("full_job_evidence_file")
        or "docs/current/phase4-cnooc-unit-job-evidence-2026-10-19.json"
    ).replace("\\", "/")
    full_job_evidence_path = PROJECT_ROOT / full_job_evidence_relpath
    full_job_evidence_sha256 = str(evidence.get("full_job_evidence_sha256") or "")
    full_job_evidence_actual_sha256 = _sha256_file(full_job_evidence_path)
    result: dict[str, dict[str, Any]] = {}
    for observed in observed_units:
        if not isinstance(observed, dict):
            continue
        observed_name = str(observed.get("canonical_name") or "").strip()
        unit = catalog_units.get(normalized(observed_name))
        if unit is None or unit["group_id"] != "cnooc":
            continue
        hash_matches = bool(
            full_job_evidence_sha256
            and full_job_evidence_actual_sha256
            and full_job_evidence_sha256.lower() == full_job_evidence_actual_sha256.lower()
        )
        result[str(unit["id"])] = {
            "status": "verified" if hash_matches else "pending",
            "basis": "official_portal_employer_record",
            "evidence_scope": "official_group_portal_active_unit_job_cards",
            "display_evidence_file": display_evidence_relpath,
            "display_evidence_sha256": _sha256_file(evidence_path),
            "job_evidence_file": full_job_evidence_relpath,
            "job_evidence_sha256": full_job_evidence_sha256,
            "job_evidence_actual_sha256": full_job_evidence_actual_sha256,
            "job_evidence_hash_matches": hash_matches,
            "job_evidence_file_present": full_job_evidence_path.is_file(),
            "observed_name": observed_name,
            "observed_job_count": int(observed.get("job_count") or 0),
            "official_application_url": str(
                observed.get("first_official_application_url") or ""
            ),
            "official_application_url_count": int(
                observed.get("unique_official_application_url_count") or 0
            ),
            "verified_at": str(evidence.get("verified_at") or "2026-10-19"),
        }
    return result


def build_three_oil_dns_audit(
    catalog: dict[str, Any] | None = None,
    *,
    organization_registry: dict[str, Any] | None = None,
    resolve: Callable[[str], bool] | None = None,
    observed_at: str | None = None,
) -> dict[str, Any]:
    """Check registered entry hosts without treating DNS as ownership or vacancies."""
    payload = catalog or _cached_catalog(str(CATALOG_PATH))
    if organization_registry is None:
        from job_hub.organizations import load_organization_registry

        organization_registry = load_organization_registry()
    unit_ids = {unit["id"] for unit in three_oil_units(payload)}
    hosts: dict[str, dict[str, set[str]]] = {}
    declared_domains: set[str] = set()
    for organization in organization_registry.get("organizations", []):
        if organization.get("id") not in unit_ids:
            continue
        organization_id = str(organization["id"])
        for domain in organization.get("official_domains", []):
            host = str(domain).lower().rstrip(".")
            declared_domains.add(host)
            entry = hosts.setdefault(host, {"organizations": set(), "channels": set(), "claims": set()})
            entry["organizations"].add(organization_id)
            entry["claims"].add(organization_id)
        for channel in organization.get("channels", []):
            for url in [channel.get("official_url"), *channel.get("backup_urls", [])]:
                host = (urlsplit(str(url or "")).hostname or "").lower().rstrip(".")
                if not host:
                    continue
                entry = hosts.setdefault(host, {"organizations": set(), "channels": set(), "claims": set()})
                entry["organizations"].add(organization_id)
                entry["channels"].add(str(channel.get("id") or ""))

    def default_resolve(host: str) -> bool:
        return bool(socket.getaddrinfo(host, None))

    resolver = resolve or default_resolve
    rows = []
    for host, entry in sorted(hosts.items()):
        try:
            resolved = bool(resolver(host))
            status = "resolved" if resolved else "unresolved"
        except (OSError, ValueError):
            status = "unresolved"
        rows.append(
            {
                "host": host,
                "dns_status": status,
                "declared_official_domain": host in declared_domains,
                "organization_ids": sorted(entry["organizations"]),
                "declaring_organization_ids": sorted(entry["claims"]),
                "channel_ids": sorted(entry["channels"] - {""}),
            }
        )
    return {
        "version": "three-oil-dns-audit-v1",
        "observed_at": observed_at or datetime.now(timezone.utc).isoformat(),
        "declared_domain_count": len(declared_domains),
        "entry_host_count": len(rows),
        "unresolved_declared_domains": [
            row["host"] for row in rows
            if row["declared_official_domain"] and row["dns_status"] == "unresolved"
        ],
        "items": rows,
        "interpretation": (
            "DNS 只反映本次运行环境的解析结果；解析成功不证明官网归属或岗位存在，"
            "解析失败不证明单位没有招聘。共享集团 URL 不等于下属单位专属入口。"
        ),
    }


def build_three_oil_catalog_audit(
    catalog: dict[str, Any] | None = None,
    *,
    organization_registry: dict[str, Any] | None = None,
    source_records: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
    job_records: Iterable[dict[str, Any]] | None = None,
    external_identity_evidence: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compare the scope catalog with individually reviewed runtime entries.

    A catalog row is an identity/scope assertion.  It is only considered
    ``automation_ready`` when the matching organization row has an
    automation-ready channel bound to a registered and enabled source.  This
    deliberately keeps a shared group portal from implying that every
    subsidiary has an independently verified adapter.
    """
    payload = catalog or _cached_catalog(str(CATALOG_PATH))
    external_identity_evidence = external_identity_evidence or {}
    if organization_registry is None:
        from job_hub.organizations import load_organization_registry

        organization_registry = load_organization_registry()
    if source_records is None:
        from job_hub.organizations import _registered_source_ids
        from job_hub.sources import load_source_registries

        source_paths = [
            str(PROJECT_ROOT / "data" / "sources.json"),
            str(PROJECT_ROOT / "data" / "provincial_sources.json"),
        ]
        source_records = load_source_registries(source_paths)
        registered_source_ids = {
            str(source["id"])
            for source in source_records
            if source.get("id")
        }
        # Keep the helper import above intentionally local; the public audit
        # contract still accepts an explicit source list for deterministic tests.
        registered_source_ids |= _registered_source_ids()
    else:
        registered_source_ids = {
            str(source["id"])
            for source in source_records
            if source.get("id")
        }

    # A shared group platform is only an entry point.  This second index is
    # built from actual parsed job rows and is intentionally independent of
    # the channel URL duplication heuristic above.  It lets operators answer
    # the important question: did an official row explicitly name this unit?
    job_binding_by_unit = _build_job_binding_index(
        payload,
        organization_registry,
        job_records or (),
    )

    source_by_id = {
        str(source["id"]): source
        for source in source_records
        if source.get("id")
    }
    organizations = list(organization_registry.get("organizations", []))
    org_by_id = {str(item.get("id")): item for item in organizations if item.get("id")}
    channel_key_counts = Counter(
        (
            str(channel.get("source_id") or "").strip(),
            str(channel.get("official_url") or "").strip(),
        )
        for organization in organizations
        for channel in organization.get("channels", [])
        if isinstance(channel, dict)
    )

    def normalized(value: Any) -> str:
        return "".join(str(value or "").lower().split())

    def find_organization(unit: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        unit_id = str(unit["id"])
        direct = org_by_id.get(unit_id)
        if direct is not None:
            return direct, "id"
        expected = {normalized(unit.get("name"))}
        for candidate in organizations:
            names = {candidate.get("canonical_name"), *candidate.get("aliases", [])}
            if expected & {normalized(name) for name in names}:
                return candidate, "name_or_alias"
        return None, "missing"

    rows: list[dict[str, Any]] = []
    for unit in three_oil_units(payload):
        organization, match_method = find_organization(unit)
        channels = list(organization.get("channels", [])) if organization else []
        identity_evidence = dict(
            external_identity_evidence.get(unit["id"], {})
            or {"status": "pending"}
        )
        job_binding = job_binding_by_unit.get(unit["id"], {})
        official_job_source_ids = [
            source_id
            for source_id in job_binding.get("source_ids", [])
            if source_id in OFFICIAL_THREE_OIL_IDENTITY_SOURCE_IDS
            and OFFICIAL_THREE_OIL_SOURCE_GROUPS.get(source_id) == unit["group_id"]
        ]
        if (
            str(identity_evidence.get("status") or "") != "verified"
            and str(job_binding.get("status") or "") == "verified"
            and official_job_source_ids
        ):
            identity_evidence = {
                "status": "verified",
                "basis": "official_job_detail_employer_record",
                "evidence_scope": "official_job_detail_employer_name",
                "source_ids": official_job_source_ids,
                "evidence_urls": list(job_binding.get("evidence_urls", [])),
                "matched_job_count": int(job_binding.get("match_count", 0)),
                "independent_unit_recruitment_portal_proven": False,
            }
        channel_rows: list[dict[str, Any]] = []
        for channel in channels:
            source_id = str(channel.get("source_id") or "") or None
            source = source_by_id.get(source_id) if source_id else None
            channel_rows.append(
                {
                    "id": channel.get("id"),
                    "channel_type": channel.get("channel_type"),
                    "verification_status": channel.get("verification_status"),
                    "official_url": channel.get("official_url"),
                    "backup_urls": list(channel.get("backup_urls") or []),
                    "source_id": source_id,
                    "source_registered": bool(source_id and source_id in registered_source_ids),
                    "source_enabled": bool(source.get("enabled")) if source else None,
                    "source_type": source.get("source_type") if source else None,
                    "binding_scope": (
                        "unbound"
                        if not str(channel.get("official_url") or "").strip()
                        else (
                            "unit_scoped"
                            if channel_key_counts[
                                (
                                    str(channel.get("source_id") or "").strip(),
                                    str(channel.get("official_url") or "").strip(),
                                )
                            ]
                            <= 1
                            else "shared_group_entry"
                        )
                    ),
                }
            )
        eligible = [
            channel
            for channel in channel_rows
            if channel["verification_status"] == "automation_ready"
            and channel["source_registered"]
            and channel["source_enabled"]
        ]
        confirmed_channels = [
            channel for channel in channel_rows
            if channel["verification_status"] in {"official_confirmed", "automation_ready"}
        ]
        rows.append(
            {
                "catalog_id": unit["id"],
                "catalog_name": unit["name"],
                "group_id": unit["group_id"],
                "group_name": unit["group_name"],
                "role": unit["role"],
                "organization_role": unit["organization_role"],
                "registry_id": organization.get("id") if organization else None,
                "registry_name": organization.get("canonical_name") if organization else None,
                "registry_match": match_method,
                "name_exact_match": bool(
                    organization
                    and normalized(unit["name"]) == normalized(organization.get("canonical_name"))
                ),
                "name_evidence_status": (
                    "organization_registry_exact_match"
                    if organization
                    and normalized(unit["name"]) == normalized(organization.get("canonical_name"))
                    else "manual_name_review_required"
                ),
                "external_identity_evidence": identity_evidence,
                "channels": channel_rows,
                "official_channel_count": len(channel_rows),
                "automation_ready_enabled": bool(eligible),
                "automation_ready_source_ids": [channel["source_id"] for channel in eligible],
                "unit_scoped_recruitment_entry_count": sum(
                    1
                    for channel in confirmed_channels
                    if channel["channel_type"]
                    in {
                        "campus_recruitment",
                        "official_announcement",
                        "official_attachment",
                        "authorized_platform",
                    }
                    and channel["binding_scope"] == "unit_scoped"
                ),
                "shared_recruitment_entry_count": sum(
                    1
                    for channel in confirmed_channels
                    if channel["channel_type"]
                    in {
                        "campus_recruitment",
                        "official_announcement",
                        "official_attachment",
                        "authorized_platform",
                    }
                    and channel["binding_scope"] == "shared_group_entry"
                ),
                "unbound_recruitment_entry_count": sum(
                    1
                    for channel in confirmed_channels
                    if channel["channel_type"]
                    in {
                        "campus_recruitment",
                        "official_announcement",
                        "official_attachment",
                        "authorized_platform",
                    }
                    and channel["binding_scope"] == "unbound"
                ),
                "job_detail_employer_binding_count": int(
                    job_binding_by_unit.get(unit["id"], {}).get("match_count", 0)
                ),
                "job_detail_employer_binding_status": str(
                    job_binding_by_unit.get(unit["id"], {}).get("status", "not_provided")
                ),
                "job_detail_employer_binding_source_ids": list(
                    job_binding_by_unit.get(unit["id"], {}).get("source_ids", [])
                ),
                "fallback_entry_coverage": {
                    "unit_homepage": any(
                        item["channel_type"] == "official_homepage"
                        for item in confirmed_channels
                    ),
                    "official_announcement": any(
                        item["channel_type"] == "official_announcement"
                        for item in confirmed_channels
                    ),
                    "official_attachment": any(
                        item["channel_type"] == "official_attachment"
                        for item in confirmed_channels
                    ),
                    "official_announcement_or_attachment": any(
                        item["channel_type"] in {
                            "official_announcement",
                            "official_attachment",
                        }
                        for item in confirmed_channels
                    ),
                    "authorized_platform": any(
                        item["channel_type"] == "authorized_platform"
                        for item in confirmed_channels
                    ),
                    "group_or_shared_recruitment_platform": any(
                        item["channel_type"] == "campus_recruitment"
                        for item in confirmed_channels
                    ),
                },
            }
        )

    missing = [row for row in rows if row["registry_match"] == "missing"]
    name_unverified = [row for row in rows if not row["name_exact_match"]]
    mapped = [row for row in rows if row["registry_match"] != "missing"]
    ready = [row for row in rows if row["automation_ready_enabled"]]
    external_identity_verified = [
        row
        for row in rows
        if str(row["external_identity_evidence"].get("status") or "") == "verified"
    ]
    external_identity_pending = [
        row for row in rows if row not in external_identity_verified
    ]
    fallback_summary: dict[str, dict[str, int]] = {}
    for group_id in sorted(GROUP_IDS):
        group_rows = [row for row in rows if row["group_id"] == group_id]
        fallback_summary[group_id] = {
            "unit_count": len(group_rows),
            "unit_homepage_count": sum(
                1 for row in group_rows if row["fallback_entry_coverage"]["unit_homepage"]
            ),
            "official_announcement_or_attachment_count": sum(
                1
                for row in group_rows
                if row["fallback_entry_coverage"]["official_announcement_or_attachment"]
            ),
            "authorized_platform_count": sum(
                1
                for row in group_rows
                if row["fallback_entry_coverage"]["authorized_platform"]
            ),
            "shared_recruitment_platform_count": sum(
                1
                for row in group_rows
                if row["fallback_entry_coverage"]["group_or_shared_recruitment_platform"]
            ),
            "unit_scoped_recruitment_entry_count": sum(
                row["unit_scoped_recruitment_entry_count"] for row in group_rows
            ),
            "shared_recruitment_entry_count": sum(
                row["shared_recruitment_entry_count"] for row in group_rows
            ),
            "unbound_recruitment_entry_count": sum(
                row["unbound_recruitment_entry_count"] for row in group_rows
            ),
            "job_detail_employer_binding_unit_count": sum(
                row["job_detail_employer_binding_status"] == "verified"
                for row in group_rows
            ),
            "job_detail_employer_binding_count": sum(
                row["job_detail_employer_binding_count"] for row in group_rows
            ),
            "automation_ready_enabled_count": sum(
                1 for row in group_rows if row["automation_ready_enabled"]
            ),
        }
    fallback_tasks: list[dict[str, Any]] = []
    for row in rows:
        channels = [
            channel
            for channel in row["channels"]
            if channel.get("id")
        ]
        candidate_urls = sorted(
            {
                url
                for channel in channels
                for url in (
                    [str(channel.get("official_url") or "").strip()]
                    + [str(item).strip() for item in channel.get("backup_urls", [])]
                )
                if url
            }
        )
        for requirement in FALLBACK_CHANNEL_REQUIREMENTS:
            coverage_key = requirement["coverage_key"]
            if row["fallback_entry_coverage"].get(coverage_key):
                continue
            fallback_tasks.append(
                {
                    "task_id": (
                        f"{row['catalog_id']}:{requirement['channel_type']}"
                    ),
                    "group_id": row["group_id"],
                    "group_name": row["group_name"],
                    "catalog_id": row["catalog_id"],
                    "catalog_name": row["catalog_name"],
                    "registry_id": row["registry_id"],
                    "required_channel_type": requirement["channel_type"],
                    "status": "missing",
                    "candidate_entry_urls": candidate_urls,
                    "evidence_required": requirement["evidence_required"],
                    "next_action": (
                        "从该单位官方站点和集团公告页定位入口；核验通过后"
                        "登记 organization_registry，再创建禁用状态适配器。"
                    ),
                }
            )
    return {
        "catalog_summary": three_oil_catalog_summary(payload),
        "registry_organization_count": len(organizations),
        "registered_source_count": len(registered_source_ids),
        "mapped_unit_count": len(mapped),
        "missing_registry_unit_count": len(missing),
        "name_unverified_unit_count": len(name_unverified),
        "name_unverified_unit_ids": [row["catalog_id"] for row in name_unverified],
        "name_match_semantics": (
            "name_exact_match and name_evidence_status only describe the normalized "
            "catalog-to-organization_registry mapping; they do not prove an external "
            "legal-registration or independently archived official-name review. "
            "This mapping does not prove that review."
        ),
        "external_identity_evidence_review": {
            "status": (
                "verified"
                if not external_identity_pending
                else "partial"
                if external_identity_verified
                else "pending"
            ),
            "verified_unit_count": len(external_identity_verified),
            "pending_unit_count": len(external_identity_pending),
            "verified_unit_ids": [
                row["catalog_id"] for row in external_identity_verified
            ],
            "required_evidence": [
                "official group or unit page",
                "official recruitment notice or portal employer record",
                "legal-registration record when the display name is abbreviated or ambiguous",
            ],
            "student_publication_effect": "none",
        },
        "automation_ready_enabled_unit_count": len(ready),
        "missing_registry_unit_ids": [row["catalog_id"] for row in missing],
        "automation_ready_enabled_unit_ids": [row["catalog_id"] for row in ready],
        "fallback_entry_summary_by_group": fallback_summary,
        "fallback_tasks": fallback_tasks,
        "fallback_task_summary": {
            "task_count": len(fallback_tasks),
            "by_group": {
                group_id: sum(1 for task in fallback_tasks if task["group_id"] == group_id)
                for group_id in sorted(GROUP_IDS)
            },
            "by_channel_type": {
                channel_type: sum(
                    1
                    for task in fallback_tasks
                    if task["required_channel_type"] == channel_type
                )
                for channel_type in (
                    "official_announcement",
                    "official_attachment",
                    "authorized_platform",
                )
            },
            "scope_note": (
                "任务缺口只表示注册表没有该类已核验入口；候选 URL 仍是待核验线索，"
                "不会自动创建来源、抓取岗位或进入学生端。"
            ),
        },
        "fallback_entry_policy": {
            "unit_homepage": "只能证明单位身份，不能直接生成岗位",
            "official_announcement_or_attachment": "可作为岗位解析入口，仍需岗位级字段和当前性核验",
            "authorized_platform": "必须有单位官方公告授权，第三方转载不计入",
            "shared_recruitment_platform": "需从岗位详情的用人单位字段归属下属单位，不等于单位专属入口",
            "job_detail_employer_binding": (
                "只有官方岗位详情/表格的用人单位字段与目录单位名称或已审核别名相符，"
                "且保留来源 ID 和官方详情 URL，才计入岗位级单位绑定；未提供岗位快照时状态为 not_provided。"
            ),
        },
        "job_detail_binding_summary": {
            "provided": job_records is not None,
            "verified_unit_count": sum(
                row["job_detail_employer_binding_status"] == "verified" for row in rows
            ),
            "matched_job_count": sum(
                row["job_detail_employer_binding_count"] for row in rows
            ),
            "scope_note": (
                "该指标只证明岗位行的用人单位归属，不等于单位拥有独立招聘网站；"
                "共享集团平台仍需保留 shared_group_entry 标记。"
            ),
        },
        "items": rows,
        "gate": {
            "catalog_complete": not missing,
            "runtime_coverage_complete": len(ready) == len(rows),
            "note": (
                "catalog_complete 只表示每个目录单位已映射到组织注册表；"
                "runtime_coverage_complete 还要求每个单位有已启用且逐入口核验的自动来源。"
            ),
        },
    }


def _binding_text(value: Any) -> str:
    """Normalize employer labels for exact/alias comparison only."""

    return re.sub(r"[\s\u3000，,。；;（）()\[\]【】]", "", str(value or "")).casefold()


_THREE_OIL_GROUP_PREFIXES: dict[str, tuple[str, ...]] = {
    "cnpc": ("中国石油-", "中国石油：", "中国石油:", "中国石油集团-"),
    "sinopec": ("中国石化-", "中国石化：", "中国石化:", "中国石化集团-"),
    "cnooc": ("中国海油-", "中国海油：", "中国海油:", "中国海洋石油-"),
}


def _binding_variants(value: Any, group_id: str) -> set[str]:
    """Return conservative normalized forms of an official employer label.

    Group recruitment portals commonly prepend a display-only group label to
    the legal employer (for example ``中国海油-中海油研究总院``).  The prefix
    is not a separate employer and must not cause a row to bind to the group
    or to every subsidiary.  Strip only the reviewed prefixes for the row's
    catalog group; all other matching remains exact against canonical names or
    aliases.
    """

    normalized = _binding_text(value)
    if not normalized:
        return set()
    variants = {normalized}
    for suffix in ("有限责任公司", "股份有限公司", "有限公司", "分公司", "公司"):
        if normalized.endswith(suffix) and len(normalized) > len(suffix) + 3:
            variants.add(normalized[: -len(suffix)])
    for prefix in _THREE_OIL_GROUP_PREFIXES.get(str(group_id), ()):
        normalized_prefix = _binding_text(prefix)
        if normalized.startswith(normalized_prefix) and len(normalized) > len(normalized_prefix):
            variants.add(normalized[len(normalized_prefix) :])
    return variants


def _job_employer_values(job: dict[str, Any]) -> tuple[str, ...]:
    values: list[str] = []
    for key in ("employer", "employer_hint", "organization_name", "用人单位", "招聘单位"):
        value = job.get(key)
        if value:
            values.append(str(value))
    evidence = job.get("field_evidence")
    if isinstance(evidence, dict):
        for key, value in evidence.items():
            if str(key).casefold() in {
                "用人单位",
                "招聘单位",
                "招录机关",
                "单位名称",
                "employer",
            } and value:
                values.append(str(value))
    return tuple(dict.fromkeys(_binding_text(value) for value in values if _binding_text(value)))


def _build_job_binding_index(
    catalog: dict[str, Any],
    organization_registry: dict[str, Any],
    job_records: Iterable[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Match official job-row employer evidence to catalog units.

    Matching is deliberately exact against canonical names and reviewed
    aliases.  A generic group label such as ``中国石油`` is not allowed to
    claim every subsidiary; the detail row must name the subordinate unit.
    """

    registry_by_id = {
        str(item.get("id")): item
        for item in organization_registry.get("organizations", [])
        if isinstance(item, dict) and item.get("id")
    }
    index: dict[str, dict[str, Any]] = {
        unit["id"]: {
            "status": "not_provided",
            "match_count": 0,
            "source_ids": [],
            "evidence_urls": [],
        }
        for unit in three_oil_units(catalog)
    }
    for raw_job in job_records:
        if not isinstance(raw_job, dict):
            continue
        employer_values = set(_job_employer_values(raw_job))
        if not employer_values:
            continue
        evidence = raw_job.get("field_evidence")
        scope = str(evidence.get("evidence_scope") or "") if isinstance(evidence, dict) else ""
        if not scope:
            continue
        source_id = str(raw_job.get("source_id") or "").strip()
        evidence_url = str(
            raw_job.get("official_evidence_url") or raw_job.get("source_url") or ""
        ).strip()
        parsed_evidence_url = urlsplit(evidence_url)
        if (
            not scope
            or parsed_evidence_url.scheme not in {"http", "https"}
            or not parsed_evidence_url.netloc
        ):
            continue
        for unit in three_oil_units(catalog):
            organization = registry_by_id.get(unit["id"], {})
            accepted_names: set[str] = set()
            for name in (
                unit["name"],
                organization.get("canonical_name"),
                *(organization.get("aliases", []) or []),
            ):
                accepted_names.update(_binding_variants(name, unit["group_id"]))
            if not any(
                accepted_names.intersection(_binding_variants(value, unit["group_id"]))
                for value in employer_values
            ):
                continue
            target = index[unit["id"]]
            target["status"] = "verified"
            target["match_count"] += 1
            if source_id and source_id not in target["source_ids"]:
                target["source_ids"].append(source_id)
            if evidence_url:
                if evidence_url not in target["evidence_urls"]:
                    target["evidence_urls"].append(evidence_url)
    for item in index.values():
        item["source_ids"].sort()
        item["evidence_urls"].sort()
    return index


__all__ = [
    "CATALOG_PATH",
    "GROUP_IDS",
    "load_three_oil_catalog",
    "validate_three_oil_catalog",
    "three_oil_units",
    "three_oil_catalog_summary",
    "build_three_oil_catalog_audit",
    "FALLBACK_CHANNEL_REQUIREMENTS",
]
