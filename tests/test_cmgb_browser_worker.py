from __future__ import annotations

import json
import time
from types import SimpleNamespace
from pathlib import Path

import pytest

import job_hub.cmgb_browser_worker as worker_module
from job_hub.cmgb_browser_capture import (
    CmgbBrowserCaptureError,
    _detail_retry_needs_session_reset,
    _detail_retryable,
)
from job_hub.cmgb_browser_worker import CmgbBrowserWorker, _capture_deadline


def test_capture_deadline_interrupts_a_stalled_capture() -> None:
    if not hasattr(__import__("signal"), "SIGALRM"):
        pytest.skip("capture deadline uses POSIX SIGALRM")
    with pytest.raises(TimeoutError, match="exceeded 1s deadline"):
        with _capture_deadline(1):
            time.sleep(2)


def test_capture_deadline_zero_disables_the_alarm() -> None:
    with _capture_deadline(0):
        time.sleep(0.01)


def _worker_with_source(tmp_path):
    source = {
        "id": "cmgb-iguopin-browser",
        "homepage_url": "https://cmgb.iguopin.com/jobCampus",
        "config": {
            "capture_path": "captures/cmgb.json",
            "browser_url": "https://cmgb.iguopin.com/jobCampus",
            "allowed_hosts": ["cmgb.iguopin.com", "www.iguopin.com"],
            "retry_partial_first": True,
            "detail_retry_max_age_hours": 12,
        },
    }

    class FakeDatabase:
        def get_source(self, source_id: str):
            assert source_id == source["id"]
            return source

    worker = CmgbBrowserWorker.__new__(CmgbBrowserWorker)
    worker.settings = SimpleNamespace(data_dir=tmp_path)
    worker.database = FakeDatabase()
    worker.source_id = source["id"]
    worker.capture_timeout_seconds = 0
    heartbeats = []
    worker._heartbeat = lambda status, detail="": heartbeats.append((status, detail))
    return worker, source, heartbeats


def test_worker_retries_recent_partial_details_before_running_full_list_capture(
    monkeypatch, tmp_path
) -> None:
    worker, _source, heartbeats = _worker_with_source(tmp_path)
    failure = tmp_path / "captures" / "cmgb.failure.json"
    failure.parent.mkdir(parents=True)
    failure.write_text("{}", encoding="utf-8")
    captured = {}

    def fake_retry(**kwargs):
        captured.update(kwargs)
        return {"status": "partial", "scan": {"detail_failed": 1}}

    monkeypatch.setattr(worker_module, "run_cmgb_detail_retry", fake_retry)
    monkeypatch.setattr(
        worker_module,
        "run_cmgb_browser_capture",
        lambda **_kwargs: pytest.fail("full list capture must not run after a recent partial"),
    )

    worker._run_once()

    assert captured["retry_capture_path"] == failure
    assert captured["max_age_hours"] == 12
    assert heartbeats[-1][0] == "degraded"


def test_worker_falls_back_to_full_scan_only_when_partial_retry_is_stale(
    monkeypatch, tmp_path
) -> None:
    worker, _source, heartbeats = _worker_with_source(tmp_path)
    failure = tmp_path / "captures" / "cmgb.failure.json"
    failure.parent.mkdir(parents=True)
    failure.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        worker_module,
        "run_cmgb_detail_retry",
        lambda **_kwargs: (_ for _ in ()).throw(
            CmgbBrowserCaptureError("CMGB capture is stale (13.0h > 12.0h)")
        ),
    )
    calls = []

    def fake_full_capture(**kwargs):
        calls.append(kwargs)
        return {"status": "success", "scan": {"detail_failed": 0}}

    monkeypatch.setattr(worker_module, "run_cmgb_browser_capture", fake_full_capture)

    worker._run_once()

    assert len(calls) == 1
    assert heartbeats[-1][0] == "running"


def test_cmgb_retry_configuration_is_scoped_to_the_cmgb_source() -> None:
    registry = json.loads(
        (Path(__file__).parent.parent / "data" / "sources.json").read_text(encoding="utf-8")
    )
    sources = {source["id"]: source for source in registry}

    assert sources["cmgb-iguopin-browser"]["config"]["retry_partial_first"] is True
    assert sources["cmgb-iguopin-browser"]["config"]["detail_retry_max_age_hours"] == 12
    assert sources["cmgb-iguopin-browser"]["config"]["detail_retry_attempts"] == 3
    assert sources["cmgb-iguopin-browser"]["config"]["detail_retry_reconnect_on_crash"] is True
    assert sources["cmgb-iguopin-browser"]["config"]["detail_retry_reconnect_delay_ms"] == 750
    assert "retry_partial_first" not in sources["cnpc-career-browser"]["config"]


def test_cmgb_detail_retry_does_not_repeat_access_policy_failures() -> None:
    assert not _detail_retryable(RuntimeError("robots.txt does not permit browser capture"))
    assert not _detail_retryable(RuntimeError("CMGB retry detail returned HTTP 412"))
    assert _detail_retryable(RuntimeError("detail page timed out"))


def test_cmgb_detail_retry_resets_session_only_for_browser_runtime_failures() -> None:
    assert _detail_retry_needs_session_reset(RuntimeError("Target crashed"))
    assert _detail_retry_needs_session_reset(RuntimeError("Browser has been closed"))
    assert _detail_retry_needs_session_reset(RuntimeError("Execution context was destroyed"))
    assert not _detail_retry_needs_session_reset(RuntimeError("detail page timed out"))
    assert not _detail_retry_needs_session_reset(RuntimeError("HTTP 412"))
