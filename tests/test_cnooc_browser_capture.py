import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from job_hub.cnooc_browser_capture import (
    _candidate_job,
    _detail_failure_budget,
    _detail_data_wait_budget,
    _detail_timeout_budget,
    _detail_retry_budget,
    _is_access_limited_detail_error,
    load_cnooc_browser_capture,
    merge_cnooc_partial_capture,
    persist_cnooc_browser_capture,
    repair_cnooc_detail_requirement_evidence,
)
from job_hub.browser_capture import BrowserCaptureError


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_cnooc_detail_retry_budget_is_bounded_and_has_safe_defaults() -> None:
    assert _detail_retry_budget({}) == (2, 500)
    assert _detail_retry_budget({"detail_retry_attempts": 99, "detail_retry_delay_ms": 99_999}) == (3, 5_000)
    assert _detail_retry_budget({"detail_retry_attempts": -1, "detail_retry_delay_ms": -10}) == (0, 0)


def test_cnooc_detail_failure_circuit_breaker_is_bounded() -> None:
    assert _detail_failure_budget({}) == 5
    assert _detail_failure_budget({"max_consecutive_detail_failures": 0}) == 1
    assert _detail_failure_budget({"max_consecutive_detail_failures": 999}) == 50
    assert _detail_failure_budget({"max_consecutive_detail_failures": "bad"}) == 5


def test_cnooc_detail_timeout_is_shorter_than_outer_capture_budget() -> None:
    assert _detail_timeout_budget({}, 45_000) == 15_000
    assert _detail_timeout_budget({"detail_timeout_ms": 5_000}, 45_000) == 5_000
    assert _detail_timeout_budget({"detail_timeout_ms": 999_999}, 10_000) == 10_000
    assert _detail_timeout_budget({"detail_timeout_ms": 10}, 45_000) == 1_000


def test_cnooc_detail_data_wait_is_bounded_by_navigation_timeout() -> None:
    assert _detail_data_wait_budget({}, 15_000) == 3_000
    assert _detail_data_wait_budget({"detail_data_wait_ms": 8_000}, 5_000) == 5_000
    assert _detail_data_wait_budget({"detail_data_wait_ms": 10}, 15_000) == 250
    assert _detail_data_wait_budget({"detail_data_wait_ms": "bad"}, 15_000) == 3_000


def test_cnooc_access_limited_detail_errors_are_detected() -> None:
    assert _is_access_limited_detail_error(
        RuntimeError("official detail is missing window.__INITIAL_DATA__")
    )
    assert _is_access_limited_detail_error(RuntimeError("HTTP 429"))
    assert _is_access_limited_detail_error(RuntimeError("robots.txt disallows path"))
    assert not _is_access_limited_detail_error(RuntimeError("table has no degree column"))


def _payload(*, failed: int = 0, status: str = "success") -> dict:
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    row = {
        "external_id": "CC258591510J40972459115",
        "title": "勘探地质助理工程师",
        "employer": "中国海油-中海石油（中国）有限公司深圳分公司",
        "major": "专业要求：地质学类、地质工程、地质资源与地质工程",
        "degree": "硕士",
        "location": "深圳市南山区",
        "headcount": "若干（官方未披露具体人数）",
        "deadline": "2027-10-01",
        "detail_url": "https://xiaoyuan.zhaopin.com/job/CC258591510J40972459115",
        "evidence_url": "https://xiaoyuan.zhaopin.com/job/CC258591510J40972459115",
        "field_evidence": {
            "岗位": "勘探地质助理工程师",
            "专业范围": "专业要求：地质学类、地质工程、地质资源与地质工程",
            "学历要求": "硕士",
            "工作地点": "深圳市南山区",
            "招聘人数": "若干（官方未披露具体人数）",
            "报名截止": "2027-10-01",
            "官方岗位详情": "https://xiaoyuan.zhaopin.com/job/CC258591510J40972459115",
        },
    }
    return {
        "version": 1,
        "status": status,
        "platform_url": "https://cnooc.zhaopin.com/job/index.html",
        "captured_at": now,
        "scan": {
            "pages_scanned": 4,
            "pagination_complete": failed == 0,
            "listing_total": 350,
            "candidate_rows": 1,
            "detail_discovered": 1,
            "detail_succeeded": 0 if failed else 1,
            "detail_failed": failed,
            "rows_exported": 0 if failed else 1,
        },
        "rows": [] if failed else [row],
        "failure_records": [] if not failed else [{"detail_url": row["detail_url"], "reason": "challenge"}],
    }


def test_cnooc_capture_requires_complete_detail_scan(tmp_path) -> None:
    path = tmp_path / "cnooc.json"
    path.write_text(json.dumps(_payload()), encoding="utf-8")

    payload = load_cnooc_browser_capture(path)

    assert payload["scan"]["listing_total"] == 350
    assert payload["rows"][0]["deadline"] == "2027-10-01"
    assert payload["capture_age_hours"] >= 0


def test_cnooc_capture_repairs_legacy_detail_fields_from_same_job_block(tmp_path) -> None:
    payload = _payload()
    legacy_detail = (
        "任职要求：1.学历要求：硕士研究生及以上 "
        "2.专业要求：地质学、地球物理学 "
        "3.外语要求：大学英语六级"
    )
    row = payload["rows"][0]
    row["description"] = legacy_detail
    row["major"] = legacy_detail
    row["field_evidence"]["专业要求"] = legacy_detail
    row["field_evidence"]["专业范围"] = legacy_detail
    path = tmp_path / "cnooc-legacy.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    restored = load_cnooc_browser_capture(path)
    restored_row = restored["rows"][0]

    assert restored_row["major"] == "地质学、地球物理学"
    assert restored_row["degree"] == "硕士研究生及以上"
    assert restored_row["field_evidence"]["专业范围"] == "地质学、地球物理学"
    assert restored_row["field_evidence"]["学历要求"] == "硕士研究生及以上"


def test_cnooc_detail_repair_rejects_duties_without_major_requirement() -> None:
    row = {
        "description": "岗位职责：开展地质资料解释。学历要求：硕士研究生及以上。",
        "major": "岗位职责：开展地质资料解释。学历要求：硕士研究生及以上。",
        "degree": "硕士",
    }
    evidence = {
        "专业要求": row["major"],
        "专业范围": row["major"],
        "学历要求": "硕士",
    }

    with pytest.raises(BrowserCaptureError, match="bounded job-level major"):
        repair_cnooc_detail_requirement_evidence(row, evidence, index=1)


def test_cnooc_live_source_reconciles_only_after_complete_capture() -> None:
    sources = json.loads((PROJECT_ROOT / "data" / "sources.json").read_text(encoding="utf-8"))
    source = next(item for item in sources if item["id"] == "cnooc-career-browser")

    assert source["source_type"] == "cnooc_browser_rows"
    assert source["config"]["require_complete_scan"] is True
    assert source["config"]["reconcile_missing_external_ids"] is True
    assert source["config"]["allow_unclassified_domestic_address"] is True
    assert source["config"]["max_consecutive_detail_failures"] == 5
    assert source["config"]["detail_timeout_ms"] == 15_000
    assert source["config"]["detail_data_wait_ms"] == 3_000
    assert source["config"]["api_allowed_hosts"] == ["fe.zhaopin.com"]


def test_cnooc_partial_capture_never_becomes_empty_success(tmp_path) -> None:
    path = tmp_path / "cnooc.json"
    path.write_text(json.dumps(_payload(failed=1, status="partial")), encoding="utf-8")

    with pytest.raises(BrowserCaptureError, match="not publishable"):
        load_cnooc_browser_capture(path)


def test_cnooc_partial_capture_does_not_replace_success_snapshot(tmp_path) -> None:
    path = tmp_path / "cnooc.json"
    success = _payload()
    persist_cnooc_browser_capture(output=path, payload=success)
    before = path.read_text(encoding="utf-8")

    partial = _payload(failed=1, status="partial")
    persist_cnooc_browser_capture(output=path, payload=partial)

    assert path.read_text(encoding="utf-8") == before
    failure = path.with_suffix(".failure.json")
    assert failure.is_file()
    assert json.loads(failure.read_text(encoding="utf-8"))["status"] == "partial"


def test_cnooc_partial_complete_listing_can_be_repaired_atomically() -> None:
    payload = _payload(failed=1, status="partial")
    payload["scan"]["pagination_complete"] = True
    payload["scan"]["candidate_rows"] = 1
    payload["scan"]["detail_discovered"] = 1
    payload["scan"]["detail_succeeded"] = 0
    payload["scan"]["detail_failed"] = 1
    payload["scan"]["rows_exported"] = 0
    repaired = dict(_payload()["rows"][0])

    merged = merge_cnooc_partial_capture(
        payload,
        [repaired],
        captured_at="2026-10-07T00:00:00Z",
    )

    assert merged["status"] == "success"
    assert len(merged["rows"]) == 1
    assert merged["scan"]["detail_failed"] == 0
    assert merged["retry"] == {
        "attempted": 1,
        "repaired": 1,
        "remaining": 0,
        "source_capture": "same_complete_listing_pass",
    }


def test_cnooc_partial_retry_rejects_incomplete_listing() -> None:
    payload = _payload(failed=1, status="partial")
    with pytest.raises(BrowserCaptureError, match="complete listing pagination"):
        merge_cnooc_partial_capture(payload, [])


def test_cnooc_partial_retry_keeps_unresolved_detail_partial() -> None:
    payload = _payload(failed=1, status="partial")
    payload["scan"]["pagination_complete"] = True
    payload["scan"]["candidate_rows"] = 1
    payload["scan"]["detail_discovered"] = 1
    payload["scan"]["detail_succeeded"] = 0
    payload["scan"]["detail_failed"] = 1
    payload["scan"]["rows_exported"] = 0

    merged = merge_cnooc_partial_capture(payload, [])

    assert merged["status"] == "partial"
    assert merged["scan"]["detail_failed"] == 1
    assert len(merged["failure_records"]) == 1


def test_cnooc_capture_rejects_non_official_detail_url(tmp_path) -> None:
    payload = _payload()
    payload["rows"][0]["detail_url"] = "https://example.com/job/1"
    path = tmp_path / "cnooc.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(BrowserCaptureError, match="allowlisted"):
        load_cnooc_browser_capture(path)


def test_cnooc_candidate_filter_excludes_non_geoscience_function_with_broad_keyword() -> None:
    row = {
        "job": {
            "title": "审计中心审计岗",
            "detail": "专业要求：工商管理类、经济学类；热爱海洋石油事业。",
            "jobCategories": ["非一线岗位"],
            "url": "https://xiaoyuan.zhaopin.com/job/CC258591510J40972505615",
        }
    }

    assert _candidate_job(
        row,
        ["地质|勘查|油气|海洋"],
        ["财务|法务|行政|人力资源|审计|采购|市场|风控|合规|法律"],
    ) is None


def test_cnooc_candidate_filter_can_defer_unrestricted_major_to_detail_gate() -> None:
    row = {
        "job": {
            "title": "综合管理岗",
            "detail": "专业不限；本科及以上。",
            "jobCategories": ["校园招聘"],
            "url": "https://xiaoyuan.zhaopin.com/job/CC258591510J40972505616",
        }
    }

    assert _candidate_job(
        row,
        ["地质|勘查|油气|海洋"],
        ["财务|法务|行政|人力资源|审计|采购|市场|风控|合规|法律"],
        allow_unrestricted_candidates=True,
    ) == ("综合管理岗", row["job"]["url"])


def test_cnooc_candidate_filter_does_not_exclude_technical_role_for_body_keyword() -> None:
    row = {
        "job": {
            "title": "采油工艺助理工程师",
            "detail": "负责工艺技术支持和合规管理，专业要求：石油工程、地质工程。",
            "jobCategories": ["技术研发"],
            "url": "https://xiaoyuan.zhaopin.com/job/CC258591510J40972505617",
        }
    }

    assert _candidate_job(
        row,
        ["地质|勘查|油气|海洋"],
        ["财务|法务|行政|人力资源|审计|采购|市场|风控|合规|法律"],
        allow_unrestricted_candidates=True,
    ) == ("采油工艺助理工程师", row["job"]["url"])
