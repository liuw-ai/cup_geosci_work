from datetime import date

from job_hub.phase3_audit import derive_phase3_update


def _job(**overrides):
    value = {
        "id": 1,
        "source_id": "official",
        "source_url": "https://official.example/job/1",
        "official_evidence_url": "https://official.example/job/1",
        "application_url": "https://official.example/apply/1",
        "last_official_verified_at": "2026-10-10T00:00:00Z",
        "last_seen_at": "2026-10-10T00:00:00Z",
        "published_date": "2026-10-10",
        "deadline_date": "2026-12-31",
        "field_evidence_json": '{"专业范围":"地质学"}',
        "major_tags_json": '["地质学"]',
        "publication_status": "student_eligible",
        "publication_basis_json": '{"major_match_mode":"exact_four_names"}',
        "status": "open",
        "withdrawal_reason": None,
    }
    value.update(overrides)
    return value


def test_backfill_derives_exact_layer_and_official_links():
    update, audit = derive_phase3_update(
        _job(),
        source={"id": "official", "enabled": 1, "trust_grade": "A"},
        source_health={"status": "success"},
        withdrawal_event=None,
        as_of=date(2026, 10, 17),
    )

    assert update["publication_layer"] == "exact_match"
    assert update["trust_grade"] == "A"
    assert update["official_application_url"].endswith("/1")
    assert update["last_official_verified_at"] == "2026-10-10T00:00:00Z"
    assert audit["classification"] == "not_withdrawn"


def test_legacy_freshness_withdrawal_is_reopened_and_source_unavailable():
    update, audit = derive_phase3_update(
        _job(
            id=2,
            status="withdrawn",
            publication_status="student_eligible",
            publication_basis_json='{ "major_match_mode": "exact_four_names" }',
        ),
        source={"id": "official", "enabled": 1, "trust_grade": "A"},
        source_health={"status": "access_limited"},
        withdrawal_event={"reason": "官方捕获证据已超过 30 小时新鲜度窗口"},
        as_of=date(2026, 10, 17),
    )

    assert audit["classification"] == "legacy_source_failure"
    assert audit["eligible_withdrawal"] == 0
    assert update["status"] == "open"
    assert update["withdrawal_reason"] is None
    assert update["publication_layer"] == "source_unavailable"
