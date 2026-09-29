from __future__ import annotations

import pytest

from job_hub.provincial_matrix_audit import build_provincial_matrix_audit


ROLES = [
    "human_resources_or_exam",
    "natural_resources",
    "geology_bureau_or_institute",
    "public_institution_recruitment",
    "civil_service",
]


def _matrix() -> dict[str, object]:
    return {
        "required_roles": ROLES,
        "province_targets": {
            "测试省": {
                "human_resources_or_exam": {
                    "state": "verified",
                    "source_id": "verified-source",
                },
                "natural_resources": {
                    "state": "candidate",
                    "official_entry_url": "https://natural.example.test/",
                },
                "geology_bureau_or_institute": {"state": "unlocated"},
                "public_institution_recruitment": {"state": "blocked"},
                "civil_service": {"state": "unlocated"},
            }
        },
    }


def _registry() -> dict[str, object]:
    return {
        "records": [
            {
                "id": "verified-record",
                "source_id": "verified-source",
                "province": "测试省",
                "role": "human_resources_or_exam",
                "validation_stage": "server_health_and_adapter_verified",
                "official_entry_url": "https://hr.example.test/recruit/",
                "backup_entry_urls": ["https://hr.example.test/"],
                "fixture_path": "tests/fixtures/source_validation/sample.html",
                "sample": {
                    "official_url": "https://hr.example.test/recruit/notice.html",
                    "field_evidence": {
                        "publisher": "官方单位",
                        "published_date": "2026-09-01",
                        "recruitment_scope": "公开招聘",
                        "application_or_deadline": "报名截止2026-10-01",
                        "attachment_or_position_table": "官方岗位表",
                    },
                },
            }
        ]
    }


def test_audit_separates_evidence_from_runtime_and_keeps_unlocated_unready() -> None:
    sources = (
        {
            "id": "verified-source",
            "name": "测试人社官方来源",
            "enabled": True,
        },
    )
    report = build_provincial_matrix_audit(
        matrix=_matrix(),
        validation_registry=_registry(),
        source_records=(item for item in sources),
        health_records=[
            {"source_id": "verified-source", "status": "source_active", "checked_at": "2026-09-29"}
        ],
        latest_runs=[
            {"source_id": "verified-source", "status": "finished", "finished_at": "2026-09-29T00:00:00Z"}
        ],
    )

    assert report["target_count"] == 5
    assert report["state_counts"] == {
        "blocked": 1,
        "candidate": 1,
        "unlocated": 2,
        "verified": 1,
    }
    assert report["ready_for_review_count"] == 1
    assert report["ready_for_activation_count"] == 1

    rows = {(item["role"]): item for item in report["rows"]}
    assert rows["human_resources_or_exam"]["evidence_state"] == "sample_and_fixture_verified"
    assert rows["human_resources_or_exam"]["ready_for_activation"] is True
    assert "target_state:candidate" in rows["natural_resources"]["blocked_reasons"]
    assert rows["geology_bureau_or_institute"]["evidence_state"] == "not_recorded"
    assert "no_bound_source" in rows["geology_bureau_or_institute"]["blocked_reasons"]
    assert "target_state:blocked" in rows["public_institution_recruitment"]["blocked_reasons"]


def test_audit_rejects_unknown_role_filter() -> None:
    with pytest.raises(ValueError, match="Unknown provincial source role"):
        build_provincial_matrix_audit(
            matrix=_matrix(), validation_registry=_registry(), roles=["other"]
        )

