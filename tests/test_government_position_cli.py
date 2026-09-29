from __future__ import annotations

import json
import sys

from job_hub.cli import main as cli_main
from job_hub.db import Database

from conftest import source


def test_government_position_audit_uses_persisted_source_verifications(
    tmp_path, monkeypatch, capsys
) -> None:
    database_path = tmp_path / "jobs.sqlite3"
    database = Database(database_path)
    database.initialize()
    database.upsert_source(source())
    database.record_government_source_verification(
        "official-test-source",
        status="source_unavailable",
        checked_at="2026-09-29T01:00:00Z",
        detail="server direct connection failed",
    )

    captured: dict[str, object] = {}

    def fake_report(registry, **kwargs):
        captured["registry"] = registry
        captured.update(kwargs)
        return {"source_evidence_verifications": kwargs["source_verifications"]}

    monkeypatch.setenv("APP_DATABASE_PATH", str(database_path))
    monkeypatch.setattr("job_hub.cli.load_position_registry", lambda _path: {"records": []})
    monkeypatch.setattr("job_hub.cli.government_position_quality_report", fake_report)
    monkeypatch.setattr(
        sys,
        "argv",
        ["job_hub.cli", "government-position-audit", "--today", "2026-09-29"],
    )

    cli_main()

    output = json.loads(capsys.readouterr().out)
    assert output["source_evidence_verifications"]["official-test-source"]["status"] == (
        "source_unavailable"
    )
    assert captured["registry"] == {"records": []}
