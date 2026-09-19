from __future__ import annotations

import json
from pathlib import Path

from job_hub.locations import PROVINCES
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
