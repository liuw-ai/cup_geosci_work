from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from job_hub.browser_capture import BrowserCaptureError
from job_hub.cmgb_browser_capture import (
    CmgbBrowserCaptureError,
    extract_cmgb_detail,
    load_cmgb_browser_capture,
)
from job_hub.profiles import evaluate_student_publication
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


def test_cmgb_browser_detail_evidence_passes_student_publication_gate(tmp_path: Path) -> None:
    """A captured official detail must not be stranded as pending evidence."""
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
    posting = OfficialSourceCollector(make_settings(tmp_path)).collect(source)[0]
    decision = evaluate_student_publication(
        {
            "title": posting.title,
            "employer": posting.employer,
            "location": posting.location,
            "category": source["category"],
            "field_evidence": posting.field_evidence,
            "degree_levels": ["本科"],
        }
    )
    assert posting.field_evidence["evidence_scope"] == "official_cmgb_browser_detail"
    assert decision.status == "student_eligible"
    assert "undergraduate-resource-exploration" in decision.matched_profile_ids


class _FakeLocator:
    def __init__(self, value: str, *, count: int = 1) -> None:
        self.value = value
        self._count = count

    def inner_text(self) -> str:
        return self.value

    @property
    def first(self) -> "_FakeLocator":
        return self

    def count(self) -> int:
        return self._count

    def nth(self, _index: int) -> "_FakeLocator":
        return self

    def locator(self, _selector: str) -> "_FakeLocator":
        return _FakeLocator("")


class _FakePage:
    def __init__(self, text: str) -> None:
        self.text = text

    def locator(self, selector: str) -> _FakeLocator:
        if selector == ".overview-item":
            return _FakeLocator("", count=0)
        if selector == "body":
            return _FakeLocator(self.text)
        if selector == "h1":
            return _FakeLocator("地质勘查技术岗")
        if selector == ".company-name":
            return _FakeLocator("中国冶金地质总局一局")
        return _FakeLocator("")


class _HtmlLocator:
    def __init__(self, nodes: list[object]) -> None:
        self.nodes = nodes

    @property
    def first(self) -> "_HtmlLocator":
        return _HtmlLocator(self.nodes[:1])

    def count(self) -> int:
        return len(self.nodes)

    def nth(self, index: int) -> "_HtmlLocator":
        return _HtmlLocator(self.nodes[index : index + 1])

    def locator(self, selector: str) -> "_HtmlLocator":
        children = []
        for node in self.nodes:
            children.extend(node.select(selector))
        return _HtmlLocator(children)

    def inner_text(self) -> str:
        return " ".join(node.get_text(" ", strip=True) for node in self.nodes)


class _HtmlFixturePage:
    def __init__(self, html: str) -> None:
        self.soup = BeautifulSoup(html, "html.parser")

    def locator(self, selector: str) -> _HtmlLocator:
        return _HtmlLocator(self.soup.select(selector))


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


def test_cmgb_detail_parser_reads_live_iguopin_detail_fixture() -> None:
    fixture = Path(__file__).parent / "fixtures" / "domestic" / "cmgb_iguopin_detail.html"
    detail = extract_cmgb_detail(
        _HtmlFixturePage(fixture.read_text(encoding="utf-8")),
        detail_url="https://www.iguopin.com/job/detail?id=219390059252548343",
        allowed_hosts=set(HOSTS),
    )
    assert detail["title"] == "地勘专业技术岗"
    assert detail["employer"] == "中国冶金地质总局一局"
    assert detail["major"] == "地质工程、资源勘查工程"
    assert detail["degree"] == "本科"
    assert detail["location"] == "邢台，保定"
    assert detail["headcount"] == "1人"
    assert detail["deadline"] == "2026-11-06 23:59:59"
    assert "地质工程、资源勘查工程" in detail["description"]


def test_cmgb_detail_parser_rejects_summary_only_major() -> None:
    page = _HtmlFixturePage(
        """
        <main>
          <div class="title">普通技术岗</div>
          <div class="company-title">示例单位</div>
          <span class="address">北京</span>
          <div class="overview-item"><span class="overview-title">专业要求：</span><span class="overview-desc">详见职位描述</span></div>
          <div class="overview-item"><span class="overview-title">最低学历：</span><span class="overview-desc">本科</span></div>
          <div class="overview-item"><span class="overview-title">招聘人数：</span><span class="overview-desc">1人</span></div>
          <div class="overview-item"><span class="overview-title">报名截止：</span><span class="overview-desc">2026-11-06</span></div>
        </main>
        """
    )
    with pytest.raises(BrowserCaptureError, match="major evidence"):
        extract_cmgb_detail(
            page,
            detail_url="https://www.iguopin.com/job/detail?id=summary-only",
            allowed_hosts=set(HOSTS),
        )


def test_cmgb_detail_parser_extracts_major_from_condition_sentence() -> None:
    page = _HtmlFixturePage(
        """
        <main>
          <div class="title">地质专业技术岗（化探类方向）</div>
          <div class="company-title">示例地质单位</div>
          <span class="address">太原</span>
          <div class="overview-item"><span class="overview-title">专业要求：</span><span class="overview-desc">详见职位描述</span></div>
          <div class="overview-item"><span class="overview-title">最低学历：</span><span class="overview-desc">硕士</span></div>
          <div class="overview-item"><span class="overview-title">招聘人数：</span><span class="overview-desc">1人</span></div>
          <div class="overview-item"><span class="overview-title">报名截止：</span><span class="overview-desc">2026-11-06 23:59:59</span></div>
          <div class="job-duty">1.具备地球化学等化探类相关专业；2.参与区域地球化学野外勘查。</div>
        </main>
        """
    )
    detail = extract_cmgb_detail(
        page,
        detail_url="https://www.iguopin.com/job/detail?id=condition-major",
        allowed_hosts=set(HOSTS),
    )
    assert detail["major"] == "地球化学等化探类相关专业"
