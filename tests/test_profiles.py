from __future__ import annotations

from job_hub.profiles import (
    classify_major_match,
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
    assert get_student_profile("master-geophysics") is None
    assert sum(item.degree == "硕士" for item in profiles) == 3
    assert sum(item.degree == "博士" for item in profiles) == 3


def test_major_match_projection_separates_exact_broad_and_public_broad() -> None:
    exact = classify_major_match(job())
    assert exact["level"] == "exact"
    assert exact["publication_status"] == "student_eligible"

    broad_geoscience = classify_major_match(
        job(
            title="\u77ff\u4ea7\u52d8\u67e5\u6280\u672f\u5c97",
            description="\u4e13\u4e1a\u8981\u6c42\uff1a\u77ff\u4ea7\u52d8\u67e5\uff1b\u5b66\u5386\u8981\u6c42\uff1a\u7855\u58eb\u7814\u7a76\u751f\u3002",
            major_tags=["\u77ff\u4ea7\u52d8\u67e5"],
            field_evidence={
                "evidence_scope": "official_html_table_row",
                "\u5c97\u4f4d": "\u77ff\u4ea7\u52d8\u67e5\u6280\u672f\u5c97",
                "\u4e13\u4e1a\u8303\u56f4": "\u77ff\u4ea7\u52d8\u67e5",
                "\u5b66\u5386\u8981\u6c42": "\u7855\u58eb\u7814\u7a76\u751f",
            },
        )
    )
    assert broad_geoscience["level"] == "broad"
    assert broad_geoscience["publication_status"] == "student_eligible"

    broad = classify_major_match(
        job(
            category="\u516c\u52a1\u5458\u4e0e\u9009\u8c03",
            title="\u81ea\u7136\u8d44\u6e90\u7ba1\u7406\u5c97",
            location="\u5317\u4eac",
            field_evidence={
                "evidence_scope": "official_html_table_row",
                "\u5c97\u4f4d": "\u81ea\u7136\u8d44\u6e90\u7ba1\u7406\u5c97",
                "\u4e13\u4e1a\u8303\u56f4": "\u5de5\u5b66\u5927\u7c7b",
                "\u5b66\u5386\u8981\u6c42": "\u7855\u58eb\u7814\u7a76\u751f",
            },
        )
    )
    assert broad["level"] == "broad"


def test_explicit_geophysics_is_a_visible_broad_geoscience_tier() -> None:
    result = evaluate_student_publication(
        job(
            title="岩石物理工程师",
            description="专业要求：测井、岩石物理、地球物理学等相关专业。学历要求：硕士研究生及以上。",
            degree_levels=["硕士"],
            major_tags=["地球物理学"],
            field_evidence={
                "evidence_scope": "official_zhaopin_detail_initial_data",
                "岗位": "岩石物理工程师",
                "专业范围": "测井、岩石物理、地球物理学等相关专业",
                "学历要求": "硕士研究生及以上",
                "官方岗位详情": "https://xiaoyuan.zhaopin.com/job/verified-geophysics",
            },
        )
    )

    assert result.status == "student_eligible"
    assert result.major_match_mode == "broad_geoscience"
    assert result.is_public is True


def test_browser_detail_scopes_pass_the_same_job_level_gate() -> None:
    """A complete browser capture must not be downgraded to pending evidence."""
    for scope in ("official_browser_capture_row", "official_cnpc_browser_job_row"):
        result = evaluate_student_publication(
            job(
                field_evidence={
                    "evidence_scope": scope,
                    "岗位": "油气勘探地质工程师",
                    "专业范围": "地质工程",
                    "学历要求": "硕士研究生",
                }
            )
        )
        assert result.status == "student_eligible"


def test_pipeline_normalized_foreign_location_is_never_public() -> None:
    result = evaluate_student_publication(
        job(country_or_region="境内外混合", location="北京，非洲")
    )

    assert result.status == "out_of_scope"
    assert result.label == "非中国大陆岗位"


def test_internship_and_trainee_roles_are_not_student_employment_rows() -> None:
    for title, description in (
        ("地质实习生", "专业要求：地质工程。学历要求：硕士。"),
        ("地质工程师", "本岗位为见习岗位，专业要求：地质工程。学历要求：硕士。"),
    ):
        result = evaluate_student_publication(
            job(
                title=title,
                description=description,
                field_evidence={
                    "evidence_scope": "official_detail_block",
                    "岗位": title,
                    "专业范围": "地质工程",
                    "学历要求": "硕士",
                },
            )
        )
        assert result.status == "out_of_scope"
        assert result.label == "实习/见习岗位"


def test_internship_experience_preference_does_not_reject_regular_role() -> None:
    result = evaluate_student_publication(
        job(
            description=(
                "校园招聘岗位；有相关实习经历者优先。"
                "专业要求：地质工程。学历要求：硕士。"
            )
        )
    )

    assert result.status == "student_eligible"


def test_procurement_workflow_titles_are_not_geoscience_student_roles() -> None:
    result = evaluate_student_publication(
        job(
            title="招标业务员",
            description=(
                "专业范围：资源勘查类；学历要求：硕士。"
            ),
            field_evidence={
                "evidence_scope": "official_detail_block",
                "岗位": "招标业务员",
                "专业范围": "资源勘查类",
                "学历要求": "硕士",
            },
        )
    )

    assert result.status == "out_of_scope"
    assert result.label == "非地学业务岗位"


def test_social_recruitment_is_kept_private_for_this_phase() -> None:
    result = evaluate_student_publication(
        job(
            description=(
                "社会招聘岗位。专业要求：地质工程；学历要求：硕士。"
            )
        )
    )

    assert result.status == "out_of_scope"
    assert result.label == "社会招聘岗位"


def test_structured_social_type_and_long_experience_clause_are_both_blocking() -> None:
    """Legacy detail rows must not reopen during a taxonomy reindex."""
    result = evaluate_student_publication(
        job(
            title="煤质数据库开发及应用负责人",
            description=(
                "专业要求：煤炭类、地质类、矿产类相关专业，本科及以上学历，"
                "五年以上数据库建设应用或煤炭矿山、地质类、煤质类相关工作经历。"
            ),
            field_evidence={
                "evidence_scope": "official_iguopin_browser_detail",
                "岗位": "煤质数据库开发及应用负责人",
                "专业范围": (
                    "煤炭类、地质类、矿产类相关专业，本科及以上学历，"
                    "五年以上数据库建设应用或煤炭矿山、地质类、煤质类相关工作经历"
                ),
                "学历要求": "本科",
                "职位性质": "社招",
            },
        )
    )

    assert result.status == "out_of_scope"
    assert result.label == "社会招聘岗位"

    experience_only = evaluate_student_publication(
        job(
            title="煤质数据库开发及应用负责人",
            description=(
                "专业要求：煤炭类、地质类、矿产类相关专业，本科及以上学历，"
                "五年以上数据库建设应用或煤炭矿山、地质类、煤质类相关工作经历。"
            ),
            field_evidence={
                "evidence_scope": "official_iguopin_browser_detail",
                "岗位": "煤质数据库开发及应用负责人",
                "专业范围": (
                    "煤炭类、地质类、矿产类相关专业，本科及以上学历，"
                    "五年以上数据库建设应用或煤炭矿山、地质类、煤质类相关工作经历"
                ),
                "学历要求": "本科",
                "职位性质": "校招",
            },
        )
    )

    assert experience_only.status == "out_of_scope"
    assert experience_only.label == "需工作经验"


def test_nested_structured_social_type_is_also_blocking() -> None:
    result = evaluate_student_publication(
        job(
            field_evidence={
                "evidence_scope": "official_iguopin_browser_detail",
                "fields": {
                    "岗位": "地质工程师",
                    "专业范围": "地质工程",
                    "学历要求": "硕士",
                    "职位性质": "社招",
                },
            }
        )
    )

    assert result.status == "out_of_scope"
    assert result.label == "社会招聘岗位"


def test_explicit_postdoctoral_vacancy_is_allowed_despite_platform_social_label() -> None:
    result = evaluate_student_publication(
        job(
            title="2027-油气勘探开发与转化【博士后】",
            description="职位性质：社招。博士后岗位，开展油气勘探开发研究。",
            degree_levels=["博士"],
            field_evidence={
                "evidence_scope": "official_iguopin_browser_detail",
                "岗位": "2027-油气勘探开发与转化【博士后】",
                "专业范围": "地质资源与地质工程、地质学",
                "学历要求": "博士",
                "职位性质": "社招",
            },
        )
    )

    assert result.status == "student_eligible"
    assert "doctoral-geological-resources-engineering" in result.matched_profile_ids


def test_postdoctoral_workstation_researcher_is_not_implicitly_postdoctoral() -> None:
    result = evaluate_student_publication(
        job(
            title="博士后工作站研究员",
            description="职位性质：社招。专业要求：地质学；学历要求：博士。",
            degree_levels=["博士"],
            field_evidence={
                "evidence_scope": "official_iguopin_browser_detail",
                "岗位": "博士后工作站研究员",
                "专业范围": "地质学",
                "学历要求": "博士",
                "职位性质": "社招",
            },
        )
    )

    assert result.status == "out_of_scope"
    assert result.label == "社会招聘岗位"


def test_international_source_is_kept_private_even_with_legacy_opt_in() -> None:
    result = evaluate_student_publication(
        job(
            country_or_region="哈萨克斯坦",
            location="哈萨克斯坦·阿克套",
            field_evidence={
                "evidence_scope": "official_browser_capture_row",
                "岗位": "地质工程师",
                "专业范围": "地质工程",
                "学历要求": "硕士",
                "官方岗位详情": "https://careers.example.test/jobs/1",
            },
        ),
        allow_non_domestic=True,
    )

    assert result.status == "out_of_scope"
    assert result.label == "非中国大陆岗位"


def test_international_source_still_rejects_generic_location_scope() -> None:
    result = evaluate_student_publication(
        job(
            country_or_region="海外",
            location="海外项目",
            field_evidence={
                "evidence_scope": "official_browser_capture_row",
                "岗位": "地质工程师",
                "专业范围": "地质工程",
                "学历要求": "硕士",
            },
        ),
        allow_non_domestic=True,
    )

    assert result.status == "out_of_scope"
    assert result.public_scope == "domestic"


def test_profile_match_requires_both_explicit_major_and_degree_evidence() -> None:
    profile = get_student_profile("master-geological-engineering")
    assert profile is not None

    result = evaluate_profile_match(job(), profile)

    assert result.level == "explicit"
    assert result.label == "明确匹配"
    assert "地质工程" in result.reason
    assert "硕士" in result.reason


def test_lower_education_floor_covers_higher_geoscience_students() -> None:
    result = evaluate_student_publication(
        job(
            title="石油勘探岗",
            description="专业要求：资源勘查工程、地球物理勘查技术。学历要求：大专（高职）。",
            degree_levels=["大专"],
            major_tags=["资源勘查工程", "地球物理"],
            field_evidence={
                "evidence_scope": "official_sinopec_detail_snapshot",
                "岗位": "石油勘探岗",
                "专业范围": "资源勘查工程、地球物理勘查技术",
                "学历要求": "大专（高职）",
            },
        )
    )

    assert result.status == "student_eligible"


def test_degree_floor_hierarchy_preserves_explicit_degree_boundaries() -> None:
    """Official degree floors must not hide doctoral matches or widen exact rows."""
    expected_profiles = {
        "本科": set(),
        "本科及以上": {"master-geology", "doctoral-geology"},
        "硕士": {"master-geology"},
        "硕士研究生及以上": {"master-geology", "doctoral-geology"},
        "研究生及以上": {"master-geology", "doctoral-geology"},
        "博士": {"doctoral-geology"},
        "本科、硕士": {"master-geology"},
    }
    for degree, expected in expected_profiles.items():
        decision = evaluate_student_publication(
            job(
                title="地质学研究岗",
                field_evidence={
                    "evidence_scope": "official_detail_block",
                    "岗位": "地质学研究岗",
                    "专业范围": "地质学",
                    "学历要求": degree,
                },
            )
        )
        if expected:
            assert decision.status == "student_eligible", degree
        else:
            assert decision.status == "out_of_scope", degree
        assert set(decision.matched_profile_ids) == expected, degree


def test_degree_floor_accepts_common_above_wording_without_widening_exact_rows() -> None:
    """Common official wording must preserve the same minimum-degree closure."""
    expected_profiles = {
        "本科以上学历": {"master-geology", "doctoral-geology"},
        "硕士以上学历": {"master-geology", "doctoral-geology"},
        "Bachelor's or higher": {"master-geology", "doctoral-geology"},
        "Master's or higher": {"master-geology", "doctoral-geology"},
    }
    for degree, expected in expected_profiles.items():
        decision = evaluate_student_publication(
            job(
                title="地质学研究岗",
                field_evidence={
                    "evidence_scope": "official_detail_block",
                    "岗位": "地质学研究岗",
                    "专业范围": "地质学",
                    "学历要求": degree,
                },
            )
        )
        assert decision.status == "student_eligible", degree
        assert set(decision.matched_profile_ids) == expected, degree


def test_postdoctoral_title_is_not_treated_as_a_doctoral_degree() -> None:
    decision = evaluate_student_publication(
        job(
            title="地质学博士后",
            field_evidence={
                "evidence_scope": "official_detail_block",
                "岗位": "地质学博士后",
                "专业范围": "地质学",
                "学历要求": "博士后研究人员",
            },
        )
    )

    assert decision.status == "pending_evidence"


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


def test_related_suffix_does_not_hide_explicit_target_for_matching_degree() -> None:
    """An explicit target major remains eligible when the notice says related."""
    decision = evaluate_student_publication(
        job(
            title="地质专业技术岗",
            degree_levels=["硕士"],
            field_evidence={
                "evidence_scope": "official_cmgb_browser_detail",
                "岗位": "地质专业技术岗",
                "专业范围": "地质工程相关专业",
                "学历要求": "硕士",
                "工作地点": "北京",
            },
        )
    )

    assert decision.status == "student_eligible"
    assert decision.matched_profile_ids == ("master-geological-engineering",)


def test_explicit_petroleum_engineering_related_requirement_is_out_of_scope() -> None:
    decision = evaluate_student_publication(
        job(
            title="钻完井工程师",
            field_evidence={
                "evidence_scope": "official_cnooc_browser_detail",
                "岗位": "钻完井工程师",
                "专业范围": "石油工程、油气井工程、油气田开发工程等相关专业",
                "学历要求": "硕士研究生及以上",
                "工作地点": "天津",
            },
        )
    )

    assert decision.status == "out_of_scope"
    assert decision.label == "石油工程方向不匹配"


def test_petroleum_engineering_filter_preserves_ambiguous_geophysical_direction() -> None:
    decision = evaluate_student_publication(
        job(
            title="物探工程师",
            field_evidence={
                "evidence_scope": "official_cnooc_browser_detail",
                "岗位": "物探工程师",
                "专业范围": "石油工程、地球物理学等相关专业",
                "学历要求": "硕士研究生及以上",
                "工作地点": "天津",
            },
        )
    )

    assert decision.status == "student_eligible"
    assert decision.major_match_mode == "broad_geoscience"
    assert decision.label == "专业范围宽泛"


def test_petroleum_engineering_filter_preserves_target_major_with_wrong_degree() -> None:
    decision = evaluate_student_publication(
        job(
            title="油气开发岗",
            degree_levels=["本科"],
            field_evidence={
                "evidence_scope": "official_cnooc_browser_detail",
                "岗位": "油气开发岗",
                "专业范围": "石油工程、地质工程等相关专业",
                "学历要求": "本科",
                "工作地点": "天津",
            },
        )
    )

    assert decision.status == "pending_evidence"
    assert decision.label == "待补岗位级证据"


def test_explicit_graduate_major_does_not_match_unrelated_undergraduate_profile() -> None:
    """A bachelor row naming a graduate-only major stays private."""
    decision = evaluate_student_publication(
        job(
            title="地质专业技术岗",
            degree_levels=["本科"],
            field_evidence={
                "evidence_scope": "official_cmgb_browser_detail",
                "岗位": "地质专业技术岗",
                "专业范围": "地质工程相关专业",
                "学历要求": "本科",
                "工作地点": "山东",
            },
        )
    )

    assert decision.status == "pending_evidence"
    assert decision.matched_profile_ids == ()
    assert "未直接列出本院目标专业" in decision.reason


def test_publication_gate_classifies_named_geology_second_level_discipline_as_similar() -> None:
    decision = evaluate_student_publication(
        job(
            title="环境地球化学研究岗",
            field_evidence={
                "evidence_scope": "official_attachment_row",
                "岗位": "环境地球化学研究岗",
                "专业要求": "070902 地球化学；0830 环境科学与工程",
                "学历要求": "博士研究生",
                "工作地点": "长沙",
            },
        )
    )

    assert decision.status == "student_eligible"
    assert decision.major_match_mode == "broad_geoscience"


def test_publication_gate_classifies_geological_resources_direction_as_similar() -> None:
    decision = evaluate_student_publication(
        job(
            title="工程检测技术岗",
            field_evidence={
                "evidence_scope": "official_attachment_row",
                "岗位": "工程检测技术岗",
                "专业要求": "081802 地球探测与信息技术",
                "学历要求": "硕士研究生及以上",
                "工作地点": "长沙",
            },
        )
    )

    assert decision.status == "pending_evidence"
    assert decision.label == "待核岗位方向"
    assert decision.is_public is False


def test_publication_gate_accepts_explicit_undergraduate_geology_category() -> None:
    decision = evaluate_student_publication(
        job(
            title="勘探地质现场操作岗",
            category="事业单位与人才引进",
            field_evidence={
                "evidence_scope": "official_detail_block",
                "岗位": "勘探地质现场操作岗",
                "专业范围": "地质类、地质学类、地球物理学类等相关专业",
                "学历要求": "大学本科及以上学历",
                "工作地点": "天津、海南",
            },
        )
    )

    assert decision.status == "student_eligible"
    assert "undergraduate-resource-exploration" in decision.matched_profile_ids


def test_broad_engineering_category_matches_engineering_profiles() -> None:
    decision = evaluate_student_publication(
        job(
            title="地质工程师",
            category="事业单位与人才引进",
            field_evidence={
                "evidence_scope": "official_detail_block",
                "岗位": "地质工程师",
                "专业范围": "工学大类",
                "学历要求": "硕士研究生及以上",
                "工作地点": "上海",
            },
        )
    )

    assert decision.status == "student_eligible"
    assert set(decision.matched_profile_ids) == {
        "master-geological-engineering",
        "doctoral-geological-engineering",
        "master-geological-resources-engineering",
        "doctoral-geological-resources-engineering",
    }


def test_broad_science_category_matches_geology_profiles() -> None:
    decision = evaluate_student_publication(
        job(
            title="地学研究岗",
            category="公务员与选调",
            field_evidence={
                "evidence_scope": "official_cmgb_browser_detail",
                "岗位": "地学研究岗",
                "专业范围": "理学大类",
                "学历要求": "硕士研究生及以上",
                "工作地点": "山东",
            },
        )
    )

    assert decision.status == "student_eligible"
    assert set(decision.matched_profile_ids) == {"master-geology", "doctoral-geology"}


def test_nested_management_category_does_not_match_science_discipline() -> None:
    decision = evaluate_student_publication(
        job(
            title="人力资源管理",
            field_evidence={
                "evidence_scope": "official_detail_block",
                "岗位": "人力资源管理",
                "专业范围": "管理学类、心理学类、法学类",
                "学历要求": "硕士研究生",
                "工作地点": "天津",
            },
        )
    )

    assert decision.status == "out_of_scope"


def test_degree_floor_in_major_field_covers_higher_student_profiles() -> None:
    decision = evaluate_student_publication(
        job(
            title="地质测绘岗",
            field_evidence={
                "evidence_scope": "official_detail_block",
                "岗位": "地质测绘岗",
                "专业范围": "本科及以上学历，地质工程、测绘工程等相关专业",
                "学历要求": "本科",
                "工作地点": "长沙",
            },
        )
    )

    assert decision.status == "student_eligible"
    assert "master-geological-engineering" in decision.matched_profile_ids


def test_adjacent_geophysical_direction_is_separate_from_exact_match() -> None:
    decision = evaluate_student_publication(
        job(
            title="地球物理勘查工程师",
            field_evidence={
                "evidence_scope": "official_detail_block",
                "岗位": "地球物理勘查工程师",
                "专业范围": "地球物理勘查、应用地球物理",
                "学历要求": "硕士研究生及以上",
                "工作地点": "北京",
            },
        )
    )

    assert decision.status == "student_eligible"
    assert decision.label == "专业范围宽泛"
    assert decision.major_match_mode == "broad_geoscience"


def test_government_position_without_row_location_stays_private() -> None:
    decision = evaluate_student_publication(
        job(
            source_id="anhui-geology-bureau",
            category="事业单位与人才引进",
            title="专业技术岗位",
            field_evidence={
                "evidence_scope": "official_attachment_row",
                "岗位": "专业技术岗位",
                "专业范围": "地质学、地质资源与地质工程",
                "学历要求": "博士研究生",
            },
        )
    )

    assert decision.status == "pending_evidence"
    assert decision.label == "待补岗位地点"


def test_normalized_enterprise_row_without_location_enters_incomplete_basic_layer() -> None:
    decision = evaluate_student_publication(
        job(
            title="注册土木工程师（岩土）",
            summary="勘查技术与工程、土木工程及相关专业；本科及以上。",
            description="专业要求：勘查技术与工程、土木工程及相关专业。学历要求：本科及以上。",
            major_tags=["勘查技术与工程"],
            country_or_region=None,
            location="",
            field_evidence={
                "evidence_scope": "official_attachment_row",
                "岗位名称": "注册土木工程师（岩土）",
                "专业要求": "勘查技术与工程、土木工程及相关专业",
                "学历要求": "本科及以上",
            },
        )
    )

    assert decision.status == "pending_evidence"
    assert decision.label == "官方信息不完整"
    assert decision.major_match_mode == "incomplete_but_basic"
    assert decision.matched_profile_ids


def test_unrelated_government_position_without_location_is_not_pending_location() -> None:
    """Missing location must not hide a non-geoscience professional mismatch."""
    decision = evaluate_student_publication(
        job(
            source_id="beijing-hrss",
            category="事业单位与人才引进",
            title="肿瘤生物信息中心科研",
            field_evidence={
                "evidence_scope": "official_attachment_row",
                "岗位": "肿瘤生物信息中心科研",
                "专业范围": "生物信息学",
                "学历要求": "硕士研究生、博士研究生",
            },
        )
    )

    assert decision.status == "out_of_scope"
    assert decision.label == "专业不匹配"


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


def test_publication_gate_reads_experience_embedded_in_degree_field() -> None:
    """Portals must not hide an experience clause inside ``学历要求``."""
    decision = evaluate_student_publication(
        job(
            title="Seismic Data Processing Geophysicist (Experienced)",
            field_evidence={
                "evidence_scope": "official_detail_block",
                "岗位": "Seismic Data Processing Geophysicist (Experienced)",
                "专业范围": "Geophysics, Exploration Geophysics",
                "学历要求": (
                    "Bachelor's degree or above in Geophysics; "
                    "Minimum 3+ years of professional seismic data processing experience."
                ),
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


def test_publication_gate_reads_experience_from_one_attachment_row() -> None:
    decision = evaluate_student_publication(
        job(
            title="地质工程师",
            field_evidence={
                "evidence_scope": "official_attachment_row",
                "岗位": "地质工程师",
                "专业范围": "地质工程",
                "学历要求": "硕士研究生",
                "row_text": (
                    "岗位名称：地质工程师；专业要求：地质工程；学历要求：硕士研究生；"
                    "任职条件：具有3年以上地质工作经验"
                ),
            },
        )
    )

    assert decision.status == "out_of_scope"
    assert decision.label == "需工作经验"


def test_publication_gate_rejects_suffix_and_chinese_numeral_experience_forms() -> None:
    for requirement in (
        "本科及以上学历，地质工程专业，项目管理相关工作经验5年以上。",
        "本科及以上学历，地质工程专业，具有五年以上工程勘察经验。",
    ):
        decision = evaluate_student_publication(
            job(
                title="工程地质技术岗",
                field_evidence={
                    "evidence_scope": "official_attachment_row",
                    "岗位": "工程地质技术岗",
                    "岗位要求": requirement,
                    "学历要求": requirement,
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
            title="油气勘探地质工程师",
            category="事业单位与人才引进",
            degree_levels=["本科", "硕士"],
            field_evidence={
                "evidence_scope": "official_html_table_row",
                "岗位": "油气勘探地质工程师",
                "专业范围": "不限专业",
                "学历要求": "本科、硕士研究生",
                "工作地点": "北京",
            },
        )
    )

    assert decision.status == "unrestricted_eligible"
    assert decision.is_public is True


def test_publication_gate_rejects_unrestricted_enterprise_role() -> None:
    decision = evaluate_student_publication(
        job(
            title="综合管理岗",
            category="三桶油与油服",
            degree_levels=["本科", "硕士"],
            field_evidence={
                "evidence_scope": "official_html_table_row",
                "岗位": "综合管理岗",
                "专业范围": "不限专业",
                "学历要求": "本科、硕士研究生",
                "工作地点": "北京",
            },
        )
    )

    assert decision.status == "out_of_scope"
    assert decision.label == "非公共岗位不限专业"


def test_publication_gate_rejects_private_education_company_unrestricted_role() -> None:
    decision = evaluate_student_publication(
        job(
            title="主讲教师",
            employer="北京学而思教育科技有限公司",
            category="科研院所、高校与博士后",
            degree_levels=["本科"],
            field_evidence={
                "evidence_scope": "official_iguopin_browser_detail",
                "岗位": "主讲教师",
                "专业范围": "专业不限",
                "学历要求": "本科",
                "工作地点": "南通",
            },
        )
    )

    assert decision.status == "out_of_scope"
    assert decision.label == "非公共岗位不限专业"


def test_publication_gate_rejects_unrestricted_university_identity() -> None:
    decision = evaluate_student_publication(
        job(
            title="实验技术员",
            employer="中国石油大学（北京）",
            category="科研院所、高校与博士后",
            degree_levels=["本科"],
            field_evidence={
                "evidence_scope": "official_html_table_row",
                "岗位": "实验技术员",
                "专业范围": "不限专业",
                "学历要求": "本科",
                "工作地点": "北京",
            },
        )
    )

    assert decision.status == "out_of_scope"
    assert decision.label == "非公共岗位不限专业"


def test_publication_gate_rejects_company_research_institute_unrestricted_role() -> None:
    decision = evaluate_student_publication(
        job(
            title="2027届校园招聘",
            employer="中国能源建设集团广东省电力设计研究院有限公司",
            category="科研院所、高校与博士后",
            degree_levels=["硕士"],
            field_evidence={
                "evidence_scope": "official_html_table_row",
                "岗位": "2027届校园招聘",
                "专业范围": "不限专业",
                "学历要求": "硕士",
                "工作地点": "广东省广州市",
            },
        )
    )

    assert decision.status == "out_of_scope"
    assert decision.label == "非公共岗位不限专业"


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
