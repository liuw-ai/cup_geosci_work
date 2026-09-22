from __future__ import annotations

from dataclasses import dataclass

import pytest

from job_hub.sources import OfficialSourceCollector, SourceCollectionError

from conftest import make_settings


@dataclass
class FakeResponse:
    text: str
    url: str
    payload: object | None = None

    def json(self) -> object:
        if self.payload is None:
            raise ValueError("no JSON payload")
        return self.payload


def _source() -> dict[str, object]:
    return {
        "id": "cnooc-career",
        "name": "中国海油招聘平台",
        "publisher": "中国海洋石油集团有限公司",
        "homepage_url": "https://cnooc.zhaopin.com/",
        "source_type": "zhaopin_campus",
        "category": "油气上游业主与研究机构",
        "source_tier": "A",
        "config": {
            "listing_url": "https://cnooc.zhaopin.com/job/index.html",
            "company_id": "CZ258591510",
            "scene": "cam",
            "api_host": "https://fe.zhaopin.com",
            "api_path": "/grace/api/dsc/search-job-list",
            "api_allowed_hosts": ["fe.zhaopin.com"],
            "allowed_hosts": ["cnooc.zhaopin.com"],
            "require_major_match": True,
            "include_patterns": ["地质|地球物理|油气"],
            "exclude_patterns": ["财务|行政"],
            "request_interval_seconds": 0,
            "max_items": 10,
        },
    }


def test_zhaopin_adapter_accepts_only_explicit_empty_public_result(tmp_path, monkeypatch) -> None:
    collector = OfficialSourceCollector(make_settings(tmp_path))
    source = _source()
    landing_url = source["config"]["listing_url"]  # type: ignore[index]
    api_url = "https://fe.zhaopin.com/grace/api/dsc/search-job-list"

    def get(url, _source):
        assert url == landing_url
        return FakeResponse('<html><title>中国海油</title></html>', landing_url)

    def post(url, payload, *, headers=None):
        assert url == api_url
        assert payload["orgNumbers"] == ["CZ258591510"]
        assert payload["jobSource"] == 2
        assert headers and headers["Origin"] == "https://cnooc.zhaopin.com"
        return FakeResponse(
            "",
            api_url,
            {"code": 200, "data": {"jobList": [], "pageInfo": {"totalNum": 0}}},
        )

    monkeypatch.setattr(collector, "_get", get)
    monkeypatch.setattr(collector, "_post_json", post)

    assert collector.collect(source) == []


def test_zhaopin_adapter_never_turns_business_error_into_no_match(tmp_path, monkeypatch) -> None:
    collector = OfficialSourceCollector(make_settings(tmp_path))
    source = _source()
    landing_url = source["config"]["listing_url"]  # type: ignore[index]

    monkeypatch.setattr(
        collector,
        "_get",
        lambda _url, _source: FakeResponse("<html></html>", landing_url),
    )
    monkeypatch.setattr(
        collector,
        "_post_json",
        lambda *_args, **_kwargs: FakeResponse(
            "",
            "https://fe.zhaopin.com/grace/api/dsc/search-job-list",
            {
                "code": 500,
                "message": "接口转换失败",
                "data": {"stack": "backend error"},
            },
        ),
    )

    with pytest.raises(SourceCollectionError, match="business error code=500"):
        collector.collect(source)


def test_zhaopin_adapter_requires_official_job_url_and_major_evidence(
    tmp_path, monkeypatch
) -> None:
    collector = OfficialSourceCollector(make_settings(tmp_path))
    source = _source()
    landing_url = source["config"]["listing_url"]  # type: ignore[index]
    api_url = "https://fe.zhaopin.com/grace/api/dsc/search-job-list"
    rows = [
        {
            "job": {
                "jobNumber": "CC258591510J001",
                "title": "地球物理勘探岗",
                "jobCategories": ["地质技术"],
                "cityName": "天津",
                "detail": "负责地震资料解释，要求地质学、地球物理学相关专业。",
                "url": "https://cnooc.zhaopin.com/job/CC258591510J001",
            }
        },
        {
            "job": {
                "jobNumber": "CC258591510J002",
                "title": "财务管理岗",
                "cityName": "北京",
                "detail": "负责财务核算。",
                "url": "https://cnooc.zhaopin.com/job/CC258591510J002",
            }
        },
    ]

    monkeypatch.setattr(
        collector,
        "_get",
        lambda _url, _source: FakeResponse("<html></html>", landing_url),
    )
    monkeypatch.setattr(
        collector,
        "_post_json",
        lambda *_args, **_kwargs: FakeResponse(
            "",
            api_url,
            {"code": 200, "data": {"jobList": rows, "pageInfo": {"totalNum": 2}}},
        ),
    )

    postings = collector.collect(source)

    assert len(postings) == 1
    assert postings[0].title == "地球物理勘探岗"
    assert postings[0].source_url.endswith("CC258591510J001")
    assert postings[0].location == "天津"
    assert "地质学" in (postings[0].match_text or "")


def test_zhaopin_metadata_can_be_read_from_public_asset(tmp_path, monkeypatch) -> None:
    collector = OfficialSourceCollector(make_settings(tmp_path))
    source = _source()
    source["config"].pop("company_id")  # type: ignore[index]
    source["config"]["metadata_asset_limit"] = 2  # type: ignore[index]
    landing_url = source["config"]["listing_url"]  # type: ignore[index]
    asset_url = "https://cnooc.zhaopin.com/assets/app.js"

    def get(url, _source):
        if url == landing_url:
            return FakeResponse(
                '<script src="/assets/app.js"></script>', landing_url
            )
        assert url == asset_url
        return FakeResponse(
            'const globalData={companyId:"CZ123",scene:"cam",companyNumber:"123"}',
            asset_url,
        )

    monkeypatch.setattr(collector, "_get", get)
    metadata = collector._zhaopin_campaign_metadata(
        '<script src="/assets/app.js"></script>',
        source["config"],  # type: ignore[index]
        landing_url,
    )

    assert metadata["companyId"] == "CZ123"
    assert metadata["companyNumber"] == "123"


def test_zhaopin_missing_campaign_id_is_not_the_literal_none(tmp_path, monkeypatch) -> None:
    collector = OfficialSourceCollector(make_settings(tmp_path))
    source = _source()
    source["config"].pop("company_id")  # type: ignore[index]
    source["config"].pop("campaign_id", None)  # type: ignore[index]
    landing_url = source["config"]["listing_url"]  # type: ignore[index]

    monkeypatch.setattr(
        collector,
        "_get",
        lambda _url, _source: FakeResponse("<html><body>no id</body></html>", landing_url),
    )
    metadata = collector._zhaopin_campaign_metadata(
        "<html><body>no id</body></html>",
        source["config"],  # type: ignore[index]
        landing_url,
    )

    assert "companyId" not in metadata
    assert "xiaozhaoId" not in metadata
