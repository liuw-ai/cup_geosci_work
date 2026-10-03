import json

import pytest

from job_hub.profiles import evaluate_student_publication
from job_hub.zhaopin_detail import ZhaopinDetailError, parse_zhaopin_detail_html


DETAIL_URL = "https://xiaoyuan.zhaopin.com/job/CC258591510J40972459115"


def _html(*, number: str = "CC258591510J40972459115", deadline: str = "2027-10-01 00:00:00") -> str:
    payload = {
        "main": {
            "positionDetail": {
                "positionNumber": number,
                "positionName": "勘探地质助理工程师",
                "education": "硕士",
                "workAddress": "深圳市南山区后海滨路",
                "dateEnd": deadline,
                "dateStart": "2026-09-30 21:58:06",
                "recruitNumber": 0,
                "jobDesc": "学历要求：硕士研究生及以上；专业要求：地质学类、地质工程、地质资源与地质工程。",
                "positionURL": DETAIL_URL,
            },
            "campusJobDetail": {
                "companyName": "中国海油-中海石油（中国）有限公司深圳分公司",
                "applyEndTime": 0 if not deadline else 1793548799999,
            },
        }
    }
    return "<html><script>window.__INITIAL_DATA__ = " + json.dumps(payload, ensure_ascii=False) + ";</script></html>"


def test_parse_zhaopin_detail_extracts_job_level_evidence() -> None:
    parsed = parse_zhaopin_detail_html(
        _html(), detail_url=DETAIL_URL, expected_job_number="CC258591510J40972459115"
    )

    assert parsed["title"] == "勘探地质助理工程师"
    assert parsed["employer"].startswith("中国海油")
    assert parsed["degree"] == "硕士"
    assert parsed["location"].startswith("深圳")
    assert parsed["deadline"] == "2027-10-01"
    assert parsed["quantity"] == "若干（官方未披露具体人数）"
    assert parsed["evidence"]["evidence_scope"] == "official_zhaopin_detail_initial_data"
    assert "地质资源与地质工程" in parsed["evidence"]["专业范围"]
    assert "学历要求" not in parsed["evidence"]["专业范围"]
    assert "岗位职责" not in parsed["evidence"]["专业范围"]
    assert parsed["evidence"]["学历要求"] == "硕士研究生及以上"


def test_parse_zhaopin_detail_preserves_degree_floor_from_requirement_block() -> None:
    payload = {
        "main": {
            "positionDetail": {
                "positionNumber": "CC258591510J40972459715",
                "positionName": "钻井地质助理工程师",
                # The ATS enum is only the lowest listed degree.  The detail
                # requirement is the authoritative "and above" wording.
                "education": "本科",
                "workAddress": "深圳市南山区后海滨路",
                "dateEnd": "2027-10-01 00:00:00",
                "dateStart": "2026-09-30 21:58:06",
                "recruitNumber": 1,
                "jobDesc": "学历要求：本科及以上；专业要求：地质工程、石油工程等相关专业。",
                "positionURL": "https://xiaoyuan.zhaopin.com/job/CC258591510J40972459715",
            },
            "campusJobDetail": {
                "companyName": "中国海油-中海石油（中国）有限公司深圳分公司",
                "applyEndTime": 1793548799999,
            },
        }
    }
    html = "<html><script>window.__INITIAL_DATA__ = " + json.dumps(
        payload, ensure_ascii=False
    ) + ";</script></html>"
    parsed = parse_zhaopin_detail_html(
        html,
        detail_url="https://xiaoyuan.zhaopin.com/job/CC258591510J40972459715",
        expected_job_number="CC258591510J40972459715",
    )
    assert parsed["degree"] == "本科"
    job = {
        "title": parsed["title"],
        "location": parsed["location"],
        "degree_levels": ["本科"],
        "field_evidence": parsed["evidence"],
        "source_id": "cnooc-career-browser",
        "category": "油气上游业主与研究机构",
    }
    assert evaluate_student_publication(job).status == "student_eligible"


def test_parse_zhaopin_detail_keeps_city_with_street_address() -> None:
    payload = {
        "main": {
            "positionDetail": {
                "positionNumber": "CC258591510J40972459615",
                "positionName": "地质科研岗",
                "education": "硕士",
                "positionWorkCity": "湛江",
                "positionCityDistrict": "坡头区",
                "workAddress": "南调路1388号",
                "dateEnd": "2027-10-01 00:00:00",
                "jobDesc": "学历要求：硕士研究生及以上；专业要求：地质学。",
                "positionURL": "https://xiaoyuan.zhaopin.com/job/CC258591510J40972459615",
            },
            "campusJobDetail": {"companyName": "中国海油测试单位"},
        }
    }
    html = "<html><script>window.__INITIAL_DATA__ = " + json.dumps(
        payload, ensure_ascii=False
    ) + ";</script></html>"

    parsed = parse_zhaopin_detail_html(
        html,
        detail_url="https://xiaoyuan.zhaopin.com/job/CC258591510J40972459615",
        expected_job_number="CC258591510J40972459615",
    )

    assert parsed["location"] == "湛江 坡头区 南调路1388号"


def test_parse_zhaopin_detail_stops_at_unpunctuated_numbered_requirement() -> None:
    payload = {
        "main": {
            "positionDetail": {
                "positionNumber": "CC258591510J40972459815",
                "positionName": "地球物理研究岗",
                "education": "硕士",
                "workAddress": "北京市",
                "dateEnd": "2027-10-01 00:00:00",
                "jobDesc": (
                    "任职要求：1.学历要求：硕士研究生及以上 "
                    "2.专业要求：地质学、地球物理学 "
                    "3.外语要求：大学英语六级"
                ),
                "positionURL": "https://xiaoyuan.zhaopin.com/job/CC258591510J40972459815",
            },
            "campusJobDetail": {"companyName": "中国海油测试单位"},
        }
    }
    html = "<html><script>window.__INITIAL_DATA__ = " + json.dumps(
        payload, ensure_ascii=False
    ) + ";</script></html>"

    parsed = parse_zhaopin_detail_html(
        html,
        detail_url="https://xiaoyuan.zhaopin.com/job/CC258591510J40972459815",
        expected_job_number="CC258591510J40972459815",
    )

    assert parsed["evidence"]["学历要求"] == "硕士研究生及以上"
    assert parsed["evidence"]["专业范围"] == "地质学、地球物理学"


def test_major_requirement_skips_a_legacy_outer_wrapper() -> None:
    from job_hub.zhaopin_detail import extract_zhaopin_major_requirement

    detail = (
        "专业要求：一、岗位职责 1.开展地质资料解释；"
        "二、任职要求 1.学历要求：硕士研究生及以上；"
        "2.专业要求：地质学、地球物理学；"
        "3.外语要求：大学英语六级。"
    )

    assert extract_zhaopin_major_requirement(detail) == "地质学、地球物理学"


def test_parse_zhaopin_detail_fails_closed_on_id_mismatch() -> None:
    with pytest.raises(ZhaopinDetailError, match="positionNumber mismatch"):
        parse_zhaopin_detail_html(
            _html(), detail_url=DETAIL_URL, expected_job_number="CC258591510J00000000000"
        )


def test_parse_zhaopin_detail_fails_closed_when_deadline_is_missing() -> None:
    with pytest.raises(ZhaopinDetailError, match="deadline"):
        parse_zhaopin_detail_html(_html(deadline=""), detail_url=DETAIL_URL)


def test_parse_zhaopin_detail_accepts_numbered_major_requirement() -> None:
    payload = {
        "main": {
            "positionDetail": {
                "positionNumber": "CC258591510J40972470715",
                "positionName": "工艺工程师",
                "education": "硕士",
                "workAddress": "天津市滨海新区",
                "dateEnd": "2027-10-01 00:00:00",
                "jobDesc": (
                    "一、岗位职责：负责海洋工程项目工艺系统设计。"
                    "二、任职要求：1.2027年应届毕业生；"
                    "2.石油与天然气类、化学工程与技术类、油气储运工程等相关专业；"
                    "3.研究生达到大学英语六级水平；"
                    "4.工作地点：天津、青岛、深圳。"
                ),
                "positionURL": "https://xiaoyuan.zhaopin.com/job/CC258591510J40972470715",
            },
            "campusJobDetail": {"companyName": "中国海油-海洋石油工程股份有限公司"},
        }
    }
    html = "<html><script>window.__INITIAL_DATA__ = " + json.dumps(
        payload, ensure_ascii=False
    ) + ";</script></html>"

    parsed = parse_zhaopin_detail_html(
        html,
        detail_url="https://xiaoyuan.zhaopin.com/job/CC258591510J40972470715",
        expected_job_number="CC258591510J40972470715",
    )

    assert parsed["major_text"] == "石油与天然气类、化学工程与技术类、油气储运工程等相关专业"
    assert parsed["evidence"]["专业要求"] == parsed["major_text"]


def test_parse_zhaopin_detail_rejects_duties_without_major_condition() -> None:
    payload = {
        "main": {
            "positionDetail": {
                "positionNumber": "CC258591510J40972459999",
                "positionName": "地质技术岗",
                "education": "硕士",
                "workAddress": "北京市",
                "dateEnd": "2027-10-01 00:00:00",
                "jobDesc": "岗位职责：开展地质研究与地球物理资料解释；学历要求：硕士及以上。",
                "positionURL": "https://xiaoyuan.zhaopin.com/job/CC258591510J40972459999",
            },
            "campusJobDetail": {"companyName": "中国海油测试单位"},
        }
    }
    html = "<html><script>window.__INITIAL_DATA__ = " + json.dumps(
        payload, ensure_ascii=False
    ) + ";</script></html>"

    with pytest.raises(ZhaopinDetailError, match="job-level major requirement"):
        parse_zhaopin_detail_html(
            html,
            detail_url="https://xiaoyuan.zhaopin.com/job/CC258591510J40972459999",
            expected_job_number="CC258591510J40972459999",
        )


def test_parse_zhaopin_detail_rejects_unofficial_detail_host() -> None:
    with pytest.raises(ZhaopinDetailError, match="host"):
        parse_zhaopin_detail_html(_html(), detail_url="https://example.com/job/1")


def test_cnooc_detail_scope_reaches_student_publication_gate() -> None:
    parsed = parse_zhaopin_detail_html(
        _html(), detail_url=DETAIL_URL, expected_job_number="CC258591510J40972459115"
    )
    job = {
        "title": parsed["title"],
        "location": parsed["location"],
        "degree_levels": ["硕士"],
        "field_evidence": parsed["evidence"],
        "source_id": "cnooc-career-browser",
        "category": "油气上游业主与研究机构",
    }
    decision = evaluate_student_publication(job)
    assert decision.status == "student_eligible"
