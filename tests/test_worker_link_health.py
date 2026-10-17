from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from job_hub.worker import DailyWorker

from conftest import make_settings


def test_worker_persists_one_link_health_report_per_day(monkeypatch, tmp_path: Path) -> None:
    settings = replace(make_settings(tmp_path), link_health_enabled=True)
    worker = DailyWorker(settings)
    monkeypatch.setattr(
        "job_hub.worker.build_link_health_report",
        lambda *_args, **_kwargs: {
            "checked": 2,
            "status_counts": {"reachable": 1, "access_limited": 1},
            "items": [],
            "policy": "read_only",
        },
    )

    first = worker._run_link_health_if_due("2026-10-02")
    second = worker._run_link_health_if_due("2026-10-02")

    assert first["status"] == "ok"
    assert second["status"] == "already_recorded"
    report_path = tmp_path / "link-health" / "2026-10-02.json"
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["observed_on"] == "2026-10-02"
    assert payload["status_counts"]["access_limited"] == 1


def test_worker_link_health_failure_does_not_block_source_sync(monkeypatch, tmp_path: Path) -> None:
    worker = DailyWorker(
        replace(make_settings(tmp_path), link_health_enabled=True)
    )

    def fail(*_args, **_kwargs):
        raise RuntimeError("test network failure")

    monkeypatch.setattr("job_hub.worker.build_link_health_report", fail)

    result = worker._run_link_health_if_due("2026-10-02")

    assert result["status"] == "failed"
    assert "test network failure" in str(result["error"])
    assert not (tmp_path / "link-health" / "2026-10-02.json").exists()
