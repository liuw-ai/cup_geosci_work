from pathlib import Path
from types import SimpleNamespace

import job_hub.cupb_browser_worker as worker_module
from job_hub.cupb_browser_worker import CupbBrowserWorker


def _worker(tmp_path: Path, pipeline: object) -> tuple[CupbBrowserWorker, dict[str, object]]:
    output = tmp_path / "snapshot.json"
    source = {
        "id": "cupb-career-browser-20261007",
        "config": {
            "snapshot_path": str(output),
            "direct_notice_urls": ["https://career.cup.edu.cn/campus/view/id/1"],
        },
    }

    class FakeDatabase:
        def __init__(self) -> None:
            self.freshness = []

        def get_source(self, source_id: str):
            assert source_id == source["id"]
            return source

        def record_source_health(self, source_id: str, *, status: str, detail: str = "") -> None:
            assert source_id == source["id"]
            self.last_health = (status, detail)

        def record_source_capture_freshness(self, source_id: str, **kwargs):
            assert source_id == source["id"]
            self.freshness.append(kwargs)

    worker = CupbBrowserWorker.__new__(CupbBrowserWorker)
    worker.settings = SimpleNamespace(data_dir=tmp_path)
    worker.database = FakeDatabase()
    worker.pipeline = pipeline
    worker.source_id = source["id"]
    worker.executable_path = "chrome"
    return worker, source


def test_sync_failure_restores_previous_cupb_snapshot(monkeypatch, tmp_path: Path) -> None:
    class FailedPipeline:
        def sync_source_manual(self, _source):
            return SimpleNamespace(status="failed", error="snapshot rejected")

    worker, source = _worker(tmp_path, FailedPipeline())
    output = Path(source["config"]["snapshot_path"])
    output.write_text("new", encoding="utf-8")
    rollback = tmp_path / "snapshot.json.previous"
    rollback.write_text("old", encoding="utf-8")
    monkeypatch.setattr(
        worker_module,
        "run_cupb_browser_capture",
        lambda **_kwargs: {
            "status": "success",
            "captured_at": "2026-10-08T09:00:00Z",
            "rollback_path": str(rollback),
        },
    )
    monkeypatch.setattr(worker_module, "write_cupb_capture_failure", lambda **_kwargs: None)

    try:
        worker.run_once()
    except RuntimeError as error:
        assert "snapshot rejected" in str(error)
    else:  # pragma: no cover - the worker must surface a failed sync
        raise AssertionError("failed CUPB synchronization was swallowed")

    assert output.read_text(encoding="utf-8") == "old"
    assert not rollback.exists()


def test_successful_sync_receives_new_capture_timestamp(monkeypatch, tmp_path: Path) -> None:
    received: list[dict[str, object]] = []

    class SuccessfulPipeline:
        def sync_source_manual(self, source):
            received.append(source)
            return SimpleNamespace(status="finished", error=None)

    worker, _source = _worker(tmp_path, SuccessfulPipeline())
    monkeypatch.setattr(
        worker_module,
        "run_cupb_browser_capture",
        lambda **_kwargs: {
            "status": "success",
            "captured_at": "2026-10-08T09:00:00Z",
            "rollback_path": "",
        },
    )

    result = worker.run_once()

    assert result["sync_status"] == "finished"
    assert received[0]["config"]["snapshot_captured_at"] == "2026-10-08T09:00:00Z"
    assert worker.database.freshness == [{
        "captured_at": "2026-10-08T09:00:00Z",
        "crawl_run_id": None,
        "evidence_kind": "browser_capture_result",
    }]


def test_capture_and_failure_paths_use_writable_runtime_volume(monkeypatch, tmp_path: Path) -> None:
    received: list[dict[str, object]] = []

    class SuccessfulPipeline:
        def sync_source_manual(self, source):
            received.append(source)
            return SimpleNamespace(status="finished", error=None)

    worker, source = _worker(tmp_path, SuccessfulPipeline())
    source["config"]["snapshot_path"] = "data/verified/readonly.json"
    source["config"]["capture_path"] = "captures/cupb.json"
    captured: list[Path] = []

    def fake_capture(**kwargs):
        captured.append(Path(kwargs["output"]))
        return {"status": "success", "captured_at": "2026-10-08T09:00:00Z", "rollback_path": ""}

    monkeypatch.setattr(worker_module, "run_cupb_browser_capture", fake_capture)

    worker.run_once()

    assert captured == [tmp_path / "captures" / "cupb.json"]
    assert received[0]["config"]["snapshot_path"] == str(tmp_path / "captures" / "cupb.json")
