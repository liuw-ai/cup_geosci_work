"""Read-only lifecycle projection for official recruitment sources."""

from __future__ import annotations

from collections import Counter
import json
from typing import Any


SOURCE_LIFECYCLE_STATES = (
    "registered",
    "identity_verified",
    "adapter_ready",
    "capture_started",
    "partial",
    "complete",
    "validated",
    "published",
    "stale",
    "withdrawn",
    "access_limited",
    "structure_changed",
    "source_error",
)


def project_source_lifecycle(
    source: dict[str, Any],
    *,
    health: dict[str, Any] | None = None,
    latest_run: dict[str, Any] | None = None,
) -> str:
    """Map existing source/run evidence to one conservative state.

    This is deliberately a projection, not a second source of truth. A failed
    or missing run never becomes ``complete`` or ``published`` merely because
    old rows exist in SQLite.
    """
    config = source.get("config") if isinstance(source.get("config"), dict) else {}
    automation_status = str(config.get("automation_status") or "").lower()
    if not source.get("enabled"):
        return "withdrawn" if automation_status.startswith(("retired", "replaced", "deprecated")) else "stale"
    if not health and not latest_run:
        return "identity_verified" if config.get("identity_verified") else "registered"
    health_status = str((health or {}).get("status") or "")
    detail = str((health or {}).get("detail") or "").lower()
    if health_status in {"source_blocked", "access_limited", "robots_blocked"} or (
        health_status != "source_active"
        and any(token in detail for token in ("access", "robots", "captcha", "maintenance"))
    ):
        return "access_limited"
    if health_status != "source_active" and any(
        token in detail for token in ("structure", "selector", "schema", "parse")
    ):
        return "structure_changed"
    if latest_run is None:
        return "adapter_ready"
    if latest_run.get("status") == "running":
        return "capture_started"
    metadata = _metadata(latest_run)
    if latest_run.get("status") != "finished":
        return "source_error"
    if latest_run.get("outcome") in {"partial", "incomplete"} or metadata.get("partial") is True:
        return "partial"
    if metadata.get("validated") is True:
        return "validated"
    if int(latest_run.get("open_matching_count") or 0) > 0:
        return "published"
    return "complete"


def lifecycle_report(
    sources: list[dict[str, Any]],
    *,
    health_by_id: dict[str, dict[str, Any]],
    latest_runs_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    rows = []
    counts: Counter[str] = Counter()
    for source in sources:
        state = project_source_lifecycle(
            source,
            health=health_by_id.get(str(source["id"])),
            latest_run=latest_runs_by_id.get(str(source["id"])),
        )
        counts[state] += 1
        rows.append({"id": str(source["id"]), "state": state, "enabled": bool(source.get("enabled"))})
    return {
        "states": dict(sorted(counts.items())),
        "registered": len(sources),
        "rows": rows,
        "state_definitions": {
            "registered": "仅登记，尚无身份或适配器运行证据",
            "identity_verified": "官方主体已核验，尚无本次运行",
            "adapter_ready": "已有适配器且等待运行",
            "capture_started": "本次采集仍在运行",
            "partial": "采集未完整结束，不能解释为无岗位",
            "complete": "本次扫描完整结束，可能没有匹配岗位",
            "validated": "扫描结果通过独立证据校验",
            "published": "本次扫描存在可发布匹配岗位",
            "stale": "启用状态失去新鲜运行证据",
            "withdrawn": "来源已停用，历史记录仅供审计",
            "access_limited": "访问策略、维护或 robots 阻断",
            "structure_changed": "页面结构或字段解析发生变化",
            "source_error": "运行失败或被中断",
        },
    }


def _metadata(run: dict[str, Any]) -> dict[str, Any]:
    value = run.get("metadata")
    if isinstance(value, dict):
        return value
    raw = run.get("metadata_json")
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        value = {}
    return value if isinstance(value, dict) else {}
