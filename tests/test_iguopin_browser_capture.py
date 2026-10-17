from __future__ import annotations

import copy
import json
import signal
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import job_hub.iguopin_browser_worker as worker_module
from job_hub.browser_capture import BrowserCaptureError
from job_hub.cmgb_browser_capture import CmgbBrowserCaptureError, persist_cmgb_browser_capture
from job_hub.contracts import ContractValidationError, validate_source_registry
from job_hub.employers import official_job_link
from job_hub.iguopin_browser_capture import (
    _concrete_iguopin_detail_url,
    _configured_major_filters,
    _keyword_batches,
    _configured_search_keywords,
    _complete_general_detail_identity,
    _configured_known_detail_ids,
    detail_ids_from_capture_payload,
    _general_verified_row,
    _wait_for_rendered_detail,
    _rendered_card_detail_id,
    build_iguopin_rejection_repair_payload,
    load_iguopin_browser_capture,
    merge_iguopin_general_batch_payloads,
    write_iguopin_capture_failure,
    promote_deterministic_rejections,
)
from job_hub.iguopin_browser_worker import IguopinBrowserWorker, _capture_deadline
from job_hub.profiles import evaluate_student_publication
from job_hub.sources import OfficialSourceCollector

from conftest import make_settings


HOSTS = ["chinalco2027.iguopin.com", "www.iguopin.com", "iguopin.com"]


def _duplicate_only_batch() -> dict[str, object]:
    return {
        "version": 1,
        "status": "success",
        "platform_url": "https://www.iguopin.com/job",
        "captured_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "scan": {
            "pages_scanned": 2,
            "pagination_complete": True,
            "rows_discovered": 0,
            "rows_exported": 0,
            "failed_rows": 0,
            "detail_discovered": 0,
            "detail_succeeded": 0,
            "detail_failed": 0,
            "rejected_rows": 0,
            "cards_seen_before_deduplication": 25,
            "duplicate_cards_skipped": 25,
            "prevalidated_detail_count": 100,
            "keyword_scans": [{
                "keyword": "油气地质",
                "pages_scanned": 2,
                "cards_seen": 25,
                "pagination_complete": True,
            }],
            "failure_records": [],
            "rejected_records": [],
        },
        "rows": [],
    }


def test_iguopin_zero_new_row_batch_requires_complete_duplicate_evidence(tmp_path: Path) -> None:
    path = tmp_path / "batch-16.json"
    payload = _duplicate_only_batch()
    with pytest.raises(CmgbBrowserCaptureError, match="non-empty"):
        persist_cmgb_browser_capture(output=path, payload=payload)
    persist_cmgb_browser_capture(
        output=path, payload=payload, allow_empty_duplicate_batch=True
    )
    with pytest.raises(CmgbBrowserCaptureError, match="non-empty"):
        load_iguopin_browser_capture(path, allowed_hosts=HOSTS)
    loaded = load_iguopin_browser_capture(
        path,
        allowed_hosts=HOSTS,
        require_capture_manifest=True,
        allow_empty_duplicate_batch=True,
    )
    assert loaded["rows"] == []
    assert loaded["scan"]["duplicate_cards_skipped"] == 25
    with pytest.raises(CmgbBrowserCaptureError, match="non-empty"):
        persist_cmgb_browser_capture(
            output=tmp_path / "all-empty-merged.json",
            payload=merge_iguopin_general_batch_payloads(
                [loaded],
                source_id="iguopin-general-geoscience-browser",
                adapter_version="test-v1",
            ),
            source_id="iguopin-general-geoscience-browser",
            adapter_version="test-v1",
        )


@pytest.mark.parametrize("change", [
    {"duplicate_cards_skipped": 24},
    {"prevalidated_detail_count": 0},
    {"pagination_complete": False},
    {"failed_rows": 1},
    {"cards_seen_before_deduplication": 0, "duplicate_cards_skipped": 0},
])
def test_iguopin_zero_row_batch_rejects_incomplete_duplicate_proof(
    tmp_path: Path, change: dict[str, object]
) -> None:
    payload = _duplicate_only_batch()
    payload["scan"].update(change)
    with pytest.raises(CmgbBrowserCaptureError):
        persist_cmgb_browser_capture(
            output=tmp_path / "batch.json",
            payload=payload,
            allow_empty_duplicate_batch=True,
        )


def test_iguopin_capture_deadline_interrupts_a_stalled_capture() -> None:
    if not hasattr(signal, "SIGALRM"):
        pytest.skip("capture deadline uses POSIX SIGALRM")
    with pytest.raises(TimeoutError, match="exceeded 1s deadline"):
        with _capture_deadline(1):
            time.sleep(2)


def test_iguopin_capture_deadline_zero_disables_the_alarm() -> None:
    with _capture_deadline(0):
        time.sleep(0.01)


def test_general_detail_wait_returns_as_soon_as_evidence_is_rendered() -> None:
    class _Body:
        def __init__(self, page: "_Page") -> None:
            self.page = page

        def inner_text(self) -> str:
            return self.page.body

    class _Locator:
        def __init__(self, page: "_Page") -> None:
            self.page = page

        def first(self) -> "_Locator":
            return self

        def inner_text(self) -> str:
            return self.page.body

    class _Page:
        def __init__(self) -> None:
            self.body = "岗位详情加载中"
            self.waits = 0

        def locator(self, selector: str) -> _Locator:
            assert selector == "body"
            return _Locator(self)

        def wait_for_timeout(self, milliseconds: int) -> None:
            self.waits += 1
            assert milliseconds <= 100
            if self.waits == 2:
                self.body = "专业要求：地质工程；学历要求：硕士"

    page = _Page()
    _wait_for_rendered_detail(page, timeout_ms=5_000, max_wait_ms=600)

    assert page.waits == 2


def test_general_identity_fallback_uses_only_the_official_search_card() -> None:
    detail = {
        "title": "",
        "employer": "",
        "major": "地质工程",
        "degree": "硕士",
        "location": "北京",
        "headcount": "1",
        "deadline": "2026-10-31",
        "field_evidence": {"专业范围": "地质工程"},
    }

    completed = _complete_general_detail_identity(
        detail,
        card_title="地质技术岗",
        card_employer="官方地质单位",
    )

    assert completed["title"] == "地质技术岗"
    assert completed["employer"] == "官方地质单位"
    assert completed["major"] == "地质工程"
    assert completed["field_evidence"]["身份补充字段"] == "title, employer"
    assert completed["field_evidence"]["岗位"] == "地质技术岗"
    assert completed["field_evidence"]["招聘单位"] == "官方地质单位"


def test_general_identity_fallback_rejects_missing_card_identity() -> None:
    with pytest.raises(BrowserCaptureError, match="missing title"):
        _complete_general_detail_identity(
            {"title": "", "employer": "", "field_evidence": {}},
            card_title="",
            card_employer="官方地质单位",
        )


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


def test_promote_deterministic_detail_rejections_rehashes_success_manifest(
    tmp_path: Path,
) -> None:
    partial = _payload(
        status="partial",
        scan={
            "pages_scanned": 1,
            "pagination_complete": True,
            "rows_discovered": 2,
            "rows_exported": 1,
            "failed_rows": 1,
            "detail_discovered": 2,
            "detail_succeeded": 1,
            "detail_failed": 1,
            "failure_records": [
                {
                    "keyword": "地质",
                    "page": 1,
                    "row": 2,
                    "detail_id": "reject-1",
                    "reason": "CMGB detail is missing required fields: major evidence",
                }
            ],
        },
    )
    partial_path = tmp_path / "partial.json"
    output = tmp_path / "success.json"
    partial_path.write_text(json.dumps(partial, ensure_ascii=False), encoding="utf-8")
    promoted = promote_deterministic_rejections(
        partial_path,
        output,
        source_id="iguopin-general-geoscience-browser",
        adapter_version="iguopin-general-browser-v1",
    )
    assert promoted["status"] == "success"
    assert promoted["scan"]["rows_discovered"] == 1
    assert promoted["scan"]["rejected_rows"] == 1
    assert promoted["scan"]["detail_succeeded"] == 2
    loaded = load_iguopin_browser_capture(
        output,
        allowed_hosts=HOSTS,
        max_age_hours=100_000,
        require_complete_scan=True,
        require_capture_manifest=True,
    )
    assert loaded["scan"]["detail_failed"] == 0


def test_promote_card_detail_identity_conflict_as_deterministic_rejection(
    tmp_path: Path,
) -> None:
    partial = _payload(
        status="partial",
        scan={
            "pages_scanned": 1,
            "pagination_complete": True,
            "rows_discovered": 1,
            "rows_exported": 1,
            "failed_rows": 1,
            "detail_discovered": 2,
            "detail_succeeded": 1,
            "detail_failed": 1,
            "failure_records": [
                {
                    "keyword": "地质工程",
                    "detail_id": "identity-conflict-1",
                    "reason": "国聘详情岗位名称与公开搜索卡片不一致",
                }
            ],
        },
    )
    partial_path = tmp_path / "partial.json"
    output = tmp_path / "success.json"
    partial_path.write_text(json.dumps(partial, ensure_ascii=False), encoding="utf-8")

    promoted = promote_deterministic_rejections(
        partial_path,
        output,
        source_id="iguopin-general-geoscience-browser",
        adapter_version="iguopin-general-browser-v1",
    )

    assert promoted["status"] == "success"
    assert promoted["scan"]["rejected_rows"] == 1
    assert promoted["scan"]["detail_failed"] == 0


def test_rejected_detail_repair_merges_only_completed_targets() -> None:
    detail_url = "https://www.iguopin.com/job/detail?id=repair-1"
    repaired = {
        **_row(),
        "external_id": "iguopin-general-repair-1",
        "title": "地质工程师",
        "employer": "示例地质单位",
        "detail_url": detail_url,
        "evidence_url": detail_url,
        "field_evidence": {
            "岗位": "地质工程师",
            "专业范围": "地质工程",
            "学历要求": "硕士",
            "工作地点": "北京",
            "招聘人数": "1",
            "报名截止": "2026-12-31",
            "官方详情链接": detail_url,
        },
    }
    base = _payload(
        scan={
            **_payload()["scan"],
            "detail_discovered": 2,
            "detail_succeeded": 2,
            "rejected_rows": 1,
            "rejected_records": [
                {
                    "keyword": "地质",
                    "detail_id": "repair-1",
                    "card_title": "地质工程师",
                    "card_employer": "示例地质单位",
                    "reason": "CMGB detail is missing required fields: major evidence",
                }
            ],
        }
    )
    merged = build_iguopin_rejection_repair_payload(
        base,
        repaired_rows=[repaired],
        repaired_detail_ids=["repair-1"],
        captured_at="2026-10-07T00:00:00Z",
        allowed_hosts=HOSTS,
    )

    assert merged["status"] == "success"
    assert merged["scan"]["rows_exported"] == 2
    assert merged["scan"]["rejected_rows"] == 0
    assert merged["scan"]["repair_mode"] == "deterministic_rejected_detail_repair"
    assert merged["scan"]["detail_succeeded"] == 2


def test_rejected_detail_repair_rejects_empty_completion() -> None:
    base = _payload(
        scan={
            **_payload()["scan"],
            "detail_discovered": 2,
            "detail_succeeded": 2,
            "rejected_records": [
                {"detail_id": "repair-1", "reason": "CMGB detail is missing required fields: major evidence"}
            ],
        }
    )
    with pytest.raises(BrowserCaptureError, match="no completed targets"):
        build_iguopin_rejection_repair_payload(
            base,
            repaired_rows=[],
            repaired_detail_ids=[],
            allowed_hosts=HOSTS,
        )


def test_promote_deterministic_detail_rejections_rejects_unknown_failure(
    tmp_path: Path,
) -> None:
    partial = _payload(
        status="partial",
        scan={
            "pages_scanned": 1,
            "pagination_complete": True,
            "rows_discovered": 1,
            "rows_exported": 1,
            "failed_rows": 1,
            "detail_discovered": 2,
            "detail_succeeded": 1,
            "detail_failed": 1,
            "failure_records": [{"reason": "timeout"}],
        },
    )
    path = tmp_path / "partial.json"
    path.write_text(json.dumps(partial), encoding="utf-8")
    with pytest.raises(BrowserCaptureError, match="access, timeout, or unknown"):
        promote_deterministic_rejections(
            path,
            tmp_path / "success.json",
            source_id="iguopin-general-geoscience-browser",
            adapter_version="iguopin-general-browser-v1",
        )


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

    invalid_batch_size = copy.deepcopy(source)
    invalid_batch_size["config"]["search_keyword_batch_size"] = 0
    with pytest.raises(ContractValidationError, match="search_keyword_batch_size"):
        validate_source_registry([invalid_batch_size])

    invalid_batch_timeout = copy.deepcopy(source)
    invalid_batch_timeout["config"]["search_keyword_batch_timeout_seconds"] = 30
    with pytest.raises(
        ContractValidationError, match="search_keyword_batch_timeout_seconds"
    ):
        validate_source_registry([invalid_batch_timeout])

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


def test_iguopin_general_keyword_batches_merge_only_complete_unique_rows() -> None:
    config = {
        "search_keywords": ["地质", "地球物理", "资源勘查", "地质工程"],
        "search_keyword_batch_size": 2,
    }
    assert _keyword_batches(config) == [["地质", "地球物理"], ["资源勘查", "地质工程"]]
    payload = lambda external_id, keyword: {
        "status": "success",
        "platform_url": "https://www.iguopin.com/job",
        "capture_evidence": {"capture_id": f"capture-{external_id}"},
        "scan": {
            "pages_scanned": 1,
            "pagination_complete": True,
            "failed_rows": 0,
            "detail_failed": 0,
            "cards_seen_before_deduplication": 2,
            "keyword_scans": [{"keyword": keyword, "pagination_complete": True}],
            "rejected_records": [],
        },
        "rows": [{"external_id": external_id, "title": keyword}],
    }
    merged = merge_iguopin_general_batch_payloads(
        [payload("job-1", "地质"), payload("job-1", "地球物理")],
        source_id="iguopin-general-geoscience-browser",
        adapter_version="test-v1",
    )
    assert merged["status"] == "success"
    assert merged["scan"]["batch_count"] == 2
    assert merged["scan"]["pages_scanned"] == 2
    assert merged["scan"]["rows_exported"] == 1
    assert merged["scan"]["detail_discovered"] == 1
    assert merged["capture_evidence"]["source_id"] == "iguopin-general-geoscience-browser"


def test_cross_batch_detail_id_projection_reuses_verified_and_rejected_ids() -> None:
    payload = {
        "rows": [{"external_id": "iguopin-general-123456789"}],
        "scan": {"rejected_records": [{"detail_id": "987654321"}]},
    }
    assert detail_ids_from_capture_payload(
        payload,
        external_id_prefix="iguopin-general",
    ) == {"123456789", "987654321"}
    assert _configured_known_detail_ids(
        {"known_detail_ids": ["123456789", "987654321"]}
    ) == {"123456789", "987654321"}


def test_known_detail_id_projection_rejects_malformed_values() -> None:
    with pytest.raises(BrowserCaptureError, match="known_detail_ids"):
        _configured_known_detail_ids({"known_detail_ids": ["bad id"]})

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
    worker.service_name = "iguopin-general-geoscience-browser"
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
    assert row["field_evidence"]["岗位"] == "地质工程"
    assert row["field_evidence"]["招聘单位"] == "国核铀业发展有限责任公司"
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


def test_iguopin_general_worker_persists_merged_batches_before_sync(
    monkeypatch, tmp_path: Path
) -> None:
    source = _general_source()
    source["enabled"] = True
    source["config"]["search_keyword_batch_size"] = 1
    source["config"]["search_keyword_batch_timeout_seconds"] = 60

    class FakeDatabase:
        def get_source(self, source_id: str):
            assert source_id == "iguopin-general-geoscience-browser"
            return source

    class FakePipeline:
        def __init__(self) -> None:
            self.calls = 0

        def sync_source_manual(self, _received_source):
            self.calls += 1
            return SimpleNamespace(status="finished", error=None)

    worker = IguopinBrowserWorker.__new__(IguopinBrowserWorker)
    worker.settings = SimpleNamespace(data_dir=tmp_path)
    worker.database = FakeDatabase()
    worker.pipeline = FakePipeline()
    worker.source_id = "iguopin-general-geoscience-browser"
    worker.service_name = worker.source_id
    worker.capture_timeout_seconds = 60
    heartbeats: list[tuple[str, str]] = []
    worker._heartbeat = lambda status, detail="": heartbeats.append((status, detail))

    calls: list[tuple[list[str], list[str]]] = []

    def fake_timeout(_capture, *, kwargs, timeout_seconds, **_options):
        assert timeout_seconds == 60
        keywords = list(kwargs["config"]["search_keywords"])
        calls.append((keywords, list(kwargs["config"].get("known_detail_ids", []))))
        return {
            "status": "success",
            "platform_url": "https://www.iguopin.com/job",
            "scan": {
                "pages_scanned": 1,
                "pagination_complete": True,
                "rows_discovered": 1,
                "rows_exported": 1,
                "failed_rows": 0,
                "detail_discovered": 1,
                "detail_succeeded": 1,
                "detail_failed": 0,
                "keyword_scans": [
                    {"keyword": keywords[0], "pagination_complete": True}
                ],
                "rejected_records": [],
            },
            "rows": [{"external_id": f"iguopin-general-{123456789 + len(calls)}", "title": keywords[0]}],
            "capture_evidence": {"capture_id": f"capture-{keywords[0]}"},
        }

    monkeypatch.setattr(worker_module, "run_capture_with_timeout", fake_timeout)
    monkeypatch.setattr(
        worker_module,
        "run_iguopin_general_browser_capture",
        lambda **_kwargs: pytest.fail("capture callable is passed through timeout wrapper"),
    )

    worker._run_once()

    assert calls[0] == (["地质"], [])
    assert calls[1] == (["地球物理"], ["123456790"])
    assert worker.pipeline.calls == 1
    persisted = json.loads(
        (tmp_path / "captures" / "chinalco.json").read_text(encoding="utf-8")
    )
    assert persisted["status"] == "success"
    assert persisted["scan"]["batch_count"] == 2
    assert heartbeats[-1][0] == "running"


@pytest.mark.parametrize("reusable", [True, False])
def test_iguopin_general_worker_reuses_only_valid_complete_batch(
    monkeypatch, tmp_path: Path, reusable: bool
) -> None:
    source = _general_source()
    source["enabled"] = True
    source["config"]["search_keyword_batch_size"] = 1
    source["config"]["search_keyword_batch_timeout_seconds"] = 1800
    first_path = tmp_path / "captures" / "chinalco.batch-1.json"
    first_path.parent.mkdir(parents=True)
    first_path.touch()
    monkeypatch.setenv("IGUOPIN_BROWSER_REUSE_COMPLETE_BATCHES", "1")

    class FakeDatabase:
        def get_source(self, _source_id: str):
            return source

    class FakePipeline:
        calls = 0

        def sync_source_manual(self, _source):
            self.calls += 1
            return SimpleNamespace(status="finished", error=None)

    def batch_payload(keyword: str) -> dict:
        return {
            "status": "success",
            "platform_url": "https://www.iguopin.com/job",
            "scan": {
                "pages_scanned": 1,
                "pagination_complete": True,
                "rows_discovered": 1,
                "rows_exported": 1,
                "failed_rows": 0,
                "detail_discovered": 1,
                "detail_succeeded": 1,
                "detail_failed": 0,
                "keyword_scans": [{"keyword": keyword, "pagination_complete": True}],
                "rejected_records": [],
            },
            "rows": [{"external_id": f"iguopin-general-{keyword}-123456"}],
            "capture_evidence": {"capture_id": f"capture-{keyword}"},
        }

    def load_batch(_path, **_kwargs):
        if not reusable:
            raise ValueError("batch is stale or incomplete")
        return batch_payload("地质")

    captured: list[str] = []

    def capture_batch(_capture, *, kwargs, timeout_seconds, **_options):
        assert timeout_seconds == 1800
        keyword = kwargs["config"]["search_keywords"][0]
        captured.append(keyword)
        return batch_payload(keyword)

    worker = IguopinBrowserWorker.__new__(IguopinBrowserWorker)
    worker.settings = SimpleNamespace(data_dir=tmp_path)
    worker.database = FakeDatabase()
    worker.pipeline = FakePipeline()
    worker.source_id = "iguopin-general-geoscience-browser"
    worker.service_name = worker.source_id
    worker.capture_timeout_seconds = 1800
    worker._heartbeat = lambda *_args, **_kwargs: None
    monkeypatch.setattr(worker_module, "load_iguopin_browser_capture", load_batch)
    monkeypatch.setattr(worker_module, "run_capture_with_timeout", capture_batch)
    monkeypatch.setattr(
        worker_module,
        "persist_cmgb_browser_capture",
        lambda **kwargs: kwargs["payload"],
    )

    worker._run_once()

    assert captured == (["地球物理"] if reusable else ["地质", "地球物理"])
    assert worker.pipeline.calls == 1


def test_iguopin_general_worker_keeps_main_snapshot_when_a_later_batch_fails(
    monkeypatch, tmp_path: Path
) -> None:
    source = _general_source()
    source["enabled"] = True
    source["config"]["search_keyword_batch_size"] = 1
    source["config"]["search_keyword_batch_timeout_seconds"] = 60

    class FakeDatabase:
        def get_source(self, source_id: str):
            assert source_id == "iguopin-general-geoscience-browser"
            return source

    class FakePipeline:
        def __init__(self) -> None:
            self.calls = 0

        def sync_source_manual(self, _received_source):
            self.calls += 1
            return SimpleNamespace(status="finished", error=None)

    worker = IguopinBrowserWorker.__new__(IguopinBrowserWorker)
    worker.settings = SimpleNamespace(data_dir=tmp_path)
    worker.database = FakeDatabase()
    worker.pipeline = FakePipeline()
    worker.source_id = "iguopin-general-geoscience-browser"
    worker.service_name = worker.source_id
    worker.capture_timeout_seconds = 60
    worker._heartbeat = lambda *_args, **_kwargs: None
    attempts = 0

    def fake_timeout(_capture, *, kwargs, timeout_seconds, **_options):
        nonlocal attempts
        attempts += 1
        assert timeout_seconds == 60
        if attempts == 2:
            raise TimeoutError("second batch timed out")
        keyword = list(kwargs["config"]["search_keywords"])[0]
        return {
            "status": "success",
            "platform_url": "https://www.iguopin.com/job",
            "scan": {
                "pages_scanned": 1,
                "pagination_complete": True,
                "rows_discovered": 1,
                "rows_exported": 1,
                "failed_rows": 0,
                "detail_discovered": 1,
                "detail_succeeded": 1,
                "detail_failed": 0,
                "keyword_scans": [{"keyword": keyword, "pagination_complete": True}],
                "rejected_records": [],
            },
            "rows": [{"external_id": f"job-{keyword}", "title": keyword}],
            "capture_evidence": {"capture_id": f"capture-{keyword}"},
        }

    monkeypatch.setattr(worker_module, "run_capture_with_timeout", fake_timeout)
    monkeypatch.setattr(worker_module, "write_iguopin_capture_failure", lambda **_kwargs: None)

    worker._run_once()

    assert attempts == 2
    assert worker.pipeline.calls == 0
    assert not (tmp_path / "captures" / "chinalco.json").exists()


def test_iguopin_general_worker_does_not_sync_a_partial_capture(monkeypatch, tmp_path: Path) -> None:
    source = _general_source()
    source["enabled"] = True

    class FakeDatabase:
        def get_source(self, source_id: str):
            assert source_id == "iguopin-general-geoscience-browser"
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
    worker.source_id = "iguopin-general-geoscience-browser"
    worker.service_name = worker.source_id
    heartbeats: list[tuple[str, str]] = []
    worker._heartbeat = lambda status, detail="": heartbeats.append((status, detail))
    monkeypatch.setattr(
        worker_module,
        "run_iguopin_general_browser_capture",
        lambda **_kwargs: {
            "status": "partial",
            "scan": {"pagination_complete": False, "failed_rows": 1},
        },
    )

    worker._run_once()

    assert worker.pipeline.calls == []
    assert heartbeats[-1][0] == "degraded"


def test_iguopin_general_worker_marks_sync_failure_degraded(monkeypatch, tmp_path: Path) -> None:
    source = _general_source()
    source["enabled"] = True

    class FakeDatabase:
        def get_source(self, source_id: str):
            assert source_id == "iguopin-general-geoscience-browser"
            return source

    class FakePipeline:
        def sync_source_manual(self, _received_source):
            return SimpleNamespace(status="failed", error="database unavailable")

    worker = IguopinBrowserWorker.__new__(IguopinBrowserWorker)
    worker.settings = SimpleNamespace(data_dir=tmp_path)
    worker.database = FakeDatabase()
    worker.pipeline = FakePipeline()
    worker.source_id = "iguopin-general-geoscience-browser"
    worker.service_name = worker.source_id
    heartbeats: list[tuple[str, str]] = []
    worker._heartbeat = lambda status, detail="": heartbeats.append((status, detail))
    monkeypatch.setattr(
        worker_module,
        "run_iguopin_general_browser_capture",
        lambda **_kwargs: {"status": "success", "scan": {"rows_exported": 1}},
    )
    monkeypatch.setattr(worker_module, "write_iguopin_capture_failure", lambda **_kwargs: None)

    worker._run_once()

    assert heartbeats[-1][0] == "degraded"
    assert "synchronization did not finish" in heartbeats[-1][1]


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


def test_iguopin_one_shot_failure_keeps_degraded_heartbeat() -> None:
    statuses: list[str] = []

    class FakeDatabase:
        def record_service_heartbeat(self, _service_name, status, _detail):
            statuses.append(status)

    worker = IguopinBrowserWorker.__new__(IguopinBrowserWorker)
    worker.database = FakeDatabase()
    worker.service_name = "iguopin-general-geoscience-browser"
    worker.stop_event = SimpleNamespace(is_set=lambda: False)
    worker.run_once = True
    worker._run_once = lambda: worker._heartbeat("degraded", "capture timeout")

    assert worker.run_forever() == 1

    assert statuses == ["starting", "degraded"]


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


def test_iguopin_general_registry_is_enabled_after_two_complete_server_captures() -> None:
    registry = json.loads(
        (Path(__file__).parent.parent / "data" / "sources.json").read_text(
            encoding="utf-8"
        )
    )
    source = next(
        item for item in registry if item["id"] == "iguopin-general-geoscience-browser"
    )

    assert source["enabled"] is True
    assert source["source_type"] == "iguopin_general_browser_rows"
    assert source["config"]["card_identity_mode"] == "rendered_react_job_id"
    assert source["config"]["max_pages_per_keyword"] == 20
    assert source["config"]["search_keyword_batch_size"] == 1
    assert source["config"]["search_keyword_batch_timeout_seconds"] == 1800
    assert source["config"]["require_complete_scan"] is True
    assert source["config"]["require_capture_manifest"] is True
