from __future__ import annotations

from job_hub.student_discovery import (
    DISCOVERY_PENDING_STATUS,
    DISCOVERY_PUBLIC_STATUS,
    build_student_discovery_jobs,
)


def _job(job_id: int, *, country: str = "中国大陆", key: str | None = None, degree: str = "硕士") -> dict[str, object]:
    return {
        "id": job_id,
        "source_id": "official-source",
        "source_url": f"https://official.example/jobs/{job_id}",
        "official_evidence_url": f"https://official.example/jobs/{job_id}",
        "official_detail_key": key or f"official:{job_id}",
        "verification_status": "published_official",
        "status": "open",
        "country_or_region": country,
        "title": "地质工程师",
        "employer": "官方地质单位",
        "category": "自然资源、地调与地勘",
        "location": "北京",
        "deadline_date": "2026-12-31",
        "no_deadline_expires_at": None,
        "degree_levels": [degree] if degree else [],
        "major_tags": ["地质工程"],
        "field_evidence": {
            "evidence_scope": "official_detail_block",
            "岗位": "地质工程师",
            "专业范围": "地质工程",
            **({"学历要求": degree} if degree else {}),
            "工作地点": "北京",
        },
        "relevance_score": 80,
        "relevance_band": "强相关",
        "official_detail_precedence": 10,
        "updated_at": "2026-10-29T00:00:00Z",
    }


def test_discovery_keeps_incomplete_official_rows_visible_with_warning() -> None:
    row = _job(1, degree="")
    row["location"] = ""
    row["field_evidence"] = {"岗位": "地质工程师", "专业范围": "地质工程"}

    records, counts = build_student_discovery_jobs([row], as_of_date="2026-10-29")

    assert len(records) == 1
    assert records[0]["discovery_status"] == DISCOVERY_PENDING_STATUS
    assert "学历要求" in records[0]["discovery_missing_fields"]
    assert "工作地点" in records[0]["discovery_missing_fields"]
    assert counts[DISCOVERY_PENDING_STATUS] == 1


def test_discovery_labels_international_rows_instead_of_mixing_them_with_domestic() -> None:
    row = _job(2, country="美国")
    row["location"] = "Houston, USA"
    row["field_evidence"]["工作地点"] = "Houston, USA"
    records, _ = build_student_discovery_jobs([row], as_of_date="2026-10-29")

    assert len(records) == 1
    assert records[0]["discovery_region"] == "海外拓展"
    assert records[0]["discovery_label"] == "海外拓展机会"
    assert records[0]["discovery_status"] == DISCOVERY_PUBLIC_STATUS


def test_discovery_deduplicates_official_detail_and_prefers_confirmed_row() -> None:
    incomplete = _job(3, key="same-detail", degree="")
    incomplete["field_evidence"] = {"岗位": "地质工程师", "专业范围": "地质工程"}
    confirmed = _job(4, key="same-detail")

    records, _ = build_student_discovery_jobs(
        [incomplete, confirmed], as_of_date="2026-10-29"
    )

    assert len(records) == 1
    assert records[0]["id"] == 4
    assert records[0]["discovery_status"] == DISCOVERY_PUBLIC_STATUS


def test_discovery_excludes_expired_and_unverified_rows() -> None:
    expired = _job(5)
    expired["deadline_date"] = "2026-10-28"
    unverified = _job(6)
    unverified["verification_status"] = "candidate"

    records, _ = build_student_discovery_jobs(
        [expired, unverified], as_of_date="2026-10-29"
    )

    assert records == []
