"""Versioned professional taxonomy for the CUPB Geoscience profiles.

The taxonomy separates an official major name from adjacent discovery terms.
Only exact terms can support an explicit profile match; related terms keep a
useful opportunity visible for review without claiming eligibility.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from job_hub.contracts import ContractValidationError


PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class MajorDefinition:
    id: str
    name: str
    student_levels: tuple[str, ...]
    classification: dict[str, Any]
    exact_terms: tuple[str, ...]
    english_exact_terms: tuple[str, ...]
    related_terms: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "student_levels": list(self.student_levels),
            "classification": dict(self.classification),
            "exact_terms": list(self.exact_terms),
            "english_exact_terms": list(self.english_exact_terms),
            "related_terms": list(self.related_terms),
        }


def load_major_taxonomy(path: Path | None = None) -> dict[str, Any]:
    taxonomy_path = path or PROJECT_ROOT / "data" / "major_taxonomy.json"
    with taxonomy_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return validate_major_taxonomy(payload)


@lru_cache(maxsize=8)
def _cached_taxonomy(path: str) -> dict[str, Any]:
    return load_major_taxonomy(Path(path))


def validate_major_taxonomy(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ContractValidationError("major taxonomy must be an object")
    version = payload.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ContractValidationError("major taxonomy version must be a positive integer")
    as_of = payload.get("as_of")
    if not isinstance(as_of, str) or len(as_of) != 10:
        raise ContractValidationError("major taxonomy as_of must be an ISO date")
    majors = payload.get("majors")
    if not isinstance(majors, list) or not majors:
        raise ContractValidationError("major taxonomy majors must be a non-empty list")

    normalized: list[dict[str, Any]] = []
    ids: set[str] = set()
    exact_terms: set[str] = set()
    for index, value in enumerate(majors):
        if not isinstance(value, dict):
            raise ContractValidationError(f"major taxonomy item {index} must be an object")
        required = ("id", "name", "student_levels", "classification", "exact_terms", "english_exact_terms", "related_terms")
        missing = [field for field in required if field not in value]
        if missing:
            raise ContractValidationError(f"major taxonomy item {index} missing: {', '.join(missing)}")
        item = dict(value)
        item_id = str(item["id"]).strip()
        if not item_id or item_id in ids:
            raise ContractValidationError(f"major taxonomy duplicate or empty id: {item_id}")
        ids.add(item_id)
        item["name"] = str(item["name"]).strip()
        if not item["name"]:
            raise ContractValidationError(f"major taxonomy {item_id} name is empty")
        levels = item["student_levels"]
        if not isinstance(levels, list) or not levels or any(level not in {"本科", "硕士", "博士"} for level in levels):
            raise ContractValidationError(f"major taxonomy {item_id} student_levels is invalid")
        if not isinstance(item["classification"], dict) or not item["classification"]:
            raise ContractValidationError(f"major taxonomy {item_id} classification is invalid")
        for field in ("exact_terms", "english_exact_terms", "related_terms"):
            terms = item[field]
            if not isinstance(terms, list) or any(not str(term).strip() for term in terms):
                raise ContractValidationError(f"major taxonomy {item_id} {field} is invalid")
            item[field] = list(dict.fromkeys(str(term).strip() for term in terms))
        for term in item["exact_terms"]:
            folded = term.casefold()
            if folded in exact_terms:
                raise ContractValidationError(f"duplicate exact major term: {term}")
            exact_terms.add(folded)
        item["id"] = item_id
        item["student_levels"] = list(dict.fromkeys(levels))
        normalized.append(item)
    result = dict(payload)
    result["version"] = version
    result["description"] = str(payload.get("scope") or "").strip()
    result["majors"] = normalized
    return result


def list_major_definitions(path: Path | None = None) -> tuple[MajorDefinition, ...]:
    payload = load_major_taxonomy(path)
    return tuple(
        MajorDefinition(
            id=item["id"],
            name=item["name"],
            student_levels=tuple(item["student_levels"]),
            classification=dict(item["classification"]),
            exact_terms=tuple(item["exact_terms"]),
            english_exact_terms=tuple(item["english_exact_terms"]),
            related_terms=tuple(item["related_terms"]),
        )
        for item in payload["majors"]
    )


def get_major_definition(major_id: str, path: Path | None = None) -> MajorDefinition:
    for definition in list_major_definitions(path):
        if definition.id == major_id:
            return definition
    raise KeyError(f"unknown major taxonomy id: {major_id}")


def profile_major_definition(profile_id: str, path: Path | None = None) -> MajorDefinition:
    profile_major_ids = {
        "undergraduate-resource-exploration": "resource-exploration-engineering",
        "master-geology": "geology",
        "doctoral-geology": "geology",
        "master-geological-engineering": "geological-engineering",
        "doctoral-geological-engineering": "geological-engineering",
        "master-geological-resources-engineering": "geological-resources-and-engineering",
        "doctoral-geological-resources-engineering": "geological-resources-and-engineering",
    }
    major_id = profile_major_ids.get(profile_id, profile_id)
    return get_major_definition(major_id, path)


def exact_terms_for_profile(profile_id: str, path: Path | None = None) -> tuple[str, ...]:
    return profile_major_definition(profile_id, path).exact_terms


def english_exact_terms_for_profile(profile_id: str, path: Path | None = None) -> tuple[str, ...]:
    return profile_major_definition(profile_id, path).english_exact_terms


def related_terms_for_profile(profile_id: str, path: Path | None = None) -> tuple[str, ...]:
    return profile_major_definition(profile_id, path).related_terms
