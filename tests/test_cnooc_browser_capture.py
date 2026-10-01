import json
from datetime import datetime, timezone

import pytest

from job_hub.cnooc_browser_capture import load_cnooc_browser_capture
from job_hub.browser_capture import BrowserCaptureError


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


def test_cnooc_partial_capture_never_becomes_empty_success(tmp_path) -> None:
    path = tmp_path / "cnooc.json"
    path.write_text(json.dumps(_payload(failed=1, status="partial")), encoding="utf-8")

    with pytest.raises(BrowserCaptureError, match="not publishable"):
        load_cnooc_browser_capture(path)


def test_cnooc_capture_rejects_non_official_detail_url(tmp_path) -> None:
    payload = _payload()
    payload["rows"][0]["detail_url"] = "https://example.com/job/1"
    path = tmp_path / "cnooc.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(BrowserCaptureError, match="allowlisted"):
        load_cnooc_browser_capture(path)
