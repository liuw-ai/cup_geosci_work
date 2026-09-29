"""Read-only production-readiness checks shared by CLI and HTTP health views."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from job_hub.audit import audit_database
from job_hub.backups import BackupStatus, DatabaseBackupManager
from job_hub.config import Settings
from job_hub.db import Database
from job_hub.domain_probe import DomainProbeResult, probe_public_domain


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
    backup = DatabaseBackupManager(settings).latest_status(
        max_age_seconds=backup_max_age_seconds
    )
    domain: dict[str, Any] | None = None
    if domain_hostname:
        domain = domain_probe(
            domain_hostname,
            expected_ip=expected_ip,
        ).as_dict()
    internal_ready = bool(audit.get("ok")) and bool(worker.get("ok")) and backup.ok
    public_ready = internal_ready and bool(domain and domain.get("ready"))
    return {
        "internal_ready": internal_ready,
        "public_ready": public_ready,
        "audit": audit,
        "worker": worker,
        "backup": backup.as_dict(),
        "domain": domain,
        "publication_policy": (
            "公网正式发布必须同时通过数据审计、worker 心跳、已验证备份和正式域名 HTTPS；"
            "任何一项失败均不得将系统标记为可正式服务学生。"
        ),
    }
