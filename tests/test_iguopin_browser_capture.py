from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import job_hub.iguopin_browser_worker as worker_module
from job_hub.browser_capture import BrowserCaptureError
from job_hub.contracts import ContractValidationError, validate_source_registry
from job_hub.employers import official_job_link
from job_hub.iguopin_browser_capture import (
    _concrete_iguopin_detail_url,
    _configured_major_filters,
    _configured_search_keywords,
    _general_verified_row,
    _rendered_card_detail_id,
    load_iguopin_browser_capture,
    write_iguopin_capture_failure,
)
from job_hub.iguopin_browser_worker import IguopinBrowserWorker
from job_hub.profiles import evaluate_student_publication
from job_hub.sources import OfficialSourceCollector

from conftest import make_settings


HOSTS = ["chinalco2027.iguopin.com", "www.iguopin.com", "iguopin.com"]


def _row() -> dict[str, object]:
    detail_url = "https://www.iguopin.com/job/detail?id=218518701773684917"
    return {
        "external_id": "chinalco-iguopin-218518701773684917",
        "title": "地质技术员",
        "employer": "中铝数智物联科技有限公司",
        "major": "地质学、资源勘查工程",
        "degree": "硕士",
        "location": "北京昌平",
        "headcount": "1",
        "deadline": "2026-10-31 23:59:59",
        "detail_url": detail_url,
        "evidence_url": detail_url,
        "description": "专业要求：地质学、资源勘查工程。",
        "field_evidence": {
            "岗位": "地质技术员",
            "专业范围": "地质学、资源勘查工程",
            "学历要求": "硕士",
            "工作地点": "北京昌平",
            "招聘人数": "1",
            "报名截止": "2026-10-31 23:59:59",
            "官方详情链接": detail_url,
        },
    }


def _payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "version": 1,
        "status": "success",
        "platform_url": "https://chinalco2027.iguopin.com/job",
        "captured_at": "2026-10-03T02:00:00Z",
        "scan": {
            "pages_scanned": 4,
            "pagination_complete": True,
            "rows_discovered": 1,
            "rows_exported": 1,
            "failed_rows": 0,
            "detail_discovered": 1,
            "detail_succeeded": 1,
            "detail_failed": 0,
            "filter_scans": [
                {
                    "filter": "工学 / 地质类",
                    "pages_scanned": 2,
                    "cards_seen": 1,
                    "pagination_complete": True,
                }
            ],
        },
        "rows": [_row()],
    }
    payload.update(overrides)
    return payload


def _source() -> dict[str, object]:
    return {
        "id": "chinalco-iguopin-browser",
        "name": "中国铝业集团国聘测试来源",
        "publisher": "中国铝业集团有限公司",
        "homepage_url": "https://chinalco2027.iguopin.com/job",
        "source_type": "iguopin_browser_rows",
        "category": "矿产资源与矿业",
        "source_tier": "A",
        "enabled": False,
        "config": {
            "browser_url": "https://chinalco2027.iguopin.com/job",
            "application_url": "https://chinalco2027.iguopin.com/job",
            "capture_path": "captures/chinalco.json",
            "allowed_hosts": HOSTS,
            "major_filters": [
                {"parent": "理学", "child": "地质学类"},
                {"parent": "工学", "child": "地质类"},
            ],
            "external_id_prefix": "chinalco-iguopin",
            "max_age_hours": 100_000,
            "require_complete_scan": True,
            "require_capture_manifest": False,
            "max_items": 2000,
        },
    }


def _general_source() -> dict[str, object]:
    source = _source()
    source.update(
        {
            "id": "iguopin-general-geoscience-browser",
            "name": "国聘主站地学岗位测试来源",
            "publisher": "国聘网",
            "homepage_url": "https://www.iguopin.com/job",
            "source_type": "iguopin_general_browser_rows",
            "category": "综合央国企与公共就业",
        }
    )
    source["config"] = {
        "browser_url": "https://www.iguopin.com/job",
        "application_url": "https://www.iguopin.com/job",
        "capture_path": "captures/chinalco.json",
        "allowed_hosts": ["www.iguopin.com", "iguopin.com"],
        "search_keywords": ["地质", "地球物理"],
        "card_identity_mode": "rendered_react_job_id",
        "external_id_prefix": "iguopin-general",
        "max_pages_per_keyword": 20,
        "max_age_hours": 100_000,
        "require_complete_scan": True,
        "require_capture_manifest": False,
        "max_items": 2_000,
    }
    return source


def _write_capture(tmp_path: Path, payload: dict[str, object]) -> Path:
    path = tmp_path / "captures" / "chinalco.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_general_capture_allows_audited_detail_rejections_without_publishing_them(
    tmp_path: Path,
) -> None:
    payload = _payload()
    payload["scan"] = {
        **payload["scan"],
        "rows_discovered": 2,
        "rows_exported": 1,
        "rejected_rows": 1,
        "rejected_records": [
            {
                "keyword": "地质",
                "page": 1,
                "row": 2,
                "detail_id": "217000000000000001",
                "detail_url": "https://www.iguopin.com/job/detail?id=217000000000000001",
                "reason": "CMGB detail is missing required fields: location",
            }
        ],
        "detail_discovered": 2,
        "detail_succeeded": 1,
        "detail_failed": 0,
    }
    loaded = load_iguopin_browser_capture(
        _write_capture(tmp_path, payload),
        allowed_hosts=HOSTS,
        max_age_hours=100_000,
        require_complete_scan=True,
    )

    assert loaded["status"] == "success"
    assert loaded["scan"]["rejected_rows"] == 1
    assert len(loaded["rows"]) == 1


def test_iguopin_source_contract_requires_bounded_filters_and_id_prefix() -> None:
    source = _source()
    validated = validate_source_registry([source])[0]
    assert validated["source_type"] == "iguopin_browser_rows"
    assert validated["config"]["external_id_prefix"] == "chinalco-iguopin"

    missing_filters = copy.deepcopy(source)
    del missing_filters["config"]["major_filters"]
    with pytest.raises(ContractValidationError, match="major_filters"):
        validate_source_registry([missing_filters])

    duplicate_filters = copy.deepcopy(source)
    duplicate_filters["config"]["major_filters"].append(
        {"parent": "理学", "child": "地质学类"}
    )
    with pytest.raises(ContractValidationError, match="duplicate"):
        validate_source_registry([duplicate_filters])


def test_iguopin_general_contract_requires_bounded_keywords_identity_and_page_cap() -> None:
    source = _general_source()
    validated = validate_source_registry([source])[0]
    assert validated["source_type"] == "iguopin_general_browser_rows"
    assert validated["config"]["search_keywords"] == ["地质", "地球物理"]
    assert validated["config"]["max_pages_per_keyword"] == 20

    missing_keywords = copy.deepcopy(source)
    del missing_keywords["config"]["search_keywords"]
    with pytest.raises(ContractValidationError, match="search_keywords"):
        validate_source_registry([missing_keywords])

    duplicate_keywords = copy.deepcopy(source)
    duplicate_keywords["config"]["search_keywords"].append(" 地 质 ")
    with pytest.raises(ContractValidationError, match="duplicate"):
        validate_source_registry([duplicate_keywords])

    wrong_identity = copy.deepcopy(source)
    wrong_identity["config"]["card_identity_mode"] = "unknown"
    with pytest.raises(ContractValidationError, match="card_identity_mode"):
        validate_source_registry([wrong_identity])

    missing_page_cap = copy.deepcopy(source)
    del missing_page_cap["config"]["max_pages_per_keyword"]
    with pytest.raises(ContractValidationError, match="max_pages_per_keyword"):
        validate_source_registry([missing_page_cap])


def test_iguopin_capture_requires_complete_rows_and_concrete_detail_page(tmp_path: Path) -> None:
    path = _write_capture(tmp_path, _payload())
    loaded = load_iguopin_browser_capture(
        path,
        allowed_hosts=HOSTS,
        max_age_hours=24,
        now=datetime(2026, 10, 3, 10, tzinfo=timezone.utc),
    )
    assert loaded["scan"]["detail_succeeded"] == 1
    assert loaded["rows"][0]["detail_url"].endswith("218518701773684917")

    partial = _payload(status="partial")
    partial["scan"] = {**partial["scan"], "failed_rows": 1, "detail_failed": 1}
    _write_capture(tmp_path, partial)
    with pytest.raises(ValueError, match="not publishable"):
        load_iguopin_browser_capture(path, allowed_hosts=HOSTS)

    with pytest.raises(BrowserCaptureError, match="岗位详情页"):
        _concrete_iguopin_detail_url("https://www.iguopin.com/job", set(HOSTS))


def test_iguopin_failure_does_not_replace_last_successful_capture(tmp_path: Path) -> None:
    path = _write_capture(tmp_path, _payload())
    original = path.read_text(encoding="utf-8")

    archived = write_iguopin_capture_failure(
        output=path,
        platform_url="https://chinalco2027.iguopin.com/job",
        status="parse_failed",
        reason="official detail layout changed",
        source_id="chinalco-iguopin-browser",
        adapter_version="iguopin-browser-v1",
    )

    assert path.read_text(encoding="utf-8") == original
    assert archived["status"] == "parse_failed"
    assert archived["capture_evidence"]["source_id"] == "chinalco-iguopin-browser"


def test_iguopin_rows_keep_their_own_evidence_scope_and_detail_link(tmp_path: Path) -> None:
    _write_capture(tmp_path, _payload())
    posting = OfficialSourceCollector(make_settings(tmp_path)).collect(_source())[0]

    assert posting.field_evidence["evidence_scope"] == "official_iguopin_browser_detail"
    detail_url, label = official_job_link(
        {
            "source_url": posting.source_url,
            "official_evidence_url": posting.official_evidence_url,
            "field_evidence": posting.field_evidence,
        }
    )
    assert detail_url == posting.source_url
    assert label == "打开官方岗位详情"

    decision = evaluate_student_publication(
        {
            "title": posting.title,
            "employer": posting.employer,
            "location": posting.location,
            "category": "矿产资源与矿业",
            "field_evidence": posting.field_evidence,
            "degree_levels": ["硕士"],
        }
    )
    assert decision.status == "student_eligible"


def test_iguopin_worker_never_captures_a_disabled_candidate(monkeypatch, tmp_path: Path) -> None:
    source = _source()

    class FakeDatabase:
        def get_source(self, source_id: str):
            assert source_id == "chinalco-iguopin-browser"
            return source

    worker = IguopinBrowserWorker.__new__(IguopinBrowserWorker)
    worker.settings = SimpleNamespace(data_dir=tmp_path)
    worker.database = FakeDatabase()
    worker.source_id = "chinalco-iguopin-browser"
    worker.service_name = "chinalco-iguopin-browser"
    heartbeats: list[tuple[str, str]] = []
    worker._heartbeat = lambda status, detail="": heartbeats.append((status, detail))
    monkeypatch.setattr(
        worker_module,
        "run_iguopin_browser_capture",
        lambda **_kwargs: pytest.fail("disabled source must not capture"),
    )

    worker._run_once()

    assert heartbeats[-1][0] == "idle"


def test_iguopin_worker_rejects_a_misconfigured_source_type(monkeypatch, tmp_path: Path) -> None:
    source = _source()
    source["enabled"] = True
    source["source_type"] = "cmgb_browser_rows"

    class FakeDatabase:
        def get_source(self, source_id: str):
            assert source_id == "chinalco-iguopin-browser"
            return source

    worker = IguopinBrowserWorker.__new__(IguopinBrowserWorker)
    worker.settings = SimpleNamespace(data_dir=tmp_path)
    worker.database = FakeDatabase()
    worker.source_id = "chinalco-iguopin-browser"
    worker.service_name = "chinalco-iguopin-browser"
    heartbeats: list[tuple[str, str]] = []
    worker._heartbeat = lambda status, detail="": heartbeats.append((status, detail))
    monkeypatch.setattr(
        worker_module,
        "run_iguopin_browser_capture",
        lambda **_kwargs: pytest.fail("wrong source type must not capture"),
    )

    worker._run_once()

    assert heartbeats[-1][0] == "degraded"
    assert "incompatible type" in heartbeats[-1][1]


def test_iguopin_filter_parser_rejects_duplicate_visible_choices() -> None:
    with pytest.raises(BrowserCaptureError, match="duplicate"):
        _configured_major_filters(
            {
                "major_filters": [
                    {"parent": "工学", "child": "地质类"},
                    {"parent": "工学", "child": "地质类"},
                ]
            }
        )


def test_iguopin_general_keyword_and_rendered_identity_gates_reject_invalid_cards() -> None:
    assert _configured_search_keywords(
        {"search_keywords": ["地质", "地球物理"]}
    ) == ["地质", "地球物理"]
    with pytest.raises(BrowserCaptureError, match="duplicate"):
        _configured_search_keywords({"search_keywords": ["地质", " 地 质 "]})

    class FakeCard:
        def __init__(self, value: str) -> None:
            self.value = value

        def evaluate(self, _script: str) -> str:
            return self.value

    assert _rendered_card_detail_id(FakeCard("217600756180583891")) == "217600756180583891"
    with pytest.raises(BrowserCaptureError, match="岗位编号"):
        _rendered_card_detail_id(FakeCard(""))


def test_iguopin_general_row_requires_card_detail_agreement_and_deduplicates_ids() -> None:
    detail_url = "https://www.iguopin.com/job/detail?id=217600756180583891"
    detail = {
        "title": "地质工程",
        "employer": "国核铀业发展有限责任公司",
        "major": "地质学类、地质资源与地质工程类",
        "degree": "硕士",
        "location": "北京-海淀区",
        "headcount": "1",
        "deadline": "2026-12-31",
        "detail_url": detail_url,
        "evidence_url": detail_url,
        "description": "专业要求：地质学类。",
        "field_evidence": {"官方详情链接": detail_url},
    }
    seen: set[str] = set()
    row = _general_verified_row(
        detail_id="217600756180583891",
        seen_detail_ids=seen,
        card_title="地质工程",
        card_employer="国核铀业发展有限责任公司",
        detail=detail,
        keyword="地质",
        external_id_prefix="iguopin-general",
    )
    assert row is not None
    assert row["external_id"] == "iguopin-general-217600756180583891"
    assert _general_verified_row(
        detail_id="217600756180583891",
        seen_detail_ids=seen,
        card_title="地质工程",
        card_employer="国核铀业发展有限责任公司",
        detail=detail,
        keyword="地球物理",
        external_id_prefix="iguopin-general",
    ) is None

    with pytest.raises(BrowserCaptureError, match="岗位名称"):
        _general_verified_row(
            detail_id="another-217600756180583891",
            seen_detail_ids=set(),
            card_title="地球物理工程",
            card_employer="国核铀业发展有限责任公司",
            detail=detail,
            keyword="地球物理",
            external_id_prefix="iguopin-general",
        )

    with pytest.raises(BrowserCaptureError, match="招聘单位"):
        _general_verified_row(
            detail_id="third-217600756180583891",
            seen_detail_ids=set(),
            card_title="地质工程",
            card_employer="另一家单位",
            detail=detail,
            keyword="地质",
            external_id_prefix="iguopin-general",
        )


def test_iguopin_general_worker_selects_only_the_main_board_capture(monkeypatch, tmp_path: Path) -> None:
    source = _general_source()
    source["enabled"] = True

    class FakeDatabase:
        def get_source(self, source_id: str):
            assert source_id == "iguopin-general-geoscience-browser"
            return source

    worker = IguopinBrowserWorker.__new__(IguopinBrowserWorker)
    worker.settings = SimpleNamespace(data_dir=tmp_path)
    worker.database = FakeDatabase()
    worker.source_id = "iguopin-general-geoscience-browser"
    worker.service_name = worker.source_id
    heartbeats: list[tuple[str, str]] = []
    worker._heartbeat = lambda status, detail="": heartbeats.append((status, detail))
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        worker_module,
        "run_iguopin_general_browser_capture",
        lambda **kwargs: calls.append(kwargs) or {"status": "success", "scan": {}},
    )
    monkeypatch.setattr(
        worker_module,
        "run_iguopin_browser_capture",
        lambda **_kwargs: pytest.fail("employer portal capture must not run for main board"),
    )

    worker._run_once()

    assert len(calls) == 1
    assert calls[0]["url"] == "https://www.iguopin.com/job"
    assert heartbeats[-1][0] == "running"


def test_iguopin_worker_syncs_a_successful_capture_immediately(monkeypatch, tmp_path: Path) -> None:
    source = _source()
    source["enabled"] = True

    class FakeDatabase:
        def get_source(self, source_id: str):
            assert source_id == "chinalco-iguopin-browser"
            return source

    class FakePipeline:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def sync_source_manual(self, received_source):
            self.calls.append(received_source)
            return SimpleNamespace(status="finished", error=None)

    worker = IguopinBrowserWorker.__new__(IguopinBrowserWorker)
    worker.settings = SimpleNamespace(data_dir=tmp_path)
    worker.database = FakeDatabase()
    worker.pipeline = FakePipeline()
    worker.source_id = "chinalco-iguopin-browser"
    worker.service_name = worker.source_id
    heartbeats: list[tuple[str, str]] = []
    worker._heartbeat = lambda status, detail="": heartbeats.append((status, detail))
    monkeypatch.setattr(
        worker_module,
        "run_iguopin_browser_capture",
        lambda **_kwargs: {"status": "success", "scan": {"rows_exported": 35}},
    )

    worker._run_once()

    assert worker.pipeline.calls == [source]
    assert heartbeats[-1][0] == "running"


def test_chinalco_production_registry_preserves_the_isolated_capture_gate() -> None:
    registry = json.loads(
        (Path(__file__).parent.parent / "data" / "sources.json").read_text(
            encoding="utf-8"
        )
    )
    source = next(item for item in registry if item["id"] == "chinalco-iguopin-browser")

    assert source["enabled"] is True
    assert source["source_type"] == "iguopin_browser_rows"
    assert source["config"]["capture_path"] == "captures/chinalco-iguopin-browser.json"
    assert source["config"]["require_complete_scan"] is True
    assert source["config"]["require_capture_manifest"] is True
    assert source["config"]["reconcile_missing_external_ids"] is True
    assert "35/35" in source["config"]["note"]


def test_iguopin_general_registry_stays_disabled_until_two_complete_server_captures() -> None:
    registry = json.loads(
        (Path(__file__).parent.parent / "data" / "sources.json").read_text(
            encoding="utf-8"
        )
    )
    source = next(
        item for item in registry if item["id"] == "iguopin-general-geoscience-browser"
    )

    assert source["enabled"] is False
    assert source["source_type"] == "iguopin_general_browser_rows"
    assert source["config"]["card_identity_mode"] == "rendered_react_job_id"
    assert source["config"]["max_pages_per_keyword"] == 20
    assert source["config"]["require_complete_scan"] is True
    assert source["config"]["require_capture_manifest"] is True
