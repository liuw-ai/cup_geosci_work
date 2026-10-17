from __future__ import annotations

import signal
import time

import pytest
from types import SimpleNamespace

import job_hub.cnooc_browser_worker as worker_module
from job_hub.cnooc_browser_worker import _capture_deadline
from job_hub.cnooc_browser_worker import CnoocBrowserWorker


def test_cnooc_capture_deadline_interrupts_a_stalled_capture() -> None:
    if not hasattr(signal, "SIGALRM"):
        pytest.skip("capture deadline uses POSIX SIGALRM")
    with pytest.raises(TimeoutError, match="exceeded 1s deadline"):
        with _capture_deadline(1):
            time.sleep(2)


def test_cnooc_capture_deadline_zero_disables_the_alarm() -> None:
    with _capture_deadline(0):
        time.sleep(0.01)


def _source() -> dict[str, object]:
    return {
        "id": "cnooc-career-browser",
        "homepage_url": "https://cnooc.zhaopin.com/",
        "config": {
            "application_url": "https://cnooc.zhaopin.com/job/index.html",
            "capture_path": "verified/cnooc-browser-2027.json",
            "allowed_hosts": ["cnooc.zhaopin.com", "xiaoyuan.zhaopin.com"],
        },
    }


def test_cnooc_worker_syncs_a_successful_capture_immediately(monkeypatch, tmp_path) -> None:
    source = _source()

    class FakeDatabase:
        def get_source(self, source_id: str):
            assert source_id == "cnooc-career-browser"
            return source

    class FakePipeline:
        def __init__(self) -> None:
            self.calls = []

        def sync_source_manual(self, received_source):
            self.calls.append(received_source)
            return SimpleNamespace(status="finished", error=None)

    worker = CnoocBrowserWorker.__new__(CnoocBrowserWorker)
    worker.settings = SimpleNamespace(data_dir=tmp_path)
    worker.database = FakeDatabase()
    worker.pipeline = FakePipeline()
    worker.source_id = "cnooc-career-browser"
    heartbeats = []
    worker._heartbeat = lambda status, detail="": heartbeats.append((status, detail))
    captured = {}
    monkeypatch.setattr(
        worker_module,
        "run_cnooc_browser_capture",
        lambda **kwargs: captured.update(kwargs) or {"status": "success", "rows": [{"id": 1}], "scan": {}},
    )

    worker._run_once()

    assert worker.pipeline.calls == [source]
    assert captured["config"]["transport_mode"] == "environment"
    assert captured["config"]["http_client"] == "requests"
    assert heartbeats[-1][0] == "running"


def test_cnooc_worker_does_not_report_running_when_sync_fails(monkeypatch, tmp_path) -> None:
    source = _source()

    class FakeDatabase:
        def get_source(self, source_id: str):
            return source

    class FakePipeline:
        def sync_source_manual(self, _received_source):
            return SimpleNamespace(status="failed", error="collector failed")

    worker = CnoocBrowserWorker.__new__(CnoocBrowserWorker)
    worker.settings = SimpleNamespace(data_dir=tmp_path)
    worker.database = FakeDatabase()
    worker.pipeline = FakePipeline()
    worker.source_id = "cnooc-career-browser"
    heartbeats = []
    worker._heartbeat = lambda status, detail="": heartbeats.append((status, detail))
    monkeypatch.setattr(
        worker_module,
        "run_cnooc_browser_capture",
        lambda **_kwargs: {"status": "success", "rows": [{"id": 1}], "scan": {}},
    )
    monkeypatch.setattr(worker_module, "write_cnooc_capture_failure", lambda **_kwargs: None)

    worker._run_once()

    assert heartbeats[-1][0] == "degraded"
    assert "synchronization did not finish" in heartbeats[-1][1]


def test_cnooc_one_shot_degraded_worker_returns_nonzero() -> None:
    worker = CnoocBrowserWorker.__new__(CnoocBrowserWorker)
    worker.stop_event = SimpleNamespace(is_set=lambda: False)
    worker.run_once = True
    worker._heartbeat = lambda status, detail="": setattr(worker, "_last_heartbeat_status", status)
    worker._run_once = lambda: worker._heartbeat("degraded", "capture timeout")

    assert worker.run_forever() == 1
