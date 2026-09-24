from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from job_hub.browser_capture import (
    BrowserCaptureError,
    extract_rendered_rows,
    load_browser_capture,
)
from job_hub.sources import OfficialSourceCollector

from conftest import make_settings


ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "browser" / "pipechina_capture.json"


def _source(tmp_path: Path) -> dict[str, object]:
    return {
        "id": "pipechina-browser-capture",
        "name": "国家管网动态捕获夹具",
        "publisher": "国家石油天然气管网集团有限公司",
        "homepage_url": "https://zhaopin.pipechina.com.cn/recruit",
        "source_type": "official_browser_rows",
        "category": "管网、炼化与综合能源",
        "source_tier": "A",
        "enabled": True,
        "config": {
            "allowed_hosts": ["zhaopin.pipechina.com.cn", "www.pipechina.com.cn"],
            "capture_path": "captures/pipechina.json",
            "application_url": "https://zhaopin.pipechina.com.cn/recruit",
            "max_age_hours": 30,
            "require_complete_scan": True,
            "minimum_relevance": 0,
        },
    }


def _copy_capture(tmp_path: Path) -> Path:
    target = tmp_path / "captures" / "pipechina.json"
    target.parent.mkdir(parents=True)
    target.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    return target


def test_browser_capture_requires_complete_pagination_and_fresh_rows(tmp_path: Path) -> None:
    path = _copy_capture(tmp_path)
    payload = load_browser_capture(
        path,
        allowed_hosts=["zhaopin.pipechina.com.cn", "www.pipechina.com.cn"],
        max_age_hours=30,
        now=datetime(2026, 9, 25, 1, 0, tzinfo=timezone.utc),
    )
    assert payload["scan"]["pagination_complete"] is True
    assert payload["rows"][0]["field_evidence"]["专业范围"].startswith("地质")


def test_browser_capture_rejects_stale_manifest(tmp_path: Path) -> None:
    path = _copy_capture(tmp_path)
    with pytest.raises(BrowserCaptureError, match="stale"):
        load_browser_capture(
            path,
            allowed_hosts=["zhaopin.pipechina.com.cn", "www.pipechina.com.cn"],
            max_age_hours=1,
            now=datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc),
        )


def test_browser_capture_rejects_non_official_detail_url(tmp_path: Path) -> None:
    path = _copy_capture(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["rows"][0]["detail_url"] = "https://example.com/job/1"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(BrowserCaptureError, match="allowlisted"):
        load_browser_capture(
            path,
            allowed_hosts=["zhaopin.pipechina.com.cn", "www.pipechina.com.cn"],
            now=datetime(2026, 9, 25, 1, 0, tzinfo=timezone.utc),
        )


def test_rendered_row_parser_requires_explicit_fields_and_official_link() -> None:
    html = """
    <div class='row' data-key='job-1'>
      <span class='title'>地质工程师</span><span class='employer'>官方单位</span>
      <span class='degree'>硕士</span><span class='major'>地质工程</span>
      <span class='location'>北京</span><span class='deadline'>2026-10-20</span>
      <a class='detail' href='/job/1'>详情</a>
    </div>
    """
    rows = extract_rendered_rows(
        html,
        page_url="https://zhaopin.pipechina.com.cn/recruit",
        allowed_hosts={"zhaopin.pipechina.com.cn"},
        config={
            "row_selector": ".row",
            "id_attribute": "data-key",
            "field_selectors": {
                "title": ".title",
                "employer": ".employer",
                "degree": ".degree",
                "major": ".major",
                "location": ".location",
                "deadline": ".deadline",
            },
            "detail_selector": ".detail",
        },
    )
    assert rows[0]["external_id"] == "job-1"
    assert rows[0]["detail_url"].endswith("/job/1")
    assert rows[0]["field_evidence"]["专业范围"] == "地质工程"


def test_browser_source_converts_verified_rows_to_raw_postings(tmp_path: Path) -> None:
    _copy_capture(tmp_path)
    settings = replace(make_settings(tmp_path), data_dir=tmp_path)
    collector = OfficialSourceCollector(settings)
    postings = collector.collect(_source(tmp_path))
    assert len(postings) == 1
    assert postings[0].external_id == "pipechina-browser-001"
    assert postings[0].official_evidence_url.endswith("3004720.html")
    assert postings[0].field_evidence["evidence_scope"] == "official_browser_capture_row"


def test_browser_source_fails_closed_for_partial_capture(tmp_path: Path) -> None:
    path = _copy_capture(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["scan"]["pagination_complete"] = False
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    collector = OfficialSourceCollector(replace(make_settings(tmp_path), data_dir=tmp_path))
    with pytest.raises(Exception, match="incomplete"):
        collector.collect(_source(tmp_path))
