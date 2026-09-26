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


def test_official_government_registry_loads_and_reports_verified_rows() -> None:
    registry = load_position_registry(PROJECT_ROOT / "data" / "government_position_registry.json")
    report = government_position_quality_report(registry, today="2026-09-25")

    assert report["source_assessments"] == 7
    assert report["records"] == 9
    assert report["verified_open_records"] == 9
    assert report["explicit_student_matches"] == 9
    assert report["source_failures_or_pending"] == 0
    assert report["scan_interpretation"].startswith("台账中的正式来源")


def test_verified_open_row_requires_location_and_deadline() -> None:
    record = {
        "id": "test-1",
        "source_id": "test-source",
        "position_type": "public_institution",
        "province": "安徽",
        "employer": "测试单位",
        "position_code": "A-1",
        "title": "地质专业技术岗",
        "major_requirement": "地质学",
        "degree_requirement": "博士研究生",
        "location": "",
        "headcount": 1,
        "deadline_date": "2026-10-31",
        "deadline_policy": "fixed_date",
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
        "position_code": "B-1",
        "title": "综合管理岗",
        "major_requirement": "不限专业",
        "degree_requirement": "本科及以上",
        "location": "北京市",
        "headcount": 1,
        "deadline_date": "2026-01-31",
        "deadline_policy": "fixed_date",
        "official_notice_url": "https://example.gov.cn/notice/2",
        "official_attachment_url": "https://example.gov.cn/notice/2.xlsx",
        "evidence_locator": "职位表!12",
        "record_status": "verified_closed",
        "match_status": "out_of_scope",
    }
    assert validate_position_record(record)["id"] == "test-2"


def test_open_until_filled_row_can_be_published_without_invented_deadline() -> None:
    record = {
        "id": "test-open-ended",
        "source_id": "test-source",
        "position_type": "public_institution",
        "province": "湖北",
        "employer": "测试空间规划研究院",
        "position_code": "2",
        "title": "矿产资源管理岗",
        "major_requirement": "地质学、地质资源与地质工程",
        "degree_requirement": "全日制硕士研究生及以上",
        "location": "武汉市",
        "headcount": 1,
        "deadline_date": "",
        "deadline_policy": "open_until_filled",
        "official_notice_url": "https://example.gov.cn/notice/3",
        "official_attachment_url": "https://example.gov.cn/notice/3.xlsx",
        "evidence_locator": "计划表!6",
        "record_status": "verified_open",
        "match_status": "explicit_match",
    }
    assert validate_position_record(record)["deadline_policy"] == "open_until_filled"


def test_open_until_filled_row_rejects_invented_fixed_deadline() -> None:
    record = {
        "id": "test-open-ended-invalid",
        "source_id": "test-source",
        "position_type": "public_institution",
        "province": "湖北",
        "employer": "测试空间规划研究院",
        "position_code": "2",
        "title": "矿产资源管理岗",
        "major_requirement": "地质学",
        "degree_requirement": "全日制硕士研究生及以上",
        "location": "武汉市",
        "headcount": 1,
        "deadline_date": "2026-12-31",
        "deadline_policy": "open_until_filled",
        "official_notice_url": "https://example.gov.cn/notice/4",
        "official_attachment_url": "https://example.gov.cn/notice/4.xlsx",
        "evidence_locator": "计划表!6",
        "record_status": "verified_open",
        "match_status": "explicit_match",
    }
    with pytest.raises(GovernmentPositionContractError, match="must not invent"):
        validate_position_record(record)
