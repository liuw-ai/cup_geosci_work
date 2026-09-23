from job_hub.matching import (
    classify_category,
    extract_deadline,
    extract_degree_levels,
    extract_major_tags,
    extract_published_date,
    parse_date_value,
    score_relevance,
)


def test_geoscience_oil_and_gas_post_is_strong_match() -> None:
    text = (
        "中国石油某油田 2027 届校园招聘，面向资源勘查工程、地质工程、"
        "地质学和地球物理专业本科、硕士、博士毕业生。"
    )
    category = classify_category(text)
    score, band, tags = score_relevance(text, "A", category)

    assert category == "油气上游业主与研究机构"
    assert score >= 55
    assert band == "强相关"
    assert "资源勘查工程" in tags
    assert extract_degree_levels(text) == ["本科", "硕士", "博士"]


def test_unverified_body_keywords_cannot_be_strong_match() -> None:
    score, band, tags = score_relevance(
        "中国石油炼化设备技术招聘，正文提到油气地质大赛和地球物理大赛。",
        "B",
        "油气上游业主与研究机构",
        qualification_evidence=False,
    )

    assert score < 55
    assert band != "强相关"
    assert tags


def test_deadline_extraction_understands_chinese_date() -> None:
    text = "请于 2026年10月8日前完成网申，报名截止时间为2026年10月8日。"

    assert extract_deadline(text) == "2026-10-08"


def test_deadline_extraction_uses_the_end_of_a_public_application_window() -> None:
    text = "报名时间：2026.09.09-2026.10.15，请按官方渠道完成投递。"

    assert extract_deadline(text) == "2026-10-15"


def test_deadline_extraction_repairs_digit_spans_from_rich_text_announcements() -> None:
    text = "报名时间：202 6 .09 .09 -202 6 .10 .15。"

    assert extract_deadline(text) == "2026-10-15"


def test_deadline_extraction_handles_same_year_application_window_with_times() -> None:
    text = "网上报名时间：2026年9月9日9:00至9月16日17:00。"

    assert extract_deadline(text) == "2026-09-16"


def test_deadline_extraction_prefers_overall_rolling_window_over_first_batch() -> None:
    text = (
        "报名时间：自公告发布之日起至2026年10月31日止，拟分批次进行。"
        "第一批次：报名截止时间为2026年5月18日。"
    )

    assert extract_deadline(text) == "2026-10-31"


def test_expiry_label_is_a_deadline_not_a_publication_date() -> None:
    text = "中国石油东方物探公司招聘公告 过期时间：2026-11-17"

    assert extract_deadline(text) == "2026-11-17"
    assert extract_published_date(text) is None


def test_geophysical_exploration_terms_are_exposed_as_major_tags() -> None:
    assert "物探" in extract_major_tags("中国石油东方物探公司校园招聘")


def test_external_employer_is_classified_without_losing_major_match() -> None:
    text = "SLB 中国招聘测井与储层评价工程师，要求地质工程或资源勘查工程硕士。"

    assert classify_category(text) == "油气工程技术服务"
    assert {"测井", "储层", "地质工程", "资源勘查工程"}.issubset(
        set(extract_major_tags(text))
    )


def test_english_official_job_fields_are_matched_and_dated() -> None:
    text = (
        "Halliburton is hiring a Geophysicist for a reservoir characterization "
        "role. A Master's degree in geophysics is required. "
        "Application deadline: December 31, 2026."
    )
    category = classify_category(text)
    score, band, tags = score_relevance(text, "A", category)

    assert category == "油气工程技术服务"
    assert extract_degree_levels(text) == ["硕士"]
    assert "geophysics" in tags
    assert extract_deadline(text) == "2026-12-31"
    assert parse_date_value("31 December 2026") == "2026-12-31"
    assert score >= 55
    assert band == "强相关"


def test_employment_taxonomy_separates_operator_service_and_scope() -> None:
    from job_hub.employers import classify_employment

    operator = classify_employment("中国石油某油田勘探开发研究院地质工程师招聘")
    service = classify_employment("中国石油东方物探公司物探解释岗位招聘")
    international = classify_employment(
        "Halliburton Geophysicist", location="Kuala Lumpur, 10, MY, 50400"
    )

    assert operator.category == "油气上游业主与研究机构"
    assert operator.affiliation == "中国石油体系"
    assert service.category == "油气工程技术服务"
    assert service.employer_type == "油气地球物理技术服务企业"
    assert service.affiliation == "中国石油体系"
    assert international.category == "油气工程技术服务"
    assert international.affiliation == "国际企业"
    assert international.opportunity_scope == "海外岗位"


def test_service_affiliation_uses_employer_before_competitor_mentions() -> None:
    from job_hub.employers import enrich_job

    job = enrich_job(
        {
            "title": "中国石油东方物探公司物探地质研发岗招聘",
            "employer": "中国石油东方物探公司",
            "group_name": "中国石油大学（北京）",
            "category": "油气工程技术服务",
            "description": (
                "东方物探为中国石油体系内专业化子公司，产品也服务中石化、"
                "中海油等客户。"
            ),
            "summary": "面向地质资源与地质工程专业。",
            "location": "北京、成都",
        }
    )

    assert job["category"] == "油气工程技术服务"
    assert job["category_label"] == "油气工程技术服务"
    assert job["affiliation"] == "中国石油体系"


def test_upstream_category_uses_precise_student_facing_label() -> None:
    from job_hub.employers import enrich_job

    job = enrich_job(
        {
            "title": "油气地质研究岗",
            "employer": "中国石油勘探开发研究院",
            "category": "油气上游业主与研究机构",
        }
    )

    assert job["category"] == "油气上游业主与研究机构"
    assert job["category_label"] == "油气勘探开发运营与研究机构"


def test_operator_affiliation_uses_employer_before_body_mentions() -> None:
    from job_hub.employers import classify_employment

    profile = classify_employment(
        "中国石油某油田公开招聘，项目同时服务中国石化和中国海油客户。",
        source_category="油气上游业主与研究机构",
        identity_text="中国石油某油田",
    )

    assert profile.category == "油气上游业主与研究机构"
    assert profile.affiliation == "中国石油体系"


def test_specific_petrochemical_subsidiary_beats_parent_group_alias() -> None:
    from job_hub.employers import resolve_employer

    resolved = resolve_employer("中国石油天然气股份有限公司呼和浩特石化分公司")

    assert resolved is not None
    assert resolved["canonical_employer_id"] == "cnpc-hohhot-petrochemical"
    assert resolved["category"] == "管网、炼化与综合能源"


def test_official_source_category_beats_generic_body_keyword() -> None:
    from job_hub.employers import classify_employment

    profile = classify_employment(
        "中国煤炭地质总局公开招聘，服务国家能源资源安全和地质调查。",
        source_category="自然资源、地调与地勘",
        identity_text="中国煤炭地质总局公开招聘公告 中国煤炭地质总局",
    )

    assert profile.category == "自然资源、地调与地勘"
    assert profile.affiliation == "中央地勘单位"
