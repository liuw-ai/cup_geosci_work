from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from job_hub.sources import OfficialSourceCollector, SourceCollectionError

from conftest import make_settings


PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class FakeResponse:
    text: str = ""
    url: str = ""
    content: bytes = b""


def _sources() -> list[dict[str, object]]:
    return json.loads(
        (PROJECT_ROOT / "data" / "sources.json").read_text(encoding="utf-8")
    )


def _source(source_id: str) -> dict[str, object]:
    return next(item for item in _sources() if item["id"] == source_id)


def test_cmgb_second_institute_splits_direct_notice_into_verified_roles(
    tmp_path, monkeypatch
) -> None:
    collector = OfficialSourceCollector(make_settings(tmp_path))
    source = _source("cmgb-second-geology-recruitment")
    fixture = (
        PROJECT_ROOT / "tests" / "fixtures" / "domestic" / "cmgb_second_recruitment.html"
    ).read_text(encoding="utf-8")
    monkeypatch.setattr(
        collector,
        "_get",
        lambda url, _source: FakeResponse(text=fixture, url=url),
    )
    monkeypatch.setattr(collector, "_wait", lambda _source: None)

    postings = collector.collect(source)

    assert len(postings) == 1
    posting = postings[0]
    assert posting.title == "地质专业技术人员1名"
    assert posting.published_date == "2026-09-17"
    assert posting.deadline_date == "2026-09-27"
    assert posting.employer == "中国冶金地质总局第二地质勘查院"
    assert posting.official_evidence_url == posting.source_url
    assert "地质相关专业本科及以上学历" in posting.text
    assert "环境工程专业技术人员" not in posting.title


def test_cmgb_inner_mongolia_xlsx_publishes_only_hash_locked_reviewed_rows(
    tmp_path, monkeypatch
) -> None:
    collector = OfficialSourceCollector(make_settings(tmp_path))
    source = _source("cmgb-inner-mongolia-geology-recruitment")
    content = (
        PROJECT_ROOT
        / "tests"
        / "fixtures"
        / "domestic"
        / "cmgb_inner_mongolia_positions.xlsx"
    ).read_bytes()
    monkeypatch.setattr(
        collector,
        "_get",
        lambda url, _source: FakeResponse(url=url, content=content),
    )

    postings = collector.collect(source)

    assert [posting.title for posting in postings] == [
        "项目经理",
        "技术总工",
        "地质、水文、工程、环境地质技术人员",
    ]
    assert all(posting.official_evidence_url.endswith(".xlsx") for posting in postings)
    assert all("内蒙古地质勘查院" in posting.employer for posting in postings)
    assert all(posting.published_date == "2026-08-04" for posting in postings)
    assert all(posting.deadline_date is None for posting in postings)


def test_cmgb_inner_mongolia_xlsx_fails_closed_when_official_file_changes(
    tmp_path, monkeypatch
) -> None:
    collector = OfficialSourceCollector(make_settings(tmp_path))
    source = _source("cmgb-inner-mongolia-geology-recruitment")
    monkeypatch.setattr(
        collector,
        "_get",
        lambda url, _source: FakeResponse(url=url, content=b"changed official file"),
    )

    with pytest.raises(SourceCollectionError, match="hash changed"):
        collector.collect(source)
