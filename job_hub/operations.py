"""Read-only production-readiness checks shared by CLI and HTTP health views."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from job_hub.audit import audit_database
from job_hub.backups import BackupStatus, DatabaseBackupManager
from job_hub.config import Settings
from job_hub.db import Database
from job_hub.domain_probe import DomainProbeResult, probe_public_domain


_BROWSER_SERVICE_BY_SOURCE_TYPE = {
    "cnpc_browser_rows": "cnpc-browser",
    "cnooc_browser_rows": "cnooc-browser",
    "cmgb_browser_rows": "cmgb-browser",
    "official_browser_rows": "pipechina-browser",
    "sinopec_spa_rows": "sinopec-browser",
}

_EXPECTED_BROWSER_LIMITATION_MARKERS = (
    "robots.txt returned http 403",
    "robots.txt returned http 412",
    "access-limited",
    "access limited",
    "listing returned http 400",
    "listing returned http 412",
)


def worker_health(database: Database, max_age_seconds: int) -> dict[str, Any]:
    """Return the Worker liveness contract used by Docker and readiness checks."""
    maximum = max(1, int(max_age_seconds))
    heartbeat = database.get_service_heartbeat("worker")
    if heartbeat is None:
        return {
            "ok": False,
            "message": "尚未收到 worker 心跳。",
            "max_age_seconds": maximum,
        }
    try:
        updated_at = datetime.fromisoformat(
            str(heartbeat["updated_at"]).replace("Z", "+00:00")
        )
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        age_seconds = max(
            0,
            int((datetime.now(timezone.utc) - updated_at).total_seconds()),
        )
    except (TypeError, ValueError):
        return {
            "ok": False,
            "message": "worker 心跳时间格式无效。",
            "heartbeat": heartbeat,
            "max_age_seconds": maximum,
        }
    healthy_statuses = {"starting", "running", "syncing", "publishing", "delivering"}
    return {
        "ok": age_seconds <= maximum and heartbeat["status"] in healthy_statuses,
        "age_seconds": age_seconds,
        "max_age_seconds": maximum,
        "heartbeat": heartbeat,
    }


def browser_worker_health(
    database: Database,
    max_age_seconds: int = 14_400,
) -> dict[str, Any]:
    """Check enabled browser capture workers separately from the main worker.

    Browser captures run in isolated containers and may be blocked or stuck
    while the ordinary source worker remains healthy.  A missing or stale
    browser heartbeat must therefore fail the internal release gate instead
    of silently presenting an old dynamic snapshot as continuously refreshed.
    The four-hour default covers the configured three-hour capture interval
    plus startup and scan overhead.
    """

    required: dict[str, str] = {}
    for source in database.list_sources():
        config = source.get("config") if isinstance(source.get("config"), dict) else {}
        worker_only = str(config.get("runtime_mode") or "").strip() == "browser_worker_only"
        if not source.get("enabled") and not worker_only:
            continue
        service_name = _BROWSER_SERVICE_BY_SOURCE_TYPE.get(
            str(source.get("source_type") or "")
        )
        if service_name:
            required[service_name] = str(source.get("id") or service_name)
    if not required:
        return {"ok": True, "required": False, "workers": {}}

    maximum = max(1, int(max_age_seconds))
    healthy_statuses = {"starting", "running", "capturing"}
    workers: dict[str, dict[str, Any]] = {}
    overall_ok = True
    release_ok = True
    expected_limitations: list[str] = []
    for service_name, source_id in sorted(required.items()):
        heartbeat = database.get_service_heartbeat(service_name)
        item: dict[str, Any] = {
            "source_id": source_id,
            "ok": False,
            "max_age_seconds": maximum,
        }
        if heartbeat is None:
            item["message"] = "尚未收到浏览器 worker 心跳。"
            overall_ok = False
            release_ok = False
            workers[service_name] = item
            continue
        item["heartbeat"] = heartbeat
        try:
            updated_at = datetime.fromisoformat(
                str(heartbeat["updated_at"]).replace("Z", "+00:00")
            )
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)
            age_seconds = max(
                0,
                int((datetime.now(timezone.utc) - updated_at).total_seconds()),
            )
        except (KeyError, TypeError, ValueError):
            item["message"] = "浏览器 worker 心跳时间格式无效。"
            overall_ok = False
            release_ok = False
            workers[service_name] = item
            continue
        item["age_seconds"] = age_seconds
        item["ok"] = age_seconds <= maximum and heartbeat.get("status") in healthy_statuses
        if not item["ok"]:
            overall_ok = False
            detail = str(heartbeat.get("detail") or "").strip().lower()
            expected = any(marker in detail for marker in _EXPECTED_BROWSER_LIMITATION_MARKERS)
            item["expected_access_limited"] = expected
            if expected:
                expected_limitations.append(service_name)
            else:
                release_ok = False
        workers[service_name] = item
    return {
        "ok": overall_ok,
        # A policy/robots denial is a source-level limitation, not a broken
        # service. It remains visible through ``ok`` and the worker detail,
        # while ``release_ok`` lets unaffected official sources continue to
        # serve students.
        "release_ok": release_ok,
        "expected_access_limited": sorted(expected_limitations),
        "required": True,
        "workers": workers,
    }


def backup_health_payload(status: BackupStatus) -> dict[str, Any]:
    """Expose backup health without leaking private storage paths over HTTP."""
    return {
        "ok": status.ok,
        "age_seconds": status.age_seconds,
        "max_age_seconds": status.max_age_seconds,
        "verification": (
            {
                "valid": status.verification.valid,
                "integrity": status.verification.integrity,
                "required_tables_present": status.verification.required_tables_present,
            }
            if status.verification
            else None
        ),
    }


def build_production_readiness(
    settings: Settings,
    database: Database,
    *,
    worker_max_age_seconds: int = 180,
    backup_max_age_seconds: int = 86_400,
    domain_hostname: str | None = None,
    expected_ip: str | None = None,
    domain_probe: Callable[..., DomainProbeResult] = probe_public_domain,
) -> dict[str, Any]:
    """Build an auditable release gate without changing jobs or source state.

    A deployment can be internally safe while a public domain still lacks DNS
    or TLS.  The returned `internal_ready` and `public_ready` values keep these
    different conditions explicit, rather than making a failed hostname look
    like a crawler or data-quality failure.
    """
    audit = audit_database(database, settings)
    worker = worker_health(database, worker_max_age_seconds)
    browser_workers = browser_worker_health(database)
    backup = DatabaseBackupManager(settings).latest_status(
        max_age_seconds=backup_max_age_seconds
    )
    domain: dict[str, Any] | None = None
    if domain_hostname:
        domain = domain_probe(
            domain_hostname,
            expected_ip=expected_ip,
        ).as_dict()
    internal_ready = (
        bool(audit.get("ok"))
        and bool(worker.get("ok"))
        and bool(browser_workers.get("release_ok", browser_workers.get("ok")))
        and backup.ok
    )
    public_ready = internal_ready and bool(domain and domain.get("ready"))
    return {
        "internal_ready": internal_ready,
        "public_ready": public_ready,
        "audit": audit,
        "worker": worker,
        "browser_workers": browser_workers,
        "backup": backup.as_dict(),
        "domain": domain,
        "publication_policy": (
            "公网正式发布必须同时通过数据审计、worker 心跳、已验证备份和正式域名 HTTPS；"
            "任何一项失败均不得将系统标记为可正式服务学生。"
        ),
    }
