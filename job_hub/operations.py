"""Read-only production-readiness checks shared by CLI and HTTP health views."""

from __future__ import annotations

import json
import os
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
    "iguopin_general_browser_rows": "iguopin-general-browser",
}

_EXPECTED_BROWSER_LIMITATION_MARKERS = (
    "robots.txt returned http 403",
    "robots.txt returned http 412",
    "robots.txt cannot be verified",
    "certificate_verify_failed",
    "certificate verify failed",
    "maintenance window",
    "maintenance_window",
    "access-limited",
    "access limited",
    "listing returned http 400",
    "listing returned http 412",
)


def release_configuration(
    settings: Settings,
    *,
    require_pinned_images: bool = True,
) -> dict[str, Any]:
    """Validate the release identity required before a production switch.

    This is a configuration gate, not a deployment driver. Web/Worker and
    browser services may use different images, but the browser base must be
    the exact application image selected for the release.
    """
    release = settings.release_info()
    image_refs = {
        "application": os.getenv("JOB_HUB_IMAGE", "").strip(),
        "browser": os.getenv("JOB_HUB_BROWSER_IMAGE", "").strip(),
        "runtime_base": os.getenv("RUNTIME_IMAGE", "").strip(),
    }
    issues: list[str] = []
    if require_pinned_images:
        for key, value in release.items():
            if value in {"", "unknown", "dev"}:
                issues.append(f"release.{key} 未注入生产身份")
        for key, value in image_refs.items():
            if not value:
                issues.append(f"{key} 镜像引用为空")
            elif value.endswith(":latest"):
                issues.append(f"{key} 镜像仍使用 latest")
        if (
            image_refs["application"]
            and image_refs["runtime_base"]
            and image_refs["application"] != image_refs["runtime_base"]
        ):
            issues.append("浏览器 runtime_base 与 application 镜像不一致")
    return {
        "ok": not issues,
        "release": release,
        "images": image_refs,
        "require_pinned_images": require_pinned_images,
        "issues": issues,
    }


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
    require_continuous_validation: bool = False,
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
    continuous = continuous_validation(database)
    link_health = link_health_validation(settings)
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
        and bool(link_health.get("ok"))
        and backup.ok
        and (continuous["ok"] or not require_continuous_validation)
    )
    public_ready = internal_ready and bool(domain and domain.get("ready"))
    return {
        "internal_ready": internal_ready,
        "public_ready": public_ready,
        "audit": audit,
        "worker": worker,
        "browser_workers": browser_workers,
        "link_health": link_health,
        "backup": backup.as_dict(),
        "continuous_validation": continuous,
        "domain": domain,
        "publication_policy": (
            "公网正式发布必须同时通过数据审计、worker 心跳、已验证备份和正式域名 HTTPS；"
            "任何一项失败均不得将系统标记为可正式服务学生。"
        ),
    }


def link_health_validation(
    settings: Settings,
    *,
    max_age_seconds: int = 90_000,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Validate the latest persisted official-link health report.

    The link probe is read-only and independent from publication, but an
    enabled production deployment must still prove that the daily probe
    produced a usable report. A missing or malformed report is an operational
    failure, not evidence that links are broken and not a reason to withdraw
    jobs.
    """
    enabled = bool(getattr(settings, "link_health_enabled", False))
    maximum = max(1, int(max_age_seconds))
    if not enabled:
        return {
            "enabled": False,
            "ok": True,
            "max_age_seconds": maximum,
            "message": "链接健康门禁未启用。",
        }

    root = settings.data_dir / "link-health"
    try:
        candidates = sorted(
            root.glob("*.json"), key=lambda path: path.stat().st_mtime
        )
    except OSError:
        candidates = []
    if not candidates:
        return {
            "enabled": True,
            "ok": False,
            "max_age_seconds": maximum,
            "report_date": None,
            "age_seconds": None,
            "message": "尚未生成官方链接健康报告。",
        }
    report_path = candidates[-1]
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return {
            "enabled": True,
            "ok": False,
            "max_age_seconds": maximum,
            "report_date": report_path.stem,
            "age_seconds": None,
            "message": f"官方链接健康报告无法读取：{error.__class__.__name__}。",
        }
    if not isinstance(payload, dict):
        return {
            "enabled": True,
            "ok": False,
            "max_age_seconds": maximum,
            "report_date": report_path.stem,
            "age_seconds": None,
            "message": "官方链接健康报告不是 JSON 对象。",
        }
    generated_at = str(payload.get("generated_at") or "").strip()
    try:
        observed = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        reference = now or datetime.now(timezone.utc)
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=timezone.utc)
        age_seconds = max(
            0,
            int((reference - observed.astimezone(timezone.utc)).total_seconds()),
        )
    except (TypeError, ValueError):
        return {
            "enabled": True,
            "ok": False,
            "max_age_seconds": maximum,
            "report_date": str(payload.get("observed_on") or report_path.stem),
            "age_seconds": None,
            "message": "官方链接健康报告缺少有效 generated_at。",
        }
    status_counts = payload.get("status_counts")
    checked = payload.get("checked")
    valid_shape = (
        isinstance(status_counts, dict)
        and isinstance(checked, int)
        and checked >= 0
        and all(
            isinstance(value, int) and value >= 0
            for value in status_counts.values()
        )
    )
    ok = age_seconds <= maximum and valid_shape
    return {
        "enabled": True,
        "ok": ok,
        "max_age_seconds": maximum,
        "report_date": str(payload.get("observed_on") or report_path.stem),
        "age_seconds": age_seconds,
        "checked": checked if isinstance(checked, int) else None,
        "status_counts": status_counts if isinstance(status_counts, dict) else {},
        "message": (
            "官方链接健康报告在连续运行窗口内。"
            if ok
            else "官方链接健康报告过期或结构无效；不能将链接可用性视为已验证。"
        ),
    }


def continuous_validation(
    database: Database,
    *,
    max_age_seconds: int = 90_000,
) -> dict[str, Any]:
    """Check that daily observability artifacts are actually being produced.

    This is intentionally separate from worker liveness: a live process that
    never records a coverage snapshot or daily report is not continuously
    validating the service. The check is diagnostic by default; a release
    command can opt into making it a hard gate.
    """
    maximum = max(1, int(max_age_seconds))
    now = datetime.now(timezone.utc)
    snapshot = database.list_coverage_snapshots(limit=1)
    latest_snapshot = snapshot[0] if snapshot else None
    snapshot_age = _age_seconds(latest_snapshot.get("captured_at") if latest_snapshot else None, now)
    report = database.latest_daily_report()
    report_age = _age_seconds(report.get("published_at") if report else None, now)
    report_delivery = str(report.get("delivery_status") or "") if report else "missing"
    snapshot_ok = snapshot_age is not None and snapshot_age <= maximum
    report_ok = report_age is not None and report_age <= maximum and report_delivery in {"sent", "skipped", "pending"}
    result = {
        "ok": bool(snapshot_ok and report_ok),
        "max_age_seconds": maximum,
        "coverage_snapshot": {
            "present": latest_snapshot is not None,
            "age_seconds": snapshot_age,
            "snapshot_date": latest_snapshot.get("snapshot_date") if latest_snapshot else None,
        },
        "daily_report": {
            "present": report is not None,
            "age_seconds": report_age,
            "report_date": report.get("report_date") if report else None,
            "delivery_status": report_delivery,
        },
        "message": (
            "覆盖快照和日报均在连续运行窗口内。"
            if snapshot_ok and report_ok
            else "尚未形成近期覆盖快照和日报；不能把进程存活当成每日更新成功。"
        ),
    }
    return result


def _age_seconds(value: Any, now: datetime) -> int | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0, int((now - parsed.astimezone(timezone.utc)).total_seconds()))
    except (TypeError, ValueError):
        return None
