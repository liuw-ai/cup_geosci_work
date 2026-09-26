from __future__ import annotations

import sys
from pathlib import Path

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
