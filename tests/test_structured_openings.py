from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from job_hub.contracts import ContractValidationError, validate_source_registry
from job_hub.sources import OfficialSourceCollector, SourceCollectionError

from conftest import make_settings


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BGP_URL = "https://www.bgp.com.cn/bgpen/Recruitment/first_common2023hr.shtml"


@dataclass
class FakeResponse:
    text: str
    url: str


def _bgp_source() -> dict[str, object]:
    records = json.loads(
        (PROJECT_ROOT / "data" / "sources.json").read_text(encoding="utf-8")
    )
    return next(item for item in records if item["id"] == "cnpc-bgp-recruitment")


def test_bgp_structured_page_preserves_official_fields_and_evidence(
    tmp_path, monkeypatch
) -> None:
    collector = OfficialSourceCollector(make_settings(tmp_path))
    source = _bgp_source()
    fixture = (
        PROJECT_ROOT / "tests" / "fixtures" / "national_energy" / "bgp_recruitment_2026.html"
    ).read_text(encoding="utf-8")

    monkeypatch.setattr(
        collector,
        "_get",
        lambda url, _source: FakeResponse(text=fixture, url=url),
    )

    postings = collector.collect(source)

    assert len(postings) == 1
    posting = postings[0]
    assert posting.title == "Seismic Data Processing Geophysicist (Experienced)"
    assert posting.employer == "中国石油集团东方地球物理勘探有限责任公司"
    assert posting.location == "Saudi Aramco GDAD Office, Saudi Arabia"
    assert posting.published_date == "2026-06-27"
    assert posting.deadline_date is None
    assert posting.source_url == BGP_URL
    assert posting.official_evidence_url == BGP_URL
    assert posting.application_url is None
    assert "Geophysics" in (posting.match_text or "")
    assert "Bachelor's degree" in posting.text
    assert posting.external_id and "#opening-1-" in posting.external_id


def test_structured_page_rejects_title_content_mismatch(tmp_path, monkeypatch) -> None:
    collector = OfficialSourceCollector(make_settings(tmp_path))
    source = {
        "id": "structured-test",
        "name": "结构化测试来源",
        "publisher": "测试单位",
        "homepage_url": BGP_URL,
        "source_type": "structured_opening_page",
        "category": "油气工程技术服务",
        "source_tier": "A",
        "config": {
            "allowed_hosts": ["www.bgp.com.cn"],
            "opening_title_selector": "h3",
            "opening_title_pattern": "^Job Opening",
            "opening_content_selector": "td.job-content",
            "opening_title_field_label": "Job Title",
            "opening_location_field_label": "Work Location",
            "opening_date_field_label": "Date",
            "require_recruitment_word": True,
            "require_major_match": True,
            "request_interval_seconds": 0,
        },
    }
    document = (
        "<h3>Job Opening: Geophysicist</h3>"
        "<h3>Job Opening: Geologist</h3>"
        "<div class='job-content'>Job Title<br>Geophysicist<br>"
        "Work Location<br>Beijing<br>Geophysics</div>"
    )
    monkeypatch.setattr(
        collector,
        "_get",
        lambda url, _source: FakeResponse(text=document, url=url),
    )

    with pytest.raises(SourceCollectionError, match="count mismatch"):
        collector.collect(source)


def test_structured_source_contract_requires_field_selectors() -> None:
    source = {
        "id": "structured-invalid",
        "name": "无效结构化来源",
        "publisher": "测试单位",
        "homepage_url": BGP_URL,
        "source_type": "structured_opening_page",
        "category": "油气工程技术服务",
        "source_tier": "A",
        "config": {"allowed_hosts": ["www.bgp.com.cn"]},
    }

    with pytest.raises(ContractValidationError, match="opening_title_selector"):
        validate_source_registry([source])


def test_official_role_split_notice_creates_one_record_per_matching_role(
    tmp_path, monkeypatch
) -> None:
    collector = OfficialSourceCollector(make_settings(tmp_path))
    source = next(
        item
        for item in json.loads(
            (PROJECT_ROOT / "data" / "sources.json").read_text(encoding="utf-8")
        )
        if item["id"] == "cmgb-geoexp-recruitment"
    )
    fixture = (
        PROJECT_ROOT / "tests" / "fixtures" / "domestic" / "geoexp_recruitment.html"
    ).read_text(encoding="utf-8")
    listing_url = source["config"]["listing_urls"][0]
    listing_calls = 0

    def get(url: str, _source: dict[str, object]) -> FakeResponse:
        nonlocal listing_calls
        if url == listing_url:
            listing_calls += 1
            if listing_calls == 1:
                return FakeResponse(
                    '<a href="https://www.geoexp.cn/contact/view_272.html">人才招聘</a>',
                    listing_url,
                )
        return FakeResponse(fixture, "https://www.geoexp.cn/contact/view_272.html")

    monkeypatch.setattr(collector, "_get", get)
    monkeypatch.setattr(collector, "_wait", lambda _source: None)

    postings = collector.collect(source)

    assert len(postings) == 5
    assert [posting.title for posting in postings] == [
        "工程技术员（物探偏航重磁、航电方向、地震专业、矿床地质专业、地质大数据专业）",
        "工程技术员（遥感地质相关专业）",
        "工程技术员（测绘工程相关专业）",
        "工程技术员（测绘工程、地理信息相关专业）",
        "工程技术员（物探相关专业）",
    ]
    assert all(posting.official_evidence_url == postings[0].source_url for posting in postings)
    assert all("市场专员" not in posting.title for posting in postings)
    assert all("本科" in posting.text or "研究生" in posting.text for posting in postings)
    assert all("资源勘查分公司" in posting.employer or "城市治理分公司" in posting.employer for posting in postings)
    assert postings[-1].employer == "中国冶金地质总局地球物理勘查院 - 城市治理分公司"
    assert len({posting.external_id for posting in postings}) == 5


def test_html_notice_body_hiring_phrase_does_not_hide_a_real_vacancy(
    tmp_path, monkeypatch
) -> None:
    collector = OfficialSourceCollector(make_settings(tmp_path))
    source = {
        "id": "html-body-phrase",
        "name": "正文短语回归来源",
        "publisher": "官方地质单位",
        "homepage_url": "https://official.example/jobs/",
        "source_type": "html_notice",
        "category": "自然资源、地调与地勘",
        "source_tier": "A",
        "config": {
            "listing_urls": ["https://official.example/jobs/"],
            "allowed_hosts": ["official.example"],
            "listing_selector": "a",
            "detail_path_patterns": ["/jobs/\\d+"],
            "title_selector": "h1",
            "content_selector": "article",
            "require_recruitment_word": True,
            "require_major_match": True,
            "exclude_patterns": ["拟录用|采购"],
            "minimum_relevance": 0,
            "max_items": 4,
            "request_interval_seconds": 0,
        },
    }
    listing = '<a href="https://official.example/jobs/1">地质工程师公开招聘公告</a>'
    detail = (
        "<h1>地质工程师公开招聘公告</h1>"
        "<article>地质工程专业，硕士及以上。招聘流程完成后将公示拟录用人员。</article>"
    )

    def get(url: str, _source: dict[str, object]) -> FakeResponse:
        return FakeResponse(listing if url.endswith("/jobs/") else detail, url)

    monkeypatch.setattr(collector, "_get", get)
    postings = collector.collect(source)

    assert len(postings) == 1
    assert postings[0].title == "地质工程师公开招聘公告"
