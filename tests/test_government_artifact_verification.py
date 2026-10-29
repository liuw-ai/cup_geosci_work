from __future__ import annotations

from pathlib import Path

from job_hub.db import Database
from job_hub.government_artifact_verification import (
    _government_major_requirement_matches,
    _requirement_matches,
    verify_government_artifacts,
)

from conftest import make_settings, source


def _setup(
    tmp_path: Path,
    *,
    rolling: bool = False,
    location: str | None = "测试市",
    degree: str | None = "硕士研究生",
    major: str = "地质工程",
):
    settings = make_settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    database.upsert_source(source())
    parent_url = "https://careers.example.edu.cn/notice.html"
    artifact_url = "https://careers.example.edu.cn/table.xlsx"
    artifact = database.upsert_source_artifact(
        {
            "source_id": "official-test-source",
            "parent_url": parent_url,
            "artifact_url": artifact_url,
            "artifact_kind": "position_table",
            "extraction_status": "registered",
            "metadata": {"government_artifact_id": "test-artifact"},
        }
    )
    database.update_source_artifact_processing(
        int(artifact["id"]),
        extraction_status="extracted",
        content_sha256="a" * 64,
        storage_path="sha256/aa/" + "a" * 64 + ".xlsx",
        parser_version="attachments-v-test",
    )
    row = database.upsert_source_artifact_rows(
        int(artifact["id"]),
        [
            {
                "sheet_name": "岗位表",
                "row_number": 2,
                "cells": {
                    "岗位代码": "A-001",
                    "岗位名称": "地质工程技术岗",
                    "招聘单位": "测试能源集团",
                    "专业": major,
                    "学历": degree or "",
                    "工作地点": location or "",
                    "招聘人数": "1",
                    "报名截止": "2099-12-31",
                },
                "row_text": "岗位代码：A-001；招聘单位：测试能源集团；专业：地质工程；学历：硕士研究生；工作地点：测试市；招聘人数：1",
            }
        ],
    )[0]
    candidate = database.upsert_artifact_job_candidate(
        {
            "artifact_row_id": row["id"],
            "source_id": "official-test-source",
            "official_page_url": parent_url,
            "title": "地质工程技术岗",
            "employer": "测试能源集团",
            "location": location,
            "deadline_date": "" if rolling else "2099-12-31",
            "degree_levels": ["硕士"] if degree else [],
            "major_tags": [] if major == "不限专业" else [major],
            "summary": "官方岗位表第2行",
            "description": "官方岗位表第2行",
            "extraction_confidence": "high",
            "field_evidence": {
                "artifact_url": artifact_url,
                "职位代码": "A-001",
                "专业范围": major,
                "学历要求": degree or "",
                "工作地点": location or "",
                "招聘人数": "1",
                "table_row": "岗位表!2",
            },
            "review_status": "needs_review",
        }
    )
    record = {
        "id": "test-record",
        "source_id": "official-test-source",
        "position_type": "public_institution",
        "province": "测试省",
        "employer": "测试能源集团",
        "position_code": "A-001",
        "title": "地质工程技术岗",
        "major_requirement": major,
        "degree_requirement": "硕士研究生",
        "location": location or "测试市",
        "headcount": 1,
        "deadline_date": "" if rolling else "2099-12-31",
        "deadline_policy": "open_until_filled" if rolling else "fixed_date",
        "official_notice_url": parent_url,
        "official_attachment_url": artifact_url,
        "evidence_locator": "岗位表!2",
        "record_status": "verified_open",
        "match_status": "explicit_match",
    }
    declared = {
        "id": "test-artifact",
        "source_id": "official-test-source",
        "province": "测试省",
        "position_type": "public_institution",
        "notice_url": parent_url,
        "attachment_url": artifact_url,
        "artifact_kind": "position_table",
        "deadline_date": "" if rolling else "2099-12-31",
        "deadline_policy": "open_until_filled" if rolling else "fixed_date",
        "observed_on": "2026-10-07",
        "status": "server_download_pending",
    }
    return database, declared, {"records": [record]}, candidate


def test_exact_fixed_table_row_is_automatically_content_verified(tmp_path: Path) -> None:
    database, declared, registry, candidate = _setup(tmp_path, location="北京市")

    report = verify_government_artifacts(
        database,
        {"artifacts": [declared]},
        registry,
        today="2026-10-07",
    )

    assert report["row_evidence_verified"] == 1
    assert report["publication_ready"] == 1
    assert report["candidates_promoted"] == 1
    assert database.get_artifact_job_candidate(int(candidate["id"]))["review_status"] == (
        "official_content_verified"
    )


def test_out_of_scope_row_is_audited_without_blocking_eligible_rows(tmp_path: Path) -> None:
    database, declared, registry, candidate = _setup(tmp_path, location="北京市")
    artifact = database.get_source_artifact(int(candidate["artifact_id"]))
    assert artifact is not None
    blocked_row = database.upsert_source_artifact_rows(
        int(artifact["id"]),
        [
            {
                "sheet_name": "岗位表",
                "row_number": 3,
                "cells": {
                    "岗位代码": "A-002",
                    "岗位名称": "石油工程技术岗",
                    "招聘单位": "测试能源集团",
                    "专业": "石油工程",
                    "学历": "硕士研究生",
                    "工作地点": "北京市",
                    "招聘人数": "1",
                },
                "row_text": "岗位代码：A-002；专业：石油工程；学历：硕士研究生",
            }
        ],
    )[0]
    registry["records"].append(
        registry["records"][0]
        | {
            "id": "test-record-2",
            "position_code": "A-002",
            "title": "石油工程技术岗",
            "major_requirement": "石油工程",
            "evidence_locator": "岗位表!3",
        }
    )

    report = verify_government_artifacts(
        database,
        {"artifacts": [declared]},
        registry,
        today="2026-10-07",
    )

    assert report["row_evidence_verified"] == 1
    assert report["publication_ready"] == 1
    assert report["candidates_promoted"] == 1
    item = report["items"][0]
    assert item["matched_record_ids"] == ["test-record"]
    assert item["blocked_record_ids"] == ["test-record-2"]
    assert item["blocked_records"][0]["row_locator"] == "岗位表!3"
    assert item["blocked_records"][0]["publication_status"] == "out_of_scope"
    assert database.get_artifact_job_candidate(int(candidate["id"]))["review_status"] == (
        "official_content_verified"
    )


def test_field_conflict_in_one_row_does_not_block_another_exact_row(tmp_path: Path) -> None:
    database, declared, registry, candidate = _setup(tmp_path, location="北京市")
    artifact = database.get_source_artifact(int(candidate["artifact_id"]))
    assert artifact is not None
    conflict_row = database.upsert_source_artifact_rows(
        int(artifact["id"]),
        [
            {
                "sheet_name": "岗位表",
                "row_number": 3,
                "cells": {
                    "岗位代码": "A-002",
                    "岗位名称": "地质工程技术岗",
                    "招聘单位": "测试能源集团",
                    "专业": "地质工程",
                    "学历": "硕士研究生",
                    "工作地点": "北京市",
                    "招聘人数": "1",
                    "报名截止": "2099-12-31",
                },
                "row_text": "岗位代码：A-002；专业：地质工程；学历：硕士研究生",
            }
        ],
    )[0]
    conflict_candidate = database.upsert_artifact_job_candidate(
        {
            "artifact_row_id": conflict_row["id"],
            "source_id": "official-test-source",
            "official_page_url": artifact["parent_url"],
            "title": "地质工程技术岗",
            "employer": "测试能源集团",
            "location": "北京市",
            "deadline_date": "2099-12-31",
            "degree_levels": ["硕士"],
            "major_tags": ["地质工程"],
            "summary": "官方岗位表第3行",
            "description": "官方岗位表第3行",
            "extraction_confidence": "high",
            "field_evidence": {
                "artifact_url": artifact["artifact_url"],
                "职位代码": "A-002",
                "专业范围": "地质工程",
                "学历要求": "硕士研究生",
                "工作地点": "北京市",
                "招聘人数": "1",
                "table_row": "岗位表!3",
            },
            "review_status": "needs_review",
        }
    )
    registry["records"].append(
        registry["records"][0]
        | {
            "id": "test-record-field-conflict",
            "position_code": "A-002",
            "major_requirement": "地质学",
            "evidence_locator": "岗位表!3",
        }
    )

    report = verify_government_artifacts(
        database,
        {"artifacts": [declared]},
        registry,
        today="2026-10-07",
    )

    assert report["row_evidence_verified"] == 1, report["items"]
    assert report["publication_ready"] == 1
    assert report["candidates_promoted"] == 1
    item = report["items"][0]
    assert item["matched_record_ids"] == ["test-record"]
    assert item["blocked_record_ids"] == ["test-record-field-conflict"]
    assert item["blocked_records"][0]["reason"].startswith("record_field_mismatch:")
    assert database.get_artifact_job_candidate(int(candidate["id"]))["review_status"] == (
        "official_content_verified"
    )
    assert database.get_artifact_job_candidate(int(conflict_candidate["id"]))["review_status"] == (
        "needs_review"
    )


def test_numbered_major_code_list_matches_only_complete_code_name_pairs() -> None:
    observed = "(1) 0709 地质学\n(2) 0818 地质资源与地质工程"
    expected = "0709 地质学；0818 地质资源与地质工程"

    assert _requirement_matches(observed, expected)
    assert not _requirement_matches(observed, "0709 地质学")
    assert not _requirement_matches(observed, "0709 地质学；0818 地质工程")
    assert not _requirement_matches("0709 地质学；0818 地质工程", expected)
    assert not _requirement_matches("地质工程技术", "地质工程")
    assert _requirement_matches(
        "(1) 070901 矿物学、岩石学、矿床学\n(2) 081403K 资源勘查工程",
        "070901 矿物学、岩石学、矿床学；081403K 资源勘查工程",
    )
    assert not _requirement_matches(
        "(1) 070901 矿物学、岩石学、矿床学\n(2) 081403K 资源勘查工程",
        "070901 矿物学、岩石学；081403K 资源勘查工程",
    )


def test_registry_major_subset_is_scoped_and_rejects_conflicting_term() -> None:
    record = {"location_evidence_policy": "registry_province"}
    expected = "地质资源与地质工程（081800，含地质工程）等"

    assert _government_major_requirement_matches(
        record, "地质资源与地质工程、地质工程", expected
    )
    assert not _government_major_requirement_matches(
        record, "地质资源与地质工程、土木工程", expected
    )
    assert not _government_major_requirement_matches(
        {"location_evidence_policy": "row_exact"},
        "地质资源与地质工程、地质工程",
        expected,
    )


def test_registry_major_subset_promotes_fragmented_official_row(tmp_path: Path) -> None:
    database, declared, registry, candidate = _setup(tmp_path, location="北京市")
    registry["records"][0].update(
        {
            "location_evidence_policy": "registry_province",
            "major_requirement": "地质资源与地质工程",
        }
    )
    artifact = database.get_source_artifact(int(candidate["artifact_id"]))
    assert artifact is not None
    row = database.list_source_artifact_rows(int(artifact["id"]))[0]
    database.upsert_source_artifact_rows(
        int(artifact["id"]),
        [
            {
                "sheet_name": row["sheet_name"],
                "row_number": row["row_number"],
                "cells": dict(row["cells"])
                | {"专业": "地质资源与地质工程、地质工程"},
                "row_text": row["row_text"],
            }
        ],
    )
    database.update_artifact_job_candidate(
        int(candidate["id"]),
        {"field_evidence_updates": {"专业范围": "地质资源与地质工程、地质工程"}},
    )

    report = verify_government_artifacts(
        database, {"artifacts": [declared]}, registry, today="2026-10-07"
    )

    assert report["publication_ready"] == 1, report["items"]
    assert report["candidates_promoted"] == 1


def test_numbered_major_code_list_is_verified_against_row(tmp_path: Path) -> None:
    database, declared, registry, candidate = _setup(tmp_path, location="北京市")
    observed = "(1) 0709 地质学\n(2) 0818 地质资源与地质工程"
    registry["records"][0]["major_requirement"] = "0709 地质学；0818 地质资源与地质工程"
    artifact = database.get_source_artifact(int(candidate["artifact_id"]))
    assert artifact is not None
    row = database.list_source_artifact_rows(int(artifact["id"]))[0]
    database.upsert_source_artifact_rows(
        int(artifact["id"]),
        [{
            "sheet_name": row["sheet_name"],
            "row_number": row["row_number"],
            "cells": dict(row["cells"]) | {"专业": observed},
            "row_text": row["row_text"],
        }],
    )
    database.update_artifact_job_candidate(
        int(candidate["id"]),
        {"field_evidence_updates": {"专业范围": observed}},
    )

    report = verify_government_artifacts(
        database, {"artifacts": [declared]}, registry, today="2026-10-07"
    )

    assert report["publication_ready"] == 1, report["items"]
    assert report["candidates_promoted"] == 1


def test_serial_number_code_and_labeled_headcount_are_supported_as_explicit_row_fields(
    tmp_path: Path,
) -> None:
    """Some official XLSX tables have no code column and label headcount differently."""

    database, declared, registry, candidate = _setup(tmp_path, location="北京市")
    registry["records"][0]["position_code"] = "1"
    registry["records"][0]["evidence_locator"] = "岗位表!2 / 序号1"
    artifact = database.get_source_artifact(int(candidate["artifact_id"]))
    assert artifact is not None
    row = database.list_source_artifact_rows(int(artifact["id"]))[0]
    cells = dict(row["cells"])
    cells.pop("岗位代码")
    cells.pop("招聘人数")
    cells["序号"] = "1"
    # Real Hunan workbook headers contain a line break; _value() must match
    # them through its whitespace-normalized aliases.
    cells["招聘\n计划数"] = "1"
    database.upsert_source_artifact_rows(
        int(artifact["id"]),
        [{
            "sheet_name": row["sheet_name"],
            "row_number": row["row_number"],
            "cells": cells,
            "row_text": row["row_text"],
        }],
    )
    database.update_artifact_job_candidate(
        int(candidate["id"]),
        {"field_evidence_updates": {"职位代码": None, "招聘人数": None}},
    )

    report = verify_government_artifacts(
        database, {"artifacts": [declared]}, registry, today="2026-10-07"
    )

    assert report["publication_ready"] == 1, report["items"]
    assert report["candidates_promoted"] == 1


def test_registry_verifier_reuses_candidates_for_each_record(tmp_path: Path) -> None:
    database, declared, registry, first = _setup(tmp_path, location="北京市")
    artifact = database.get_source_artifact(int(first["artifact_id"]))
    assert artifact is not None
    row = database.upsert_source_artifact_rows(
        int(artifact["id"]),
        [{
            "sheet_name": "岗位表",
            "row_number": 3,
            "cells": {
                "岗位代码": "A-002",
                "岗位名称": "地质工程技术岗",
                "招聘单位": "测试能源集团",
                "专业": "地质工程",
                "学历": "硕士研究生",
                "工作地点": "北京市",
                "招聘人数": "1",
                "报名截止": "2099-12-31",
            },
            "row_text": "岗位代码：A-002；专业：地质工程；学历：硕士研究生",
        }],
    )[0]
    second = database.upsert_artifact_job_candidate(
        {
            "artifact_row_id": row["id"],
            "source_id": "official-test-source",
            "official_page_url": artifact["parent_url"],
            "title": "地质工程技术岗",
            "employer": "测试能源集团",
            "location": "北京市",
            "deadline_date": "2099-12-31",
            "degree_levels": ["硕士"],
            "major_tags": ["地质工程"],
            "summary": "官方岗位表第3行",
            "description": "官方岗位表第3行",
            "extraction_confidence": "high",
            "field_evidence": {
                "artifact_url": artifact["artifact_url"],
                "职位代码": "A-002",
                "专业范围": "地质工程",
                "学历要求": "硕士研究生",
                "工作地点": "北京市",
                "招聘人数": "1",
                "table_row": "岗位表!3",
            },
            "review_status": "needs_review",
        }
    )
    registry["records"].append(
        registry["records"][0] | {
            "id": "test-record-2",
            "position_code": "A-002",
            "evidence_locator": "岗位表!3",
        }
    )

    report = verify_government_artifacts(
        database, {"artifacts": [declared]}, registry, today="2026-10-07"
    )

    assert report["publication_ready"] == 1, report["items"]
    assert report["candidates_promoted"] == 2
    assert set(report["items"][0]["matched_candidate_ids"]) == {
        str(first["id"]), str(second["id"])
    }

    second_report = verify_government_artifacts(
        database, {"artifacts": [declared]}, registry, today="2026-10-07"
    )
    assert second_report["candidates_promoted"] == 0
    assert {
        database.get_artifact_job_candidate(int(first["id"]))["review_status"],
        database.get_artifact_job_candidate(int(second["id"]))["review_status"],
    } == {"official_content_verified"}


def test_registry_verifier_reaches_candidate_after_first_500(tmp_path: Path) -> None:
    database, declared, registry, first = _setup(tmp_path, location="北京市")
    artifact = database.get_source_artifact(int(first["artifact_id"]))
    assert artifact is not None
    rows = database.upsert_source_artifact_rows(
        int(artifact["id"]),
        [
            {
                "sheet_name": "岗位表",
                "row_number": number,
                "cells": {
                    "岗位代码": f"A-{number:03d}",
                    "岗位名称": "地质工程技术岗",
                    "招聘单位": "测试能源集团",
                    "专业": "地质工程",
                    "学历": "硕士研究生",
                    "工作地点": "北京市",
                    "招聘人数": "1",
                    "报名截止": "2099-12-31",
                },
                "row_text": f"岗位代码：A-{number:03d}；专业：地质工程",
            }
            for number in range(3, 503)
        ],
    )
    last = None
    for row in rows:
        number = int(row["row_number"])
        last = database.upsert_artifact_job_candidate(
            {
                "artifact_row_id": row["id"],
                "source_id": "official-test-source",
                "official_page_url": artifact["parent_url"],
                "title": "地质工程技术岗",
                "employer": "测试能源集团",
                "location": "北京市",
                "deadline_date": "2099-12-31",
                "degree_levels": ["硕士"],
                "major_tags": ["地质工程"],
                "summary": "官方岗位表岗位行",
                "description": "官方岗位表岗位行",
                "extraction_confidence": "high",
                "field_evidence": {
                    "artifact_url": artifact["artifact_url"],
                    "职位代码": f"A-{number:03d}",
                    "专业范围": "地质工程",
                    "学历要求": "硕士研究生",
                    "工作地点": "北京市",
                    "招聘人数": "1",
                    "table_row": f"岗位表!{number}",
                },
                "review_status": "needs_review" if number == 502 else "rejected",
            }
        )
    assert last is not None
    assert len(list(database.iter_artifact_job_candidates(artifact_id=int(artifact["id"])))) == 501
    registry["records"].append(
        registry["records"][0] | {
            "id": "test-record-501",
            "position_code": "A-502",
            "evidence_locator": "岗位表!502",
        }
    )

    report = verify_government_artifacts(
        database, {"artifacts": [declared]}, registry, today="2026-10-07"
    )

    assert report["publication_ready"] == 1, report["items"]
    assert str(last["id"]) in report["items"][0]["matched_candidate_ids"]


def test_self_contained_row_does_not_require_stale_registry_field_equality(tmp_path: Path) -> None:
    database, declared, registry, candidate = _setup(tmp_path, location="北京市")
    declared["evidence_mode"] = "row_self_contained"
    registry["records"][0].update(
        {
            "employer": "过期台账单位",
            "location": "过期台账地点",
            "headcount": 99,
            "deadline_date": "2026-01-01",
        }
    )

    report = verify_government_artifacts(
        database,
        {"artifacts": [declared]},
        registry,
        today="2026-10-07",
    )

    assert report["row_evidence_verified"] == 1, report["items"]
    assert report["publication_ready"] == 1
    assert report["items"][0]["matched_record_ids"] == []
    assert report["items"][0]["matched_candidate_ids"] == [str(candidate["id"])]
    assert database.get_artifact_job_candidate(int(candidate["id"]))["review_status"] == (
        "official_content_verified"
    )


def test_missing_location_is_verified_and_left_for_student_warning(tmp_path: Path) -> None:
    database, declared, registry, candidate = _setup(tmp_path, location=None)

    report = verify_government_artifacts(
        database,
        {"artifacts": [declared]},
        registry,
        today="2026-10-07",
    )

    assert report["row_evidence_verified"] == 1, report["items"]
    assert report["publication_ready"] == 1
    assert database.get_artifact_job_candidate(int(candidate["id"]))["review_status"] == (
        "official_content_verified"
    )


def test_missing_degree_is_verified_and_left_for_student_warning(tmp_path: Path) -> None:
    database, declared, registry, candidate = _setup(tmp_path, degree=None)

    report = verify_government_artifacts(
        database,
        {"artifacts": [declared]},
        registry,
        today="2026-10-07",
    )

    assert report["row_evidence_verified"] == 1, report["items"]
    assert report["publication_ready"] == 1
    assert database.get_artifact_job_candidate(int(candidate["id"]))["review_status"] == (
        "official_content_verified"
    )


def test_public_unrestricted_major_without_degree_or_location_is_verified(tmp_path: Path) -> None:
    database, declared, registry, candidate = _setup(
        tmp_path,
        degree=None,
        location=None,
        major="不限专业",
    )
    public_source = source()
    public_source["category"] = "公务员与选调"
    database.upsert_source(public_source)
    declared["evidence_mode"] = "row_self_contained"
    registry["records"][0]["match_status"] = "unrestricted_match"

    report = verify_government_artifacts(
        database,
        {"artifacts": [declared]},
        registry,
        today="2026-10-07",
    )

    assert report["row_evidence_verified"] == 1, report["items"]
    assert report["publication_ready"] == 1
    assert database.get_artifact_job_candidate(int(candidate["id"]))["review_status"] == (
        "official_content_verified"
    )


def test_declared_registry_province_location_fallback_is_scoped_and_audited(tmp_path: Path) -> None:
    database, declared, registry, candidate = _setup(tmp_path, location=None)
    declared["province"] = "测试省"
    artifact = database.get_source_artifact(1)
    assert artifact is not None
    database.update_source_artifact_processing(
        int(artifact["id"]),
        extraction_status="extracted",
        metadata_updates={"province": "测试省"},
    )
    registry["records"][0]["location_evidence_policy"] = "registry_province"
    registry["records"][0]["location"] = "测试省（公告未列具体市县）"

    report = verify_government_artifacts(
        database,
        {"artifacts": [declared]},
        registry,
        today="2026-10-07",
    )

    assert report["row_evidence_verified"] == 1
    assert report["publication_ready"] == 1
    assert report["candidates_promoted"] == 1
    updated = database.get_artifact_job_candidate(int(candidate["id"]))
    assert updated is not None
    assert updated["location"] == "测试省（公告未列具体市县）"
    assert "官方台账省级地点" in updated["field_evidence"]["工作地点依据"]


def test_registry_province_fallback_rejects_conflicting_row_location(tmp_path: Path) -> None:
    database, declared, registry, candidate = _setup(tmp_path, location=None)
    declared["province"] = "测试省"
    artifact = database.get_source_artifact(1)
    assert artifact is not None
    database.update_source_artifact_processing(
        int(artifact["id"]),
        extraction_status="extracted",
        metadata_updates={"province": "测试省"},
    )
    registry["records"][0]["location_evidence_policy"] = "registry_province"
    registry["records"][0]["location"] = "测试省（公告未列具体市县）"
    database.update_artifact_job_candidate(
        int(candidate["id"]),
        {"field_evidence_updates": {"工作地点": "外省市"}},
    )

    report = verify_government_artifacts(
        database,
        {"artifacts": [declared]},
        registry,
        today="2026-10-07",
    )

    assert report["row_evidence_verified"] == 0
    assert report["publication_ready"] == 0
    assert database.get_artifact_job_candidate(int(candidate["id"]))["review_status"] == "needs_review"


def test_registry_province_fallback_cannot_override_conflicting_row_major(tmp_path: Path) -> None:
    database, declared, registry, candidate = _setup(tmp_path, location=None)
    declared["province"] = "测试省"
    artifact = database.get_source_artifact(1)
    assert artifact is not None
    database.update_source_artifact_processing(
        int(artifact["id"]),
        extraction_status="extracted",
        metadata_updates={"province": "测试省"},
    )
    registry["records"][0]["location_evidence_policy"] = "registry_province"
    registry["records"][0]["location"] = "测试省（公告未列具体市县）"
    row = database.list_source_artifact_rows(int(artifact["id"]))[0]
    database.upsert_source_artifact_rows(
        int(artifact["id"]),
        [{
            "sheet_name": row["sheet_name"],
            "row_number": row["row_number"],
            "cells": dict(row["cells"]) | {"专业": "石油工程"},
            "row_text": row["row_text"],
        }],
    )

    report = verify_government_artifacts(
        database, {"artifacts": [declared]}, registry, today="2026-10-07"
    )

    assert report["publication_ready"] == 0
    assert any("major_requirement" in reason for reason in report["items"][0]["blocked_reasons"])
    updated = database.get_artifact_job_candidate(int(candidate["id"]))
    assert updated is not None
    assert updated["review_status"] == "needs_review"
    assert updated["field_evidence"]["专业范围"] == "地质工程"


def test_rolling_row_can_verify_content_but_never_renew_availability(tmp_path: Path) -> None:
    database, declared, registry, candidate = _setup(tmp_path, rolling=True)

    report = verify_government_artifacts(
        database,
        {"artifacts": [declared]},
        registry,
        today="2026-10-07",
    )

    assert report["row_evidence_verified"] == 1
    assert report["publication_ready"] == 0
    assert "current_availability_confirmation_required" in report["items"][0]["blocked_reasons"]
    assert database.get_artifact_job_candidate(int(candidate["id"]))["review_status"] == (
        "official_content_verified"
    )


def test_manifest_and_registry_deadline_policy_mismatch_is_blocked(tmp_path: Path) -> None:
    database, declared, registry, _ = _setup(tmp_path, rolling=True)
    declared["deadline_policy"] = "fixed_date"
    declared["deadline_date"] = "2026-12-31"

    report = verify_government_artifacts(
        database,
        {"artifacts": [declared]},
        registry,
        today="2026-10-07",
    )

    assert report["row_evidence_verified"] == 0
    assert any("deadline_policy_mismatch" in reason for reason in report["items"][0]["blocked_reasons"])


def test_unique_position_code_can_supplement_different_registry_locator_text(tmp_path: Path) -> None:
    database, declared, registry, candidate = _setup(tmp_path)
    registry["records"][0]["location_evidence_policy"] = "registry_province"
    registry["records"][0]["evidence_locator"] = "岗位表（高层次人才）!岗位代码A-001"

    report = verify_government_artifacts(
        database,
        {"artifacts": [declared]},
        registry,
        today="2026-10-07",
    )

    assert report["row_evidence_verified"] == 1
    assert report["publication_ready"] == 1
    assert report["candidates_promoted"] == 1
    updated = database.get_artifact_job_candidate(int(candidate["id"]))
    assert updated is not None
    assert updated["review_status"] == "official_content_verified"


def test_registry_headcount_cannot_override_conflicting_attachment_value(tmp_path: Path) -> None:
    database, declared, registry, candidate = _setup(tmp_path)
    registry["records"][0]["location_evidence_policy"] = "registry_province"
    registry["records"][0]["headcount"] = 2

    report = verify_government_artifacts(
        database,
        {"artifacts": [declared]},
        registry,
        today="2026-10-07",
    )

    assert report["row_evidence_verified"] == 0
    assert report["publication_ready"] == 0
    assert any("headcount" in reason for reason in report["items"][0]["blocked_reasons"])
    assert database.get_artifact_job_candidate(int(candidate["id"]))["review_status"] == "needs_review"


def test_conflicting_attachment_demotes_stale_automatic_verification(tmp_path: Path) -> None:
    database, declared, registry, candidate = _setup(tmp_path)
    registry["records"][0]["location_evidence_policy"] = "registry_province"
    registry["records"][0]["headcount"] = 2
    database.update_artifact_job_candidate(
        int(candidate["id"]),
        {
            "review_status": "official_content_verified",
            "review_note": "自动核验 government-artifact-verifier-v2：旧结果",
        },
    )

    report = verify_government_artifacts(
        database,
        {"artifacts": [declared]},
        registry,
        today="2026-10-07",
    )

    assert report["row_evidence_verified"] == 0
    updated = database.get_artifact_job_candidate(int(candidate["id"]))
    assert updated is not None
    assert updated["review_status"] == "needs_review"
    assert "headcount" in updated["review_note"]


def test_unique_code_on_adjacent_pdf_row_hydrates_candidate_and_verifies(tmp_path: Path) -> None:
    database, declared, registry, _ = _setup(tmp_path)
    registry["records"][0].update(
        {
            "position_code": "A-002",
            "title": "地质工程技术岗2",
            "evidence_locator": "岗位表（高层次人才）!岗位代码A-002",
            "location_evidence_policy": "registry_province",
        }
    )
    artifact = database.get_source_artifact(1)
    assert artifact is not None
    row = database.upsert_source_artifact_rows(
        int(artifact["id"]),
        [
            {
                "sheet_name": "page-1",
                "row_number": 2,
                "cells": {
                    "岗位名称": "职位表岗位（待核验）",
                    "招聘单位": "测试能源集团",
                    "专业": "地质工程",
                    "学历": "硕士研究生",
                    "工作地点": "测试市",
                    "招聘人数": "1",
                },
                "row_text": "地质工程技术岗2 测试能源集团 地质工程 硕士研究生 测试市",
            },
            {
                "sheet_name": "page-1",
                "row_number": 3,
                "cells": {"列3": "A-002公开考核"},
                "row_text": "A-002公开考核",
            },
        ],
    )
    candidate = database.upsert_artifact_job_candidate(
        {
            "artifact_row_id": row[0]["id"],
            "source_id": "official-test-source",
            "official_page_url": declared["notice_url"],
            "title": "职位表岗位（待核验）",
            "employer": "测试能源集团",
            "location": "测试市",
            "deadline_date": "2099-12-31",
            "degree_levels": ["硕士"],
            "major_tags": ["地质工程"],
            "summary": "地质工程技术岗2",
            "description": "地质工程技术岗2",
            "extraction_confidence": "high",
            "field_evidence": {
                "artifact_url": declared["attachment_url"],
                "专业范围": "地质工程",
                "学历要求": "硕士研究生",
                "工作地点": "测试市",
                "招聘人数": "1",
                "table_row": "page-1!2",
            },
            "review_status": "needs_review",
        }
    )

    report = verify_government_artifacts(
        database,
        {"artifacts": [declared]},
        registry,
        today="2026-10-07",
    )

    assert report["row_evidence_verified"] == 1
    assert report["publication_ready"] == 1
    assert report["candidates_promoted"] == 1
    updated = database.get_artifact_job_candidate(int(candidate["id"]))
    assert updated is not None
    assert updated["review_status"] == "official_content_verified"
    assert updated["field_evidence"]["职位代码"] == "A-002"
    assert updated["field_evidence"]["position_code_binding"] == "adjacent-row:page-1!3"
