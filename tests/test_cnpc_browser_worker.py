from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import job_hub.cnpc_browser_worker as worker_module
from job_hub.cnpc_browser_worker import CnpcBrowserWorker


def _source(*, enabled: bool = True) -> dict[str, object]:
    return {
        "id": "cnpc-career-browser",
        "homepage_url": "https://zhaopin.cnpc.com.cn/web/recruitInfolist.html",
        "enabled": enabled,
        "config": {
            "capture_path": "captures/cnpc-jobs.json",
            "browser_url": "https://zhaopin.cnpc.com.cn/web/recruitInfolist.html",
            "allowed_hosts": ["zhaopin.cnpc.com.cn"],
        },
    }


def _worker(tmp_path: Path, source: dict[str, object]) -> tuple[CnpcBrowserWorker, list[tuple[str, str]]]:
    class FakeDatabase:
        def get_source(self, source_id: str):
            assert source_id == source["id"]
            return source

        def set_source_enabled(self, source_id: str, enabled: bool) -> None:
            assert source_id == source["id"]
            source["enabled"] = enabled

        def record_source_health(self, source_id: str, *, status: str, detail: str = "") -> None:
            assert source_id == source["id"]

    worker = CnpcBrowserWorker.__new__(CnpcBrowserWorker)
    worker.settings = SimpleNamespace(data_dir=tmp_path)
    worker.database = FakeDatabase()
    worker.source_id = str(source["id"])
    worker.stop_event = SimpleNamespace(wait=lambda _seconds: None)
    heartbeats: list[tuple[str, str]] = []
    worker._heartbeat = lambda status, detail="": heartbeats.append((status, detail))
    return worker, heartbeats


def test_disabled_cnpc_source_never_starts_browser_capture(monkeypatch, tmp_path: Path) -> None:
    worker, heartbeats = _worker(tmp_path, _source(enabled=False))
    monkeypatch.setattr(
        worker_module,
        "run_cnpc_browser_capture",
        lambda **_kwargs: pytest.fail("disabled CNPC source must not capture"),
    )

    worker._run_once()

    assert heartbeats[-1][0] == "idle"


def test_disabled_cnpc_source_with_background_capture_disabled_remains_idle(
    monkeypatch, tmp_path: Path
) -> None:
    source = _source(enabled=False)
    source["config"] = {
        **source["config"],
        "background_capture_enabled": False,
        "automation_status": "blocked_robots",
    }
    worker, heartbeats = _worker(tmp_path, source)
    monkeypatch.setattr(
        worker_module,
        "run_cnpc_browser_capture",
        lambda **_kwargs: pytest.fail("blocked source must not capture"),
    )

    worker._run_once()

    assert heartbeats[-1][0] == "idle"


def test_disabled_cnpc_source_with_background_capture_atomically_activates_after_success(
    monkeypatch, tmp_path: Path
) -> None:
    source = _source(enabled=False)
    source["config"] = {
        **source["config"],
        "background_capture_enabled": True,
        "auto_activate_on_complete_capture": True,
    }
    worker, heartbeats = _worker(tmp_path, source)

    class FakePipeline:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def sync_source_atomically(self, received_source, *, before_commit):
            self.calls.append(received_source)
            before_commit()
            return SimpleNamespace(status="finished", error=None)

    worker.pipeline = FakePipeline()
    monkeypatch.setattr(
        worker_module,
        "run_cnpc_browser_capture",
        lambda **_kwargs: {
            "status": "success",
            "jobs": [{"external_id": "job-1"}],
            "scan": {"pagination_complete": True},
        },
    )

    worker._run_once()

    assert worker.pipeline.calls == [source]
    assert source["enabled"] is True
    assert heartbeats[-1][0] == "running"


def test_successful_cnpc_capture_syncs_immediately(monkeypatch, tmp_path: Path) -> None:
    source = _source()
    worker, heartbeats = _worker(tmp_path, source)

    class FakePipeline:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def sync_source_manual(self, received_source):
            self.calls.append(received_source)
            return SimpleNamespace(status="finished", error=None)

    worker.pipeline = FakePipeline()
    monkeypatch.setattr(
        worker_module,
        "run_cnpc_browser_capture",
        lambda **_kwargs: {"status": "success", "jobs": [{"external_id": "job-1"}], "scan": {}},
    )

    worker._run_once()

    assert worker.pipeline.calls == [source]
    assert heartbeats[-1][0] == "running"


def test_partial_cnpc_capture_does_not_sync(monkeypatch, tmp_path: Path) -> None:
    worker, heartbeats = _worker(tmp_path, _source())

    class FakePipeline:
        def __init__(self) -> None:
            self.calls = 0

        def sync_source_manual(self, _received_source):
            self.calls += 1
            return SimpleNamespace(status="finished", error=None)

    worker.pipeline = FakePipeline()
    monkeypatch.setattr(
        worker_module,
        "run_cnpc_browser_capture",
        lambda **_kwargs: {"status": "partial", "jobs": [], "scan": {"pagination_complete": False}},
    )

    worker._run_once()

    assert worker.pipeline.calls == 0
    assert heartbeats[-1][0] == "degraded"


def test_cnpc_sync_failure_is_reported_as_degraded(monkeypatch, tmp_path: Path) -> None:
    worker, heartbeats = _worker(tmp_path, _source())

    class FakePipeline:
        def sync_source_manual(self, _received_source):
            return SimpleNamespace(status="failed", error="database unavailable")

    worker.pipeline = FakePipeline()
    monkeypatch.setattr(
        worker_module,
        "run_cnpc_browser_capture",
        lambda **_kwargs: {"status": "success", "jobs": [{"external_id": "job-1"}], "scan": {}},
    )
    monkeypatch.setattr(worker_module, "write_cnpc_capture_failure", lambda **_kwargs: None)

    worker._run_once()

    assert heartbeats[-1][0] == "degraded"
    assert "synchronization did not finish" in heartbeats[-1][1]


def test_cnpc_robots_failure_is_persisted_as_access_limited(monkeypatch, tmp_path: Path) -> None:
    worker, _heartbeats = _worker(tmp_path, _source())
    statuses: list[str] = []

    monkeypatch.setattr(
        worker_module,
        "run_cnpc_browser_capture",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("robots.txt cannot be verified")),
    )
    monkeypatch.setattr(
        worker_module,
        "write_cnpc_capture_failure",
        lambda **kwargs: statuses.append(str(kwargs["status"])),
    )

    worker._run_once()

    assert statuses == ["access_limited"]
