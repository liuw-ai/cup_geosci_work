from pathlib import Path
from types import SimpleNamespace

import pytest

import job_hub.official_browser_worker as worker_module
from job_hub.official_browser_worker import OfficialBrowserWorker


def _source(*, enabled: bool = True) -> dict[str, object]:
    return {
        "id": "pipechina-browser-capture",
        "homepage_url": "https://zhaopin.pipechina.com.cn/recruit",
        "enabled": enabled,
        "config": {
            "capture_path": "captures/pipechina.json",
            "browser_url": "https://zhaopin.pipechina.com.cn/recruit",
            "allowed_hosts": ["zhaopin.pipechina.com.cn"],
        },
    }


def _worker(tmp_path: Path, source: dict[str, object]):
    class FakeDatabase:
        def __init__(self) -> None:
            self.enabled_updates: list[tuple[str, bool]] = []
            self.health_updates: list[tuple[str, str]] = []

        def get_source(self, source_id: str):
            assert source_id == source["id"]
            return source

        def set_source_enabled(self, source_id: str, enabled: bool) -> None:
            assert source_id == source["id"]
            self.enabled_updates.append((source_id, enabled))
            source["enabled"] = enabled

        def record_source_health(self, source_id: str, *, status: str, detail: str = "") -> None:
            assert source_id == source["id"]
            self.health_updates.append((status, detail))

    worker = OfficialBrowserWorker.__new__(OfficialBrowserWorker)
    worker.settings = SimpleNamespace(data_dir=tmp_path)
    worker.database = FakeDatabase()
    worker.source_id = str(source["id"])
    worker.service_name = "pipechina-browser"
    heartbeats: list[tuple[str, str]] = []
    worker._heartbeat = lambda status, detail="": heartbeats.append((status, detail))
    return worker, heartbeats


def test_official_one_shot_degraded_worker_returns_nonzero() -> None:
    worker = OfficialBrowserWorker.__new__(OfficialBrowserWorker)
    worker.stop_event = SimpleNamespace(is_set=lambda: False)
    worker.source_id = "pipechina-browser-capture"
    worker.run_once = True
    worker._heartbeat = lambda status, detail="": setattr(worker, "_last_heartbeat_status", status)
    worker._run_once = lambda: worker._heartbeat("degraded", "robots denied")

    assert worker.run_forever() == 1


def test_official_one_shot_success_returns_zero() -> None:
    worker = OfficialBrowserWorker.__new__(OfficialBrowserWorker)
    worker.stop_event = SimpleNamespace(is_set=lambda: False)
    worker.source_id = "pipechina-browser-capture"
    worker.run_once = True
    worker._heartbeat = lambda status, detail="": setattr(worker, "_last_heartbeat_status", status)
    worker._run_once = lambda: worker._heartbeat("running", "capture complete")

    assert worker.run_forever() == 0


def test_disabled_official_source_never_starts_capture(monkeypatch, tmp_path: Path) -> None:
    worker, heartbeats = _worker(tmp_path, _source(enabled=False))
    monkeypatch.setattr(
        worker_module,
        "run_capture_with_timeout",
        lambda *_args, **_kwargs: pytest.fail("disabled official source must not capture"),
    )

    worker._run_once()

    assert heartbeats[-1][0] == "idle"


def test_successful_official_capture_syncs_immediately(monkeypatch, tmp_path: Path) -> None:
    source = _source()
    worker, heartbeats = _worker(tmp_path, source)

    class FakePipeline:
        def __init__(self) -> None:
            self.calls = []

        def sync_source_manual(self, received_source):
            self.calls.append(received_source)
            return SimpleNamespace(status="finished", error=None)

    worker.pipeline = FakePipeline()
    monkeypatch.setattr(
        worker_module,
        "run_capture_with_timeout",
        lambda *_args, **_kwargs: {"status": "success", "rows": [{"id": 1}], "scan": {}},
    )

    worker._run_once()

    assert worker.pipeline.calls == [source]
    assert heartbeats[-1][0] == "running"


def test_background_capture_atomically_activates_disabled_source(monkeypatch, tmp_path: Path) -> None:
    source = _source(enabled=False)
    source["config"] = {
        **source["config"],
        "background_capture_enabled": True,
        "auto_activate_on_complete_capture": True,
    }
    worker, heartbeats = _worker(tmp_path, source)

    class FakePipeline:
        def __init__(self) -> None:
            self.calls = []

        def sync_source_atomically(self, received_source, *, before_commit):
            self.calls.append(received_source)
            before_commit()
            return SimpleNamespace(status="finished", error=None)

    worker.pipeline = FakePipeline()
    monkeypatch.setattr(
        worker_module,
        "run_capture_with_timeout",
        lambda *_args, **_kwargs: {"status": "success", "rows": [{"id": 1}], "scan": {}},
    )

    worker._run_once()

    assert worker.pipeline.calls == [source]
    assert worker.database.enabled_updates == [("pipechina-browser-capture", True)]
    assert heartbeats[-1][0] == "running"


def test_partial_official_capture_does_not_sync(monkeypatch, tmp_path: Path) -> None:
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
        "run_capture_with_timeout",
        lambda *_args, **_kwargs: {"status": "partial", "rows": [], "scan": {}},
    )

    worker._run_once()

    assert worker.pipeline.calls == 0
    assert heartbeats[-1][0] == "degraded"


def test_partial_official_capture_records_source_health(monkeypatch, tmp_path: Path) -> None:
    worker, heartbeats = _worker(tmp_path, _source())
    monkeypatch.setattr(
        worker_module,
        "run_capture_with_timeout",
        lambda *_args, **_kwargs: {"status": "partial", "rows": [], "scan": {}},
    )

    worker._run_once()

    assert worker.database.health_updates[0][0] == "source_degraded"
    assert "状态未改变" in worker.database.health_updates[0][1]
    assert heartbeats[-1][0] == "degraded"


def test_partial_official_capture_writes_failure_snapshot(monkeypatch, tmp_path: Path) -> None:
    source = _source()
    worker, _heartbeats = _worker(tmp_path, source)
    output = tmp_path / "captures" / "pipechina.json"
    source["config"] = {**source["config"], "capture_path": "captures/pipechina.json"}
    monkeypatch.setattr(
        worker_module,
        "run_capture_with_timeout",
        lambda *_args, **_kwargs: {
            "status": "partial",
            "rows": [{"external_id": "diagnostic-only"}],
            "scan": {
                "rows_discovered": 4,
                "rows_exported": 1,
                "failed_rows": 3,
                "failure_reason": "detail pages timed out",
            },
        },
    )

    worker._run_once()

    failure = output.with_name(output.name + ".failure.json")
    assert failure.exists()
    payload = failure.read_text(encoding="utf-8")
    assert '"status": "partial"' in payload
    assert "detail pages timed out" in payload


def test_access_limited_capture_preserves_failure_status(monkeypatch, tmp_path: Path) -> None:
    source = _source()
    worker, _heartbeats = _worker(tmp_path, source)
    output = tmp_path / "captures" / "pipechina.json"
    monkeypatch.setattr(
        worker_module,
        "run_capture_with_timeout",
        lambda *_args, **_kwargs: {
            "status": "access_limited",
            "scan": {"failure_reason": "robots.txt returned HTTP 403"},
        },
    )

    worker._run_once()

    payload = (output.with_name(output.name + ".failure.json")).read_text(
        encoding="utf-8"
    )
    assert '"status": "access_limited"' in payload
    assert "HTTP 403" in payload


def test_official_sync_failure_is_degraded(monkeypatch, tmp_path: Path) -> None:
    worker, heartbeats = _worker(tmp_path, _source())

    class FakePipeline:
        def sync_source_manual(self, _received_source):
            return SimpleNamespace(status="failed", error="database unavailable")

    worker.pipeline = FakePipeline()
    monkeypatch.setattr(
        worker_module,
        "run_capture_with_timeout",
        lambda *_args, **_kwargs: {"status": "success", "rows": [{"id": 1}], "scan": {}},
    )
    monkeypatch.setattr(worker_module, "write_browser_capture_failure", lambda **_kwargs: None)

    worker._run_once()

    assert heartbeats[-1][0] == "degraded"
    assert "synchronization did not finish" in heartbeats[-1][1]


def test_background_sync_failure_restores_disabled_state(monkeypatch, tmp_path: Path) -> None:
    source = _source(enabled=False)
    source["config"] = {
        **source["config"],
        "background_capture_enabled": True,
        "auto_activate_on_complete_capture": True,
    }
    worker, heartbeats = _worker(tmp_path, source)

    class FakePipeline:
        def sync_source_atomically(self, _received_source, *, before_commit):
            before_commit()
            return SimpleNamespace(status="failed", error="database unavailable")

    worker.pipeline = FakePipeline()
    monkeypatch.setattr(
        worker_module,
        "run_capture_with_timeout",
        lambda *_args, **_kwargs: {"status": "success", "rows": [{"id": 1}], "scan": {}},
    )
    monkeypatch.setattr(worker_module, "write_browser_capture_failure", lambda **_kwargs: None)

    worker._run_once()

    assert source["enabled"] is False
    assert worker.database.enabled_updates == [
        ("pipechina-browser-capture", True),
        ("pipechina-browser-capture", False),
    ]
    assert heartbeats[-1][0] == "degraded"


def test_official_robots_failure_is_access_limited(monkeypatch, tmp_path: Path) -> None:
    worker, _heartbeats = _worker(tmp_path, _source())
    statuses: list[str] = []
    monkeypatch.setattr(
        worker_module,
        "run_capture_with_timeout",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("robots.txt cannot be verified")),
    )
    monkeypatch.setattr(
        worker_module,
        "write_browser_capture_failure",
        lambda **kwargs: statuses.append(str(kwargs["status"])),
    )

    worker._run_once()

    assert statuses == ["access_limited"]
    assert worker.database.health_updates[-1][0] == "source_degraded"
