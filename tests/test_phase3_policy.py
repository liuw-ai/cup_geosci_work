from datetime import date

from job_hub.phase3_policy import (
    derive_publication_layer,
    deadline_is_expired,
    no_deadline_expiry,
    source_failure_state,
)
from job_hub.employers import enrich_job


def test_no_deadline_uses_asia_shanghai_calendar_days():
    assert no_deadline_expiry("2026-10-16", None) == "2027-01-14"
    assert no_deadline_expiry(None, "2026-10-16T00:30:00Z") == "2027-01-14"
    assert deadline_is_expired(None, "2027-01-14", date(2027, 1, 14)) is False
    assert deadline_is_expired(None, "2027-01-14", date(2027, 1, 15)) is True


def test_freshness_failure_is_not_a_withdrawal_reason():
    assert source_failure_state("http_403") == "source_unavailable"
    assert source_failure_state("page_structure_changed") == "degraded"
    assert source_failure_state("temporary_absence") == "stale_pending_review"


def test_legacy_publication_status_maps_to_independent_layers():
    assert derive_publication_layer("student_eligible", {"major_match_mode": "exact_four_names"}) == "exact_match"
    assert derive_publication_layer("similar_eligible", {"major_match_mode": "similar_major"}) == "broad_geoscience_match"
    assert derive_publication_layer("pending_evidence", has_basic_major_evidence=True) == "incomplete_but_basic_match"
    assert derive_publication_layer("source_unavailable") == "source_unavailable"


def test_student_display_uses_phase3_publication_layer_labels():
    broad = enrich_job(
        {
            "publication_status": "similar_eligible",
            "publication_layer": "broad_geoscience_match",
            "field_evidence": {},
            "title": "地质岗位",
            "employer": "测试单位",
        }
    )
    incomplete = enrich_job(
        {
            "publication_status": "pending_evidence",
            "publication_layer": "incomplete_but_basic_match",
            "field_evidence": {},
            "title": "地质岗位",
            "employer": "测试单位",
        }
    )
    assert broad["publication_status_label"] == "专业范围宽泛"
    assert incomplete["publication_status_label"] == "官方信息不完整"
