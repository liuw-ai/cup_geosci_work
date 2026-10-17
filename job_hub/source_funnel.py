"""Auditable source-throughput ledger for expansion work.

This is intentionally a read-only projection over source metadata, durable
source tasks, latest crawl runs, attachment status and already public jobs.
It explains where a source is losing candidates without treating a failed run
or an unreviewed attachment as a vacancy.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
from typing import Any, Iterable

from job_hub.readiness_axes import is_domestic_job, is_public_job


def build_source_funnel(
    *,
    sources: Iterable[dict[str, Any]],
    jobs: Iterable[dict[str, Any]],
    crawl_runs: Iterable[dict[str, Any]],
    health_by_id: dict[str, dict[str, Any]],
    source_tasks: Iterable[dict[str, Any]],
    artifact_status_counts: Iterable[dict[str, Any]],
    target_plan: dict[str, Any] | None = None,
    capture_freshness_by_id: dict[str, dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return latest-run source throughput plus a conservative next action."""

    source_rows = list(sources)
    latest_runs = _latest_by_source(crawl_runs)
    task_by_source = {
        str(task.get("source_id") or "").strip(): task
        for task in source_tasks
        if str(task.get("source_id") or "").strip()
    }
    artifact_by_source = {
        str(item.get("source_id") or "").strip(): item
        for item in artifact_status_counts
        if str(item.get("source_id") or "").strip()
    }
    planned_segments = _planned_segments(target_plan)
    public_counts = Counter(
        str(job.get("source_id") or "unknown").strip() or "unknown"
        for job in jobs
        if is_public_job(job)
    )
    domestic_counts = Counter(
        str(job.get("source_id") or "unknown").strip() or "unknown"
        for job in jobs
        if is_public_job(job) and is_domestic_job(job)
    )

    reference_time = _as_utc(now or datetime.now(timezone.utc))
    capture_records = capture_freshness_by_id or {}
    rows = [
        _source_row(
            source,
            latest_run=latest_runs.get(str(source.get("id") or "")),
            health=health_by_id.get(str(source.get("id") or ""), {}),
            task=task_by_source.get(str(source.get("id") or ""), {}),
            artifacts=artifact_by_source.get(str(source.get("id") or ""), {}),
            public_count=int(public_counts.get(str(source.get("id") or ""), 0)),
            domestic_count=int(domestic_counts.get(str(source.get("id") or ""), 0)),
            target_segments=planned_segments.get(str(source.get("id") or ""), []),
            capture_freshness=capture_records.get(str(source.get("id") or "")),
            now=reference_time,
        )
        for source in source_rows
    ]
    rows.sort(key=_sort_key)

    latest_finished = [
        row for row in rows if row["latest_run"].get("status") == "finished"
    ]
    instrumented = [
        row for row in latest_finished
        if row["growth_metrics"]["instrumented"]
    ]
    stage_counts = Counter(str(row.get("stage") or "") for row in rows)
    capture_freshness_counts = Counter(
        str((row.get("capture_freshness") or {}).get("status") or "")
        for row in rows
    )
    enabled_rows = [row for row in rows if row["enabled"]]
    enabled_capture_freshness_counts = Counter(
        str((row.get("capture_freshness") or {}).get("status") or "")
        for row in enabled_rows
    )
    summary = {
        "registered_sources": len(rows),
        "enabled_sources": sum(1 for row in rows if row["enabled"]),
        "sources_with_latest_run": sum(1 for row in rows if row["latest_run"]),
        "latest_finished_sources": len(latest_finished),
        "growth_instrumented_sources": len(instrumented),
        "growth_uninstrumented_finished_sources": len(latest_finished) - len(instrumented),
        "latest_official_url_candidates": _sum_instrumented(
            latest_finished, "official_url_count"
        ),
        "latest_field_complete_candidates": _sum_instrumented(
            latest_finished, "field_complete_count"
        ),
        "latest_precise_major_pass_candidates": _sum_instrumented(
            latest_finished, "precise_major_pass_count"
        ),
        "latest_similar_major_pass_candidates": _sum_instrumented(
            latest_finished, "similar_major_pass_count"
        ),
        "latest_broad_major_pass_candidates": _sum_instrumented(
            latest_finished, "broad_major_pass_count"
        ),
        "latest_public_broad_pass_candidates": _sum_instrumented(
            latest_finished, "public_broad_pass_count"
        ),
        "latest_unrestricted_major_pass_candidates": _sum_instrumented(
            latest_finished, "unrestricted_major_pass_count"
        ),
        "metric_contract_complete_sources": sum(
            1
            for row in latest_finished
            if row["metric_contract"]["status"] == "complete"
        ),
        "metric_contract_incomplete_sources": sum(
            1
            for row in latest_finished
            if row["metric_contract"]["status"] == "incomplete"
        ),
        "metric_contract_invalid_sources": sum(
            1
            for row in latest_finished
            if row["metric_contract"]["status"] == "invalid"
        ),
        # An adapter with no growth counters is not a zero-yield adapter. Keep
        # the IDs visible so expansion work can target the measurement gap.
        "metric_contract_incomplete_source_ids": sorted(
            row["source_id"]
            for row in latest_finished
            if row["metric_contract"]["status"] == "incomplete"
        ),
        "metric_contract_invalid_source_ids": sorted(
            row["source_id"]
            for row in latest_finished
            if row["metric_contract"]["status"] == "invalid"
        ),
        "growth_uninstrumented_source_ids": sorted(
            row["source_id"]
            for row in latest_finished
            if not row["growth_metrics"]["instrumented"]
        ),
        "not_scanned_sources": sum(1 for row in rows if row["stage"] == "not_scanned"),
        "running_sources": sum(1 for row in rows if row["stage"] == "running"),
        "background_capture_sources": sum(
            1 for row in rows if row["stage"] == "background_capture"
        ),
        "access_limited_sources": sum(
            1 for row in rows if row["stage"] == "access_limited"
        ),
        # These three fields describe the evidence timestamp itself, rather
        # than the final stage.  Access-limited or failed sources can mask a
        # stale capture in the stage state, but must not hide the freshness
        # problem from the audit summary.
        "stale_capture_sources": int(capture_freshness_counts.get("stale", 0)),
        "freshness_unproven_sources": int(
            capture_freshness_counts.get("unproven", 0)
        ),
        "invalid_capture_sources": int(capture_freshness_counts.get("invalid", 0)),
        "capture_freshness_status_counts": dict(sorted(capture_freshness_counts.items())),
        # Production readiness evaluates enabled non-manual sources. Keep an
        # enabled-only projection beside the all-registered audit counts so an
        # operator can reconcile this report with the release gate directly.
        "enabled_stale_capture_sources": int(
            enabled_capture_freshness_counts.get("stale", 0)
        ),
        "enabled_freshness_unproven_sources": int(
            enabled_capture_freshness_counts.get("unproven", 0)
        ),
        "enabled_invalid_capture_sources": int(
            enabled_capture_freshness_counts.get("invalid", 0)
        ),
        "enabled_capture_freshness_status_counts": dict(
            sorted(enabled_capture_freshness_counts.items())
        ),
        "stage_counts": dict(sorted(stage_counts.items())),
        "failed_sources": sum(1 for row in rows if row["stage"] == "failed"),
        "sources_with_current_matches": sum(
            1 for row in rows if row["published_domestic_jobs"] > 0
        ),
        "latest_discovered_candidates": sum(
            int(row["latest_run"].get("discovered_count") or 0)
            for row in latest_finished
        ),
        "latest_evidence_complete_candidates": sum(
            int(row["latest_run"].get("evidence_complete_count") or 0)
            for row in latest_finished
        ),
        "latest_manual_review_candidates": sum(
            int(row["latest_run"].get("manual_review_count") or 0)
            for row in latest_finished
        ),
        "latest_open_matching_candidates": sum(
            int(row["latest_run"].get("open_matching_count") or 0)
            for row in latest_finished
        ),
        "current_public_jobs": sum(row["published_public_jobs"] for row in rows),
        "current_domestic_jobs": sum(row["published_domestic_jobs"] for row in rows),
        "scope_note": (
            "候选和证据数量只取每个来源最近一次完成运行，不能与当前保留岗位直接相除；"
            "当前岗位已受截止日期、新鲜度和学生端发布门禁影响。latest_open_matching_candidates "
            "表示运行完成时的历史漏斗数字；只有 capture_freshness.status=fresh 且已有学生端岗位时，"
            "来源才可视为当前匹配。stale_capture_sources、freshness_unproven_sources 和 "
            "invalid_capture_sources 按捕获证据状态独立统计；enabled_* 字段与生产 readiness"
            " 的启用来源范围一致；stage_counts 反映访问受限/失败等阶段优先级，因此二者可能不相等。"
            "growth_* 只汇总已写入岗位级计数的最近完成运行；未埋点来源单列，"
            "不能将其当作零。current_domestic_jobs 是学生端实时读门禁快照，"
            "不能除以不同时间的最新采集候选数。"
        ),
    }
    return {
        "version": 1,
        "summary": summary,
        "rows": rows,
        "stage_definitions": {
            "not_scanned": "已登记来源尚无完成运行，先完成合规访问与首次扫描。",
            "running": "来源当前正在运行；等待完成记录，不能按失败或无匹配处理。",
            "background_capture": "学生端发布尚未启用，但受控浏览器 worker 正在等待或执行完整官方捕获；成功前不展示旧快照。",
            "access_limited": "robots、访问策略或任务队列已明确受限；保留监控，不把它解释为无岗位。",
            "stale_capture": "最近运行的完整捕获已超过来源新鲜度窗口；历史运行数字保留审计，但不能当作当前岗位。",
            "freshness_unproven": "来源配置了新鲜度窗口，但没有可验证的完整捕获时间；学生端 fail-closed。",
            "failed": "最近一次运行失败或中断；需要修复入口、解析或运行环境。",
            "scanned_with_matches": "最近运行完成且已有当前国内公开岗位；继续按计划刷新。",
            "scanned_without_current_match": "运行完成但当前没有国内公开岗位；只表示本轮结果，不代表该领域没有招聘。",
            "disabled": "来源当前停用，不参与自动发布；历史证据仍保留审计。",
        },
        "metric_contract_definitions": {
            "complete": "最新完成运行的发现、证据、人工复核和开放匹配计数满足统一单调关系。",
            "incomplete": "运行发现开放匹配，但没有记录同范围的证据完整计数；不能用于漏斗转化率或扩容效率结论。",
            "invalid": "运行计数违反候选总数边界；必须修复适配器指标写入后再作为扩容证据。",
            "not_available": "没有完成运行，或该来源本轮没有可比较指标。",
        },
        "scope_note": (
            "该账本用于管理员排程和复盘。它不发布候选、不改变来源状态，"
            "也不将访问失败、待复核或历史岗位计为学生端有效岗位。"
        ),
    }


def _latest_by_source(crawl_runs: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for run in crawl_runs:
        source_id = str(run.get("source_id") or "").strip()
        if not source_id:
            continue
        if source_id not in latest or int(run.get("id") or 0) > int(latest[source_id].get("id") or 0):
            latest[source_id] = dict(run)
    return latest


def _planned_segments(target_plan: dict[str, Any] | None) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for segment in (target_plan or {}).get("segments", []):
        if not isinstance(segment, dict):
            continue
        segment_id = str(segment.get("id") or "").strip()
        if not segment_id:
            continue
        for source_id in segment.get("source_ids") or []:
            key = str(source_id or "").strip()
            if key:
                result.setdefault(key, []).append(segment_id)
    return result


def _source_row(
    source: dict[str, Any],
    *,
    latest_run: dict[str, Any] | None,
    health: dict[str, Any],
    task: dict[str, Any],
    artifacts: dict[str, Any],
    public_count: int,
    domestic_count: int,
    target_segments: list[str],
    capture_freshness: dict[str, Any] | None,
    now: datetime,
) -> dict[str, Any]:
    enabled = bool(source.get("enabled"))
    run = dict(latest_run or {})
    freshness = _capture_freshness_state(source, capture_freshness, now=now)
    stage = _stage(
        enabled=enabled,
        background_capture_enabled=bool(
            (source.get("config") or {}).get("background_capture_enabled")
        ),
        health=health,
        task=task,
        latest_run=run,
        freshness_status=str(freshness["status"]),
    )
    metric_contract = _metric_contract(run)
    growth_metrics = _growth_metrics(run, domestic_count)
    return {
        "source_id": str(source.get("id") or ""),
        "source_name": str(source.get("name") or ""),
        "enabled": enabled,
        "target_segments": target_segments,
        "stage": stage,
        "recommended_next_action": _next_action(
            stage,
            domestic_count,
            metric_contract_status=str(metric_contract["status"]),
        ),
        "source_health_status": health.get("status"),
        "task_status": task.get("status"),
        "task_attempts": int(task.get("attempts") or 0),
        "task_consecutive_failures": int(task.get("consecutive_failures") or 0),
        "capture_freshness": freshness,
        "latest_run": {
            key: run.get(key)
            for key in (
                "id",
                "status",
                "outcome",
                "started_at",
                "finished_at",
                "discovered_count",
                "evidence_complete_count",
                "manual_review_count",
                "open_matching_count",
                "inserted_count",
                "updated_count",
                "attachment_success_count",
            )
            if key in run
        },
        "metric_contract": metric_contract,
        "growth_metrics": growth_metrics,
        "artifacts": {
            "total_count": int(artifacts.get("total_count") or 0),
            "extracted_count": int(artifacts.get("extracted_count") or 0),
            "failed_count": int(artifacts.get("failed_count") or 0),
        },
        "published_public_jobs": public_count,
        "published_domestic_jobs": domestic_count,
    }


def _growth_metrics(run: dict[str, Any], current_published_count: int) -> dict[str, Any]:
    """Expose new run counters only when a completed scan recorded them."""
    metadata = run.get("metadata")
    if not isinstance(metadata, dict):
        try:
            metadata = json.loads(str(run.get("metadata_json") or "{}"))
        except (TypeError, ValueError):
            metadata = {}
    counts = metadata.get("growth_counts") if isinstance(metadata, dict) else None
    if not isinstance(counts, dict):
        counts = {}
    required_names = (
        "official_url_count", "field_complete_count",
        "precise_major_pass_count", "public_broad_pass_count",
        "unrestricted_major_pass_count",
    )
    optional_names = ("similar_major_pass_count", "broad_major_pass_count")
    disjoint_broad_count = (
        int(counts.get("broad_major_pass_count"))
        if "broad_major_pass_count" in counts
        else int(counts.get("public_broad_pass_count") or 0)
        + int(counts.get("unrestricted_major_pass_count") or 0)
    )
    valid = (
        run.get("status") == "finished"
        and isinstance(counts, dict)
        and counts.get("version") == 1
        and all(type(counts.get(name)) is int and counts[name] >= 0 for name in required_names)
        and all(
            name not in counts
            or (type(counts.get(name)) is int and counts[name] >= 0)
            for name in optional_names
        )
        and counts["precise_major_pass_count"]
        + int(counts.get("similar_major_pass_count") or 0)
        + disjoint_broad_count
        <= counts["field_complete_count"] <= counts["official_url_count"]
        <= int(run.get("discovered_count") or 0)
        and counts["public_broad_pass_count"] <= counts["field_complete_count"]
        and counts["unrestricted_major_pass_count"] <= counts["field_complete_count"]
    )
    # Version-1 runs predate the additive exact/similar/broad projection. They
    # remain auditable with a conservative zero for the new dimensions; new
    # runs write both fields explicitly.
    similar_count = int(counts.get("similar_major_pass_count") or 0)
    broad_count = disjoint_broad_count
    return {
        "instrumented": bool(valid),
        "lead_count": int(run.get("discovered_count") or 0) if run.get("status") == "finished" else None,
        "similar_major_pass_count": similar_count if valid else None,
        "broad_major_pass_count": int(broad_count) if valid else None,
        **{name: counts[name] if valid else None for name in required_names},
        "current_published_count": current_published_count,
        "scope_note": (
            "前五项是同一来源最近完成运行的岗位候选，未埋点或失败显示 null；"
            "current_published_count 是独立的当前学生端快照。"
        ),
    }


def _sum_instrumented(rows: list[dict[str, Any]], key: str) -> int | None:
    """Sum only validated growth counters, ignoring legacy/uninstrumented runs.

    A legacy source has no trustworthy value for the new counters, but it must
    not hide counters from sources that do have a validated run.  ``None`` is
    reserved for the case where there are no instrumented rows at all.
    """
    instrumented = [
        row for row in rows if row["growth_metrics"]["instrumented"]
    ]
    if not instrumented:
        return None
    return sum(int(row["growth_metrics"][key]) for row in instrumented)


def _stage(
    *,
    enabled: bool,
    background_capture_enabled: bool,
    health: dict[str, Any],
    task: dict[str, Any],
    latest_run: dict[str, Any],
    freshness_status: str,
) -> str:
    if not enabled and not background_capture_enabled:
        return "disabled"
    health_status = str(health.get("status") or "")
    health_detail = str(health.get("detail") or "").lower()
    if health_status in {"source_blocked", "access_limited", "robots_blocked"} or (
        health_status != "source_active"
        and any(marker in health_detail for marker in ("access", "robots", "captcha", "maintenance"))
    ):
        return "access_limited"
    if health_status == "source_error":
        return "failed"
    if task.get("status") == "blocked":
        return "access_limited"
    if not enabled:
        return "background_capture"
    if not latest_run:
        return "not_scanned"
    if latest_run.get("status") == "running":
        return "running"
    if latest_run.get("status") != "finished":
        return "failed"
    if freshness_status == "stale":
        return "stale_capture"
    if freshness_status in {"unproven", "invalid"}:
        return "freshness_unproven"
    if int(latest_run.get("open_matching_count") or 0) > 0:
        return "scanned_with_matches"
    return "scanned_without_current_match"


def _next_action(
    stage: str,
    domestic_count: int,
    *,
    metric_contract_status: str,
) -> str:
    if stage == "access_limited":
        return "monitor_policy_or_use_registered_official_alternate"
    if stage == "not_scanned":
        return "complete_first_compliant_scan"
    if stage == "running":
        return "wait_for_current_run_then_evaluate_evidence"
    if stage == "background_capture":
        return "wait_for_complete_background_capture_before_publication"
    if stage == "stale_capture":
        return "refresh_complete_capture_before_counting_matches"
    if stage == "freshness_unproven":
        return "record_complete_capture_timestamp_before_publication"
    if stage == "failed":
        return "repair_adapter_or_runtime_then_recheck"
    if stage == "scanned_with_matches" and metric_contract_status in {
        "incomplete",
        "invalid",
    }:
        return "normalize_adapter_funnel_metrics_before_expansion"
    if stage == "disabled":
        return "do_not_reactivate_without_new_official_access_evidence"
    if stage == "scanned_with_matches" and domestic_count:
        return "scheduled_refresh_and_lifecycle_cleanup"
    return "watch_official_notices_without_claiming_no_jobs"


def _metric_contract(latest_run: dict[str, Any]) -> dict[str, Any]:
    """Check whether latest-run counts can support source throughput analysis.

    Publication evidence is evaluated independently by the existing pipeline.
    This contract only protects operations reporting: adapters that do not
    write comparable candidate counts are visible as incomplete instead of
    being used to produce a fabricated conversion rate.
    """

    if not latest_run or latest_run.get("status") != "finished":
        return {"status": "not_available", "checks": {}, "reason": "没有完成运行。"}
    discovered = int(latest_run.get("discovered_count") or 0)
    evidence_complete = int(latest_run.get("evidence_complete_count") or 0)
    manual_review = int(latest_run.get("manual_review_count") or 0)
    open_matching = int(latest_run.get("open_matching_count") or 0)
    checks = {
        "non_negative": min(
            discovered, evidence_complete, manual_review, open_matching
        ) >= 0,
        "evidence_not_above_discovered": evidence_complete <= discovered,
        "manual_review_not_above_discovered": manual_review <= discovered,
        "open_matching_not_above_discovered": open_matching <= discovered,
        "open_matching_has_evidence_count": open_matching == 0
        or evidence_complete >= open_matching,
    }
    if not all(checks.values()):
        boundary_checks = {
            "non_negative",
            "evidence_not_above_discovered",
            "manual_review_not_above_discovered",
            "open_matching_not_above_discovered",
        }
        if not all(checks[key] for key in boundary_checks):
            return {
                "status": "invalid",
                "checks": checks,
                "reason": "候选、证据、复核或开放匹配计数超过同一运行的发现总数。",
            }
        return {
            "status": "incomplete",
            "checks": checks,
            "reason": "发现开放匹配，但适配器未记录同范围的证据完整计数。",
        }
    return {"status": "complete", "checks": checks, "reason": "运行计数可用于漏斗分析。"}


def _sort_key(row: dict[str, Any]) -> tuple[int, int, str]:
    rank = {
        "not_scanned": 0,
        "running": 1,
        "background_capture": 2,
        "failed": 3,
        "scanned_without_current_match": 4,
        "scanned_with_matches": 5,
        "access_limited": 6,
        "stale_capture": 7,
        "freshness_unproven": 8,
        "disabled": 9,
    }.get(str(row.get("stage")), 10)
    return (rank, int(row.get("published_domestic_jobs") or 0), str(row.get("source_id") or ""))


__all__ = ["build_source_funnel"]


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("source funnel reference time must include a timezone")
    return value.astimezone(timezone.utc)


def _capture_freshness_state(
    source: dict[str, Any],
    capture: dict[str, Any] | None,
    *,
    now: datetime,
) -> dict[str, Any]:
    """Classify the evidence timestamp without changing database state.

    The latest crawl run is an audit fact, not proof that its rows are still
    current.  This projection mirrors the student read-side age contract so
    the operational funnel cannot report a stale capture as a live match.
    """

    config = source.get("config") if isinstance(source.get("config"), dict) else {}
    raw_max_age = config.get("max_age_hours")
    if raw_max_age is None or str(raw_max_age).strip() == "":
        return {"status": "not_configured", "max_age_hours": None}
    try:
        max_age = float(raw_max_age)
    except (TypeError, ValueError):
        return {
            "status": "invalid",
            "max_age_hours": str(raw_max_age),
            "reason": "max_age_hours is not numeric",
        }
    if max_age < 0:
        return {
            "status": "invalid",
            "max_age_hours": max_age,
            "reason": "max_age_hours cannot be negative",
        }
    if not capture:
        return {
            "status": "unproven",
            "max_age_hours": max_age,
            "reason": "no successful capture freshness record",
        }
    raw_captured_at = str(capture.get("captured_at") or "").strip()
    try:
        captured_at = datetime.fromisoformat(raw_captured_at.replace("Z", "+00:00"))
        captured_at = _as_utc(captured_at)
    except (TypeError, ValueError):
        return {
            "status": "invalid",
            "max_age_hours": max_age,
            "captured_at": raw_captured_at or None,
            "reason": "captured_at is not a timezone-aware ISO-8601 timestamp",
        }
    age_hours = (now - captured_at).total_seconds() / 3600
    if age_hours < -1:
        return {
            "status": "invalid",
            "max_age_hours": max_age,
            "captured_at": captured_at.isoformat().replace("+00:00", "Z"),
            "age_hours": round(age_hours, 3),
            "reason": "captured_at is in the future",
        }
    status = "fresh" if age_hours <= max_age else "stale"
    return {
        "status": status,
        "max_age_hours": max_age,
        "captured_at": captured_at.isoformat().replace("+00:00", "Z"),
        "recorded_at": capture.get("recorded_at"),
        "age_hours": round(max(0.0, age_hours), 3),
    }
