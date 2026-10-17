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
    browser_service_health,
    browser_worker_health,
    build_production_readiness,
    government_position_readiness,
    link_health_validation,
    release_configuration,
    source_freshness_readiness,
    worker_health,
)
from job_hub.worker import DailyWorker
import job_hub.worker as worker_module

from conftest import make_settings, source


APP_DIGEST = "sha256:" + "a" * 64
BROWSER_DIGEST = "sha256:" + "b" * 64
DEPS_DIGEST = "sha256:" + "c" * 64
SHELL_DIGEST = "sha256:" + "d" * 64


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
        release_image_sha=APP_DIGEST,
        release_built_at="2026-10-04T00:00:00Z",
    )
    monkeypatch.setenv("JOB_HUB_IMAGE", f"registry.example/job-hub@{APP_DIGEST}")
    monkeypatch.setenv(
        "JOB_HUB_BROWSER_IMAGE", f"registry.example/job-hub-browser@{BROWSER_DIGEST}"
    )
    monkeypatch.setenv("RUNTIME_IMAGE", f"registry.example/job-hub@{APP_DIGEST}")
    monkeypatch.setenv(
        "BROWSER_DEPS_IMAGE", f"registry.example/job-hub-browser-deps@{DEPS_DIGEST}"
    )
    monkeypatch.setenv(
        "HEADLESS_SHELL_IMAGE",
        f"chromedp/headless-shell@{SHELL_DIGEST}",
    )

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
    monkeypatch.setenv("BROWSER_DEPS_IMAGE", "cupb-geoscience-job-hub-browser:latest")
    monkeypatch.setenv("HEADLESS_SHELL_IMAGE", "chromedp/headless-shell:latest")

    result = release_configuration(settings)

    assert result["ok"] is False
    assert any("sha256 digest" in issue for issue in result["issues"])
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


def test_production_readiness_requires_audit_worker_and_verified_backup(
    tmp_path, monkeypatch
) -> None:
    settings = replace(
        _settings(tmp_path),
        release_version="release-2026-10-09",
        release_git_sha="abcdef123456",
        release_image_sha=APP_DIGEST,
        release_built_at="2026-10-09T00:00:00Z",
    )
    monkeypatch.setenv("JOB_HUB_IMAGE", f"registry.example/job-hub@{APP_DIGEST}")
    monkeypatch.setenv(
        "JOB_HUB_BROWSER_IMAGE", f"registry.example/job-hub-browser@{BROWSER_DIGEST}"
    )
    monkeypatch.setenv("RUNTIME_IMAGE", f"registry.example/job-hub@{APP_DIGEST}")
    monkeypatch.setenv(
        "BROWSER_DEPS_IMAGE", f"registry.example/job-hub-browser-deps@{DEPS_DIGEST}"
    )
    monkeypatch.setenv(
        "HEADLESS_SHELL_IMAGE",
        f"chromedp/headless-shell@{SHELL_DIGEST}",
    )
    database = Database(settings.database_path)
    database.initialize()
    database.upsert_source(source())
    database.record_service_heartbeat("worker", "running", "test")
    database.save_coverage_snapshot(
        "2026-10-02",
        {
            "open_jobs": 1,
            "profile_match_quality": {"cohort_summary": {}},
            "scan_quality": {},
            "job_distribution": {},
        },
    )
    database.save_daily_report(
        "2026-10-02",
        {"stats": {}},
        delivery_status="skipped",
    )
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
    assert result["release"]["ok"] is True
    assert result["backup"]["ok"] is True
    assert result["continuous_validation"]["ok"] is True


def test_production_readiness_fails_for_unpinned_release_even_when_runtime_is_healthy(
    tmp_path, monkeypatch
) -> None:
    settings = _settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    database.upsert_source(source())
    database.record_service_heartbeat("worker", "running", "test")
    DatabaseBackupManager(settings).create_backup()
    monkeypatch.setenv("JOB_HUB_IMAGE", "cupb-geoscience-job-hub:latest")
    monkeypatch.setenv("JOB_HUB_BROWSER_IMAGE", "cupb-geoscience-job-hub-browser:latest")
    monkeypatch.setenv("RUNTIME_IMAGE", "cupb-geoscience-job-hub:latest")
    monkeypatch.setenv("HEADLESS_SHELL_IMAGE", "chromedp/headless-shell:latest")

    result = build_production_readiness(
        settings,
        database,
        require_continuous_validation=False,
    )

    assert result["release"]["ok"] is False
    assert result["worker"]["ok"] is True
    assert result["backup"]["ok"] is True
    assert result["internal_ready"] is False


def test_production_readiness_fails_without_recent_daily_artifacts(tmp_path) -> None:
    settings = _settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    database.upsert_source(source())
    database.record_service_heartbeat("worker", "running", "test")
    DatabaseBackupManager(settings).create_backup()

    result = build_production_readiness(settings, database)

    assert result["continuous_validation"]["ok"] is False
    assert result["internal_ready"] is False


def _write_government_registry(path: Path, *, as_of: str) -> None:
    path.write_text(
        '{"version":1,"as_of":"%s","source_assessments":[],"records":[]}'
        % as_of,
        encoding="utf-8",
    )


def test_government_position_readiness_fails_closed_for_stale_registry(tmp_path) -> None:
    settings = replace(
        _settings(tmp_path),
        government_position_registry_path=tmp_path / "government.json",
        government_position_max_age_hours=48,
    )
    _write_government_registry(
        settings.government_position_registry_path,
        as_of="2026-09-30",
    )
    database = Database(settings.database_path)
    database.initialize()

    result = government_position_readiness(
        settings,
        database,
        now=datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc),
    )

    assert result["enabled"] is True
    assert result["ok"] is False
    assert result["report"]["registry_freshness"] == "stale"
    assert "新鲜度" in result["message"]


def test_government_position_readiness_accepts_fresh_empty_registry(tmp_path) -> None:
    settings = replace(
        _settings(tmp_path),
        government_position_registry_path=tmp_path / "government.json",
        government_position_max_age_hours=48,
    )
    _write_government_registry(
        settings.government_position_registry_path,
        as_of="2026-10-03",
    )
    database = Database(settings.database_path)
    database.initialize()

    result = government_position_readiness(
        settings,
        database,
        now=datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc),
    )

    assert result["enabled"] is True
    assert result["ok"] is True
    assert result["report"]["registry_freshness"] == "fresh"


def test_source_freshness_readiness_requires_contract_for_enabled_live_source(tmp_path) -> None:
    settings = _settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    live_source = source()
    live_source.update({"source_type": "html_notice"})
    database.upsert_source(live_source)

    result = source_freshness_readiness(settings, database)

    assert result["ok"] is False
    assert result["missing_contract_sources"] == ["official-test-source"]


def test_source_freshness_readiness_accepts_materialized_contract(tmp_path) -> None:
    settings = _settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    live_source = source()
    live_source.update(
        {
            "source_type": "html_notice",
            "config": {"minimum_relevance": 0, "max_age_hours": 72},
        }
    )
    database.upsert_source(live_source)
    database.record_source_capture_freshness(
        "official-test-source",
        captured_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        evidence_kind="live_sync",
    )

    result = source_freshness_readiness(settings, database)

    assert result["ok"] is True
    assert result["checked_sources"] == 1
    assert result["missing_contract_sources"] == []


def test_source_freshness_readiness_rejects_stale_capture(tmp_path) -> None:
    settings = _settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    live_source = source()
    live_source.update(
        {
            "source_type": "html_notice",
            "config": {"minimum_relevance": 0, "max_age_hours": 24},
        }
    )
    database.upsert_source(live_source)
    database.record_source_capture_freshness(
        "official-test-source",
        captured_at=(datetime.now(timezone.utc) - timedelta(hours=25))
        .isoformat()
        .replace("+00:00", "Z"),
        evidence_kind="live_sync",
    )

    result = source_freshness_readiness(settings, database)

    assert result["ok"] is False
    assert result["stale_capture_sources"] == ["official-test-source"]


def test_source_freshness_readiness_rejects_zero_window(tmp_path) -> None:
    settings = _settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    live_source = source()
    live_source.update(
        {
            "source_type": "html_notice",
            "config": {"minimum_relevance": 0, "max_age_hours": 0},
        }
    )
    database.upsert_source(live_source)

    result = source_freshness_readiness(settings, database)

    assert result["ok"] is False
    assert result["invalid_contract_sources"] == ["official-test-source"]


def test_source_freshness_readiness_rejects_unproven_static_capture(tmp_path) -> None:
    settings = _settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    static_source = source()
    static_source.update(
        {
            "source_type": "official_xlsx_rows",
            "config": {
                "minimum_relevance": 0,
                "max_age_hours": 72,
                "notice_url": "https://careers.example.edu.cn/notices/xlsx",
                "attachment_url": "https://careers.example.edu.cn/notices/xlsx/jobs.xlsx",
                "sha256": "c" * 64,
                "verified_rows": [2],
                "allowed_hosts": ["careers.example.edu.cn"],
            },
        }
    )
    database.upsert_source(static_source)

    result = source_freshness_readiness(settings, database)

    assert result["ok"] is False
    assert result["freshness_unproven_sources"] == ["official-test-source"]

    database.record_source_capture_freshness(
        "official-test-source",
        captured_at=(datetime.now(timezone.utc) - timedelta(hours=1))
        .isoformat()
        .replace("+00:00", "Z"),
        evidence_kind="capture_manifest",
    )
    refreshed = source_freshness_readiness(settings, database)
    assert refreshed["ok"] is True
    assert refreshed["freshness_unproven_sources"] == []


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
    assert result["workers"]["cnooc-browser"]["policy_consistent"] is True


def test_browser_worker_health_rejects_stale_policy_code(tmp_path) -> None:
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
    with database.transaction() as connection:
        connection.execute(
            "UPDATE service_heartbeats SET detail = ? WHERE service_name = ?",
            ("policy_sha256=" + "0" * 64 + "; captured", "cnooc-browser"),
        )

    result = browser_worker_health(database)

    assert result["ok"] is False
    assert result["release_ok"] is False
    assert result["workers"]["cnooc-browser"]["policy_consistent"] is False


def test_browser_worker_health_rejects_legacy_heartbeat_without_policy_digest(tmp_path) -> None:
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
    with database.transaction() as connection:
        connection.execute(
            "UPDATE service_heartbeats SET detail = ? WHERE service_name = ?",
            ("captured by an older browser image", "cnooc-browser"),
        )

    result = browser_worker_health(database)

    assert result["ok"] is False
    assert result["release_ok"] is False
    assert result["workers"]["cnooc-browser"]["policy_digest"] is None


def test_browser_worker_health_accepts_waiting_for_capture_capacity(tmp_path) -> None:
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
    database.record_service_heartbeat(
        "cnooc-browser", "waiting", "waiting for browser capture capacity"
    )

    result = browser_worker_health(database)

    assert result["ok"] is True
    assert result["release_ok"] is True


def test_browser_worker_health_covers_both_enabled_iguopin_workers(tmp_path) -> None:
    settings = _settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    employer_portal = source()
    employer_portal.update(
        {
            "id": "chinalco-iguopin-browser",
            "source_type": "iguopin_browser_rows",
            "name": "测试中铝国聘来源",
            "config": {
                "minimum_relevance": 0,
                "browser_url": "https://chinalco.iguopin.example/job",
                "application_url": "https://chinalco.iguopin.example/job",
                "capture_path": "captures/chinalco-iguopin-browser.json",
                "allowed_hosts": ["chinalco.iguopin.example"],
                "major_filters": [{"parent": "理学", "child": "地质学类"}],
                "external_id_prefix": "chinalco-iguopin",
                "max_age_hours": 30,
                "require_complete_scan": True,
                "require_capture_manifest": True,
            },
        }
    )
    general_portal = source()
    general_portal.update(
        {
            "id": "iguopin-general-geoscience-browser",
            "source_type": "iguopin_general_browser_rows",
            "name": "测试国聘主站来源",
            "config": {
                "minimum_relevance": 0,
                "browser_url": "https://www.iguopin.example/job",
                "application_url": "https://www.iguopin.example/job",
                "capture_path": "captures/iguopin-general-geoscience-browser.json",
                "allowed_hosts": ["www.iguopin.example"],
                "search_keywords": ["地质"],
                "card_identity_mode": "rendered_react_job_id",
                "external_id_prefix": "iguopin-general",
                "max_pages_per_keyword": 20,
                "max_age_hours": 30,
                "require_complete_scan": True,
                "require_capture_manifest": True,
            },
        }
    )
    database.upsert_source(employer_portal)
    database.upsert_source(general_portal)

    missing = browser_worker_health(database)

    assert missing["ok"] is False
    assert missing["workers"]["chinalco-iguopin-browser"]["source_id"] == (
        "chinalco-iguopin-browser"
    )
    assert missing["workers"]["iguopin-general-geoscience-browser"]["source_id"] == (
        "iguopin-general-geoscience-browser"
    )

    database.record_service_heartbeat("chinalco-iguopin-browser", "running", "captured")
    database.record_service_heartbeat(
        "iguopin-general-geoscience-browser", "running", "captured"
    )
    healthy = browser_worker_health(database)

    assert healthy["ok"] is True


def test_browser_worker_health_includes_cupb_snapshot_worker(tmp_path) -> None:
    settings = _settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    browser_source = source()
    browser_source.update(
        {
            "id": "cupb-career-browser-20261007",
            "source_type": "official_snapshot_rows",
            "name": "测试 CUPB 浏览器来源",
            "config": {
                "capture_worker": "cupb_browser_worker",
                "background_capture_enabled": True,
                "runtime_mode": "browser_worker_only",
                "snapshot_path": "verified/cupb.json",
                "official_evidence_url": "https://career.cup.edu.cn/campus",
                "application_url": "https://career.cup.edu.cn/campus",
                "allowed_hosts": ["career.cup.edu.cn"],
            },
        }
    )
    database.upsert_source(browser_source)
    database.record_service_heartbeat("cupb-browser", "running", "captured")

    result = browser_worker_health(database)

    assert result["ok"] is True
    assert result["workers"]["cupb-browser"]["source_id"] == "cupb-career-browser-20261007"


def test_browser_service_health_accepts_fresh_degraded_worker(tmp_path) -> None:
    settings = _settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    database.record_service_heartbeat(
        "cnooc-browser", "degraded", "robots.txt returned HTTP 403"
    )

    result = browser_service_health(database, "cnooc-browser")

    assert result["ok"] is True
    assert result["status"] == "degraded"
    assert result["age_seconds"] < 10


def test_browser_service_health_rejects_stale_heartbeat(tmp_path) -> None:
    settings = _settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    database.record_service_heartbeat("cnooc-browser", "running", "captured")
    old = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
    with database.connect() as connection:
        connection.execute(
            "UPDATE service_heartbeats SET updated_at = ? WHERE service_name = ?",
            (old, "cnooc-browser"),
        )
        connection.commit()

    result = browser_service_health(database, "cnooc-browser")

    assert result["ok"] is False
    assert result["age_seconds"] >= 18_000


def test_browser_service_health_rejects_missing_service_name(tmp_path) -> None:
    settings = _settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()

    result = browser_service_health(database, "")

    assert result["ok"] is False


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
                "background_capture_enabled": True,
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


def test_disabled_background_browser_capture_requires_a_heartbeat(tmp_path) -> None:
    settings = _settings(tmp_path)
    database = Database(settings.database_path)
    database.initialize()
    browser_source = source()
    browser_source.update(
        {
            "id": "sinopec-career",
            "source_type": "sinopec_spa_rows",
            "name": "测试后台浏览器来源",
            "enabled": False,
            "config": {
                "runtime_mode": "browser_worker_only",
                "background_capture_enabled": True,
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

    result = browser_worker_health(database)

    assert result["required"] is True
    assert result["ok"] is False
    assert result["workers"]["sinopec-browser"]["source_id"] == "sinopec-career"


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
                "background_capture_enabled": True,
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
    monkeypatch.setattr(worker_module.time, "monotonic", lambda: 1000.0)

    worker._sync_with_alert()

    heartbeat = worker.database.get_service_heartbeat("worker")
    assert heartbeat is not None
    assert heartbeat["status"] == "degraded"
    assert "backup failed" in heartbeat["detail"]
    assert worker_health(worker.database, 180)["ok"] is False
    assert worker._last_error_detail == "database backup failed before source synchronization"
    assert worker.last_sync_monotonic == 1000.0 - (180 * 60 - 60)


def test_worker_sync_failure_retries_before_full_interval_and_stays_degraded(
    tmp_path, monkeypatch
) -> None:
    worker = DailyWorker(_settings(tmp_path))

    monkeypatch.setattr(worker.backups, "backup_if_due", lambda: None)

    def failed_sync(*_args, **_kwargs):
        raise RuntimeError("temporary parser failure")

    monkeypatch.setattr(worker.pipeline, "sync_all", failed_sync)
    monkeypatch.setattr(worker_module.time, "monotonic", lambda: 2000.0)

    worker._sync_with_alert()

    assert worker._last_error_detail == "source synchronization failed"
    assert worker.last_sync_monotonic == 2000.0 - (180 * 60 - 60)
    heartbeat = worker.database.get_service_heartbeat("worker")
    assert heartbeat is not None
    assert heartbeat["status"] == "degraded"
