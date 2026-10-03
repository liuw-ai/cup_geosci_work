from __future__ import annotations

import os
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from job_hub.backups import BackupError, DatabaseBackupManager
from job_hub.app import create_app
import job_hub.cli as cli
from job_hub.db import Database
from job_hub.domain_probe import DomainProbeResult
from job_hub.operations import (
    browser_worker_health,
    build_production_readiness,
    link_health_validation,
    release_configuration,
    worker_health,
)
from job_hub.worker import DailyWorker

from conftest import make_settings, source


def _settings(tmp_path: Path):
    return replace(
        make_settings(tmp_path),
        backup_storage_dir=Path("private-backups"),
        backup_retention_days=14,
        backup_min_interval_minutes=60,
    )


def test_release_configuration_requires_one_pinned_application_base_and_browser_image(
    tmp_path, monkeypatch
) -> None:
    settings = replace(
        make_settings(tmp_path),
        release_version="release-2026-10-04",
        release_git_sha="abcdef123456",
        release_image_sha="sha256:application",
        release_built_at="2026-10-04T00:00:00Z",
    )
    monkeypatch.setenv("JOB_HUB_IMAGE", "registry.example/job-hub:release-2026-10-04")
    monkeypatch.setenv(
        "JOB_HUB_BROWSER_IMAGE", "registry.example/job-hub-browser:release-2026-10-04"
    )
    monkeypatch.setenv("RUNTIME_IMAGE", "registry.example/job-hub:release-2026-10-04")

    result = release_configuration(settings)

    assert result["ok"] is True
    assert result["issues"] == []


def test_release_configuration_rejects_latest_and_mismatched_browser_base(
    tmp_path, monkeypatch
) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setenv("JOB_HUB_IMAGE", "cupb-geoscience-job-hub:latest")
    monkeypatch.setenv("JOB_HUB_BROWSER_IMAGE", "cupb-geoscience-job-hub-browser:latest")
    monkeypatch.setenv("RUNTIME_IMAGE", "cupb-geoscience-job-hub:other")

    result = release_configuration(settings)

    assert result["ok"] is False
    assert any("latest" in issue for issue in result["issues"])
    assert any("runtime_base" in issue for issue in result["issues"])


def test_sqlite_backup_is_verified_private_and_created_only_when_due(tmp_path) -> None:
    settings = _settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    database.upsert_source(source())
    manager = DatabaseBackupManager(settings)
    moment = datetime(2026, 9, 29, 4, 0, tzinfo=timezone.utc)

    result = manager.create_backup(now=moment)
    os.utime(Path(result.path), (moment.timestamp(), moment.timestamp()))

    assert result.verification.valid is True
    assert Path(result.path).parent == (tmp_path / "private-backups").resolve()
    assert result.sha256
    assert manager.backup_if_due(now=moment + timedelta(minutes=30)) is None
    assert manager.backup_if_due(now=moment + timedelta(minutes=61)) is not None
    status = manager.latest_status(max_age_seconds=4_000, now=moment + timedelta(minutes=61))
    assert status.verification is not None
    assert status.verification.valid is True


def test_backup_verification_rejects_non_sqlite_or_missing_application_schema(tmp_path) -> None:
    settings = _settings(tmp_path)
    manager = DatabaseBackupManager(settings)
    path = tmp_path / "not-a-backup.sqlite3"
    path.write_text("not a sqlite database", encoding="utf-8")

    result = manager.verify_path(path)

    assert result.valid is False
    assert result.integrity == "unreadable"


def test_backup_retention_prunes_only_managed_snapshot_names(tmp_path) -> None:
    settings = replace(_settings(tmp_path), backup_retention_days=2)
    database = Database(settings.database_path)
    database.initialize()
    manager = DatabaseBackupManager(settings)
    manager.backup_dir.mkdir(parents=True, exist_ok=True)
    expired_managed = manager.backup_dir / "job_hub-expired.sqlite3"
    unrelated_file = manager.backup_dir / "operator-notes.sqlite3"
    expired_managed.write_bytes(b"expired managed snapshot")
    unrelated_file.write_bytes(b"must remain untouched")
    old = (datetime.now(timezone.utc) - timedelta(days=3)).timestamp()
    os.utime(expired_managed, (old, old))
    os.utime(unrelated_file, (old, old))

    result = manager.create_backup()

    assert str(expired_managed) in result.pruned_files
    assert expired_managed.exists() is False
    assert unrelated_file.exists() is True


def test_restore_requires_confirm_and_recovers_previous_consistent_snapshot(tmp_path) -> None:
    settings = _settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    original = source()
    database.upsert_source(original)
    manager = DatabaseBackupManager(settings)
    snapshot = manager.create_backup()

    extra = source()
    extra["id"] = "later-source"
    extra["name"] = "稍后错误加入的来源"
    database.upsert_source(extra)
    assert database.get_source("later-source") is not None

    with pytest.raises(BackupError, match="explicit confirmation"):
        manager.restore_backup(Path(snapshot.path))
    restored = manager.restore_backup(Path(snapshot.path), confirm=True)

    reloaded = Database(settings.database_path)
    reloaded.initialize()
    assert restored.verification.valid is True
    assert reloaded.get_source(original["id"]) is not None
    assert reloaded.get_source("later-source") is None
    assert restored.emergency_backup_path is not None


def test_restore_accepts_only_private_managed_backups(tmp_path) -> None:
    settings = _settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    manager = DatabaseBackupManager(settings)
    external = tmp_path / "external.sqlite3"
    external.write_bytes(b"not a backup")

    with pytest.raises(BackupError, match="BACKUP_STORAGE_DIR"):
        manager.restore_backup(external, confirm=True)


def test_production_readiness_requires_audit_worker_and_verified_backup(tmp_path) -> None:
    settings = _settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    database.upsert_source(source())
    database.record_service_heartbeat("worker", "running", "test")
    DatabaseBackupManager(settings).create_backup()

    def domain_probe(*_args, **_kwargs):
        return DomainProbeResult(
            hostname="jobs.cupdky.cn",
            expected_ip="81.70.62.174",
            health_url="https://jobs.cupdky.cn/healthz",
            resolved_addresses=("81.70.62.174",),
            dns_status="ok",
            https_status="ok",
            http_status_code=200,
            detail=None,
            checked_at="2026-09-29T04:00:00Z",
        )

    result = build_production_readiness(
        settings,
        database,
        domain_hostname="jobs.cupdky.cn",
        expected_ip="81.70.62.174",
        domain_probe=domain_probe,
    )

    assert result["internal_ready"] is True
    assert result["public_ready"] is True
    assert result["backup"]["ok"] is True


def test_link_health_validation_is_opt_in_and_requires_fresh_valid_report(tmp_path) -> None:
    settings = replace(make_settings(tmp_path), link_health_enabled=True)
    reference = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)

    missing = link_health_validation(settings, now=reference)
    assert missing["enabled"] is True
    assert missing["ok"] is False

    report_dir = tmp_path / "link-health"
    report_dir.mkdir()
    (report_dir / "2026-10-02.json").write_text(
        '{"observed_on":"2026-10-02","generated_at":"2026-10-02T11:00:00Z",'
        '"checked":2,"status_counts":{"reachable":2}}',
        encoding="utf-8",
    )
    fresh = link_health_validation(settings, now=reference)
    assert fresh["ok"] is True
    assert fresh["checked"] == 2

    stale = link_health_validation(
        settings,
        now=reference.replace(day=4),
        max_age_seconds=90_000,
    )
    assert stale["ok"] is False

    disabled = link_health_validation(
        replace(settings, link_health_enabled=False), now=reference
    )
    assert disabled["enabled"] is False
    assert disabled["ok"] is True


def test_browser_worker_health_fails_when_enabled_capture_has_no_heartbeat(tmp_path) -> None:
    settings = _settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    browser_source = source()
    browser_source.update(
        {
            "id": "cnooc-career-browser",
            "source_type": "cnooc_browser_rows",
            "name": "测试浏览器来源",
        }
    )
    database.upsert_source(browser_source)

    result = browser_worker_health(database)

    assert result["required"] is True
    assert result["ok"] is False
    assert result["workers"]["cnooc-browser"]["source_id"] == "cnooc-career-browser"


def test_browser_worker_health_accepts_recent_capture_heartbeat(tmp_path) -> None:
    settings = _settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    browser_source = source()
    browser_source.update(
        {
            "id": "cnooc-career-browser",
            "source_type": "cnooc_browser_rows",
            "name": "测试浏览器来源",
        }
    )
    database.upsert_source(browser_source)
    database.record_service_heartbeat("cnooc-browser", "running", "captured")

    result = browser_worker_health(database)

    assert result["ok"] is True
    assert result["workers"]["cnooc-browser"]["age_seconds"] < 10


def test_browser_worker_access_limit_does_not_block_other_sources(tmp_path) -> None:
    settings = _settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    browser_source = source()
    browser_source.update(
        {
            "id": "sinopec-career",
            "source_type": "sinopec_spa_rows",
            "name": "测试受限浏览器来源",
            "enabled": False,
            "config": {
                "runtime_mode": "browser_worker_only",
                "snapshot_path": "data/verified/sinopec.json",
                "official_evidence_url": "https://job.sinopec.com/",
                "application_url": "https://job.sinopec.com/",
                "allowed_hosts": ["job.sinopec.com"],
                "enterprise_total": 0,
                "candidate_enterprise_total": 0,
            },
        }
    )
    database.upsert_source(browser_source)
    database.record_service_heartbeat(
        "sinopec-browser", "degraded", "robots.txt returned HTTP 403"
    )

    result = browser_worker_health(database)

    assert result["ok"] is False
    assert result["release_ok"] is True
    assert result["expected_access_limited"] == ["sinopec-browser"]
    assert result["workers"]["sinopec-browser"]["expected_access_limited"] is True


def test_browser_worker_maintenance_window_does_not_block_other_sources(tmp_path) -> None:
    settings = _settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    browser_source = source()
    browser_source.update(
        {
            "id": "cnpc-career-browser",
            "source_type": "cnpc_browser_rows",
            "name": "测试维护窗口来源",
            "config": {
                "capture_path": "verified/cnpc.json",
                "application_url": "https://zhaopin.cnpc.com.cn/",
                "allowed_hosts": ["zhaopin.cnpc.com.cn"],
            },
        }
    )
    database.upsert_source(browser_source)
    database.record_service_heartbeat(
        "cnpc-browser", "waiting", "CNPC maintenance window 00:00-06:00"
    )

    result = browser_worker_health(database)

    assert result["ok"] is False
    assert result["release_ok"] is True
    assert result["expected_access_limited"] == ["cnpc-browser"]
    assert result["workers"]["cnpc-browser"]["expected_access_limited"] is True


def test_browser_worker_tls_failure_is_an_expected_access_limitation(tmp_path) -> None:
    settings = _settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    browser_source = source()
    browser_source.update(
        {
            "id": "sinopec-career",
            "source_type": "sinopec_spa_rows",
            "name": "测试证书链受限来源",
            "enabled": False,
            "config": {
                "runtime_mode": "browser_worker_only",
                "snapshot_path": "data/verified/sinopec.json",
                "official_evidence_url": "https://job.sinopec.com/",
                "application_url": "https://job.sinopec.com/",
                "allowed_hosts": ["job.sinopec.com"],
                "enterprise_total": 0,
                "candidate_enterprise_total": 0,
            },
        }
    )
    database.upsert_source(browser_source)
    database.record_service_heartbeat(
        "sinopec-browser",
        "degraded",
        "robots.txt cannot be verified: SSLCertVerificationError: certificate verify failed",
    )

    result = browser_worker_health(database)

    assert result["ok"] is False
    assert result["release_ok"] is True
    assert result["expected_access_limited"] == ["sinopec-browser"]


def test_health_endpoint_reports_backup_state_without_private_path(tmp_path) -> None:
    settings = _settings(tmp_path)
    app = create_app(settings)
    DatabaseBackupManager(settings).create_backup()

    payload = app.test_client().get("/healthz").get_json()

    assert payload["backup"]["ok"] is True
    assert "path" not in payload["backup"]


def test_backup_command_creates_verified_snapshot(tmp_path, monkeypatch, capsys) -> None:
    settings = _settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    monkeypatch.setattr(cli.Settings, "from_env", classmethod(lambda _cls: settings))
    monkeypatch.setattr(
        "sys.argv", ["job_hub.cli", "backup-database", "--force"]
    )

    cli.main()

    output = capsys.readouterr().out
    assert '"ok": true' in output
    assert '"created": true' in output


def test_worker_does_not_mutate_sources_when_required_pre_sync_backup_fails(
    tmp_path, monkeypatch
) -> None:
    worker = DailyWorker(_settings(tmp_path))

    def failed_backup():
        raise BackupError("disk full")

    def should_not_sync(*_args, **_kwargs):
        raise AssertionError("source synchronization must not start after backup failure")

    monkeypatch.setattr(worker.backups, "backup_if_due", failed_backup)
    monkeypatch.setattr(worker.pipeline, "sync_all", should_not_sync)

    worker._sync_with_alert()

    heartbeat = worker.database.get_service_heartbeat("worker")
    assert heartbeat is not None
    assert heartbeat["status"] == "degraded"
    assert "backup failed" in heartbeat["detail"]
    assert worker_health(worker.database, 180)["ok"] is False
