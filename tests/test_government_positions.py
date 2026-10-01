from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from job_hub.government_positions import (
    GovernmentPositionContractError,
    current_publishable_position_records,
    government_position_quality_report,
    load_position_registry,
    position_record_to_posting,
    source_activation_tasks,
    upcoming_position_records,
    validate_position_record,
)
from job_hub.profiles import evaluate_student_publication


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_official_government_registry_loads_and_reports_verified_rows() -> None:
    registry = load_position_registry(PROJECT_ROOT / "data" / "government_position_registry.json")
    report = government_position_quality_report(registry, today="2026-09-25")

    assert report["source_assessments"] == 12
    assert report["records"] == 157
    assert report["verified_open_records"] == 52
    assert report["verified_upcoming_records"] == 91
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

    assert len(rows) == 91
    assert {row["source_id"] for row in rows} == {"cea-2027-recruitment"}
    assert all(row["opening_date"] == "2026-10-10" for row in rows)
    assert all(row["record_status"] == "verified_open" for row in rows)
    assert not [row for row in rows if row["deadline_date"] < "2026-09-28"]


def test_cea_rows_activate_on_opening_date_and_expire_after_deadline() -> None:
    """The upcoming ledger must transition without manual data edits.

    The China Earthquake Administration table is intentionally held out until
    2026-10-10. Once the official evidence was revalidated, the same reviewed
    rows must become current; after 2026-10-26 they must disappear again.
    """
    registry = load_position_registry(PROJECT_ROOT / "data" / "government_position_registry.json")
    verified_at_opening = {
        "cea-2027-recruitment": {
            "status": "verified",
            "last_success_at": "2026-10-10T01:00:00Z",
        }
    }

    opening_rows = current_publishable_position_records(
        registry,
        today="2026-10-10",
        max_age_hours=48,
        source_verifications=verified_at_opening,
    )
    assert len([row for row in opening_rows if row["source_id"] == "cea-2027-recruitment"]) == 91

    closed_rows = current_publishable_position_records(
        registry,
        today="2026-10-27",
        max_age_hours=48,
        source_verifications={
            "cea-2027-recruitment": {
                "status": "verified",
                "last_success_at": "2026-10-26T23:00:00Z",
            }
        },
    )
    assert not [row for row in closed_rows if row["source_id"] == "cea-2027-recruitment"]


def test_source_activation_tasks_make_future_batches_operationally_visible() -> None:
    registry = load_position_registry(PROJECT_ROOT / "data" / "government_position_registry.json")

    before_opening = source_activation_tasks(
        registry,
        today="2026-10-01",
        max_age_hours=48,
    )
    cea_before = next(item for item in before_opening if item["source_id"] == "cea-2027-recruitment")
    assert cea_before["status"] == "scheduled"
    assert cea_before["matching_row_count"] == 91
    assert cea_before["headcount"] == 112

    due_at_opening = source_activation_tasks(
        registry,
        today="2026-10-10",
        max_age_hours=48,
        # Manual-only sources must not be treated as fresh merely because the
        # reviewed registry itself is recent enough.
        manual_confirmation_source_ids={"cea-2027-recruitment"},
    )
    cea_due = next(item for item in due_at_opening if item["source_id"] == "cea-2027-recruitment")
    assert cea_due["status"] == "due_revalidation"

    verified_at_opening = source_activation_tasks(
        registry,
        today="2026-10-10",
        max_age_hours=48,
        source_verifications={
            "cea-2027-recruitment": {
                "status": "verified",
                "last_success_at": "2026-10-10T01:00:00Z",
            }
        },
        manual_confirmation_source_ids={"cea-2027-recruitment"},
        now=datetime.fromisoformat("2026-10-10T02:00:00+00:00"),
    )
    cea_verified = next(item for item in verified_at_opening if item["source_id"] == "cea-2027-recruitment")
    assert cea_verified["status"] == "verified"

    after_deadline = source_activation_tasks(
        registry,
        today="2026-10-27",
        max_age_hours=48,
    )
    cea_expired = next(item for item in after_deadline if item["source_id"] == "cea-2027-recruitment")
    assert cea_expired["status"] == "expired"


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

    assert len(rows) == 91
    assert sum(item["headcount"] for item in rows) == 112
    assert all(item["employer"] for item in rows)
    assert all(item["location"] for item in rows)
    assert all(item["deadline_date"] == "2026-10-26" for item in rows)
    assert all("Excel第" in item["evidence_locator"] for item in rows)
    assert {item["position_code"] for item in rows} >= {"R010", "R144", "R157"}
    assert next(item for item in rows if item["position_code"] == "R144")["province"] == "河南"
    decisions = []
    for item in rows:
        posting = position_record_to_posting(item)
        decisions.append(
            evaluate_student_publication(
                {
                    "title": posting.title,
                    "source_id": item["source_id"],
                    "category": "事业单位与人才引进",
                    "location": posting.location,
                    "field_evidence": posting.field_evidence,
                }
            )
        )
    assert all(decision.status == "student_eligible" for decision in decisions)


def test_position_batch_can_keep_row_level_province_when_one_notice_is_national() -> None:
    registry = {
        "version": 1,
        "as_of": "2026-09-30",
        "position_batches": [
            {
                "id": "national-test",
                "source_id": "national-test-source",
                "position_type": "public_institution",
                "province": "北京",
                "location": "北京市",
                "deadline_date": "2026-10-31",
                "deadline_policy": "fixed_date",
                "official_notice_url": "https://example.gov.cn/notice",
                "official_attachment_url": "https://example.gov.cn/table.xlsx",
                "record_status": "verified_open",
                "match_status": "explicit_match",
                "rows": [
                    [
                        "A01",
                        "测试单位",
                        "地质技术岗",
                        "地质学",
                        "硕士研究生",
                        1,
                        "岗位表第2行",
                        "郑州市",
                        "河南",
                    ]
                ],
            }
        ],
        "records": [],
    }
    path = PROJECT_ROOT / ".tmp_test_row_province_registry.json"
    try:
        path.write_text(__import__("json").dumps(registry, ensure_ascii=False), encoding="utf-8")
        loaded = load_position_registry(path)
    finally:
        path.unlink(missing_ok=True)

    assert loaded["records"][0]["province"] == "河南"
    assert loaded["records"][0]["location"] == "郑州市"


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
