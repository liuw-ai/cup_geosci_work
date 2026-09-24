from __future__ import annotations

from pathlib import Path

import pytest

from job_hub.government_positions import (
    GovernmentPositionContractError,
    government_position_quality_report,
    load_position_registry,
    validate_position_record,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_official_government_registry_loads_and_reports_pending_rows() -> None:
    registry = load_position_registry(PROJECT_ROOT / "data" / "government_position_registry.json")
    report = government_position_quality_report(registry, today="2026-09-25")

    assert report["source_assessments"] == 3
    assert report["records"] == 2
    assert report["verified_open_records"] == 0
    assert report["explicit_student_matches"] == 0
    assert report["source_failures_or_pending"] == 2
    assert report["scan_interpretation"].startswith("存在来源故障")


def test_verified_open_row_requires_location_and_deadline() -> None:
    record = {
        "id": "test-1",
        "source_id": "test-source",
        "position_type": "public_institution",
        "province": "安徽",
        "employer": "测试单位",
        "title": "地质专业技术岗",
        "major_requirement": "地质学",
        "degree_requirement": "博士研究生",
        "location": "",
        "headcount": 1,
        "deadline_date": "2026-10-31",
        "official_notice_url": "https://example.gov.cn/notice/1",
        "official_attachment_url": "https://example.gov.cn/notice/1.xlsx",
        "evidence_locator": "岗位表!2",
        "record_status": "verified_open",
        "match_status": "explicit_match",
    }
    with pytest.raises(GovernmentPositionContractError, match="location"):
        validate_position_record(record)


def test_out_of_scope_rows_can_be_closed_without_student_match() -> None:
    record = {
        "id": "test-2",
        "source_id": "test-source",
        "position_type": "civil_service",
        "province": "全国",
        "employer": "某机关",
        "title": "综合管理岗",
        "major_requirement": "不限专业",
        "degree_requirement": "本科及以上",
        "location": "北京市",
        "headcount": 1,
        "deadline_date": "2026-01-31",
        "official_notice_url": "https://example.gov.cn/notice/2",
        "official_attachment_url": "https://example.gov.cn/notice/2.xlsx",
        "evidence_locator": "职位表!12",
        "record_status": "verified_closed",
        "match_status": "out_of_scope",
    }
    assert validate_position_record(record)["id"] == "test-2"

