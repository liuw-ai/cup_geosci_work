"""Read-only production-readiness checks shared by CLI and HTTP health views."""

from __future__ import annotations

import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from job_hub.audit import audit_database
from job_hub.backups import BackupStatus, DatabaseBackupManager
from job_hub.config import Settings
from job_hub.db import Database
from job_hub.domain_probe import DomainProbeResult, probe_public_domain
from job_hub.government_artifacts import (
    load_government_artifact_manifest,
    project_government_artifact_manifest,
)
from job_hub.government_positions import (
    government_position_quality_report,
    load_position_registry,
)
from job_hub.government_revalidation import (
    requires_manual_government_evidence_confirmation,
)
from job_hub.policy_contract import publication_policy_digest


_BROWSER_SERVICE_BY_SOURCE_TYPE = {
    "cnpc_browser_rows": "cnpc-browser",
    "cnooc_browser_rows": "cnooc-browser",
    "cmgb_browser_rows": "cmgb-browser",
    # The two GuoPin workers use source-id service names in Compose. They
    # still need an explicit mapping here: otherwise their fresh snapshot can
    # be visible while the long-running Chromium process is absent.
    "iguopin_browser_rows": "chinalco-iguopin-browser",
    "iguopin_general_browser_rows": "iguopin-general-geoscience-browser",
    "official_browser_rows": "pipechina-browser",
    "sinopec_spa_rows": "sinopec-browser",
}


def _browser_service_for_source(source: dict[str, Any]) -> str | None:
    """Resolve an isolated browser service, including snapshot adapters."""

    source_type = str(source.get("source_type") or "")
    service_name = _BROWSER_SERVICE_BY_SOURCE_TYPE.get(source_type)
    if service_name:
        return service_name
    config = source.get("config") if isinstance(source.get("config"), dict) else {}
    if str(config.get("capture_worker") or "").strip() == "cupb_browser_worker":
        return "cupb-browser"
    return None

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

_GIT_SHA_PATTERN = re.compile(r"^[0-9a-f]{7,64}$")
_IMAGE_DIGEST_PATTERN = re.compile(r"^.+@(sha256:[0-9a-f]{64})$")
_SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


def _image_digest(reference: str) -> str | None:
    """Return the immutable digest in a container image reference, if present."""

    match = _IMAGE_DIGEST_PATTERN.fullmatch(reference)
    return match.group(1) if match else None


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
        "browser_deps": os.getenv("BROWSER_DEPS_IMAGE", "").strip(),
        "headless_shell": os.getenv("HEADLESS_SHELL_IMAGE", "").strip(),
    }
    issues: list[str] = []
    if require_pinned_images:
        if release["version"] in {"", "unknown", "dev"}:
            issues.append("release.version 未注入生产身份")
        if not _GIT_SHA_PATTERN.fullmatch(release["git_sha"]):
            issues.append("release.git_sha 必须是 Git 提交 SHA")
        if not _SHA256_PATTERN.fullmatch(release["image_sha"]):
            issues.append("release.image_sha 必须是 sha256 内容摘要")
        try:
            built_at = datetime.fromisoformat(release["built_at"].replace("Z", "+00:00"))
            if built_at.tzinfo is None:
                raise ValueError("timezone is required")
        except ValueError:
            issues.append("release.built_at 必须是带时区的 ISO-8601 时间")
        image_digests: dict[str, str] = {}
        for key, value in image_refs.items():
            if not value:
                issues.append(f"{key} 镜像引用为空")
                continue
            digest = _image_digest(value)
            if digest is None:
                issues.append(f"{key} 镜像必须使用不可变 sha256 digest")
                continue
            image_digests[key] = digest
        application_digest = image_digests.get("application")
        if application_digest and application_digest != release["image_sha"]:
            issues.append("release.image_sha 与 application 镜像摘要不一致")
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
        background_capture_enabled = bool(config.get("background_capture_enabled"))
        if not source.get("enabled") and not (worker_only and background_capture_enabled):
            continue
        service_name = _browser_service_for_source(source)
        if service_name:
            required[service_name] = str(source.get("id") or service_name)
    if not required:
        return {"ok": True, "required": False, "workers": {}}

    maximum = max(1, int(max_age_seconds))
    healthy_statuses = {"starting", "running", "capturing"}
    workers: dict[str, dict[str, Any]] = {}
    overall_ok = True
    release_ok = True
    expected_policy_digest = publication_policy_digest()
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
        status = str(heartbeat.get("status") or "").strip().lower()
        detail = str(heartbeat.get("detail") or "").strip().lower()
        policy_match = re.match(r"policy_sha256=([0-9a-f]{64});", detail)
        item["policy_digest"] = policy_match.group(1) if policy_match else None
        item["policy_consistent"] = item["policy_digest"] == expected_policy_digest
        if not item["policy_consistent"]:
            item["message"] = "浏览器 worker 与主 worker 的岗位发布规则版本不一致或缺少版本指纹。"
            release_ok = False
        waiting_for_capacity = (
            status == "waiting" and "waiting for browser capture capacity" in detail
        )
        item["ok"] = item["policy_consistent"] and age_seconds <= maximum and (
            status in healthy_statuses or waiting_for_capacity
        )
        if not item["ok"]:
            overall_ok = False
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


def browser_service_health(
    database: Database,
    service_name: str,
    max_age_seconds: int = 14_400,
) -> dict[str, Any]:
    """Check one browser container's liveness for its Docker healthcheck.

    This is intentionally narrower than :func:`browser_worker_health`, which
    evaluates every enabled browser source for the release gate.  A container
    healthcheck must not fail because a different official portal is blocked;
    it only needs to restart a process whose own heartbeat disappeared or
    became stale.  ``degraded`` and ``idle`` are live states: they preserve
    evidence of an access limitation or disabled source without causing a
    restart loop.
    """

    name = str(service_name or "").strip()
    maximum = max(1, int(max_age_seconds))
    if not name:
        return {
            "ok": False,
            "service_name": name,
            "max_age_seconds": maximum,
            "message": "浏览器 worker service name 不能为空。",
        }
    heartbeat = database.get_service_heartbeat(name)
    if heartbeat is None:
        return {
            "ok": False,
            "service_name": name,
            "max_age_seconds": maximum,
            "message": "尚未收到该浏览器 worker 心跳。",
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
    except (KeyError, TypeError, ValueError):
        return {
            "ok": False,
            "service_name": name,
            "max_age_seconds": maximum,
            "heartbeat": heartbeat,
            "message": "浏览器 worker 心跳时间格式无效。",
        }
    status = str(heartbeat.get("status") or "").strip().lower()
    live_statuses = {
        "starting",
        "running",
        "capturing",
        "degraded",
        "idle",
        "waiting",
        "retrying",
    }
    return {
        "ok": age_seconds <= maximum and status in live_statuses,
        "service_name": name,
        "age_seconds": age_seconds,
        "max_age_seconds": maximum,
        "status": status,
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
    worker_max_age_seconds: int = 900,
    backup_max_age_seconds: int = 86_400,
    domain_hostname: str | None = None,
    expected_ip: str | None = None,
    require_continuous_validation: bool = True,
    domain_probe: Callable[..., DomainProbeResult] = probe_public_domain,
) -> dict[str, Any]:
    """Build an auditable release gate without changing jobs or source state.

    A deployment can be internally safe while a public domain still lacks DNS
    or TLS.  The returned `internal_ready` and `public_ready` values keep these
    different conditions explicit, rather than making a failed hostname look
    like a crawler or data-quality failure.
    """
    audit = audit_database(database, settings)
    # ``release-check`` used to be a separate operator command. That allowed
    # a deployment with a mutable image or an unknown release identity to
    # pass ``/readinessz`` whenever its data-plane checks happened to be
    # healthy. A readiness result is the release decision, so include the
    # immutable build contract in the same fail-closed gate.
    release = release_configuration(settings)
    worker = worker_health(database, worker_max_age_seconds)
    browser_workers = browser_worker_health(database)
    source_freshness = source_freshness_readiness(settings, database)
    government = government_position_readiness(settings, database)
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
        and bool(release.get("ok"))
        and bool(worker.get("ok"))
        and bool(browser_workers.get("release_ok", browser_workers.get("ok")))
        and bool(source_freshness.get("ok"))
        and bool(government.get("ok"))
        and bool(link_health.get("ok"))
        and backup.ok
        and (continuous["ok"] or not require_continuous_validation)
    )
    public_ready = internal_ready and bool(domain and domain.get("ready"))
    return {
        "internal_ready": internal_ready,
        "public_ready": public_ready,
        "audit": audit,
        "release": release,
        "worker": worker,
        "browser_workers": browser_workers,
        "source_freshness": source_freshness,
        "government_positions": government,
        "link_health": link_health,
        "backup": backup.as_dict(),
        "continuous_validation": continuous,
        "domain": domain,
        "publication_policy": (
            "公网正式发布必须同时通过固定发布身份和镜像、数据审计、worker 心跳、浏览器 worker（如已启用）、"
            "政府职位台账新鲜度与证据门禁（如已配置）、近期覆盖快照与日报、已验证备份和"
            "正式域名 HTTPS；任何一项失败均不得将系统标记为可正式服务学生。"
        ),
    }


def source_freshness_readiness(
    settings: Settings,
    database: Database,
) -> dict[str, Any]:
    """Ensure every enabled live source has a bounded freshness contract.

    ``expire_stale_source_jobs`` and the read-side SQL filter can only protect
    rows when the contract is materialized in the source record. This check
    prevents a deployment from appearing ready while a registry bootstrap was
    skipped or an adapter explicitly supplied an invalid age.
    """

    checked = 0
    missing: list[str] = []
    invalid: list[str] = []
    freshness_unproven: list[str] = []
    stale_capture: list[str] = []
    invalid_capture: list[str] = []
    reference = datetime.now(timezone.utc)
    for source in database.list_sources():
        if not source.get("enabled"):
            continue
        if str(source.get("source_type") or "") == "manual":
            continue
        checked += 1
        config = source.get("config") if isinstance(source.get("config"), dict) else {}
        source_id = str(source.get("id") or "")
        if "max_age_hours" not in config:
            missing.append(source_id)
            continue
        try:
            age = float(config.get("max_age_hours"))
        except (TypeError, ValueError):
            invalid.append(source_id)
            continue
        if age <= 0 or not math.isfinite(age):
            invalid.append(source_id)
            continue
        capture = database.get_source_capture_freshness(source_id)
        captured_text = str((capture or {}).get("captured_at") or "").strip()
        if not captured_text:
            freshness_unproven.append(source_id)
            continue
        try:
            captured_at = datetime.fromisoformat(captured_text.replace("Z", "+00:00"))
        except ValueError:
            invalid_capture.append(source_id)
            continue
        if captured_at.tzinfo is None:
            invalid_capture.append(source_id)
            continue
        age_hours = (
            reference - captured_at.astimezone(timezone.utc)
        ).total_seconds() / 3600
        if age_hours < 0:
            invalid_capture.append(source_id)
        elif age_hours > age:
            stale_capture.append(source_id)
    issues: list[str] = []
    if missing:
        issues.append("启用的实时来源尚未物化 max_age_hours")
    if invalid:
        issues.append("启用的实时来源存在无效 max_age_hours")
    if freshness_unproven:
        issues.append("启用来源缺少成功捕获证据")
    if stale_capture:
        issues.append("启用来源的捕获证据已超过新鲜度窗口")
    if invalid_capture:
        issues.append("启用来源存在非法捕获时间戳")
    default_age = getattr(settings, "default_source_max_age_hours", None)
    return {
        "enabled": True,
        "ok": not issues,
        "checked_sources": checked,
        "missing_contract_sources": sorted(item for item in missing if item),
        "invalid_contract_sources": sorted(item for item in invalid if item),
        "freshness_unproven_sources": sorted(item for item in freshness_unproven if item),
        "stale_capture_sources": sorted(item for item in stale_capture if item),
        "invalid_capture_sources": sorted(item for item in invalid_capture if item),
        "default_source_max_age_hours": default_age,
        "issues": issues,
        "message": (
            "所有启用来源均有有限新鲜度合同和必要的捕获证据。"
            if not issues
            else "；".join(issues)
        ),
    }


def government_position_readiness(
    settings: Settings,
    database: Database,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Validate the government-position control plane for a release.

    The student-facing publication path already fails closed when a reviewed
    ledger becomes stale. Production readiness must make the same state
    visible at the deployment boundary; otherwise an old government snapshot
    can coexist with a green worker/backup gate and be mistaken for a healthy
    continuously refreshed source. Deployments that do not configure a
    government registry keep this gate explicitly disabled.
    """
    registry_path = getattr(settings, "government_position_registry_path", None)
    if registry_path is None:
        return {
            "enabled": False,
            "ok": True,
            "message": "政府职位台账未配置，政府职位生产门禁未启用。",
        }

    path = Path(registry_path)
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    try:
        local_zone = ZoneInfo(str(getattr(settings, "timezone", "UTC") or "UTC"))
    except Exception:  # pragma: no cover - invalid deployment timezone is reported below
        local_zone = timezone.utc
    today = reference.astimezone(local_zone).date().isoformat()

    try:
        registry = load_position_registry(path)
    except Exception as error:
        return {
            "enabled": True,
            "ok": False,
            "registry_path": str(path),
            "message": f"政府职位台账无法读取或不符合契约：{error}",
            "error_type": error.__class__.__name__,
        }

    manifest = None
    manifest_available = True
    manifest_path = getattr(settings, "government_artifact_manifest_path", None)
    if manifest_path is not None:
        try:
            manifest = load_government_artifact_manifest(manifest_path)
            manifest = project_government_artifact_manifest(
                database,
                manifest,
                registry=registry,
            )
        except Exception as error:
            manifest_available = False
            return {
                "enabled": True,
                "ok": False,
                "registry_path": str(path),
                "manifest_path": str(manifest_path),
                "message": f"政府官方附件清单无法读取或不符合契约：{error}",
                "error_type": error.__class__.__name__,
            }

    verifications = {
        str(item.get("source_id") or ""): item
        for item in database.list_government_source_verifications()
        if str(item.get("source_id") or "").strip()
    }
    manual_source_ids = {
        str(source.get("id") or "")
        for source in database.list_sources()
        if str(source.get("id") or "").strip()
        and requires_manual_government_evidence_confirmation(source)
    }
    report = government_position_quality_report(
        registry,
        today=today,
        max_age_hours=getattr(settings, "government_position_max_age_hours", 48),
        source_verifications=verifications,
        source_refresh_counts=database.government_source_refresh_counts(),
        source_refresh_events={
            source_id: database.list_government_source_verification_events(
                source_id, limit=10
            )
            for source_id in {
                str(item["source_id"])
                for item in registry.get("records", [])
                if item.get("record_status") == "verified_open"
            }
        },
        manual_confirmation_source_ids=manual_source_ids,
        artifact_manifest=manifest,
        artifact_manifest_available=manifest_available,
        now=reference,
    )
    issues: list[str] = []
    if report.get("registry_freshness") != "fresh":
        issues.append("政府职位台账已超过配置的新鲜度窗口")
    if int(report.get("source_failures_or_pending") or 0) > 0:
        issues.append("政府来源存在待复核、访问失败或当前性确认缺失")
    manifest_gate = report.get("attachment_manifest_gate") or {}
    if int(manifest_gate.get("blocked_records") or 0) > 0:
        issues.append("政府附件仍有岗位行未通过受控证据清单")
    return {
        "enabled": True,
        "ok": not issues,
        "registry_path": str(path),
        "manifest_path": str(manifest_path) if manifest_path is not None else None,
        "report": report,
        "issues": issues,
        "message": (
            "政府职位台账、来源复核和附件证据均处于生产新鲜度窗口。"
            if not issues
            else "；".join(issues)
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
    validating the service. Production readiness treats this check as a hard
    gate; callers may explicitly disable it only for local diagnostics.
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
