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
