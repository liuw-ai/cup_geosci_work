from job_hub.unrestricted_audit import build_unrestricted_role_audit


def _job(
    job_id: int,
    *,
    category: str,
    publication_status: str,
    status: str = "open",
    employer: str = "测试单位",
) -> dict:
    return {
        "id": job_id,
        "status": status,
        "publication_status": publication_status,
        "source_id": "iguopin-general-geoscience-browser",
        "source_name": "国聘主站",
        "title": "科研建模岗",
        "employer": employer,
        "category": category,
        "location": "北京",
        "deadline_date": "2026-12-31",
        "field_evidence": {
            "evidence_scope": "official_iguopin_browser_detail",
            "岗位": "科研建模岗",
            "专业范围": "专业不限",
            "学历要求": "博士",
        },
    }


def test_unrestricted_audit_keeps_enterprise_rows_private() -> None:
    result = build_unrestricted_role_audit(
        [
            _job(
                1,
                category="能源、工程与地学拓展",
                publication_status="out_of_scope",
            ),
            _job(
                2,
                category="科研院所、高校与博士后",
                publication_status="unrestricted_eligible",
                employer="测试研究所",
            ),
        ]
    )

    assert result["unrestricted_count"] == 2
    assert result["publicly_eligible_count"] == 1
    assert result["private_or_blocked_count"] == 1
    assert result["publication_mutated"] is False


def test_unrestricted_audit_defaults_to_current_open_rows() -> None:
    result = build_unrestricted_role_audit(
        [_job(1, category="事业单位与人才引进", publication_status="out_of_scope", status="expired")]
    )
    assert result["unrestricted_count"] == 0

    all_statuses = build_unrestricted_role_audit(
        [_job(1, category="事业单位与人才引进", publication_status="out_of_scope", status="expired")],
        open_only=False,
    )
    assert all_statuses["unrestricted_count"] == 1
