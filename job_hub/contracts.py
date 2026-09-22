"""Versioned data contracts for official employment-information records.

The public site is intentionally small, but its source, employer, evidence and
lead records must remain explainable as the source network grows.  This module
keeps those validation rules independent from collection and presentation code.
It performs no network I/O and does not decide whether a vacancy is relevant.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any
from urllib.parse import urlparse


class ContractValidationError(ValueError):
    """Raised when a persisted or configured record violates its data contract."""


SOURCE_TYPES = frozenset(
    {
        "manual",
        "rss",
        "json",
        "cupb_career",
        "cas_job_board",
        "successfactors_search",
        "mokahr_search",
        "zhaopin_campus",
        "mnr_recruitment",
        "slb_coveo_search",
        "html_notice",
        "landing_page",
    }
)
SOURCE_TIERS = frozenset({"A", "B"})

# Keep the five provincial roles in the data-contract layer so the target
# matrix and the validation ledger cannot silently drift apart.
PROVINCIAL_SOURCE_TARGET_ROLES = (
    "human_resources_or_exam",
    "natural_resources",
    "geology_bureau_or_institute",
    "public_institution_recruitment",
    "civil_service",
)
SOURCE_VALIDATION_STAGES = frozenset(
    {
        "official_identity_verified",
        "access_policy_verified",
        "adapter_fixture_verified",
        "entry_checked_no_recruitment_sample",
        "access_limited",
        "retired",
    }
)

# Discovery channels are deliberately a separate contract from official
# sources.  They can help an operator find a notice, but they are never a
# student-facing evidence source.
DISCOVERY_SOURCE_TYPES = frozenset(
    {
        "aggregator",
        "vertical_job_board",
        "university_aggregation",
        "official_account",
        "search_index",
        "manual_tip",
    }
)
DISCOVERY_SOURCE_STATUSES = frozenset(
    {"registered", "checked", "access_limited", "paused", "retired"}
)
DISCOVERY_ACCESS_MODES = frozenset(
    {"manual", "authorized_api", "rss", "public_page"}
)
OFFICIAL_DOMAIN_STATUSES = frozenset(
    {
        "unverified",
        "registered_source_match",
        "manual_review_required",
        "manual_review_approved",
        "source_domain_mismatch",
    }
)

ORGANIZATION_ROLES = frozenset(
    {
        "group",
        "upstream_operator",
        "research_institute",
        "internal_technical_service",
        "independent_technical_service",
        "international_technical_service",
        "pipeline_operator",
        "geology_survey",
        "geology_research",
        "mining_group",
        "university",
        "government_recruitment_system",
    }
)
ORGANIZATION_CHANNEL_TYPES = frozenset(
    {
        "official_homepage",
        "campus_recruitment",
        "social_recruitment",
        "official_announcement",
        "research_recruitment",
        "public_institution_recruitment",
    }
)
ORGANIZATION_CHANNEL_STATUSES = frozenset(
    {"automation_ready", "official_confirmed", "candidate", "blocked", "unlocated"}
)

# Phase 6 keeps the acquisition decision explicit for every national-energy
# entrance.  A registered official URL is not automatically a crawlable
# source, and a failed probe must never be represented as "no matching jobs".
NATIONAL_ACQUISITION_MODES = frozenset(
    {
        "official_homepage_manual",
        "official_recruitment_portal",
        "public_dynamic_portal",
        "public_html_listing",
        "official_api",
        "official_attachment",
        "manual_verified_import",
        "unavailable",
    }
)
NATIONAL_RUNTIME_STATUSES = frozenset(
    {
        "verified_public",
        "accessible_structure_unverified",
        "source_registered_disabled",
        "access_limited",
        "manual_only",
        "not_yet_verified",
        "blocked",
    }
)
NATIONAL_SCAN_CONCLUSIONS = frozenset(
    {
        "not_scanned",
        "scan_success_no_match",
        "source_unavailable",
        "structure_needs_adapter",
        "adapter_probe_failed",
        "manual_review_required",
        "candidate_source",
    }
)
NATIONAL_FIELD_KEYS = (
    "title",
    "employer",
    "degree",
    "major",
    "location",
    "deadline",
    "application_url",
)
NATIONAL_PROBE_RESULTS = frozenset(
    {
        "verified_public_api",
        "adapter_ready_probe_failed",
        "source_unavailable",
        "manual_review_required",
    }
)
NATIONAL_ENTRY_ROLES = frozenset(
    {"primary_recruitment", "backup_recruitment", "official_homepage"}
)
NATIONAL_ENTRY_PROBE_CLASSES = frozenset(
    {
        "accessible_html",
        "accessible_dynamic_shell",
        "access_policy_block",
        "robots_blocked",
        "source_unavailable",
        "soft_not_found",
        "redirected_outside_entry",
        "non_html",
    }
)
NATIONAL_ENTRY_PROBE_STATUSES = frozenset(
    {
        "accessible_structure_unverified",
        "access_limited",
        "source_unavailable",
        "manual_review_required",
    }
)
NATIONAL_ENTRY_TRANSPORT_MODES = frozenset({"environment", "direct", "unknown"})

ARTIFACT_KINDS = frozenset(
    {
        "announcement_attachment",
        "position_table",
        "application_material",
        "supporting_document",
    }
)
ARTIFACT_EXTRACTION_STATUSES = frozenset(
    {"registered", "downloaded", "extracted", "failed", "skipped"}
)

ARTIFACT_CANDIDATE_REVIEW_STATUSES = frozenset(
    {"needs_review", "official_content_verified", "published", "rejected", "expired"}
)
ARTIFACT_CANDIDATE_REVIEW_TRANSITIONS = {
    "needs_review": frozenset(
        {"needs_review", "official_content_verified", "rejected", "expired"}
    ),
    "official_content_verified": frozenset(
        {"official_content_verified", "published", "rejected", "expired"}
    ),
    "published": frozenset({"published"}),
    "rejected": frozenset({"rejected"}),
    "expired": frozenset({"expired"}),
}

EVIDENCE_TYPES = frozenset(
    {"official_page", "official_record", "attachment", "field_excerpt"}
)
EVIDENCE_VERIFICATION_STATUSES = frozenset({"pending", "verified", "rejected"})
OFFICIAL_EVIDENCE_TYPES = frozenset({"official_page", "official_record"})

CANDIDATE_LEAD_STATUSES = frozenset(
    {
        "candidate",
        "official_url_found",
        "official_content_verified",
        "need_review",
        "published",
        "rejected",
        "expired",
    }
)
CANDIDATE_LEAD_TRANSITIONS = {
    "candidate": frozenset(
        {"candidate", "official_url_found", "need_review", "rejected", "expired"}
    ),
    "official_url_found": frozenset(
        {
            "official_url_found",
            "official_content_verified",
            "need_review",
            "rejected",
            "expired",
        }
    ),
    "need_review": frozenset(
        {
            "need_review",
            "candidate",
            "official_url_found",
            "official_content_verified",
            "rejected",
            "expired",
        }
    ),
    "official_content_verified": frozenset(
        {"official_content_verified", "published", "need_review", "rejected", "expired"}
    ),
    "published": frozenset({"published"}),
    "rejected": frozenset({"rejected"}),
    "expired": frozenset({"expired"}),
}

_DOMAIN_PATTERN = re.compile(
    r"(?=^.{1,253}$)^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$",
    re.IGNORECASE,
)
_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$", re.IGNORECASE)


def is_http_url(value: Any) -> bool:
    """Return whether ``value`` is a complete HTTP(S) URL suitable as evidence."""
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text or any(character.isspace() for character in text):
        return False
    parsed = urlparse(text)
    return parsed.scheme in {"http", "https"} and bool(parsed.hostname)


def validate_http_url(value: Any, field_name: str) -> str:
    text = _required_text(value, field_name)
    if not is_http_url(text):
        raise ContractValidationError(f"{field_name} must be a complete HTTP(S) URL")
    return text


def validate_source_registry(payload: Any) -> list[dict[str, Any]]:
    """Validate and normalize one official-source registry without network access."""
    if not isinstance(payload, list):
        raise ContractValidationError("The source registry must be a JSON list")
    normalized: list[dict[str, Any]] = []
    source_ids: set[str] = set()
    for index, item in enumerate(payload):
        source = validate_source_record(item, context=f"Source at index {index}")
        source_id = source["id"]
        if source_id in source_ids:
            raise ContractValidationError(f"Duplicate source id: {source_id}")
        source_ids.add(source_id)
        normalized.append(source)
    return normalized


def validate_discovery_source_registry(payload: Any) -> dict[str, Any]:
    """Validate private discovery channels without treating them as evidence.

    A discovery source is intentionally weaker than an official source.  The
    contract therefore requires an explicit ``private_discovery_only`` policy
    and does not allow these records to be loaded into the collector registry.
    """
    if not isinstance(payload, Mapping):
        raise ContractValidationError("discovery source registry must be an object")
    version = payload.get("version", 1)
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ContractValidationError(
            "discovery source registry version must be a positive integer"
        )
    description = payload.get("description", "")
    if description is not None and not isinstance(description, str):
        raise ContractValidationError("discovery source registry description must be text")
    records = payload.get("sources")
    if not isinstance(records, list) or not records:
        raise ContractValidationError(
            "discovery source registry sources must be a non-empty list"
        )

    normalized: list[dict[str, Any]] = []
    source_ids: set[str] = set()
    for index, value in enumerate(records):
        context = f"Discovery source at index {index}"
        source = _mapping_copy(value, context)
        for field_name in (
            "id",
            "name",
            "source_type",
            "homepage_url",
            "access_mode",
            "status",
            "scope",
            "publication_policy",
            "last_checked_on",
        ):
            if field_name not in source:
                raise ContractValidationError(f"{context} is missing: {field_name}")
        source_id = _required_text(source["id"], f"{context} id")
        if source_id in source_ids:
            raise ContractValidationError(f"Duplicate discovery source id: {source_id}")
        source_ids.add(source_id)
        source["id"] = source_id
        source["name"] = _required_text(source["name"], f"{context} name")
        source["source_type"] = _required_text(
            source["source_type"], f"{context} source_type"
        )
        if source["source_type"] not in DISCOVERY_SOURCE_TYPES:
            raise ContractValidationError(
                f"{context} source_type is unsupported: {source['source_type']}"
            )
        source["homepage_url"] = validate_http_url(
            source["homepage_url"], f"{context} homepage_url"
        )
        source["access_mode"] = _required_text(
            source["access_mode"], f"{context} access_mode"
        )
        if source["access_mode"] not in DISCOVERY_ACCESS_MODES:
            raise ContractValidationError(
                f"{context} access_mode is unsupported: {source['access_mode']}"
            )
        source["status"] = _required_text(source["status"], f"{context} status")
        if source["status"] not in DISCOVERY_SOURCE_STATUSES:
            raise ContractValidationError(
                f"{context} status is unsupported: {source['status']}"
            )
        source["scope"] = _required_text(source["scope"], f"{context} scope")
        source["publication_policy"] = _required_text(
            source["publication_policy"], f"{context} publication_policy"
        )
        if source["publication_policy"] != "private_discovery_only":
            raise ContractValidationError(
                f"{context} publication_policy must be private_discovery_only"
            )
        source["last_checked_on"] = _validate_iso_date(
            source["last_checked_on"], f"{context} last_checked_on"
        )
        allowlist = source.get("official_domain_allowlist", [])
        if not isinstance(allowlist, list):
            raise ContractValidationError(
                f"{context} official_domain_allowlist must be a list"
            )
        source["official_domain_allowlist"] = (
            _validate_domains(allowlist, f"{context} official_domain_allowlist")
            if allowlist
            else []
        )
        source["notes"] = _optional_text(source.get("notes")) or ""
        source["verification_url"] = (
            validate_http_url(source["verification_url"], f"{context} verification_url")
            if source.get("verification_url")
            else None
        )
        normalized.append(source)

    return {
        "version": version,
        "description": (description or "").strip(),
        "sources": normalized,
    }


def validate_source_record(value: Any, *, context: str = "Source") -> dict[str, Any]:
    """Validate one source row and add the historical default fields explicitly."""
    source = _mapping_copy(value, context)
    for field_name in (
        "id",
        "name",
        "publisher",
        "homepage_url",
        "source_type",
        "category",
        "source_tier",
    ):
        _required_text(source.get(field_name), f"{context} {field_name}")

    source["id"] = _required_text(source["id"], f"{context} id")
    source["name"] = _required_text(source["name"], f"{context} name")
    source["publisher"] = _required_text(source["publisher"], f"{context} publisher")
    source["homepage_url"] = validate_http_url(
        source["homepage_url"], f"{context} homepage_url"
    )
    source["source_type"] = _required_text(
        source["source_type"], f"{context} source_type"
    )
    if source["source_type"] not in SOURCE_TYPES:
        raise ContractValidationError(
            f"{context} source_type is unsupported: {source['source_type']}"
        )
    source["category"] = _required_text(source["category"], f"{context} category")
    source["source_tier"] = _required_text(
        source["source_tier"], f"{context} source_tier"
    )
    if source["source_tier"] not in SOURCE_TIERS:
        raise ContractValidationError(
            f"{context} source_tier must be one of: {', '.join(sorted(SOURCE_TIERS))}"
        )

    config = source.get("config", {})
    if not isinstance(config, dict):
        raise ContractValidationError(f"{context} config must be an object")
    source["config"] = dict(config)
    enabled = source.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ContractValidationError(f"{context} enabled must be true or false")
    source["enabled"] = enabled
    return source


def validate_employer_registry(payload: Any) -> list[dict[str, Any]]:
    """Validate canonical employers, their aliases, domains and parent hierarchy."""
    if not isinstance(payload, list):
        raise ContractValidationError("employer_registry.json must contain a list")

    normalized: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    for index, value in enumerate(payload):
        employer = _mapping_copy(value, f"Employer at index {index}")
        for field_name in (
            "id",
            "canonical_name",
            "parent_id",
            "parent_name",
            "category",
            "employer_type",
            "affiliation",
            "aliases",
            "official_domains",
        ):
            if field_name not in employer:
                raise ContractValidationError(
                    f"Employer at index {index} is missing: {field_name}"
                )
        employer_id = _required_text(employer["id"], f"Employer at index {index} id")
        if employer_id in by_id:
            raise ContractValidationError(f"Duplicate employer id: {employer_id}")
        employer["id"] = employer_id
        employer["canonical_name"] = _required_text(
            employer["canonical_name"], f"Employer {employer_id} canonical_name"
        )
        employer["category"] = _required_text(
            employer["category"], f"Employer {employer_id} category"
        )
        employer["employer_type"] = _required_text(
            employer["employer_type"], f"Employer {employer_id} employer_type"
        )
        employer["affiliation"] = _required_text(
            employer["affiliation"], f"Employer {employer_id} affiliation"
        )
        employer["parent_id"] = _optional_text(employer["parent_id"])
        employer["parent_name"] = _optional_text(employer["parent_name"])
        employer["aliases"] = _validate_nonempty_text_list(
            employer["aliases"], f"Employer {employer_id} aliases"
        )
        employer["official_domains"] = _validate_domains(
            employer["official_domains"], f"Employer {employer_id} official_domains"
        )
        by_id[employer_id] = employer
        normalized.append(employer)

    for employer in normalized:
        parent_id = employer["parent_id"]
        parent_name = employer["parent_name"]
        if parent_id is None:
            if parent_name is not None:
                raise ContractValidationError(
                    f"Employer {employer['id']} has parent_name without parent_id"
                )
            continue
        if parent_id == employer["id"]:
            raise ContractValidationError(f"Employer {employer['id']} cannot parent itself")
        parent = by_id.get(parent_id)
        if parent is None:
            raise ContractValidationError(
                f"Employer {employer['id']} references unknown parent_id: {parent_id}"
            )
        if parent_name != parent["canonical_name"]:
            raise ContractValidationError(
                f"Employer {employer['id']} parent_name must match parent canonical_name"
            )

    for employer in normalized:
        visited: set[str] = set()
        current = employer
        while current["parent_id"] is not None:
            parent_id = str(current["parent_id"])
            if parent_id in visited:
                raise ContractValidationError(
                    f"Employer hierarchy contains a cycle at {employer['id']}"
                )
            visited.add(parent_id)
            current = by_id[parent_id]
    return normalized


def validate_organization_registry(
    payload: Any,
    *,
    source_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Validate the hierarchical organization and official-entry matrix.

    This registry describes who an organization is and where its official
    recruitment/announcement entrances live.  It does not make an entrance
    crawlable: only ``automation_ready`` channels bound to a registered source
    may be considered by the worker.
    """
    if not isinstance(payload, dict):
        raise ContractValidationError("organization registry must be an object")
    version = payload.get("version", 1)
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ContractValidationError("organization registry version must be a positive integer")
    description = payload.get("description", "")
    if description is not None and not isinstance(description, str):
        raise ContractValidationError("organization registry description must be text")
    organizations = payload.get("organizations")
    if not isinstance(organizations, list) or not organizations:
        raise ContractValidationError(
            "organization registry organizations must be a non-empty list"
        )
    normalized: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    channel_ids: set[str] = set()
    known_source_ids = source_ids
    for index, value in enumerate(organizations):
        organization = _mapping_copy(
            value, f"Organization at index {index}"
        )
        for field_name in (
            "id",
            "canonical_name",
            "parent_id",
            "organization_role",
            "industry_path",
            "affiliation",
            "aliases",
            "official_domains",
            "channels",
        ):
            if field_name not in organization:
                raise ContractValidationError(
                    f"Organization at index {index} is missing: {field_name}"
                )
        organization_id = _required_text(
            organization["id"], f"Organization at index {index} id"
        )
        if organization_id in by_id:
            raise ContractValidationError(
                f"Duplicate organization id: {organization_id}"
            )
        organization["id"] = organization_id
        organization["canonical_name"] = _required_text(
            organization["canonical_name"],
            f"Organization {organization_id} canonical_name",
        )
        organization["parent_id"] = _optional_text(organization["parent_id"])
        organization["organization_role"] = _required_text(
            organization["organization_role"],
            f"Organization {organization_id} organization_role",
        )
        if organization["organization_role"] not in ORGANIZATION_ROLES:
            raise ContractValidationError(
                f"Organization {organization_id} has unsupported organization_role"
            )
        for field_name in ("industry_path", "affiliation"):
            organization[field_name] = _required_text(
                organization[field_name],
                f"Organization {organization_id} {field_name}",
            )
        organization["aliases"] = _validate_nonempty_text_list(
            organization["aliases"], f"Organization {organization_id} aliases"
        )
        organization["official_domains"] = _validate_domains(
            organization["official_domains"],
            f"Organization {organization_id} official_domains",
        )
        channels = organization["channels"]
        if not isinstance(channels, list) or not channels:
            raise ContractValidationError(
                f"Organization {organization_id} channels must be a non-empty list"
            )
        normalized_channels: list[dict[str, Any]] = []
        for channel_index, channel_value in enumerate(channels):
            channel = _mapping_copy(
                channel_value,
                f"Organization {organization_id} channel {channel_index}",
            )
            for field_name in (
                "id",
                "channel_type",
                "official_url",
                "backup_urls",
                "verification_status",
            ):
                if field_name not in channel:
                    raise ContractValidationError(
                        f"Organization {organization_id} channel {channel_index} "
                        f"is missing: {field_name}"
                    )
            channel_id = _required_text(
                channel["id"],
                f"Organization {organization_id} channel id",
            )
            if channel_id in channel_ids:
                raise ContractValidationError(f"Duplicate organization channel id: {channel_id}")
            channel_ids.add(channel_id)
            channel["channel_type"] = _required_text(
                channel["channel_type"],
                f"Organization {organization_id} channel_type",
            )
            if channel["channel_type"] not in ORGANIZATION_CHANNEL_TYPES:
                raise ContractValidationError(
                    f"Organization {organization_id} channel {channel_id} has unsupported channel_type"
                )
            channel["official_url"] = validate_http_url(
                channel["official_url"],
                f"Organization {organization_id} channel {channel_id} official_url",
            )
            backup_urls = channel["backup_urls"]
            if not isinstance(backup_urls, list):
                raise ContractValidationError(
                    f"Organization {organization_id} channel {channel_id} backup_urls must be a list"
                )
            channel["backup_urls"] = [
                validate_http_url(
                    item,
                    f"Organization {organization_id} channel {channel_id} backup_url",
                )
                for item in backup_urls
            ]
            channel["verification_status"] = _required_text(
                channel["verification_status"],
                f"Organization {organization_id} channel {channel_id} verification_status",
            )
            if channel["verification_status"] not in ORGANIZATION_CHANNEL_STATUSES:
                raise ContractValidationError(
                    f"Organization {organization_id} channel {channel_id} has unsupported verification_status"
                )
            source_id = _optional_text(channel.get("source_id"))
            if source_id and known_source_ids is not None and source_id not in known_source_ids:
                raise ContractValidationError(
                    f"Organization {organization_id} channel {channel_id} references unknown source_id: {source_id}"
                )
            if channel["verification_status"] == "automation_ready" and not source_id:
                raise ContractValidationError(
                    f"Organization {organization_id} automation_ready channel {channel_id} requires source_id"
                )
            is_primary = channel.get("is_primary", False)
            if not isinstance(is_primary, bool):
                raise ContractValidationError(
                    f"Organization {organization_id} channel {channel_id} "
                    "is_primary must be true or false"
                )
            channel["source_id"] = source_id
            channel["is_primary"] = is_primary
            channel["evidence_note"] = _optional_text(channel.get("evidence_note")) or ""
            normalized_channels.append(channel)
        primary_count = sum(
            1 for channel in normalized_channels if channel["is_primary"]
        )
        if primary_count > 1:
            raise ContractValidationError(
                f"Organization {organization_id} has more than one primary channel"
            )
        if primary_count == 0:
            normalized_channels[0]["is_primary"] = True
        organization["channels"] = normalized_channels
        organization["notes"] = _optional_text(organization.get("notes")) or ""
        by_id[organization_id] = organization
        normalized.append(organization)

    for organization in normalized:
        parent_id = organization["parent_id"]
        if parent_id is None:
            continue
        if parent_id == organization["id"]:
            raise ContractValidationError(
                f"Organization {organization['id']} cannot parent itself"
            )
        if parent_id not in by_id:
            raise ContractValidationError(
                f"Organization {organization['id']} references unknown parent_id: {parent_id}"
            )
        visited: set[str] = set()
        current_id = organization["id"]
        while current_id is not None:
            if current_id in visited:
                raise ContractValidationError(
                    f"Organization hierarchy contains a cycle at {organization['id']}"
                )
            visited.add(current_id)
            current_id = by_id[current_id]["parent_id"]

    return {
        "version": version,
        "description": (description or "").strip(),
        "organizations": normalized,
    }


def validate_national_source_matrix(
    payload: Any,
    *,
    source_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Validate the acquisition decision ledger for national-energy sources.

    The matrix is intentionally separate from ``sources.json``.  It records
    what an operator knows about an official entrance, while the source
    registry controls whether a collector may actually run.  This prevents a
    URL being silently promoted to an active crawler merely because it appears
    in an organization hierarchy.
    """
    if not isinstance(payload, Mapping):
        raise ContractValidationError("national source matrix must be an object")
    version = payload.get("version", 1)
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ContractValidationError(
            "national source matrix version must be a positive integer"
        )
    as_of = _validate_iso_date(payload.get("as_of"), "national source matrix as_of")
    affiliations = payload.get("target_affiliations")
    if not isinstance(affiliations, list) or not affiliations:
        raise ContractValidationError(
            "national source matrix target_affiliations must be a non-empty list"
        )
    normalized_affiliations = [
        _required_text(value, "national source matrix target_affiliation")
        for value in affiliations
    ]
    channel_types = payload.get("target_channel_types")
    if not isinstance(channel_types, list) or not channel_types:
        raise ContractValidationError(
            "national source matrix target_channel_types must be a non-empty list"
        )
    normalized_channel_types: list[str] = []
    for value in channel_types:
        channel_type = _required_text(value, "national source matrix channel_type")
        if channel_type not in ORGANIZATION_CHANNEL_TYPES:
            raise ContractValidationError(
                f"national source matrix has unsupported channel_type: {channel_type}"
            )
        if channel_type not in normalized_channel_types:
            normalized_channel_types.append(channel_type)

    assessments = payload.get("source_assessments")
    if not isinstance(assessments, list) or not assessments:
        raise ContractValidationError(
            "national source matrix source_assessments must be a non-empty list"
        )
    normalized_assessments: list[dict[str, Any]] = []
    assessment_ids: set[str] = set()
    for index, value in enumerate(assessments):
        context = f"National source assessment at index {index}"
        assessment = _mapping_copy(value, context)
        for field_name in (
            "source_id",
            "official_url",
            "backup_urls",
            "acquisition_mode",
            "runtime_status",
            "scan_conclusion",
            "observed_on",
            "observed_error_class",
            "field_validation",
            "note",
        ):
            if field_name not in assessment:
                raise ContractValidationError(f"{context} is missing: {field_name}")
        source_id = _required_text(assessment["source_id"], f"{context} source_id")
        if source_id in assessment_ids:
            raise ContractValidationError(f"Duplicate national source_id: {source_id}")
        assessment_ids.add(source_id)
        if source_ids is not None and source_id not in source_ids:
            raise ContractValidationError(
                f"{context} references unknown source_id: {source_id}"
            )
        assessment["source_id"] = source_id
        assessment["official_url"] = validate_http_url(
            assessment["official_url"], f"{context} official_url"
        )
        backups = assessment["backup_urls"]
        if not isinstance(backups, list):
            raise ContractValidationError(f"{context} backup_urls must be a list")
        assessment["backup_urls"] = [
            validate_http_url(item, f"{context} backup_url") for item in backups
        ]
        assessment["acquisition_mode"] = _required_text(
            assessment["acquisition_mode"], f"{context} acquisition_mode"
        )
        if assessment["acquisition_mode"] not in NATIONAL_ACQUISITION_MODES:
            raise ContractValidationError(
                f"{context} has unsupported acquisition_mode: "
                f"{assessment['acquisition_mode']}"
            )
        assessment["runtime_status"] = _required_text(
            assessment["runtime_status"], f"{context} runtime_status"
        )
        if assessment["runtime_status"] not in NATIONAL_RUNTIME_STATUSES:
            raise ContractValidationError(
                f"{context} has unsupported runtime_status: "
                f"{assessment['runtime_status']}"
            )
        assessment["scan_conclusion"] = _required_text(
            assessment["scan_conclusion"], f"{context} scan_conclusion"
        )
        if assessment["scan_conclusion"] not in NATIONAL_SCAN_CONCLUSIONS:
            raise ContractValidationError(
                f"{context} has unsupported scan_conclusion: "
                f"{assessment['scan_conclusion']}"
            )
        if (
            assessment["runtime_status"] in {"access_limited", "blocked"}
            and assessment["scan_conclusion"] == "scan_success_no_match"
        ):
            raise ContractValidationError(
                f"{context} cannot report scan_success_no_match while access is limited"
            )
        assessment["observed_on"] = _validate_iso_date(
            assessment["observed_on"], f"{context} observed_on"
        )
        observed_status = assessment.get("observed_http_status")
        if observed_status is not None and (
            isinstance(observed_status, bool) or not isinstance(observed_status, int)
        ):
            raise ContractValidationError(
                f"{context} observed_http_status must be an integer or null"
            )
        assessment["observed_http_status"] = observed_status
        assessment["observed_error_class"] = _required_text(
            assessment["observed_error_class"], f"{context} observed_error_class"
        )
        field_validation = assessment["field_validation"]
        if not isinstance(field_validation, Mapping):
            raise ContractValidationError(f"{context} field_validation must be an object")
        normalized_fields: dict[str, bool] = {}
        for field_name in NATIONAL_FIELD_KEYS:
            value = field_validation.get(field_name)
            if not isinstance(value, bool):
                raise ContractValidationError(
                    f"{context} field_validation.{field_name} must be true or false"
                )
            normalized_fields[field_name] = value
        assessment["field_validation"] = normalized_fields
        assessment["sample_announcement_url"] = (
            validate_http_url(
                assessment["sample_announcement_url"],
                f"{context} sample_announcement_url",
            )
            if assessment.get("sample_announcement_url")
            else None
        )
        assessment["note"] = _required_text(assessment["note"], f"{context} note")
        normalized_assessments.append(assessment)

    defaults = payload.get("channel_defaults")
    if not isinstance(defaults, Mapping):
        raise ContractValidationError("national source matrix channel_defaults must be an object")
    normalized_defaults: dict[str, dict[str, Any]] = {}
    for channel_type in normalized_channel_types:
        if channel_type not in defaults:
            raise ContractValidationError(
                f"national source matrix channel_defaults is missing: {channel_type}"
            )
        policy = _mapping_copy(
            defaults[channel_type],
            f"National channel default {channel_type}",
        )
        for field_name in (
            "acquisition_mode",
            "runtime_status",
            "scan_conclusion",
            "note",
        ):
            if field_name not in policy:
                raise ContractValidationError(
                    f"National channel default {channel_type} is missing: {field_name}"
                )
        policy["acquisition_mode"] = _required_text(
            policy["acquisition_mode"],
            f"National channel default {channel_type} acquisition_mode",
        )
        if policy["acquisition_mode"] not in NATIONAL_ACQUISITION_MODES:
            raise ContractValidationError(
                f"National channel default {channel_type} has unsupported acquisition_mode"
            )
        policy["runtime_status"] = _required_text(
            policy["runtime_status"],
            f"National channel default {channel_type} runtime_status",
        )
        if policy["runtime_status"] not in NATIONAL_RUNTIME_STATUSES:
            raise ContractValidationError(
                f"National channel default {channel_type} has unsupported runtime_status"
            )
        policy["scan_conclusion"] = _required_text(
            policy["scan_conclusion"],
            f"National channel default {channel_type} scan_conclusion",
        )
        if policy["scan_conclusion"] not in NATIONAL_SCAN_CONCLUSIONS:
            raise ContractValidationError(
                f"National channel default {channel_type} has unsupported scan_conclusion"
            )
        policy["note"] = _required_text(
            policy["note"], f"National channel default {channel_type} note"
        )
        normalized_defaults[channel_type] = policy

    return {
        "version": version,
        "as_of": as_of,
        "description": _optional_text(payload.get("description")) or "",
        "target_affiliations": normalized_affiliations,
        "target_channel_types": normalized_channel_types,
        "source_assessments": normalized_assessments,
        "channel_defaults": normalized_defaults,
    }


def validate_national_source_probes(
    payload: Any,
    *,
    source_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Validate read-only probe evidence without treating it as job data."""
    if not isinstance(payload, Mapping):
        raise ContractValidationError("national source probes must be an object")
    version = payload.get("version", 1)
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ContractValidationError("national source probes version must be positive")
    as_of = _validate_iso_date(payload.get("as_of"), "national source probes as_of")
    probes = payload.get("probes")
    if not isinstance(probes, list) or not probes:
        raise ContractValidationError("national source probes must contain probes")
    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, value in enumerate(probes):
        context = f"National source probe at index {index}"
        probe = _mapping_copy(value, context)
        required = (
            "source_id",
            "observed_on",
            "landing_url",
            "landing_http_status",
            "landing_content_type",
            "landing_observation",
            "public_api_url",
            "public_api_method",
            "api_http_status",
            "api_business_code",
            "api_observed_message",
            "result",
            "scan_conclusion",
            "published_jobs",
            "field_validation",
            "evidence_urls",
            "note",
        )
        for field_name in required:
            if field_name not in probe:
                raise ContractValidationError(f"{context} is missing: {field_name}")
        source_id = _required_text(probe["source_id"], f"{context} source_id")
        if source_id in seen_ids:
            raise ContractValidationError(f"Duplicate national source probe: {source_id}")
        if source_ids is not None and source_id not in source_ids:
            raise ContractValidationError(f"{context} references unknown source_id: {source_id}")
        seen_ids.add(source_id)
        probe["source_id"] = source_id
        probe["observed_on"] = _validate_iso_date(
            probe["observed_on"], f"{context} observed_on"
        )
        for field_name in ("landing_url", "public_api_url"):
            probe[field_name] = validate_http_url(probe[field_name], f"{context} {field_name}")
        for field_name in ("landing_content_type", "public_api_method", "landing_observation", "api_observed_message", "note"):
            probe[field_name] = _required_text(probe[field_name], f"{context} {field_name}")
        for field_name in ("landing_http_status", "api_http_status"):
            status = probe[field_name]
            if status is not None and (isinstance(status, bool) or not isinstance(status, int)):
                raise ContractValidationError(f"{context} {field_name} must be an integer or null")
        business_code = probe["api_business_code"]
        if business_code is not None and not isinstance(business_code, (int, str)):
            raise ContractValidationError(f"{context} api_business_code must be text, integer or null")
        probe["result"] = _required_text(probe["result"], f"{context} result")
        if probe["result"] not in NATIONAL_PROBE_RESULTS:
            raise ContractValidationError(f"{context} has unsupported result: {probe['result']}")
        probe["scan_conclusion"] = _required_text(
            probe["scan_conclusion"], f"{context} scan_conclusion"
        )
        if probe["scan_conclusion"] not in NATIONAL_SCAN_CONCLUSIONS:
            raise ContractValidationError(
                f"{context} has unsupported scan_conclusion: {probe['scan_conclusion']}"
            )
        published_jobs = probe["published_jobs"]
        if isinstance(published_jobs, bool) or not isinstance(published_jobs, int) or published_jobs < 0:
            raise ContractValidationError(f"{context} published_jobs must be a non-negative integer")
        fields = probe["field_validation"]
        if not isinstance(fields, Mapping):
            raise ContractValidationError(f"{context} field_validation must be an object")
        probe["field_validation"] = {}
        for field_name in NATIONAL_FIELD_KEYS:
            field_value = fields.get(field_name)
            if not isinstance(field_value, bool):
                raise ContractValidationError(
                    f"{context} field_validation.{field_name} must be true or false"
                )
            probe["field_validation"][field_name] = field_value
        evidence_urls = probe["evidence_urls"]
        if not isinstance(evidence_urls, list) or not evidence_urls:
            raise ContractValidationError(f"{context} evidence_urls must be non-empty")
        probe["evidence_urls"] = [
            validate_http_url(item, f"{context} evidence_url") for item in evidence_urls
        ]
        if probe["result"] == "adapter_ready_probe_failed" and published_jobs != 0:
            raise ContractValidationError(
                f"{context} probe failure cannot claim published jobs"
            )
        normalized.append(probe)
    return {
        "version": version,
        "as_of": as_of,
        "description": _optional_text(payload.get("description")) or "",
        "probes": normalized,
    }


def validate_national_entry_targets(
    payload: Any,
    *,
    source_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Validate candidate public entries used by the probe runner.

    Targets are operations metadata, not vacancies. A failed probe therefore
    remains visible without making anything eligible for the public job corpus.
    """
    if not isinstance(payload, Mapping):
        raise ContractValidationError("national entry targets must be an object")
    version = payload.get("version", 1)
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ContractValidationError("national entry targets version must be positive")
    as_of = _validate_iso_date(payload.get("as_of"), "national entry targets as_of")
    systems = payload.get("systems")
    if not isinstance(systems, list) or not systems:
        raise ContractValidationError("national entry targets must contain systems")

    normalized: list[dict[str, Any]] = []
    seen_systems: set[str] = set()
    seen_entries: set[str] = set()
    for index, value in enumerate(systems):
        context = f"National entry target at index {index}"
        system = _mapping_copy(value, context)
        for field_name in (
            "system_id",
            "affiliation",
            "source_id",
            "name",
            "entries",
            "recruitment_keywords",
            "max_links",
        ):
            if field_name not in system:
                raise ContractValidationError(f"{context} is missing: {field_name}")
        system_id = _required_text(system["system_id"], f"{context} system_id")
        if system_id in seen_systems:
            raise ContractValidationError(f"Duplicate national entry system: {system_id}")
        seen_systems.add(system_id)
        system["system_id"] = system_id
        system["affiliation"] = _required_text(
            system["affiliation"], f"{context} affiliation"
        )
        source_id = _required_text(system["source_id"], f"{context} source_id")
        if source_ids is not None and source_id not in source_ids:
            raise ContractValidationError(
                f"{context} references unknown source_id: {source_id}"
            )
        system["source_id"] = source_id
        system["name"] = _required_text(system["name"], f"{context} name")

        entries = system["entries"]
        if not isinstance(entries, list) or not entries:
            raise ContractValidationError(f"{context} entries must be non-empty")
        normalized_entries: list[dict[str, Any]] = []
        primary_count = 0
        for entry_index, entry_value in enumerate(entries):
            entry_context = f"{context} entry at index {entry_index}"
            entry = _mapping_copy(entry_value, entry_context)
            for field_name in ("entry_id", "role", "url", "official_hosts"):
                if field_name not in entry:
                    raise ContractValidationError(
                        f"{entry_context} is missing: {field_name}"
                    )
            entry_id = _required_text(entry["entry_id"], f"{entry_context} entry_id")
            if entry_id in seen_entries:
                raise ContractValidationError(f"Duplicate national entry id: {entry_id}")
            seen_entries.add(entry_id)
            entry["entry_id"] = entry_id
            role = _required_text(entry["role"], f"{entry_context} role")
            if role not in NATIONAL_ENTRY_ROLES:
                raise ContractValidationError(
                    f"{entry_context} has unsupported role: {role}"
                )
            if role == "primary_recruitment":
                primary_count += 1
            entry["role"] = role
            entry["url"] = validate_http_url(entry["url"], f"{entry_context} url")
            hosts = entry["official_hosts"]
            if not isinstance(hosts, list) or not hosts:
                raise ContractValidationError(
                    f"{entry_context} official_hosts must be non-empty"
                )
            normalized_hosts: list[str] = []
            for host in hosts:
                host_text = _required_text(
                    host, f"{entry_context} official_host"
                ).lower()
                if "/" in host_text or "://" in host_text:
                    raise ContractValidationError(
                        f"{entry_context} official_hosts must contain hostnames"
                    )
                normalized_hosts.append(host_text)
            entry["official_hosts"] = sorted(set(normalized_hosts))
            entry["note"] = _optional_text(entry.get("note")) or ""
            normalized_entries.append(entry)
        if primary_count != 1:
            raise ContractValidationError(
                f"{context} must contain exactly one primary_recruitment entry"
            )

        keywords = system["recruitment_keywords"]
        if not isinstance(keywords, list) or not keywords:
            raise ContractValidationError(
                f"{context} recruitment_keywords must be non-empty"
            )
        system["recruitment_keywords"] = [
            _required_text(item, f"{context} recruitment_keyword")
            for item in keywords
        ]
        max_links = system["max_links"]
        if (
            isinstance(max_links, bool)
            or not isinstance(max_links, int)
            or not 1 <= max_links <= 100
        ):
            raise ContractValidationError(
                f"{context} max_links must be an integer between 1 and 100"
            )
        system["max_links"] = max_links
        system["entries"] = normalized_entries
        system["note"] = _optional_text(system.get("note")) or ""
        normalized.append(system)

    return {
        "version": version,
        "as_of": as_of,
        "description": _optional_text(payload.get("description")) or "",
        "systems": normalized,
    }


def validate_national_entry_probe_run(
    payload: Any,
    *,
    source_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Validate read-only public-entry probe output, never job records."""
    if not isinstance(payload, Mapping):
        raise ContractValidationError("national entry probe run must be an object")
    version = payload.get("version", 1)
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ContractValidationError("national entry probe run version must be positive")
    observed_on = _validate_iso_date(
        payload.get("observed_on"), "national entry probe run observed_on"
    )
    environment = _required_text(
        payload.get("environment"), "national entry probe run environment"
    )
    transport_mode = _optional_text(payload.get("transport_mode")) or "unknown"
    if transport_mode not in NATIONAL_ENTRY_TRANSPORT_MODES:
        raise ContractValidationError(
            "national entry probe run has unsupported transport_mode"
        )
    proxy_environment_present = payload.get("proxy_environment_present")
    if proxy_environment_present is not None and not isinstance(
        proxy_environment_present, bool
    ):
        raise ContractValidationError(
            "national entry probe run proxy_environment_present must be boolean or null"
        )
    systems = payload.get("systems")
    attempts = payload.get("attempts")
    if not isinstance(systems, list) or not systems:
        raise ContractValidationError(
            "national entry probe run systems must be non-empty"
        )
    if not isinstance(attempts, list) or not attempts:
        raise ContractValidationError(
            "national entry probe run attempts must be non-empty"
        )

    normalized_systems: list[dict[str, Any]] = []
    system_ids: set[str] = set()
    system_source_ids: dict[str, str] = {}
    for index, value in enumerate(systems):
        context = f"National entry probe system at index {index}"
        system = _mapping_copy(value, context)
        for field_name in (
            "system_id",
            "source_id",
            "status",
            "scan_conclusion",
            "attempt_count",
            "recruitment_links",
        ):
            if field_name not in system:
                raise ContractValidationError(f"{context} is missing: {field_name}")
        system_id = _required_text(system["system_id"], f"{context} system_id")
        if system_id in system_ids:
            raise ContractValidationError(f"Duplicate probe system: {system_id}")
        system_ids.add(system_id)
        source_id = _required_text(system["source_id"], f"{context} source_id")
        if source_ids is not None and source_id not in source_ids:
            raise ContractValidationError(
                f"{context} references unknown source_id: {source_id}"
            )
        if source_id in system_source_ids.values():
            raise ContractValidationError(
                f"{context} reuses source_id across probe systems: {source_id}"
            )
        system_source_ids[system_id] = source_id
        system["source_id"] = source_id
        status = _required_text(system["status"], f"{context} status")
        if status not in NATIONAL_ENTRY_PROBE_STATUSES:
            raise ContractValidationError(f"{context} has unsupported status: {status}")
        system["status"] = status
        conclusion = _required_text(
            system["scan_conclusion"], f"{context} scan_conclusion"
        )
        if conclusion not in NATIONAL_SCAN_CONCLUSIONS:
            raise ContractValidationError(
                f"{context} has unsupported scan_conclusion: {conclusion}"
            )
        system["scan_conclusion"] = conclusion
        count = system["attempt_count"]
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise ContractValidationError(
                f"{context} attempt_count must be positive"
            )
        system["attempt_count"] = count
        usable = system.get("usable_entry_url")
        system["usable_entry_url"] = (
            validate_http_url(usable, f"{context} usable_entry_url")
            if usable
            else None
        )
        links = system["recruitment_links"]
        if not isinstance(links, list):
            raise ContractValidationError(
                f"{context} recruitment_links must be a list"
            )
        system["recruitment_links"] = [
            validate_http_url(link, f"{context} recruitment_link")
            for link in links
        ]
        system["note"] = _optional_text(system.get("note")) or ""
        normalized_systems.append(system)

    normalized_attempts: list[dict[str, Any]] = []
    seen_attempts: set[tuple[str, str]] = set()
    for index, value in enumerate(attempts):
        context = f"National entry probe attempt at index {index}"
        attempt = _mapping_copy(value, context)
        for field_name in (
            "system_id",
            "source_id",
            "entry_id",
            "url",
            "robots_url",
            "classification",
            "discovered_links",
            "recruitment_links",
            "retries",
        ):
            if field_name not in attempt:
                raise ContractValidationError(f"{context} is missing: {field_name}")
        system_id = _required_text(attempt["system_id"], f"{context} system_id")
        source_id = _required_text(attempt["source_id"], f"{context} source_id")
        if system_id not in system_ids:
            raise ContractValidationError(
                f"{context} references unknown system_id: {system_id}"
            )
        if source_ids is not None and source_id not in source_ids:
            raise ContractValidationError(
                f"{context} references unknown source_id: {source_id}"
            )
        expected_source_id = system_source_ids[system_id]
        if source_id != expected_source_id:
            raise ContractValidationError(
                f"{context} source_id does not match its system: {source_id}"
            )
        entry_id = _required_text(attempt["entry_id"], f"{context} entry_id")
        key = (system_id, entry_id)
        if key in seen_attempts:
            raise ContractValidationError(
                f"Duplicate probe attempt: {system_id}/{entry_id}"
            )
        seen_attempts.add(key)
        attempt["system_id"] = system_id
        attempt["source_id"] = source_id
        attempt["entry_id"] = entry_id
        attempt["url"] = validate_http_url(attempt["url"], f"{context} url")
        robots_url = attempt["robots_url"]
        attempt["robots_url"] = (
            validate_http_url(robots_url, f"{context} robots_url")
            if robots_url
            else None
        )
        final_url = attempt.get("final_url")
        attempt["final_url"] = (
            validate_http_url(final_url, f"{context} final_url")
            if final_url
            else None
        )
        for field_name in ("robots_http_status", "http_status"):
            status = attempt.get(field_name)
            if status is not None and (
                isinstance(status, bool) or not isinstance(status, int)
            ):
                raise ContractValidationError(
                    f"{context} {field_name} must be an integer or null"
                )
            attempt[field_name] = status
        robots_allowed = attempt.get("robots_allowed")
        if robots_allowed is not None and not isinstance(robots_allowed, bool):
            raise ContractValidationError(
                f"{context} robots_allowed must be boolean or null"
            )
        attempt["robots_allowed"] = robots_allowed
        classification = _required_text(
            attempt["classification"], f"{context} classification"
        )
        if classification not in NATIONAL_ENTRY_PROBE_CLASSES:
            raise ContractValidationError(
                f"{context} has unsupported classification: {classification}"
            )
        attempt["classification"] = classification
        for field_name in (
            "content_type",
            "title",
            "error_class",
            "error_detail",
            "text_excerpt",
        ):
            attempt[field_name] = _optional_text(attempt.get(field_name)) or ""
        for field_name in ("discovered_links", "recruitment_links"):
            links = attempt[field_name]
            if not isinstance(links, list):
                raise ContractValidationError(
                    f"{context} {field_name} must be a list"
                )
            attempt[field_name] = [
                validate_http_url(
                    link, f"{context} {field_name[:-1]}",
                )
                for link in links
            ]
        retries = attempt["retries"]
        if isinstance(retries, bool) or not isinstance(retries, int) or retries < 0:
            raise ContractValidationError(
                f"{context} retries must be a non-negative integer"
            )
        attempt["retries"] = retries
        normalized_attempts.append(attempt)

    attempt_counts: dict[str, int] = {}
    for attempt in normalized_attempts:
        attempt_counts[attempt["system_id"]] = (
            attempt_counts.get(attempt["system_id"], 0) + 1
        )
    for system in normalized_systems:
        system_id = system["system_id"]
        actual_count = attempt_counts.get(system_id, 0)
        if actual_count != system["attempt_count"]:
            raise ContractValidationError(
                f"National entry probe system {system_id} attempt_count "
                f"does not match attempts: {system['attempt_count']} != {actual_count}"
            )

    return {
        "version": version,
        "observed_on": observed_on,
        "environment": environment,
        "transport_mode": transport_mode,
        "proxy_environment_present": proxy_environment_present,
        "description": _optional_text(payload.get("description")) or "",
        "systems": normalized_systems,
        "attempts": normalized_attempts,
    }


def validate_source_validation_registry(payload: Any) -> dict[str, Any]:
    """Validate the Phase 4 provincial source-validation ledger.

    A record documents evidence that an official provincial entry was checked.
    It is intentionally separate from the active source registry: a fixture can
    prove that a parser understands a public sample while runtime robots and
    network checks still keep that source disabled.
    """
    if not isinstance(payload, dict):
        raise ContractValidationError("source validation registry must be an object")
    version = payload.get("version", 1)
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ContractValidationError(
            "source validation registry version must be a positive integer"
        )
    description = payload.get("description", "")
    if description is not None and not isinstance(description, str):
        raise ContractValidationError("source validation registry description must be text")
    records = payload.get("records")
    if not isinstance(records, list) or not records:
        raise ContractValidationError(
            "source validation registry records must be a non-empty list"
        )

    normalized: list[dict[str, Any]] = []
    record_ids: set[str] = set()
    target_slots: set[tuple[str, str]] = set()
    for index, value in enumerate(records):
        record = _mapping_copy(value, f"Source validation record at index {index}")
        for field_name in (
            "id",
            "source_id",
            "province",
            "role",
            "validation_stage",
            "official_entry_url",
            "backup_entry_urls",
            "last_checked_on",
            "access_assessment",
            "finding",
        ):
            if field_name not in record:
                raise ContractValidationError(
                    f"Source validation record at index {index} is missing: {field_name}"
                )
        record_id = _required_text(record["id"], f"Source validation record at index {index} id")
        if record_id in record_ids:
            raise ContractValidationError(f"Duplicate source validation record id: {record_id}")
        record_ids.add(record_id)
        record["id"] = record_id
        record["source_id"] = _required_text(
            record["source_id"], f"Source validation record {record_id} source_id"
        )
        record["province"] = _required_text(
            record["province"], f"Source validation record {record_id} province"
        )
        record["role"] = _required_text(
            record["role"], f"Source validation record {record_id} role"
        )
        if record["role"] not in PROVINCIAL_SOURCE_TARGET_ROLES:
            raise ContractValidationError(
                f"Source validation record {record_id} has unsupported role"
            )
        target_slot = (record["province"], record["role"])
        if target_slot in target_slots:
            raise ContractValidationError(
                "Duplicate source validation target slot: "
                f"{record['province']}/{record['role']}"
            )
        target_slots.add(target_slot)
        record["validation_stage"] = _required_text(
            record["validation_stage"],
            f"Source validation record {record_id} validation_stage",
        )
        if record["validation_stage"] not in SOURCE_VALIDATION_STAGES:
            raise ContractValidationError(
                f"Source validation record {record_id} has unsupported validation_stage"
            )
        record["official_entry_url"] = validate_http_url(
            record["official_entry_url"],
            f"Source validation record {record_id} official_entry_url",
        )
        backup_urls = record["backup_entry_urls"]
        if not isinstance(backup_urls, list) or not backup_urls:
            raise ContractValidationError(
                f"Source validation record {record_id} backup_entry_urls must be a non-empty list"
            )
        record["backup_entry_urls"] = [
            validate_http_url(
                url,
                f"Source validation record {record_id} backup_entry_url",
            )
            for url in backup_urls
        ]
        record["last_checked_on"] = _validate_iso_date(
            record["last_checked_on"],
            f"Source validation record {record_id} last_checked_on",
        )
        access = _mapping_copy(
            record["access_assessment"],
            f"Source validation record {record_id} access_assessment",
        )
        access["observed_access"] = _required_text(
            access.get("observed_access"),
            f"Source validation record {record_id} observed_access",
        )
        access["robots_assessment"] = _required_text(
            access.get("robots_assessment"),
            f"Source validation record {record_id} robots_assessment",
        )
        access["note"] = _optional_text(access.get("note")) or ""
        record["access_assessment"] = access
        record["finding"] = _required_text(
            record["finding"], f"Source validation record {record_id} finding"
        )

        sample_value = record.get("sample")
        sample: dict[str, Any] | None = None
        if sample_value is not None:
            sample = _mapping_copy(
                sample_value, f"Source validation record {record_id} sample"
            )
            for field_name in (
                "official_url",
                "title",
                "publisher",
                "published_date",
                "field_evidence",
            ):
                if field_name not in sample:
                    raise ContractValidationError(
                        f"Source validation record {record_id} sample is missing: {field_name}"
                    )
            sample["official_url"] = validate_http_url(
                sample["official_url"],
                f"Source validation record {record_id} sample official_url",
            )
            sample["title"] = _required_text(
                sample["title"], f"Source validation record {record_id} sample title"
            )
            sample["publisher"] = _required_text(
                sample["publisher"],
                f"Source validation record {record_id} sample publisher",
            )
            sample["published_date"] = _validate_iso_date(
                sample["published_date"],
                f"Source validation record {record_id} sample published_date",
            )
            deadline_date = _optional_text(sample.get("deadline_date"))
            if deadline_date is not None:
                deadline_date = _validate_iso_date(
                    deadline_date,
                    f"Source validation record {record_id} sample deadline_date",
                )
            sample["deadline_date"] = deadline_date
            field_evidence = _mapping_copy(
                sample["field_evidence"],
                f"Source validation record {record_id} sample field_evidence",
            )
            for field_name in (
                "publisher",
                "published_date",
                "recruitment_scope",
                "application_or_deadline",
            ):
                field_evidence[field_name] = _required_text(
                    field_evidence.get(field_name),
                    f"Source validation record {record_id} sample field_evidence {field_name}",
                )
            field_evidence["attachment_or_position_table"] = (
                _optional_text(field_evidence.get("attachment_or_position_table"))
                or ""
            )
            sample["field_evidence"] = field_evidence

        fixture_path = _optional_text(record.get("fixture_path"))
        if fixture_path is not None:
            fixture_path = _validate_relative_project_path(
                fixture_path,
                f"Source validation record {record_id} fixture_path",
            )
        regression_test = _optional_text(record.get("regression_test"))
        if regression_test is not None and "::" not in regression_test:
            raise ContractValidationError(
                f"Source validation record {record_id} regression_test must name a test node"
            )
        if record["validation_stage"] == "adapter_fixture_verified":
            if sample is None or fixture_path is None or regression_test is None:
                raise ContractValidationError(
                    f"Source validation record {record_id} adapter_fixture_verified "
                    "requires sample, fixture_path and regression_test"
                )
        if record["validation_stage"] == "entry_checked_no_recruitment_sample":
            if sample is not None or fixture_path is not None or regression_test is not None:
                raise ContractValidationError(
                    f"Source validation record {record_id} entry_checked_no_recruitment_sample "
                    "must not claim a parser fixture or recruitment sample"
                )
        record["sample"] = sample
        record["fixture_path"] = fixture_path
        record["regression_test"] = regression_test
        normalized.append(record)

    return {
        "version": version,
        "description": (description or "").strip(),
        "records": normalized,
    }


def validate_source_artifact(value: Any) -> dict[str, Any]:
    """Validate official attachment metadata and its controlled processing state."""
    artifact = _mapping_copy(value, "Source artifact")
    artifact["source_id"] = _required_text(artifact.get("source_id"), "source_id")
    artifact["parent_url"] = validate_http_url(artifact.get("parent_url"), "parent_url")
    artifact["artifact_url"] = validate_http_url(
        artifact.get("artifact_url"), "artifact_url"
    )
    artifact["artifact_kind"] = _required_text(
        artifact.get("artifact_kind"), "artifact_kind"
    )
    if artifact["artifact_kind"] not in ARTIFACT_KINDS:
        raise ContractValidationError(
            "artifact_kind must be one of: " + ", ".join(sorted(ARTIFACT_KINDS))
        )
    artifact["media_type"] = _optional_text(artifact.get("media_type"))
    if artifact["media_type"] and "/" not in str(artifact["media_type"]):
        raise ContractValidationError("media_type must be a MIME type when provided")
    artifact["content_sha256"] = _optional_text(artifact.get("content_sha256"))
    if artifact["content_sha256"] and not _SHA256_PATTERN.fullmatch(
        str(artifact["content_sha256"])
    ):
        raise ContractValidationError("content_sha256 must be a SHA-256 hex digest")
    artifact["storage_path"] = _validate_relative_storage_path(
        artifact.get("storage_path")
    )
    artifact["parser_version"] = _optional_text(artifact.get("parser_version"))
    artifact["extraction_status"] = _required_text(
        artifact.get("extraction_status", "registered"), "extraction_status"
    )
    if artifact["extraction_status"] not in ARTIFACT_EXTRACTION_STATUSES:
        raise ContractValidationError(
            "extraction_status must be one of: "
            + ", ".join(sorted(ARTIFACT_EXTRACTION_STATUSES))
        )
    artifact["metadata"] = _metadata_object(artifact.get("metadata", {}), "metadata")
    if artifact["extraction_status"] in {"downloaded", "extracted"}:
        if not artifact["content_sha256"] or not artifact["storage_path"]:
            raise ContractValidationError(
                "downloaded or extracted artifacts require content_sha256 and storage_path"
            )
        if not artifact["parser_version"]:
            raise ContractValidationError(
                "downloaded or extracted artifacts require parser_version"
            )
    return artifact


def validate_artifact_candidate(value: Any) -> dict[str, Any]:
    """Validate a private candidate derived from one official attachment row."""
    candidate = _mapping_copy(value, "Artifact job candidate")
    candidate["artifact_row_id"] = _optional_positive_integer(
        candidate.get("artifact_row_id"), "artifact_row_id"
    )
    if candidate["artifact_row_id"] is None:
        raise ContractValidationError("artifact_row_id must be a positive integer")
    candidate["source_id"] = _required_text(candidate.get("source_id"), "source_id")
    candidate["official_page_url"] = validate_http_url(
        candidate.get("official_page_url"), "official_page_url"
    )
    candidate["title"] = _required_text(candidate.get("title"), "title")
    candidate["employer"] = _required_text(candidate.get("employer"), "employer")
    for field_name in (
        "application_url",
        "location",
        "published_date",
        "deadline_date",
        "summary",
        "description",
        "review_note",
    ):
        candidate[field_name] = _optional_text(candidate.get(field_name))
    if candidate["application_url"] is not None:
        candidate["application_url"] = validate_http_url(
            candidate["application_url"], "application_url"
        )
    candidate["description"] = candidate["description"] or candidate["title"]
    candidate["degree_levels"] = _optional_text_list(
        candidate.get("degree_levels", []), "degree_levels"
    )
    candidate["major_tags"] = _optional_text_list(
        candidate.get("major_tags", []), "major_tags"
    )
    try:
        candidate["relevance_score"] = int(candidate.get("relevance_score", 0))
    except (TypeError, ValueError) as error:
        raise ContractValidationError("relevance_score must be an integer") from error
    candidate["field_evidence"] = _metadata_object(
        candidate.get("field_evidence", {}), "field_evidence"
    )
    candidate["review_status"] = _required_text(
        candidate.get("review_status", "needs_review"), "review_status"
    )
    if candidate["review_status"] not in ARTIFACT_CANDIDATE_REVIEW_STATUSES:
        raise ContractValidationError(
            "review_status must be one of: "
            + ", ".join(sorted(ARTIFACT_CANDIDATE_REVIEW_STATUSES))
        )
    return candidate


def validate_artifact_candidate_transition(
    current_status: Any,
    next_status: Any,
    *,
    review_note: Any,
) -> str:
    """Require a review note before an extracted row becomes publishable."""
    current = _required_text(current_status, "current artifact candidate review_status")
    target = _required_text(next_status, "artifact candidate review_status")
    if current not in ARTIFACT_CANDIDATE_REVIEW_STATUSES:
        raise ContractValidationError("Unsupported current artifact candidate status")
    if target not in ARTIFACT_CANDIDATE_REVIEW_STATUSES:
        raise ContractValidationError("Unsupported artifact candidate review status")
    if target not in ARTIFACT_CANDIDATE_REVIEW_TRANSITIONS[current]:
        raise ContractValidationError(
            f"Illegal artifact candidate transition: {current} -> {target}"
        )
    if target == "official_content_verified" and not _optional_text(review_note):
        raise ContractValidationError(
            "Official-content verification requires a review_note"
        )
    return target


def validate_job_evidence(value: Any) -> dict[str, Any]:
    """Validate one evidence item connected to a public job record."""
    evidence = _mapping_copy(value, "Job evidence")
    evidence["artifact_id"] = _optional_positive_integer(evidence.get("artifact_id"), "artifact_id")
    evidence["evidence_type"] = _required_text(
        evidence.get("evidence_type", "official_page"), "evidence_type"
    )
    if evidence["evidence_type"] not in EVIDENCE_TYPES:
        raise ContractValidationError(
            "evidence_type must be one of: " + ", ".join(sorted(EVIDENCE_TYPES))
        )
    evidence["field_name"] = _required_text(
        evidence.get("field_name", "job_record"), "field_name"
    )
    evidence["evidence_url"] = validate_http_url(
        evidence.get("evidence_url"), "evidence_url"
    )
    evidence["locator"] = _optional_text(evidence.get("locator"))
    evidence["excerpt"] = _optional_text(evidence.get("excerpt"))
    evidence["verification_status"] = _required_text(
        evidence.get("verification_status", "verified"), "verification_status"
    )
    if evidence["verification_status"] not in EVIDENCE_VERIFICATION_STATUSES:
        raise ContractValidationError(
            "verification_status must be one of: "
            + ", ".join(sorted(EVIDENCE_VERIFICATION_STATUSES))
        )
    evidence["metadata"] = _metadata_object(evidence.get("metadata", {}), "metadata")
    evidence_key = _optional_text(evidence.get("evidence_key"))
    if evidence_key is not None:
        evidence["evidence_key"] = evidence_key
    return evidence


def validate_candidate_lead_transition(
    current_status: Any,
    next_status: Any,
    *,
    official_url: Any,
    verification_note: Any,
) -> tuple[str, str]:
    """Validate a private lead state transition and its evidence prerequisites."""
    current = _required_text(current_status, "current candidate lead status")
    target = _required_text(next_status, "candidate lead verification_status")
    if current not in CANDIDATE_LEAD_STATUSES:
        raise ContractValidationError(f"Unsupported current candidate lead status: {current}")
    if target not in CANDIDATE_LEAD_STATUSES:
        raise ContractValidationError("Unsupported candidate lead verification status")
    if target not in CANDIDATE_LEAD_TRANSITIONS[current]:
        raise ContractValidationError(
            f"Illegal candidate lead transition: {current} -> {target}"
        )

    normalized_url = _optional_text(official_url)
    note = _optional_text(verification_note) or ""
    if target in {"official_url_found", "official_content_verified", "published"}:
        if not normalized_url:
            raise ContractValidationError(
                f"Candidate lead status {target} requires an official_url"
            )
        validate_http_url(normalized_url, "official_url")
    if target == "official_content_verified" and not note:
        raise ContractValidationError(
            "Official-content verification requires a verification_note"
        )
    if target == "need_review" and not note:
        raise ContractValidationError(
            "need_review requires a note describing the next verification action"
        )
    return target, normalized_url or ""


def _mapping_copy(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractValidationError(f"{context} must be an object")
    return dict(value)


def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise ContractValidationError(f"{field_name} must be non-empty text")
    text = value.strip()
    if not text:
        raise ContractValidationError(f"{field_name} must be non-empty text")
    return text


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ContractValidationError("Optional text fields must be text or null")
    text = value.strip()
    return text or None


def _validate_nonempty_text_list(value: Any, field_name: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ContractValidationError(f"{field_name} must be a non-empty list")
    return [_required_text(item, field_name) for item in value]


def _optional_text_list(value: Any, field_name: str) -> list[str]:
    if not isinstance(value, list):
        raise ContractValidationError(f"{field_name} must be a list")
    return [_required_text(item, field_name) for item in value]


def _validate_domains(value: Any, field_name: str) -> list[str]:
    domains = _validate_nonempty_text_list(value, field_name)
    normalized: list[str] = []
    for domain in domains:
        candidate = domain.lower().rstrip(".")
        if not _DOMAIN_PATTERN.fullmatch(candidate):
            raise ContractValidationError(f"{field_name} contains an invalid domain: {domain}")
        normalized.append(candidate)
    return normalized


def _validate_relative_storage_path(value: Any) -> str | None:
    text = _optional_text(value)
    if text is None:
        return None
    windows_path = PureWindowsPath(text)
    posix_path = PurePosixPath(text)
    if (
        windows_path.is_absolute()
        or posix_path.is_absolute()
        or bool(windows_path.drive)
        or ".." in windows_path.parts
        or ".." in posix_path.parts
    ):
        raise ContractValidationError("storage_path must be a relative path inside managed storage")
    return text


def _validate_relative_project_path(value: Any, field_name: str) -> str:
    text = _required_text(value, field_name)
    windows_path = PureWindowsPath(text)
    posix_path = PurePosixPath(text)
    if (
        windows_path.is_absolute()
        or posix_path.is_absolute()
        or bool(windows_path.drive)
        or ".." in windows_path.parts
        or ".." in posix_path.parts
    ):
        raise ContractValidationError(
            f"{field_name} must be a relative path inside the project"
        )
    return text.replace("\\", "/")


def _validate_iso_date(value: Any, field_name: str) -> str:
    text = _required_text(value, field_name)
    try:
        date.fromisoformat(text)
    except ValueError as error:
        raise ContractValidationError(f"{field_name} must use ISO YYYY-MM-DD") from error
    return text


def _metadata_object(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractValidationError(f"{field_name} must be an object")
    return dict(value)


def _optional_positive_integer(value: Any, field_name: str) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ContractValidationError(f"{field_name} must be a positive integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise ContractValidationError(f"{field_name} must be a positive integer") from error
    if parsed < 1:
        raise ContractValidationError(f"{field_name} must be a positive integer")
    return parsed
