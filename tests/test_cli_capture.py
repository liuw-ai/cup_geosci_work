from __future__ import annotations

import sys
from pathlib import Path

import pytest

import job_hub.cli as cli


def test_cnpc_capture_command_initializes_services_before_source_lookup(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    calls: list[str] = []

    class FakeSettings:
        data_dir = tmp_path

    class FakeDatabase:
        def get_source(self, source_id: str):
            assert source_id == "cnpc-career-browser"
            return {
                "id": source_id,
                "source_type": "cnpc_browser_rows",
                "homepage_url": "https://zhaopin.cnpc.com.cn/web/recruitInfolist.html",
                "config": {
                    "capture_path": "captures/cnpc.json",
                    "allowed_hosts": ["zhaopin.cnpc.com.cn"],
                    "browser_url": "https://zhaopin.cnpc.com.cn/web/recruitInfolist.html",
                },
            }

    monkeypatch.setattr(
        cli,
        "services",
        lambda: (calls.append("services") or (FakeSettings(), FakeDatabase(), object())),
    )
    monkeypatch.setattr(
        cli,
        "run_cnpc_browser_capture",
        lambda **_kwargs: {
            "status": "access_limited",
            "scan": {"pages_scanned": 0},
        },
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["job_hub.cli", "cnpc-job-capture-run", "--source-id", "cnpc-career-browser"],
    )

    cli.main()

    assert calls == ["services"]
    assert '"source_id": "cnpc-career-browser"' in capsys.readouterr().out


def test_cmgb_detail_retry_command_uses_only_the_registered_failure_capture(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    calls: list[str] = []

    class FakeSettings:
        data_dir = tmp_path

    class FakeDatabase:
        def get_source(self, source_id: str):
            assert source_id == "cmgb-iguopin-browser"
            return {
                "id": source_id,
                "source_type": "cmgb_browser_rows",
                "homepage_url": "https://cmgb.iguopin.com/jobCampus",
                "config": {
                    "capture_path": "captures/cmgb.json",
                    "allowed_hosts": ["cmgb.iguopin.com", "www.iguopin.com"],
                },
            }

    monkeypatch.setattr(
        cli,
        "services",
        lambda: (calls.append("services") or (FakeSettings(), FakeDatabase(), object())),
    )
    captured: dict[str, object] = {}

    def fake_retry(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {"status": "partial", "scan": {"detail_failed": 1}}

    monkeypatch.setattr(cli, "run_cmgb_detail_retry", fake_retry)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "job_hub.cli",
            "cmgb-detail-retry",
            "--failure-capture",
            "captures/cmgb.failures/partial.json",
        ],
    )

    cli.main()

    assert calls == ["services"]
    assert captured["retry_capture_path"] == tmp_path / "captures/cmgb.failures/partial.json"
    assert captured["output"] == tmp_path / "captures/cmgb.json"
    assert captured["max_age_hours"] == 12
    assert '"status": "partial"' in capsys.readouterr().out


def test_cmgb_quarantine_command_requires_explicit_confirmation(
    monkeypatch, tmp_path: Path
) -> None:
    class FakeSettings:
        data_dir = tmp_path

    class FakeDatabase:
        def get_source(self, _source_id: str):
            return {
                "source_type": "cmgb_browser_rows",
                "config": {"capture_path": "captures/cmgb.json"},
            }

    monkeypatch.setattr(cli, "services", lambda: (FakeSettings(), FakeDatabase(), object()))
    monkeypatch.setattr(
        sys,
        "argv",
        ["job_hub.cli", "cmgb-quarantine-partial"],
    )

    with pytest.raises(SystemExit) as error:
        cli.main()

    assert error.value.code == 2
