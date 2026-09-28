from __future__ import annotations

from pathlib import Path

import pytest

from job_hub.government_positions import (
    GovernmentPositionContractError,
    government_position_quality_report,
    load_position_registry,
    upcoming_position_records,
    validate_position_record,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_official_government_registry_loads_and_reports_verified_rows() -> None:
    registry = load_position_registry(PROJECT_ROOT / "data" / "government_position_registry.json")
    report = government_position_quality_report(registry, today="2026-09-25")

    assert report["source_assessments"] == 12
    assert report["records"] == 136
    assert report["verified_open_records"] == 52
    assert report["verified_upcoming_records"] == 70
    assert report["explicit_student_matches"] == 52
    assert report["source_failures_or_pending"] == 0
    assert report["verified_scan_no_current_match"] == 3
    assert "扫描成功" in report["scan_interpretation"]


def test_upcoming_records_are_separate_from_current_publishable_rows() -> None:
    registry = load_position_registry(PROJECT_ROOT / "data" / "government_position_registry.json")
    rows = upcoming_position_records(
        registry,
        today="2026-09-28",
        max_age_hours=48,
    )

    assert len(rows) == 70
    assert {row["source_id"] for row in rows} == {"cea-2027-recruitment"}
    assert all(row["opening_date"] == "2026-10-10" for row in rows)
    assert all(row["record_status"] == "verified_open" for row in rows)
    assert not [row for row in rows if row["deadline_date"] < "2026-09-28"]


def test_stale_registry_report_excludes_rows_from_current_open_count() -> None:
    registry = load_position_registry(PROJECT_ROOT / "data" / "government_position_registry.json")
    report = government_position_quality_report(
        registry,
        today="2026-09-30",
        max_age_hours=48,
    )

    assert report["registry_freshness"] == "stale"
    assert report["verified_open_records"] == 0
    assert report["explicit_student_matches"] == 0


def test_provincial_civil_service_scans_keep_closed_or_non_student_entries_out() -> None:
    registry = load_position_registry(PROJECT_ROOT / "data" / "government_position_registry.json")
    assessments = {
        item["source_id"]: item
        for item in registry["source_assessments"]
        if item["position_type"] == "civil_service"
    }

    assert assessments["shandong-civil-service-2026"]["status"] == "verified_scan_no_current_match"
    assert assessments["shandong-civil-service-2026"]["deadline_date"] == "2025-11-10"
    assert assessments["zhejiang-civil-service-2026"]["status"] == "verified_scan_no_current_match"
    assert assessments["zhejiang-civil-service-2026"]["official_attachment_url"] == ""
    assert not [row for row in registry["records"] if row["position_type"] == "civil_service"]


def test_hunan_position_batch_expands_each_official_attachment_row() -> None:
    registry = load_position_registry(PROJECT_ROOT / "data" / "government_position_registry.json")
    hunan = [item for item in registry["records"] if item["source_id"] == "hunan-geology-institute"]

    assert len(hunan) == 48
    assert sum(item["headcount"] for item in hunan) == 58
    assert {item["position_code"] for item in hunan} >= {"A02", "A09", "A53"}
    assert sum(item["record_status"] == "verified_open" for item in hunan) == 34
    assert sum(item["record_status"] == "verified_closed" for item in hunan) == 14
    assert sum(item["deadline_policy"] == "open_until_filled" for item in hunan) == 34
    assert sum(item["deadline_policy"] == "fixed_date" for item in hunan) == 14
    assert all(item["official_attachment_url"].endswith(".xlsx") for item in hunan)


def test_cgs_postdoctoral_batch_expands_nine_current_geoscience_rows() -> None:
    registry = load_position_registry(PROJECT_ROOT / "data" / "government_position_registry.json")
    rows = [item for item in registry["records"] if item["position_type"] == "postdoctoral"]

    assert len(rows) == 9
    assert sum(item["headcount"] for item in rows) == 9
    assert {item["position_code"] for item in rows} == {str(i) for i in range(1, 10)}
    assert all(item["deadline_date"] == "2026-10-16" for item in rows)
    geoscience_markers = ("地质", "矿产", "地球", "地图学", "资源")
    assert all(any(marker in item["major_requirement"] for marker in geoscience_markers) for item in rows)


def test_cea_batch_expands_verified_rows_with_unit_and_location_evidence() -> None:
    registry = load_position_registry(PROJECT_ROOT / "data" / "government_position_registry.json")
    rows = [item for item in registry["records"] if item["source_id"] == "cea-2027-recruitment"]

    assert len(rows) == 70
    assert sum(item["headcount"] for item in rows) == 89
    assert all(item["employer"] for item in rows)
    assert all(item["location"] for item in rows)
    assert all(item["deadline_date"] == "2026-10-26" for item in rows)
    assert all("Excel第" in item["evidence_locator"] for item in rows)
    assert {item["position_code"] for item in rows} >= {"R010", "R142", "R157"}


def test_hunan_closed_batch_is_not_publishable() -> None:
    registry = load_position_registry(PROJECT_ROOT / "data" / "government_position_registry.json")
    rows = [
        item
        for item in registry["records"]
        if item["id"].startswith("hunan-2026-fourth-geoscience-closed-")
    ]

    assert len(rows) == 14
    assert sum(item["headcount"] for item in rows) == 15
    assert all(item["record_status"] == "verified_closed" for item in rows)
    assert all(item["deadline_date"] == "2026-09-16" for item in rows)


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


def test_postdoctoral_is_a_distinct_government_source_type() -> None:
    record = {
        "id": "postdoc-1",
        "source_id": "cgs-notices",
        "position_type": "postdoctoral",
        "province": "北京",
        "employer": "中国地质调查局发展研究中心",
        "position_code": "1",
        "title": "博士后研究人员（矿产勘查）",
        "major_requirement": "地质学、矿物学、岩石学、矿床学",
        "degree_requirement": "博士研究生",
        "location": "北京市西城区",
        "headcount": 1,
        "deadline_date": "2026-10-16",
        "deadline_policy": "fixed_date",
        "official_notice_url": "http://www.drc.cgs.gov.cn/ggl/202609/t20260921_869371.html",
        "official_attachment_url": "http://www.drc.cgs.gov.cn/ggl/202609/t20260921_869371.html",
        "evidence_locator": "公告正文\"招收计划\"表格第1行",
        "record_status": "verified_open",
        "match_status": "explicit_match",
    }
    assert validate_position_record(record)["position_type"] == "postdoctoral"


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
