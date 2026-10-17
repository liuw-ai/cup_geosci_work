from __future__ import annotations

import json

from job_hub.worker import DailyWorker

from conftest import make_settings


def test_worker_persists_private_identity_only_entry_health_report(
    tmp_path, monkeypatch
) -> None:
    settings = make_settings(tmp_path)
    worker = DailyWorker(settings)
    monkeypatch.setattr(
        "job_hub.worker.load_domestic_expansion_queue",
        lambda: {"records": [{"id": "candidate"}]},
    )
    monkeypatch.setattr(
        "job_hub.worker.run_identity_only_entry_health",
        lambda received_settings, queue: {
            "scope": "official_identity_only_entry_health",
            "candidate_count": len(queue["records"]),
            "reachable_count": 0,
            "items": [],
            "publication_policy": "private only",
        },
    )

    worker._run_domestic_entry_health()

    path = tmp_path / "domestic-entry-health" / "latest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["candidate_count"] == 1
    assert payload["reachable_count"] == 0
    assert payload["scope"] == "official_identity_only_entry_health"
    assert payload["generated_at"].endswith("+08:00")
    assert worker.last_domestic_entry_health_monotonic > 0


def test_worker_retries_failed_identity_only_entry_health_promptly(
    tmp_path, monkeypatch
) -> None:
    settings = make_settings(tmp_path)
    worker = DailyWorker(settings)
    monkeypatch.setattr(
        "job_hub.worker.load_domestic_expansion_queue",
        lambda: (_ for _ in ()).throw(ValueError("bad queue")),
    )

    worker._run_domestic_entry_health()

    interval = settings.domestic_entry_health_interval_minutes * 60
    elapsed = __import__("time").monotonic() - worker.last_domestic_entry_health_monotonic
    assert interval - elapsed <= settings.worker_failure_retry_seconds + 1
