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


def test_manual_government_evidence_confirmation_requires_explicit_operator_action(
    tmp_path, monkeypatch, capsys
) -> None:
    database_path = tmp_path / "jobs.sqlite3"
    database = Database(database_path)
    database.initialize()
    source_record = source()
    source_record["config"] = {
        "government_evidence_recheck": True,
        "government_evidence_recheck_mode": "manual_only",
    }
    database.upsert_source(source_record)

    monkeypatch.setenv("APP_DATABASE_PATH", str(database_path))
    monkeypatch.setattr(
        "job_hub.cli.load_position_registry",
        lambda _path: {
            "records": [
                {
                    "source_id": "official-test-source",
                    "record_status": "verified_open",
                }
            ]
        },
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "job_hub.cli",
            "confirm-government-source-evidence",
            "official-test-source",
            "--confirm",
            "--note",
            "管理员已逐项核对官方公告、官方附件与报名状态。",
        ],
    )

    cli_main()

    output = json.loads(capsys.readouterr().out)
    verification = database.list_government_source_verifications()[0]
    assert output["source_id"] == "official-test-source"
    assert output["verified_open_records"] == 1
    assert verification["status"] == "verified"
    assert "Administrator manual confirmation" in verification["detail"]
