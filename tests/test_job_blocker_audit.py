from __future__ import annotations

from job_hub.job_blocker_audit import build_job_blocker_audit
from job_hub.profiles import PublicationDecision


def _job(**changes: object) -> dict[str, object]:
    value: dict[str, object] = {
        "id": 1,
        "source_id": "official-test-source",
        "source_name": "测试官方来源",
        "title": "地质工程技术岗",
        "employer": "测试地质调查院",
        "location": "北京市",
        "country_or_region": "中国大陆",
        "status": "open",
        "publication_status": "student_eligible",
        "deadline_date": "2099-12-31",
        "no_deadline_expires_at": None,
        "official_detail_key": "detail-1",
        "source_url": "https://careers.example.edu.cn/jobs/1",
        "official_evidence_url": "https://careers.example.edu.cn/jobs/1",
        "field_evidence": {
            "evidence_scope": "admin_verified_official_record",
            "岗位": "地质工程技术岗",
            "专业范围": "地质工程",
            "学历要求": "硕士研究生",
            "工作地点": "北京市",
        },
    }
    value.update(changes)
    return value


def test_job_blocker_audit_is_read_only_and_keeps_persisted_state_separate() -> None:
    jobs = [
        _job(id=1),
        _job(
            id=2,
            title="地质工程技术岗",
            publication_status="out_of_scope",
            country_or_region="加拿大",
            location="加拿大",
            official_detail_key="detail-2",
        ),
        _job(
            id=3,
            title="地质工程技术岗",
            publication_status="student_eligible",
            field_evidence={
                "evidence_scope": "official_detail_block",
                "岗位": "地质工程技术岗",
                "专业范围": "地质相关专业",
                "学历要求": "硕士研究生",
                "工作地点": "北京市",
            },
            official_detail_key="detail-3",
        ),
        _job(
            id=4,
            title="地质工程技术岗",
            publication_status="student_eligible",
            deadline_date="2020-01-01",
            official_detail_key="detail-4",
        ),
    ]

    report = build_job_blocker_audit(jobs, as_of="2026-10-27", sample_limit=2)

    assert report["read_only"] is True
    assert report["total_jobs"] == 4
    assert report["current_record_count"] == 3
    assert report["current_blocker_counts_recomputed"]["public_gate_passed"] == 1
    assert report["current_blocker_counts_recomputed"]["non_mainland_location"] == 1
    assert report["current_blocker_counts_recomputed"]["major_or_role_evidence_missing"] == 1
    assert report["persisted_vs_recomputed_mismatch_count"] == 1
    assert len(report["samples_by_blocker"]["non_mainland_location"]) == 1


def test_job_blocker_audit_deduplicates_current_public_detail_keys() -> None:
    jobs = [
        _job(id=1, official_detail_key="same-detail"),
        _job(id=2, official_detail_key="same-detail"),
        _job(id=3, official_detail_key="other-detail"),
    ]

    report = build_job_blocker_audit(jobs, as_of="2026-10-27")

    assert report["current_record_count"] == 3
    assert report["current_recomputed_eligible_deduplicated_count"] == 2


def test_pending_blockers_use_specific_decision_labels() -> None:
    from job_hub.job_blocker_audit import _blocker_code

    assert _blocker_code(
        PublicationDecision(
            status="pending_evidence",
            label="岗位类型需核验",
            reason="博士后类型不能按普通学生学历门槛展示。",
            matched_profile_ids=(),
        )
    ) == "special_role_type_needs_review"
    assert _blocker_code(
        PublicationDecision(
            status="pending_evidence",
            label="待核岗位方向",
            reason="相近专业但岗位方向未证明。",
            matched_profile_ids=(),
        )
    ) == "major_or_role_evidence_missing"
    assert _blocker_code(
        PublicationDecision(
            status="pending_evidence",
            label="待补岗位级证据",
            reason="未提供岗位级字段定位。",
            matched_profile_ids=(),
        )
    ) == "job_level_evidence_missing"
