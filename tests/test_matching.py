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

    assert category == "三桶油与油服"
    assert score >= 55
    assert band == "强相关"
    assert "资源勘查工程" in tags
    assert extract_degree_levels(text) == ["本科", "硕士", "博士"]


def test_deadline_extraction_understands_chinese_date() -> None:
    text = "请于 2026年10月8日前完成网申，报名截止时间为2026年10月8日。"

    assert extract_deadline(text) == "2026-10-08"


def test_deadline_extraction_uses_the_end_of_a_public_application_window() -> None:
    text = "报名时间：2026.09.09-2026.10.15，请按官方渠道完成投递。"

    assert extract_deadline(text) == "2026-10-15"


def test_deadline_extraction_repairs_digit_spans_from_rich_text_announcements() -> None:
    text = "报名时间：202 6 .09 .09 -202 6 .10 .15。"

    assert extract_deadline(text) == "2026-10-15"


def test_expiry_label_is_a_deadline_not_a_publication_date() -> None:
    text = "中国石油东方物探公司招聘公告 过期时间：2026-11-17"

    assert extract_deadline(text) == "2026-11-17"
    assert extract_published_date(text) is None


def test_geophysical_exploration_terms_are_exposed_as_major_tags() -> None:
    assert "物探" in extract_major_tags("中国石油东方物探公司校园招聘")


def test_external_employer_is_classified_without_losing_major_match() -> None:
    text = "SLB 中国招聘测井与储层评价工程师，要求地质工程或资源勘查工程硕士。"

    assert classify_category(text) == "在华外企与国际机会"
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

    assert category == "在华外企与国际机会"
    assert extract_degree_levels(text) == ["硕士"]
    assert "geophysics" in tags
    assert extract_deadline(text) == "2026-12-31"
    assert parse_date_value("31 December 2026") == "2026-12-31"
    assert score >= 55
    assert band == "强相关"
