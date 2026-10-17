from job_hub.pending_evidence_audit import build_pending_evidence_audit


def _job(job_id: int, *, status: str = "open", major: str = "地质工程") -> dict:
    return {
        "id": job_id,
        "source_id": "iguopin-general-geoscience-browser",
        "source_name": "国聘主站地学岗位动态浏览器采集",
        "title": "地质工程师",
        "employer": "测试单位",
        "location": "北京",
        "status": status,
        "publication_status": "pending_evidence",
        "official_detail_key": "iguopin-job:same-detail",
        "field_evidence": {
            "evidence_scope": "official_iguopin_browser_detail",
            "岗位": "地质工程师",
            "专业范围": major,
            "学历要求": "硕士研究生",
        },
        "major_tags": ["地质工程"],
    }


def test_audit_is_diagnostic_and_groups_cross_source_detail_duplicates() -> None:
    result = build_pending_evidence_audit([_job(1), _job(2)])

    assert result["pending_count"] == 2
    assert result["geoscience_signal_count"] == 2
    assert result["explicit_taxonomy_signal_count"] == 2
    assert result["duplicate_detail_group_count"] == 1
    assert result["duplicate_detail_groups"] == {"iguopin-job:same-detail": [1, 2]}
    assert result["duplicate_record_count"] == 2
    assert result["same_source_duplicate_group_count"] == 1
    assert result["cross_source_duplicate_group_count"] == 0
    assert result["location_split_duplicate_group_count"] == 0
    assert result["publication_mutated"] is False


def test_audit_distinguishes_cross_source_and_location_split_duplicates() -> None:
    first = _job(1)
    second = _job(2)
    second["source_id"] = "other-official-source"
    location_a = _job(3)
    location_a["official_detail_key"] = "iguopin-job:multi-location::location:beijing"
    location_b = _job(4)
    location_b["official_detail_key"] = "iguopin-job:multi-location::location:beijing"

    result = build_pending_evidence_audit([first, second, location_a, location_b])

    assert result["duplicate_detail_group_count"] == 2
    assert result["same_source_duplicate_group_count"] == 1
    assert result["cross_source_duplicate_group_count"] == 1
    assert result["location_split_duplicate_group_count"] == 1


def test_audit_defaults_to_open_records_and_can_include_closed_records() -> None:
    result = build_pending_evidence_audit([_job(1), _job(2, status="expired")])
    assert result["pending_count"] == 1

    all_statuses = build_pending_evidence_audit(
        [_job(1), _job(2, status="expired")],
        open_only=False,
    )
    assert all_statuses["pending_count"] == 2


def test_audit_does_not_use_unrelated_description_keywords() -> None:
    job = _job(1, major="人力资源管理")
    job["title"] = "人力资源专员"
    job["major_tags"] = []
    job["field_evidence"]["岗位"] = "人力资源专员"
    job["description"] = "单位介绍：长期开展地质调查业务。"

    result = build_pending_evidence_audit([job])

    assert result["pending_count"] == 1
    assert result["geoscience_signal_count"] == 0


def test_audit_reads_job_requirement_fields_used_by_publication_gate() -> None:
    job = _job(1, major="")
    job["field_evidence"].pop("专业范围")
    job["field_evidence"]["岗位要求"] = "专业要求：地质工程相关专业；学历要求：硕士。"

    result = build_pending_evidence_audit([job])

    assert result["geoscience_signal_count"] == 1
    assert result["explicit_taxonomy_signal_count"] == 1


def test_audit_labels_location_and_experience_as_independent_blockers() -> None:
    job = _job(1, major="地质工程相关专业")
    job["country_or_region"] = ""
    job["field_evidence"]["岗位要求"] = "地质工程相关专业，本科，至少3年工作经验"

    result = build_pending_evidence_audit([job])

    candidate = result["candidates"][0]
    assert candidate["blocker_codes"] == [
        "mainland_location_unverified",
        "requires_work_experience",
    ]
    assert candidate["blocking_experience_requirement"] == "至少3年工作经验"
    assert result["blocker_code_counts"] == {
        "mainland_location_unverified": 1,
        "requires_work_experience": 1,
    }


def test_audit_marks_same_official_detail_without_mutating_records() -> None:
    result = build_pending_evidence_audit([_job(1), _job(2)])

    assert result["explicit_taxonomy_unique_detail_count"] == 1
    assert result["candidates"][0]["same_detail_record_ids"] == [2]
    assert result["candidates"][1]["same_detail_record_ids"] == [1]
    assert result["candidates"][0]["has_same_detail_record"] is True
