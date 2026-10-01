from __future__ import annotations

import json
import sys

from job_hub.cli import main as cli_main


def test_government_position_audit_defaults_to_configured_business_date(
    tmp_path, monkeypatch, capsys
) -> None:
    database_path = tmp_path / "jobs.sqlite3"
    monkeypatch.setenv("APP_DATABASE_PATH", str(database_path))
    monkeypatch.setattr("job_hub.cli.coverage_snapshot_date", lambda _settings: "2026-10-02")
    monkeypatch.setattr("job_hub.cli.load_position_registry", lambda _path: {"records": []})

    captured: dict[str, object] = {}

    def fake_report(_registry, **kwargs):
        captured.update(kwargs)
        return {"today": kwargs["today"]}

    monkeypatch.setattr("job_hub.cli.government_position_quality_report", fake_report)
    monkeypatch.setattr(sys, "argv", ["job_hub.cli", "government-position-audit"])

    cli_main()

    assert captured["today"] == "2026-10-02"
    assert json.loads(capsys.readouterr().out)["today"] == "2026-10-02"


def test_cross_ledger_audit_defaults_to_configured_business_date(
    tmp_path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("APP_DATABASE_PATH", str(tmp_path / "unused.sqlite3"))
    monkeypatch.setattr("job_hub.cli.coverage_snapshot_date", lambda _settings: "2026-10-02")

    captured: dict[str, object] = {}

    def fake_audit(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "today": kwargs["today"]}

    monkeypatch.setattr("job_hub.cli.build_government_ledger_consistency_audit", fake_audit)
    monkeypatch.setattr(sys, "argv", ["job_hub.cli", "cross-ledger-audit"])

    cli_main()

    assert captured["today"] == "2026-10-02"
    assert json.loads(capsys.readouterr().out)["today"] == "2026-10-02"
