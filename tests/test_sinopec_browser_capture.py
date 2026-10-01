from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from job_hub.browser_capture import BrowserCaptureError
from job_hub.sinopec_browser_capture import load_sinopec_browser_capture


def _payload() -> dict[str, object]:
    captured = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    detail = "https://job.sinopec.com/#/school/recruitEnterpriseDetail?deptId=unit-1"
    return {
        "version": 1,
        "status": "success",
        "platform_url": "https://job.sinopec.com/#/school/recruitmentPositions",
        "captured_at": captured,
        "enterprise_total": 2,
        "candidate_enterprise_total": 1,
        "candidate_enterprise_captured": 1,
        "enterprises": [
            {"id": "unit-1", "name": "地质调查单位", "detail_url": detail},
            {"id": "unit-2", "name": "其他单位", "detail_url": detail.replace("unit-1", "unit-2")},
        ],
        "jobs": [
            {
                "external_id": "sinopec-unit-1-position-row-1",
                "title": "油气地质研究岗",
                "major": "地质资源与地质工程",
                "employer": "地质调查单位",
                "degree": "硕士研究生",
                "headcount": "2",
                "location": "山东",
                "detail_url": detail,
                "evidence_url": detail,
                "deadline": "2026-10-28 17:00:00",
                "field_evidence": {
                    "岗位名称": "油气地质研究岗",
                    "专业要求": "地质资源与地质工程",
                    "学历要求": "硕士研究生",
                    "工作地点": "山东",
                    "招聘人数": "2",
                    "截止时间": "2026-10-28 17:00:00",
                },
            }
        ],
        "scan": {
            "enterprise_total": 2,
            "enterprise_captured": 2,
            "candidate_enterprise_total": 1,
            "candidate_enterprise_captured": 1,
            "pagination_complete": True,
            "detail_discovered": 2,
            "detail_succeeded": 2,
            "detail_failed": 0,
            "jobs_exported": 1,
        },
    }


def test_sinopec_browser_capture_validates_unit_and_detail_counts(tmp_path: Path) -> None:
    path = tmp_path / "capture.json"
    path.write_text(json.dumps(_payload(), ensure_ascii=False), encoding="utf-8")

    result = load_sinopec_browser_capture(path, max_age_hours=1)

    assert result["scan"]["detail_succeeded"] == 2
    assert result["scan"]["jobs_exported"] == 1
    assert result["jobs"][0]["field_evidence"]["专业要求"] == "地质资源与地质工程"


def test_sinopec_browser_capture_rejects_partial_detail_scan(tmp_path: Path) -> None:
    payload = _payload()
    payload["scan"]["detail_succeeded"] = 1  # type: ignore[index]
    payload["scan"]["detail_failed"] = 1  # type: ignore[index]
    path = tmp_path / "partial.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(BrowserCaptureError, match="incomplete"):
        load_sinopec_browser_capture(path, max_age_hours=1)
