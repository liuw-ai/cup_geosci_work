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


def test_government_revalidation_command_is_source_scoped_and_does_not_activate(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    calls: list[object] = []

    class FakeSettings:
        database_path = tmp_path / "jobs.sqlite3"

        @staticmethod
        def from_env():
            return FakeSettings()

        def ensure_runtime_paths(self) -> None:
            calls.append("ensure_runtime_paths")

    class FakeDatabase:
        def __init__(self, _path: Path) -> None:
            calls.append("database")

        def initialize(self) -> None:
            calls.append("initialize")

        def list_sources(self) -> list[dict[str, object]]:
            return [
                {"id": "cea-2027-recruitment"},
                {"id": "other-government-source"},
            ]

    class FakeBackup:
        def as_dict(self) -> dict[str, object]:
            return {"path": "backup.sqlite3", "verification": {"valid": True}}

    class FakeBackupManager:
        def __init__(self, _settings: object) -> None:
            calls.append("backup_manager")

        def create_backup(self) -> FakeBackup:
            calls.append("backup")
            return FakeBackup()

    class FakeWorker:
        def __init__(self, _settings: object) -> None:
            calls.append("worker")

        def _revalidate_government_position_sources(
            self, *, only_source_ids: set[str], allow_activation: bool
        ) -> dict[str, int]:
            calls.append((only_source_ids, allow_activation))
            return {
                "verified": 0,
                "scheduled": 0,
                "source_unavailable": 1,
                "withdrawn": 0,
                "not_configured": 0,
                "manual_confirmation_required": 0,
                "activated": 0,
                "error": 0,
            }

    monkeypatch.setattr(cli, "Settings", FakeSettings)
    monkeypatch.setattr(cli, "Database", FakeDatabase)
    monkeypatch.setattr(cli, "DatabaseBackupManager", FakeBackupManager)
    monkeypatch.setattr(cli, "DailyWorker", FakeWorker)
    monkeypatch.setattr(
        cli,
        "services",
        lambda: (_ for _ in ()).throw(AssertionError("services() must not run")),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "job_hub.cli",
            "revalidate-government-source-evidence",
            "--source-id",
            "cea-2027-recruitment",
        ],
    )

    cli.main()

    assert calls == [
        "ensure_runtime_paths",
        "database",
        "initialize",
        "backup_manager",
        "backup",
        "worker",
        ({"cea-2027-recruitment"}, False),
    ]
    output = capsys.readouterr().out
    assert '"allow_activation": false' in output
    assert '"source_unavailable": 1' in output


def test_government_revalidation_command_rejects_unregistered_source_before_backup(
    monkeypatch, tmp_path: Path
) -> None:
    class FakeSettings:
        database_path = tmp_path / "jobs.sqlite3"

        @staticmethod
        def from_env():
            return FakeSettings()

        def ensure_runtime_paths(self) -> None:
            pass

    class FakeDatabase:
        def __init__(self, _path: Path) -> None:
            pass

        def initialize(self) -> None:
            pass

        def list_sources(self) -> list[dict[str, object]]:
            return [{"id": "cea-2027-recruitment"}]

    monkeypatch.setattr(cli, "Settings", FakeSettings)
    monkeypatch.setattr(cli, "Database", FakeDatabase)
    monkeypatch.setattr(
        cli,
        "DatabaseBackupManager",
        lambda _settings: (_ for _ in ()).throw(
            AssertionError("backup must not run for an unknown source")
        ),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "job_hub.cli",
            "revalidate-government-source-evidence",
            "--source-id",
            "missing-source",
        ],
    )

    with pytest.raises(SystemExit) as error:
        cli.main()

    assert error.value.code == 2


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


def test_browser_capture_check_uses_cnooc_production_validation_contract(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    class FakeSettings:
        data_dir = tmp_path

    class FakeDatabase:
        def get_source(self, source_id: str):
            assert source_id == "cnooc-career-browser"
            return {
                "id": source_id,
                "source_type": "cnooc_browser_rows",
                "config": {
                    "capture_path": "captures/cnooc.json",
                    "allowed_hosts": ["cnooc.zhaopin.com", "xiaoyuan.zhaopin.com"],
                    "max_age_hours": 30,
                    "require_complete_scan": True,
                    "require_capture_manifest": True,
                },
            }

    monkeypatch.setattr(
        cli, "services", lambda: (FakeSettings(), FakeDatabase(), object())
    )
    captured: dict[str, object] = {}

    def fake_load(path: Path, **kwargs: object) -> dict[str, object]:
        captured["path"] = path
        captured.update(kwargs)
        return {
            "captured_at": "2026-10-09T14:43:21Z",
            "capture_age_hours": 0.1,
            "scan": {
                "pagination_complete": True,
                "detail_succeeded": 332,
                "detail_failed": 0,
            },
            "rows": [{}] * 332,
        }

    monkeypatch.setattr(cli, "load_cnooc_browser_capture", fake_load)
    monkeypatch.setattr(
        sys,
        "argv",
        ["job_hub.cli", "browser-capture-check", "cnooc-career-browser"],
    )

    cli.main()

    assert captured == {
        "path": tmp_path / "captures/cnooc.json",
        "allowed_hosts": ["cnooc.zhaopin.com", "xiaoyuan.zhaopin.com"],
        "max_age_hours": 30.0,
        "require_complete_scan": True,
        "require_capture_manifest": True,
    }
    result = capsys.readouterr().out
    assert '"source_type": "cnooc_browser_rows"' in result
    assert '"detail_succeeded": 332' in result


def test_browser_capture_check_honors_generic_capture_manifest_requirement(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    class FakeSettings:
        data_dir = tmp_path

    class FakeDatabase:
        def get_source(self, source_id: str):
            assert source_id == "pipechina-browser-capture"
            return {
                "id": source_id,
                "source_type": "official_browser_rows",
                "config": {
                    "capture_path": "captures/pipechina.json",
                    "allowed_hosts": ["zhaopin.pipechina.com.cn"],
                    "max_age_hours": 24,
                    "require_complete_scan": True,
                    "require_capture_manifest": True,
                },
            }

    monkeypatch.setattr(
        cli, "services", lambda: (FakeSettings(), FakeDatabase(), object())
    )
    captured: dict[str, object] = {}

    def fake_load(path: Path, **kwargs: object) -> dict[str, object]:
        captured["path"] = path
        captured.update(kwargs)
        return {"captured_at": "2026-10-09T14:43:21Z", "rows": [{}]}

    monkeypatch.setattr(cli, "load_browser_capture", fake_load)
    monkeypatch.setattr(cli, "browser_capture_summary", lambda _payload: {"rows": 1})
    monkeypatch.setattr(
        sys,
        "argv",
        ["job_hub.cli", "browser-capture-check", "pipechina-browser-capture"],
    )

    cli.main()

    assert captured == {
        "path": tmp_path / "captures/pipechina.json",
        "allowed_hosts": ["zhaopin.pipechina.com.cn"],
        "max_age_hours": 24.0,
        "require_complete_scan": True,
        "require_capture_manifest": True,
    }
    assert '"source_type": "official_browser_rows"' in capsys.readouterr().out


def test_browser_capture_check_accepts_iguopin_general_browser_source(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    class FakeSettings:
        data_dir = tmp_path

    class FakeDatabase:
        def get_source(self, source_id: str):
            assert source_id == "iguopin-general-geoscience-browser"
            return {
                "id": source_id,
                "source_type": "iguopin_general_browser_rows",
                "config": {
                    "capture_path": "captures/iguopin.json",
                    "allowed_hosts": ["www.iguopin.com", "iguopin.com"],
                    "max_age_hours": 30,
                    "require_complete_scan": True,
                    "require_capture_manifest": False,
                },
            }

    monkeypatch.setattr(
        cli, "services", lambda: (FakeSettings(), FakeDatabase(), object())
    )
    captured: dict[str, object] = {}

    def fake_load(path: Path, **kwargs: object) -> dict[str, object]:
        captured["path"] = path
        captured.update(kwargs)
        return {
            "captured_at": "2026-10-13T12:42:32Z",
            "capture_age_hours": 1.0,
            "scan": {
                "pages_scanned": 302,
                "pagination_complete": True,
                "rows_discovered": 1593,
                "rows_exported": 1593,
                "detail_discovered": 1980,
                "detail_succeeded": 1980,
                "detail_failed": 0,
            },
            "rows": [{}] * 1593,
        }

    monkeypatch.setattr(cli, "load_iguopin_browser_capture", fake_load)
    monkeypatch.setattr(
        sys,
        "argv",
        ["job_hub.cli", "browser-capture-check", "iguopin-general-geoscience-browser"],
    )

    cli.main()

    assert captured == {
        "path": tmp_path / "captures/iguopin.json",
        "allowed_hosts": ["www.iguopin.com", "iguopin.com"],
        "max_age_hours": 30.0,
        "require_complete_scan": True,
        "require_capture_manifest": False,
    }
    output = capsys.readouterr().out
    assert '"source_type": "iguopin_general_browser_rows"' in output
    assert '"rows": 1593' in output
