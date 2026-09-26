from __future__ import annotations

from job_hub.government_positions import (
    current_publishable_position_records,
    position_record_to_posting,
)
from job_hub.worker import DailyWorker


def test_current_publishable_rows_require_open_explicit_match() -> None:
    registry = {
        "records": [
            {
                "id": "open",
                "record_status": "verified_open",
                "match_status": "explicit_match",
                "deadline_date": "2026-10-01",
                "deadline_policy": "fixed_date",
            },
            {
                "id": "review",
                "record_status": "verified_open",
                "match_status": "needs_review",
                "deadline_date": "2026-10-01",
            },
            {
                "id": "closed",
                "record_status": "verified_closed",
                "match_status": "explicit_match",
                "deadline_date": "2026-10-01",
            },
        ]
    }

    assert [item["id"] for item in current_publishable_position_records(registry, today="2026-09-26")] == ["open"]


def test_position_record_preserves_notice_attachment_and_row_evidence() -> None:
    record = {
        "id": "anhui-2026-2026113",
        "position_type": "public_institution",
        "position_code": "2026113",
        "title": "专业技术岗位（地质资源与地质工程等）",
        "employer": "安徽工业经济职业技术学院",
        "major_requirement": "地质学、地质资源与地质工程",
        "degree_requirement": "博士研究生",
        "location": "合肥市",
        "headcount": 1,
        "deadline_date": "2026-10-31",
        "deadline_policy": "fixed_date",
        "official_notice_url": "https://example.gov.cn/notice.html",
        "official_attachment_url": "https://example.gov.cn/table.xls",
        "evidence_locator": "岗位表!17",
    }

    posting = position_record_to_posting(record)
    assert posting.source_url == record["official_notice_url"]
    assert posting.official_evidence_url == record["official_attachment_url"]
    assert posting.field_evidence["表格定位"] == "岗位表!17"
    assert posting.field_evidence["evidence_scope"] == "official_attachment_row"
    assert posting.field_evidence["岗位"] == record["title"]
    assert "地质资源与地质工程" in (posting.qualification_text or "")


def test_government_equivalence_ignores_transient_normalized_rows() -> None:
    record = {
        "source_id": "hunan-geology-institute",
        "employer": "湖南省地质调查所",
        "position_code": "A02",
        "major_requirement": "地质学",
        "deadline_date": "",
    }
    transient = {
        "source_id": record["source_id"],
        "employer": record["employer"],
        "deadline_date": "",
        "external_id": "government-position:hunan-2026-a02:A02",
        "field_evidence": {"专业范围": "地质学", "职位代码": "A02"},
    }

    assert DailyWorker._find_equivalent_government_job(
        [transient], record, "https://example.gov.cn/table.xlsx"
    ) is None
