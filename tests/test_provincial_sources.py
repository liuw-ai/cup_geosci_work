from __future__ import annotations

import json
from pathlib import Path

from job_hub.locations import PROVINCES
from job_hub.government_revalidation import _availability_match
from job_hub.sources import load_source_registries


def test_provincial_source_matrix_covers_all_mainland_provinces() -> None:
    path = Path(__file__).resolve().parent.parent / "data" / "provincial_sources.json"
    sources = json.loads(path.read_text(encoding="utf-8"))

    assert {item["config"]["province"] for item in sources} == set(PROVINCES)
    assert all(item["config"]["fallback_source_ids"] for item in sources)
    assert all(item["config"]["target_active_sources"] == 3 for item in sources)
    validated = {
        item["id"]
        for item in sources
        if item["config"]["automation_status"] == "validated_html_notice"
    }
    assert validated == {
        "beijing-natural-resources",
        "tianjin-natural-resources",
        "shanghai-natural-resources",
        "jiangsu-natural-resources",
        "zhejiang-natural-resources",
        "anhui-natural-resources",
        "fujian-natural-resources",
        "hubei-natural-resources",
        "hebei-geology-bureau",
        "shandong-geology-bureau",
        "henan-geology-bureau",
        "hunan-geology-institute",
        "ningxia-geology-bureau",
        "anhui-geology-bureau",
    }
    assert all(
        item["enabled"] and item["source_type"] == "html_notice"
        for item in sources
        if item["id"] in validated
    )
    attachment_feeds = {
        item["id"]
        for item in sources
        if item["config"]["automation_status"] == "attachment_discovery_only"
    }
    assert attachment_feeds == {"sichuan-geology-bureau"}
    assert all(
        item["enabled"]
        and item["source_type"] == "attachment_discovery_feed"
        and item["config"]["attachment_discovery_enabled"]
        for item in sources
        if item["id"] in attachment_feeds
    )


def test_multiple_registries_reject_duplicate_source_ids(tmp_path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    item = {
        "id": "duplicate",
        "name": "测试来源",
        "publisher": "测试单位",
        "homepage_url": "https://example.edu.cn/",
        "source_type": "landing_page",
        "category": "自然资源、地调与地勘",
        "source_tier": "A",
    }
    first.write_text(json.dumps([item], ensure_ascii=False), encoding="utf-8")
    second.write_text(json.dumps([item], ensure_ascii=False), encoding="utf-8")

    try:
        load_source_registries([str(first), str(second)])
    except Exception as error:
        assert "Duplicate source id" in str(error)
    else:  # pragma: no cover - assertion form makes the failure unambiguous
        raise AssertionError("duplicate source id was accepted")


def test_sichuan_recruitment_chain_allows_official_exam_host() -> None:
    path = Path(__file__).resolve().parent.parent / "data" / "provincial_sources.json"
    source = next(
        item
        for item in json.loads(path.read_text(encoding="utf-8"))
        if item["id"] == "sichuan-geology-bureau"
    )
    config = source["config"]

    assert "dkj.sc.gov.cn" in config["allowed_hosts"]
    assert "www.scpta.com.cn" in config["allowed_hosts"]
    assert "www.scpta.com.cn" in config["attachment_allowed_hosts"]
    assert any(
        "www.scpta.com.cn/front/News/info/" in url
        for url in config["direct_notice_urls"]
    )
    assert any("/front/News/info/" in pattern for pattern in config["detail_path_patterns"])


def test_ningxia_current_availability_status_is_source_scoped() -> None:
    path = Path(__file__).resolve().parent.parent / "data" / "provincial_sources.json"
    sources = {
        item["id"]: item
        for item in json.loads(path.read_text(encoding="utf-8"))
    }

    ningxia_config = sources["ningxia-geology-bureau"]["config"]
    anhui_config = sources["anhui-geology-bureau"]["config"]

    assert ningxia_config["current_availability_url"].startswith(
        "http://www.nxdizhiju.cn/"
    )
    assert "www.nxdizhiju.cn" in ningxia_config["allowed_hosts"]
    assert ningxia_config["availability_open_patterns"]
    assert ningxia_config["availability_closed_patterns"]
    assert "current_availability_url" not in anhui_config
    assert "availability_open_patterns" not in anhui_config
    assert "availability_closed_patterns" not in anhui_config


def test_hunan_current_availability_uses_official_recruitment_notice() -> None:
    path = Path(__file__).resolve().parent.parent / "data" / "provincial_sources.json"
    sources = {
        item["id"]: item
        for item in json.loads(path.read_text(encoding="utf-8"))
    }

    config = sources["hunan-geology-institute"]["config"]
    assert config["current_availability_url"].startswith(
        "http://rst.hunan.gov.cn/rst/xxgk/zpzl/sydwzp/202603/"
    )
    page_text = "报名时间：自公告发布之日至2026年12月31日。随到随招，招满即止。"
    assert _availability_match(config, page_text)
    assert not _availability_match(config, page_text + "招聘工作结束")


def test_gansu_current_availability_ignores_non_terminal_hiring_language() -> None:
    path = Path(__file__).resolve().parent.parent / "data" / "provincial_sources.json"
    source = next(
        item
        for item in json.loads(path.read_text(encoding="utf-8"))
        if item["id"] == "gansu-geology-bureau"
    )
    config = source["config"]
    page_text = (
        "报名时间：自本公告发布之日起至2026年12月31日18:00前，"
        "根据计划名额招满即止。按考核结果确定拟聘人员并进行公示。"
    )

    assert _availability_match(config, page_text)
    assert not _availability_match(
        config,
        page_text + "拟聘人员名单已发布，招聘工作结束。",
    )
