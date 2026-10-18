from job_hub.profiles import evaluate_student_publication
from job_hub.db import Database
from job_hub.publication_revalidation import revalidate_persisted_publication_gates


def _job(*, category: str, major: str, degree: str = "硕士研究生") -> dict[str, object]:
    return {
        "id": 1,
        "title": "测试岗位",
        "category": category,
        "field_evidence": {
            "evidence_scope": "official_detail_block",
            "岗位": "测试岗位",
            "专业范围": major,
            "学历要求": degree,
            "工作地点": "北京",
        },
    }


def test_enterprise_requires_one_of_four_named_graduation_majors() -> None:
    for major, degree in (
        ("资源勘查工程", "本科"),
        ("地质学", "硕士研究生"),
        ("地质工程", "硕士研究生"),
        ("地质资源与地质工程", "硕士研究生"),
    ):
        decision = evaluate_student_publication(
            _job(category="油气上游业主与研究机构", major=major, degree=degree)
        )
        assert decision.status == "student_eligible"
        assert decision.major_match_mode == "exact_four_names"

    decision = evaluate_student_publication(
        _job(category="油气上游业主与研究机构", major="地球探测与信息技术")
    )
    assert decision.status == "pending_evidence"
    assert decision.is_public is False


def test_enterprise_broad_category_and_catalogue_code_are_not_exact_major_matches() -> None:
    for major in ("地质类", "081802 地球探测与信息技术", "地球物理学"):
        decision = evaluate_student_publication(
            _job(category="油气上游业主与研究机构", major=major)
        )
        if major == "地质类":
            assert decision.status == "out_of_scope"
        else:
            assert decision.status == "pending_evidence"
            assert decision.is_public is False


def test_adjacent_major_needs_geoscience_role_not_just_major_text() -> None:
    cases = (
        ("出版专员", "地球物理学类", "【岗位职责】负责期刊编辑与出版。"),
        ("投资管理部矿产资源处业务经理", "地球物理学", "【岗位职责】负责地质项目投资。"),
        ("集团管培生", "地球物理学", "【岗位职责】轮岗学习地质项目。"),
        ("矿物加工工程师", "地质资源与地质工程类", "岗位描述：负责选矿工艺设计。"),
        ("高中地理教师", "地球物理学", "岗位职责：负责课堂教学。"),
        ("工程检测技术岗", "地球探测与信息技术", "专业要求：地球探测与信息技术。"),
    )
    for title, major, description in cases:
        candidate = _job(category="油气上游业主与研究机构", major=major)
        candidate["title"] = title
        candidate["field_evidence"]["岗位"] = title
        candidate["description"] = description
        decision = evaluate_student_publication(candidate)
        assert decision.is_public is False, title


def test_adjacent_major_with_independent_geoscience_role_evidence_is_broad() -> None:
    for title, description in (
        ("地质技术员", "专业要求：地球物理学。"),
        ("地球物理监测技术研发岗", "专业要求：地球物理学。"),
        ("岩矿鉴定技术员", "专业要求：地球物理学。"),
        (
            "天津分公司科研技术一岗",
            "【工作职责】负责矿区地质勘查、储量核查和物探数据分析。"
            "【任职条件】熟悉地震勘探。",
        ),
        ("反演算法与数据分析岗", "职责描述：开发地震数据反演算法。任职要求：博士学历。"),
    ):
        candidate = _job(category="油气上游业主与研究机构", major="地球物理学")
        candidate["title"] = title
        candidate["field_evidence"]["岗位"] = title
        candidate["description"] = description
        decision = evaluate_student_publication(candidate)
        assert decision.status == "student_eligible", title
        assert decision.major_match_mode == "broad_geoscience"


def test_split_location_must_match_the_published_variant() -> None:
    candidate = _job(category="油气上游业主与研究机构", major="地质学")
    candidate["location"] = "福州-闽侯县，塔城-托里县，山南"
    candidate["field_evidence"].update({
        "地点拆分基准岗位": "official-job-1",
        "官方工作地点": "塔城-托里县",
        "工作地点": "福州-闽侯县，塔城-托里县，山南",
    })
    decision = evaluate_student_publication(candidate)
    assert decision.status == "pending_evidence"
    assert decision.label == "待核拆分地点"

    candidate["location"] = "塔城-托里县"
    assert evaluate_student_publication(candidate).status == "student_eligible"


def test_enterprise_category_wording_is_not_a_named_major() -> None:
    decision = evaluate_student_publication(
        _job(category="油气上游业主与研究机构", major="地质学类")
    )
    assert decision.status == "out_of_scope"


def test_enterprise_major_match_rejects_longer_compound_names() -> None:
    for major in ("地质工程技术", "地质学类", "资源勘查工程技术"):
        decision = evaluate_student_publication(
            _job(category="油气上游业主与研究机构", major=major)
        )
        assert decision.status == "out_of_scope"


def test_enterprise_major_match_checks_later_occurrence_after_invalid_prefix() -> None:
    decision = evaluate_student_publication(
        _job(
            category="油气上游业主与研究机构",
            major="地质工程技术、地质工程",
        )
    )
    assert decision.status == "student_eligible"
    assert decision.major_match_mode == "exact_four_names"


def test_enterprise_major_match_accepts_canonical_name_with_requirement_connector() -> None:
    decision = evaluate_student_publication(
        _job(category="油气上游业主与研究机构", major="地质工程及相关专业")
    )
    assert decision.status == "student_eligible"
    assert decision.major_match_mode == "exact_four_names"


def test_enterprise_major_match_accepts_named_major_after_list_connector() -> None:
    for major in (
        "地质学或资源勘查工程等相关专业",
        "石油工程及资源勘查工程",
        "地质学和资源勘查工程",
        "石油工程与资源勘查工程",
    ):
        decision = evaluate_student_publication(
            _job(category="自然资源、地调与地勘", major=major, degree="本科")
        )
        assert decision.status == "student_eligible", major
        assert decision.major_match_mode == "exact_four_names"
        assert decision.matched_profile_ids == ("undergraduate-resource-exploration",)


def test_enterprise_major_match_rejects_target_embedded_in_longer_prefix() -> None:
    decision = evaluate_student_publication(
        _job(category="自然资源、地调与地勘", major="石油资源勘查工程", degree="本科")
    )
    assert decision.status == "out_of_scope"
    assert decision.matched_profile_ids == ()


def test_public_position_table_may_use_broad_category_or_official_code() -> None:
    category_decision = evaluate_student_publication(
        _job(category="事业单位与人才引进", major="工学大类")
    )
    assert category_decision.status == "student_eligible"
    assert "master-geological-engineering" in category_decision.matched_profile_ids

    code_decision = evaluate_student_publication(
        _job(
            category="公务员与选调",
            major="081802 地球探测与信息技术",
        )
    )
    assert code_decision.status == "student_eligible"
    assert "master-geological-resources-engineering" in code_decision.matched_profile_ids


def test_postdoctoral_and_university_roles_do_not_inherit_public_broad_major_rules() -> None:
    for category in ("博士后与科研助理", "科研院所、高校与博士后"):
        decision = evaluate_student_publication(
            _job(category=category, major="不限专业")
        )
        assert decision.status == "out_of_scope"
        assert decision.major_match_mode == ""

        broad = evaluate_student_publication(
            _job(category=category, major="工学大类")
        )
        assert broad.status == "out_of_scope"


def test_postdoctoral_role_without_doctoral_degree_evidence_stays_pending() -> None:
    decision = evaluate_student_publication(
        _job(
            category="博士后与科研助理",
            major="地质学",
            degree="博士后研究人员",
        )
    )
    assert decision.status == "pending_evidence"


def test_publication_revalidation_is_downgrade_only(tmp_path) -> None:
    database = Database(tmp_path / "jobs.sqlite3")
    database.initialize()
    source = {
        "id": "test-source",
        "name": "测试来源",
        "publisher": "测试单位",
        "homepage_url": "https://jobs.example.cn",
        "source_type": "manual",
        "category": "科研院所、高校与博士后",
        "source_tier": "A",
        "enabled": True,
        "config": {},
    }
    database.upsert_source(source)
    job = {
        **_job(category="科研院所、高校与博士后", major="不限专业"),
        "source_id": "test-source",
        "source_name": "测试来源",
        "source_tier": "A",
        "source_url": "https://jobs.example.cn/1",
        "fingerprint": "revalidation-test-1",
        "content_hash": "revalidation-content-1",
        "official_evidence_url": "https://jobs.example.cn/1",
        "employer": "中国石油大学（北京）",
        "country_or_region": "中国大陆",
        "location": "北京",
        "deadline_date": "2099-01-01",
        "publication_status": "pending_evidence",
        "publication_basis": {},
        "degree_levels": ["本科"],
        "major_tags": [],
        "relevance_score": 0,
        "relevance_band": "拓展机会",
        "category": "科研院所、高校与博士后",
        "status": "open",
    }
    job_id, _ = database.save_job(job)
    with database.transaction() as connection:
        connection.execute(
            "UPDATE jobs SET publication_status='unrestricted_eligible', publication_basis_json=? WHERE id=?",
            ('{"major_match_mode":"public_unrestricted_rule"}', job_id),
        )
    preview = revalidate_persisted_publication_gates(database)
    assert preview["apply"] is False
    assert preview["changed_rows"] == 0
    assert preview["changes"][0]["after_status"] == "out_of_scope"
    applied = revalidate_persisted_publication_gates(database, apply=True)
    assert applied["changed_rows"] == 1
    assert database.find_job(job_id, student_visible=False)["publication_status"] == "out_of_scope"
