from __future__ import annotations

import pytest

from job_hub.contracts import ContractValidationError
from job_hub.major_taxonomy import (
    get_major_definition,
    list_major_definitions,
    profile_major_definition,
    validate_major_taxonomy,
)
from job_hub.matching import extract_major_tags
from job_hub.profiles import get_student_profile


def test_taxonomy_contains_the_four_requested_majors_with_distinct_boundaries() -> None:
    definitions = {item.id: item for item in list_major_definitions()}

    assert set(definitions) == {
        "resource-exploration-engineering",
        "geology",
        "geological-engineering",
        "geological-resources-and-engineering",
    }
    assert definitions["resource-exploration-engineering"].classification["undergraduate_code"] == "081403"
    assert definitions["geology"].classification["graduate_code"] == "0709"
    assert definitions["geological-engineering"].classification["graduate_professional_code"] == "085703"
    assert definitions["geological-resources-and-engineering"].classification["graduate_code"] == "0818"


def test_profile_aliases_do_not_promote_adjacent_majors_to_exact() -> None:
    resource = get_student_profile("undergraduate-resource-exploration")
    engineering = get_student_profile("master-geological-engineering")

    assert resource is not None and engineering is not None
    assert "勘查技术与工程" not in resource.exact_major_terms
    assert "勘查技术与工程" in resource.related_major_terms
    assert "地质工程" in engineering.exact_major_terms
    assert "地质工程" in get_student_profile("master-geological-resources-engineering").related_major_terms


def test_bare_geological_resources_is_not_a_supported_exact_major() -> None:
    assert "地质资源" not in extract_major_tags("专业要求：地质资源")


def test_profile_major_ids_resolve_to_taxonomy_records() -> None:
    assert profile_major_definition("undergraduate-resource-exploration").id == (
        "resource-exploration-engineering"
    )
    assert profile_major_definition("doctoral-geology").id == "geology"


def test_taxonomy_validation_rejects_duplicate_exact_terms() -> None:
    payload = {
        "version": 1,
        "as_of": "2026-09-24",
        "majors": [
            {
                "id": "one",
                "name": "一",
                "student_levels": ["本科"],
                "classification": {"discipline_category": "工学"},
                "exact_terms": ["相同"],
                "english_exact_terms": ["same"],
                "related_terms": ["相关"],
            },
            {
                "id": "two",
                "name": "二",
                "student_levels": ["硕士"],
                "classification": {"discipline_category": "理学"},
                "exact_terms": ["相同"],
                "english_exact_terms": ["same-two"],
                "related_terms": ["相关二"],
            },
        ],
    }

    with pytest.raises(ContractValidationError, match="duplicate exact major term"):
        validate_major_taxonomy(payload)
