from __future__ import annotations

from job_hub.profiles import (
    evaluate_profile_match,
    evaluate_student_publication,
    get_student_profile,
    list_student_profiles,
)
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
        "field_evidence": {
            "evidence_scope": "official_html_table_row",
            "岗位": "油气勘探地质工程师",
            "专业范围": "地质工程",
            "学历要求": "硕士",
        },
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


def test_profile_match_does_not_trust_unstructured_major_tags() -> None:
    profile = get_student_profile("master-geology")
    assert profile is not None

    result = evaluate_profile_match(
        job(
            title="中国石油炼化设备技术招聘公告",
            summary="招聘标准提到全国油气地质大赛。",
            description="招聘岗位专业为机械工程、化工过程机械。",
            major_tags=["油气地质", "地球物理"],
            degree_levels=["本科"],
            field_evidence={},
            relevance_score=100,
        ),
        profile,
    )

    assert result.level == "not_recommended"


def test_profile_match_hides_missing_row_level_evidence() -> None:
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

    assert result.level == "not_recommended"
    assert result.label == "未见直接匹配"


def test_first_level_discipline_does_not_become_narrow_geological_engineering_match() -> None:
    profile = get_student_profile("master-geological-engineering")
    assert profile is not None

    result = evaluate_profile_match(
        job(
            major_tags=["地质资源与地质工程"],
            field_evidence={
                "evidence_scope": "official_html_table_row",
                "岗位": "油气勘探地质工程师",
                "专业范围": "地质资源与地质工程",
                "学历要求": "硕士",
            },
            description="专业范围：地质资源与地质工程；学历要求：硕士。",
        ),
        profile,
    )

    assert result.level == "not_recommended"
    assert result.label == "未见直接匹配"


def test_profile_match_translates_explicit_english_degree_and_major_evidence() -> None:
    profile = get_student_profile("master-geology")
    assert profile is not None

    result = evaluate_profile_match(
        job(
            title="Geologist",
            degree_levels=["本科", "硕士"],
            major_tags=["geology", "geophysics"],
            field_evidence={
                "evidence_scope": "official_detail_block",
                "岗位": "Geologist",
                "专业范围": "Geology, Geophysics",
                "学历要求": "Master's",
            },
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

    assert result.level == "not_recommended"


def test_publication_gate_excludes_refinery_mechanical_role() -> None:
    decision = evaluate_student_publication(
        job(
            title="炼化设备技术",
            degree_levels=["本科", "硕士"],
            field_evidence={
                "岗位": "炼化设备技术",
                "evidence_scope": "official_html_table_row",
                "专业范围": "过程装备与控制工程、机械工程、电气工程及其自动化",
                "学历要求": "本科、硕士研究生",
            },
        )
    )

    assert decision.status == "out_of_scope"
    assert decision.is_public is False


def test_publication_gate_accepts_explicit_target_major() -> None:
    decision = evaluate_student_publication(
        job(
            degree_levels=["本科", "硕士"],
            field_evidence={
                "evidence_scope": "official_html_table_row",
                "岗位": "油气勘探地质工程师",
                "专业范围": "资源勘查工程、地质工程",
                "学历要求": "本科、硕士研究生",
            },
        )
    )

    assert decision.status == "student_eligible"
    assert "undergraduate-resource-exploration" in decision.matched_profile_ids
    assert "master-geological-engineering" in decision.matched_profile_ids


def test_publication_gate_rejects_experienced_role_for_current_students() -> None:
    decision = evaluate_student_publication(
        job(
            title="技术总工",
            field_evidence={
                "evidence_scope": "official_attachment_row",
                "岗位": "技术总工",
                "岗位要求": "大学本科以上学历，资源勘查工程专业毕业，3年以上相关工作经验。",
                "学历要求": "大学本科以上学历，资源勘查工程专业毕业，3年以上相关工作经验。",
            },
        )
    )

    assert decision.status == "out_of_scope"
    assert decision.label == "需工作经验"
    assert decision.matched_profile_ids == ()


def test_publication_gate_rejects_profession_qualified_experience_requirement() -> None:
    decision = evaluate_student_publication(
        job(
            title="地质专业技术人员",
            field_evidence={
                "evidence_scope": "official_role_section",
                "岗位": "地质专业技术人员",
                "岗位要求": "地质相关专业本科及以上学历，具有5年以上地质工作经验。",
                "学历要求": "地质相关专业本科及以上学历，具有5年以上地质工作经验。",
            },
        )
    )

    assert decision.status == "out_of_scope"
    assert decision.label == "需工作经验"


def test_publication_gate_keeps_explicit_graduate_exception() -> None:
    decision = evaluate_student_publication(
        job(
            title="生产地质工程师",
            field_evidence={
                "evidence_scope": "official_detail_block",
                "岗位": "生产地质工程师",
                "岗位要求": (
                    "本科及以上学历，地质学相关专业，具有2年以上矿山工作经验；"
                    "优秀应届毕业生也可投递。"
                ),
                "学历要求": "本科及以上学历，地质学相关专业。",
            },
        )
    )

    assert decision.status == "student_eligible"
    assert set(decision.matched_profile_ids) == {"master-geology", "doctoral-geology"}


def test_publication_gate_recognizes_or_above_degree_wording() -> None:
    decision = evaluate_student_publication(
        job(
            title="地质工程师",
            field_evidence={
                "evidence_scope": "official_detail_block",
                "岗位": "地质工程师",
                "岗位要求": "地质工程专业，全日制本科或以上学历。",
                "学历要求": "全日制本科或以上学历。",
            },
        )
    )

    assert decision.status == "student_eligible"
    assert "master-geological-engineering" in decision.matched_profile_ids
    assert "doctoral-geological-engineering" in decision.matched_profile_ids


def test_publication_gate_does_not_promote_english_responsibility_to_major_requirement() -> None:
    decision = evaluate_student_publication(
        job(
            title="Subsurface Analyst",
            field_evidence={
                "evidence_scope": "official_detail_block",
                "岗位": "Subsurface Analyst",
                "岗位要求": (
                    "Responsibilities include reviewing geology reports. "
                    "Bachelor's degree in engineering is required."
                ),
                "学历要求": "Bachelor's degree in engineering is required.",
            },
        )
    )

    assert decision.status == "out_of_scope"
    assert decision.is_public is False


def test_publication_gate_keeps_official_unrestricted_major_opportunity() -> None:
    decision = evaluate_student_publication(
        job(
            degree_levels=["本科", "硕士"],
            field_evidence={
                "evidence_scope": "official_html_table_row",
                "岗位": "油气勘探地质工程师",
                "专业范围": "不限专业",
                "学历要求": "本科、硕士研究生",
            },
        )
    )

    assert decision.status == "unrestricted_eligible"
    assert decision.is_public is True


def test_publication_gate_rejects_unbound_announcement_level_qualification() -> None:
    decision = evaluate_student_publication(
        job(
            field_evidence={
                "专业范围": "地质工程",
                "学历要求": "硕士研究生",
            },
        )
    )

    assert decision.status == "pending_evidence"
    assert decision.is_public is False


def test_publication_gate_does_not_use_degree_mentions_outside_the_job_field() -> None:
    decision = evaluate_student_publication(
        job(
            description="本单位另有硕士人才计划。",
            field_evidence={
                "evidence_scope": "official_html_table_row",
                "岗位": "油气勘探地质工程师",
                "专业范围": "地质工程",
                "学历要求": "相关学历",
            },
        )
    )

    assert decision.status == "pending_evidence"
    assert decision.is_public is False


def test_publication_gate_keeps_related_english_discipline_private() -> None:
    decision = evaluate_student_publication(
        job(
            title="Seismic Data Processing Geophysicist",
            field_evidence={
                "evidence_scope": "official_detail_block",
                "岗位": "Seismic Data Processing Geophysicist",
                "岗位要求": (
                    "Bachelor's degree or above in Petroleum Geology or other "
                    "related geoscience majors."
                ),
                "学历要求": "Bachelor's degree or above",
            },
        )
    )

    assert decision.status == "pending_evidence"
    assert decision.is_public is False


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
