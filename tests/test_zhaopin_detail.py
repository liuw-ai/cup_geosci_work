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


def test_parse_zhaopin_detail_fails_closed_on_id_mismatch() -> None:
    with pytest.raises(ZhaopinDetailError, match="positionNumber mismatch"):
        parse_zhaopin_detail_html(
            _html(), detail_url=DETAIL_URL, expected_job_number="CC258591510J00000000000"
        )


def test_parse_zhaopin_detail_fails_closed_when_deadline_is_missing() -> None:
    with pytest.raises(ZhaopinDetailError, match="deadline"):
        parse_zhaopin_detail_html(_html(deadline=""), detail_url=DETAIL_URL)


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
