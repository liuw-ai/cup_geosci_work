"""Versioned data contracts for official employment-information records.

The public site is intentionally small, but its source, employer, evidence and
lead records must remain explainable as the source network grows.  This module
keeps those validation rules independent from collection and presentation code.
It performs no network I/O and does not decide whether a vacancy is relevant.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
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
        "mnr_recruitment",
        "slb_coveo_search",
        "html_notice",
        "landing_page",
    }
)
SOURCE_TIERS = frozenset({"A", "B"})

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
