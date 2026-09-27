from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from job_hub.cmgb_browser_capture import (
    CmgbBrowserCaptureError,
    extract_cmgb_detail,
    load_cmgb_browser_capture,
)
from job_hub.sources import OfficialSourceCollector

from conftest import make_settings


HOSTS = ["cmgb.iguopin.com", "www.iguopin.com", "www.cmgb.com.cn"]


def _row(external_id: str = "cmgb-1") -> dict[str, object]:
    evidence = {
        "岗位": "地质勘查技术岗",
        "专业范围": "地质工程、资源勘查工程",
        "学历要求": "本科及以上",
        "工作地点": "河北邢台",
        "招聘人数": "2",
        "报名截止": "2026-11-06",
        "官方详情链接": f"https://www.iguopin.com/job/detail?id={external_id}",
    }
    return {
        "external_id": external_id,
        "title": "地质勘查技术岗",
        "employer": "中国冶金地质总局一局",
        "major": "地质工程、资源勘查工程",
        "degree": "本科及以上",
        "location": "河北邢台",
        "headcount": "2",
        "deadline": "2026-11-06",
        "detail_url": evidence["官方详情链接"],
        "evidence_url": evidence["官方详情链接"],
        "field_evidence": evidence,
    }


def _payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "version": 1,
        "status": "success",
        "platform_url": "https://cmgb.iguopin.com/jobCampus",
        "captured_at": "2026-09-27T02:00:00Z",
        "scan": {
            "pages_scanned": 8,
            "pagination_complete": True,
            "rows_discovered": 1,
            "rows_exported": 1,
            "failed_rows": 0,
            "detail_discovered": 1,
            "detail_succeeded": 1,
            "detail_failed": 0,
        },
        "rows": [_row()],
    }
    payload.update(overrides)
    return payload


def _write(tmp_path: Path, payload: dict[str, object]) -> Path:
    path = tmp_path / "captures" / "cmgb.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_cmgb_capture_requires_complete_pagination_and_detail_success(tmp_path: Path) -> None:
    path = _write(tmp_path, _payload())
    payload = load_cmgb_browser_capture(
        path,
        allowed_hosts=HOSTS,
        max_age_hours=24,
        now=datetime(2026, 9, 27, 10, 0, tzinfo=timezone.utc),
    )
    assert payload["scan"]["pages_scanned"] == 8
    assert payload["rows"][0]["field_evidence"]["专业范围"].startswith("地质")


def test_cmgb_capture_rejects_partial_detail_scan(tmp_path: Path) -> None:
    scan = dict(_payload()["scan"])
    scan.update({"detail_failed": 1, "failed_rows": 1})
    path = _write(tmp_path, _payload(status="partial", scan=scan))
    with pytest.raises(CmgbBrowserCaptureError, match="not publishable"):
        load_cmgb_browser_capture(path, allowed_hosts=HOSTS)


def test_cmgb_capture_rejects_missing_evidence_field(tmp_path: Path) -> None:
    row = _row()
    del row["field_evidence"]["专业范围"]
    path = _write(tmp_path, _payload(rows=[row]))
    with pytest.raises(CmgbBrowserCaptureError, match="专业范围"):
        load_cmgb_browser_capture(path, allowed_hosts=HOSTS)


def test_cmgb_capture_rejects_non_official_detail_url(tmp_path: Path) -> None:
    row = _row()
    row["detail_url"] = "https://example.com/job/1"
    path = _write(tmp_path, _payload(rows=[row]))
    with pytest.raises(CmgbBrowserCaptureError, match="allowlisted"):
        load_cmgb_browser_capture(path, allowed_hosts=HOSTS)


def test_cmgb_capture_rejects_stale_manifest(tmp_path: Path) -> None:
    path = _write(tmp_path, _payload())
    with pytest.raises(CmgbBrowserCaptureError, match="stale"):
        load_cmgb_browser_capture(
            path,
            allowed_hosts=HOSTS,
            max_age_hours=1,
            now=datetime(2026, 9, 27, 10, 0, tzinfo=timezone.utc),
        )


def test_cmgb_source_routes_verified_rows_through_normal_pipeline(tmp_path: Path) -> None:
    _write(tmp_path, _payload())
    source = {
        "id": "cmgb-browser-test",
        "name": "国聘动态浏览器测试来源",
        "publisher": "中国冶金地质总局",
        "homepage_url": "https://cmgb.iguopin.com/jobCampus",
        "source_type": "cmgb_browser_rows",
        "category": "自然资源、地调与地勘",
        "source_tier": "A",
        "enabled": True,
        "config": {
            "allowed_hosts": HOSTS,
            "capture_path": "captures/cmgb.json",
            "application_url": "https://cmgb.iguopin.com/jobCampus",
            "max_age_hours": 24,
            "require_complete_scan": True,
            "minimum_relevance": 0,
        },
    }
    postings = OfficialSourceCollector(make_settings(tmp_path)).collect(source)
    assert len(postings) == 1
    assert postings[0].field_evidence["招聘人数"] == "2"
    assert postings[0].official_evidence_url.startswith("https://www.iguopin.com/")


class _FakeLocator:
    def __init__(self, value: str) -> None:
        self.value = value

    def inner_text(self) -> str:
        return self.value

    @property
    def first(self) -> "_FakeLocator":
        return self

    def count(self) -> int:
        return 1


class _FakePage:
    def __init__(self, text: str) -> None:
        self.text = text

    def locator(self, selector: str) -> _FakeLocator:
        if selector == "body":
            return _FakeLocator(self.text)
        if selector == "h1":
            return _FakeLocator("地质勘查技术岗")
        if selector == ".company-name":
            return _FakeLocator("中国冶金地质总局一局")
        return _FakeLocator("")


def test_cmgb_detail_parser_uses_rendered_text_for_generic_nodes() -> None:
    page = _FakePage(
        "岗位名称：地质勘查技术岗 专业要求：地质工程、资源勘查工程 "
        "最低学历：本科及以上 工作地点：河北邢台 招聘人数：2 报名截止：2026-11-06"
    )
    detail = extract_cmgb_detail(
        page,
        detail_url="https://www.iguopin.com/job/detail?id=cmgb-1",
        allowed_hosts=set(HOSTS),
    )
    assert detail["major"] == "地质工程、资源勘查工程"
    assert detail["headcount"] == "2"
