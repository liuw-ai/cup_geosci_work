from __future__ import annotations

from job_hub.profiles import evaluate_profile_match, get_student_profile, list_student_profiles
from job_hub.simulation import build_synthetic_cohort, simulate_cohort


def job(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": 1,
        "title": "油气勘探地质工程师",
        "employer": "测试能源集团",
        "summary": "面向地质工程硕士毕业生。",
        "description": "专业范围：地质工程。学历要求：硕士。",
        "degree_levels": ["硕士"],
        "major_tags": ["地质工程", "油气地质"],
        "field_evidence": {"专业范围": "地质工程", "学历要求": "硕士"},
        "category": "三桶油与油服",
        "relevance_score": 88,
    }
    base.update(overrides)
    return base


def test_supported_profiles_exactly_cover_requested_tracks() -> None:
    profiles = list_student_profiles()

    assert len(profiles) == 7
    assert get_student_profile("undergraduate-resource-exploration").label == (
        "本科 · 资源勘查工程"
    )
    assert sum(item.degree == "硕士" for item in profiles) == 3
    assert sum(item.degree == "博士" for item in profiles) == 3


def test_profile_match_requires_both_explicit_major_and_degree_evidence() -> None:
    profile = get_student_profile("master-geological-engineering")
    assert profile is not None

    result = evaluate_profile_match(job(), profile)

    assert result.level == "explicit"
    assert result.label == "明确匹配"
    assert "地质工程" in result.reason
    assert "硕士" in result.reason


def test_profile_match_marks_related_or_missing_fields_for_original_notice_review() -> None:
    profile = get_student_profile("doctoral-geology")
    assert profile is not None

    result = evaluate_profile_match(
        job(
            degree_levels=[],
            major_tags=["地球物理", "油气地质"],
            description="油气勘探相关岗位，公告未列出具体学历和专业范围。",
        ),
        profile,
    )

    assert result.level == "review"
    assert result.label == "需核验原公告"


def test_first_level_discipline_does_not_become_narrow_geological_engineering_match() -> None:
    profile = get_student_profile("master-geological-engineering")
    assert profile is not None

    result = evaluate_profile_match(
        job(
            major_tags=["地质资源与地质工程"],
            field_evidence={"专业范围": "地质资源与地质工程", "学历要求": "硕士"},
            description="专业范围：地质资源与地质工程；学历要求：硕士。",
        ),
        profile,
    )

    assert result.level == "review"
    assert result.label == "需核验原公告"


def test_profile_match_translates_explicit_english_degree_and_major_evidence() -> None:
    profile = get_student_profile("master-geology")
    assert profile is not None

    result = evaluate_profile_match(
        job(
            title="Geologist",
            degree_levels=["本科", "硕士"],
            major_tags=["geology", "geophysics"],
            field_evidence={"专业范围": "Geology, Geophysics", "学历要求": "Master's"},
            description=(
                "Requirements: Bachelor's or Master's degree in Geology, "
                "Geophysics, or a related discipline."
            ),
        ),
        profile,
    )

    assert result.level == "explicit"
    assert "geology" in result.reason


def test_profile_match_does_not_translate_a_title_only_english_discipline() -> None:
    profile = get_student_profile("master-geology")
    assert profile is not None

    result = evaluate_profile_match(
        job(
            title="Geologist",
            degree_levels=["硕士"],
            major_tags=["geology"],
            description=(
                "The Geologist interprets seismic data. A master's degree in "
                "engineering is required."
            ),
        ),
        profile,
    )

    assert result.level == "review"


def test_lower_degree_candidate_is_not_recommended_when_notice_requires_higher_degree() -> None:
    profile = get_student_profile("undergraduate-resource-exploration")
    assert profile is not None

    result = evaluate_profile_match(
        job(
            degree_levels=["硕士", "博士"],
            major_tags=["资源勘查工程"],
            description="面向资源勘查工程硕士、博士毕业生。",
        ),
        profile,
    )

    assert result.level == "not_recommended"
    assert result.label == "学历不匹配"


def test_synthetic_cohort_is_fixed_at_one_hundred_without_personal_data() -> None:
    cohort = build_synthetic_cohort()

    assert len(cohort) == 100
    assert cohort[0].id == "GS-001"
    assert cohort[-1].id == "GS-100"
    assert {student.profile_id for student in cohort} == {
        item.id for item in list_student_profiles()
    }


def test_simulation_reports_coverage_for_all_one_hundred_students() -> None:
    result = simulate_cohort([job()])

    assert result["cohort_size"] == 100
    assert sum(item["count"] for item in result["distribution"]) == 100
    assert len(result["students"]) == 100
    assert result["summary"]["students_with_explicit_match"] == 14
    assert all("student_id" in item for item in result["students"])
