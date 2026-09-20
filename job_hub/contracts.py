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


def validate_source_artifact(value: Any) -> dict[str, Any]:
    """Validate attachment metadata only; Phase 1 does not download any content."""
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
    return artifact


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
