from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from job_hub.sources import OfficialSourceCollector

from conftest import make_settings


FIXTURE = Path(__file__).parent / "fixtures" / "domestic" / "pepris_2026_recruitment.html"
NOTICE_URL = (
    "http://pepris.sinopec.com/pepris/careers/rwzl/2026/6/"
    "I1519026538069098496.shtml"
)


@dataclass
class FakeResponse:
    text: str
    url: str


def pepris_source() -> dict[str, object]:
    return {
        "id": "sinopec-pepris-recruitment",
        "name": "中国石化石油勘探开发研究院2026年校园招聘公告",
        "publisher": "中国石化石油勘探开发研究院有限公司",
        "homepage_url": NOTICE_URL,
        "source_type": "official_table_rows",
        "category": "油气上游业主与研究机构",
        "source_tier": "A",
        "config": {
            "direct_notice_urls": [NOTICE_URL],
            "direct_only": True,
            "allowed_hosts": ["pepris.sinopec.com"],
            "title_selector": "h1, title",
            "content_selector": ".article-content",
            "table_selector": "table",
            "table_contexts": [
                {
                    "table_index": 1,
                    "employer": "中国石化石油勘探开发研究院有限公司",
                    "location": "北京",
                },
                {
                    "table_index": 2,
                    "employer": "中国石化石油勘探开发研究院有限公司",
                    "location": "无锡",
                },
            ],
            "row_include_patterns": [
                "油气|油田|石油地质|地球物理|测井|岩石物理|勘探|开发地质|油气藏|储气库|采收率|海洋石油|地热|沉积|储层|地面工程|非常规|地质"
            ],
            "row_exclude_patterns": ["人工智能|项目管理|软件开发|计算机"],
            "employer_hint": "中国石化石油勘探开发研究院有限公司",
            "published_date": "2025-10-11",
            "deadline_date": "2025-11-15",
            "require_page_recruitment_word": True,
            "require_recruitment_word": False,
            "require_major_match": True,
            "minimum_relevance": 0,
            "max_items": 40,
            "request_interval_seconds": 0,
            "allow_shared_source_url": True,
        },
    }


def test_official_table_rows_preserve_context_and_evidence(tmp_path) -> None:
    collector = OfficialSourceCollector(
        replace(make_settings(tmp_path), max_source_items=40)
    )
    document = FIXTURE.read_text(encoding="utf-8")
    collector._get = lambda url, source: FakeResponse(document, url)  # type: ignore[method-assign]

    postings = collector.collect(pepris_source())

    assert len(postings) == 22
    assert postings[0].title == "油气地质博士岗（一）"
    assert postings[0].location == "北京"
    assert postings[-1].title == "非常规地质博士岗（无锡）"
    assert postings[-1].location == "无锡"
    assert all(
        posting.employer == "中国石化石油勘探开发研究院有限公司"
        for posting in postings
    )
    assert all(posting.source_url == NOTICE_URL for posting in postings)
    assert all(posting.official_evidence_url == NOTICE_URL for posting in postings)
    assert all(posting.deadline_date == "2025-11-15" for posting in postings)
    assert all(
        posting.field_evidence
        and posting.field_evidence["evidence_scope"] == "official_html_table_row"
        and posting.field_evidence["岗位"] == posting.title
        for posting in postings
    )


def test_official_table_rows_exclude_non_geoscience_rows(tmp_path) -> None:
    collector = OfficialSourceCollector(
        replace(make_settings(tmp_path), max_source_items=40)
    )
    document = FIXTURE.read_text(encoding="utf-8")
    collector._get = lambda url, source: FakeResponse(document, url)  # type: ignore[method-assign]

    titles = {posting.title for posting in collector.collect(pepris_source())}

    assert "人工智能硕士岗" not in titles
    assert "项目管理硕士岗" not in titles
    assert "软件开发硕士岗（无锡）" not in titles
    # These rows are valid official geoscience evidence even though the title
    # does not contain one of the narrow canonical major labels.
    assert "非常规勘探博士岗" in titles
    assert "沉积储层及预测博士岗（无锡）" in titles


def test_accept_candidate_supports_per_call_policy_overrides(tmp_path) -> None:
    collector = OfficialSourceCollector(make_settings(tmp_path))
    source = pepris_source()

    assert collector._accept_candidate(
        "石勘院校园招聘",
        source,
        require_recruitment_word=False,
        require_major_match=False,
    )
    assert not collector._accept_candidate(
        "石勘院校园招聘",
        source,
        require_recruitment_word=False,
        require_major_match=True,
    )
