"""Auditable source-throughput ledger for expansion work.

This is intentionally a read-only projection over source metadata, durable
source tasks, latest crawl runs, attachment status and already public jobs.
It explains where a source is losing candidates without treating a failed run
or an unreviewed attachment as a vacancy.
"""

from __future__ import annotations

from collections import Counter
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
        )
        for source in source_rows
    ]
    rows.sort(key=_sort_key)

    latest_finished = [
        row for row in rows if row["latest_run"].get("status") == "finished"
    ]
    summary = {
        "registered_sources": len(rows),
        "enabled_sources": sum(1 for row in rows if row["enabled"]),
        "sources_with_latest_run": sum(1 for row in rows if row["latest_run"]),
        "latest_finished_sources": len(latest_finished),
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
        "not_scanned_sources": sum(1 for row in rows if row["stage"] == "not_scanned"),
        "running_sources": sum(1 for row in rows if row["stage"] == "running"),
        "access_limited_sources": sum(
            1 for row in rows if row["stage"] == "access_limited"
        ),
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
            "当前岗位已受截止日期、新鲜度和学生端发布门禁影响。"
        ),
    }
    return {
        "version": 1,
        "summary": summary,
        "rows": rows,
        "stage_definitions": {
            "not_scanned": "已登记来源尚无完成运行，先完成合规访问与首次扫描。",
            "running": "来源当前正在运行；等待完成记录，不能按失败或无匹配处理。",
            "access_limited": "robots、访问策略或任务队列已明确受限；保留监控，不把它解释为无岗位。",
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
) -> dict[str, Any]:
    enabled = bool(source.get("enabled"))
    run = dict(latest_run or {})
    stage = _stage(enabled=enabled, health=health, task=task, latest_run=run)
    metric_contract = _metric_contract(run)
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
        "artifacts": {
            "total_count": int(artifacts.get("total_count") or 0),
            "extracted_count": int(artifacts.get("extracted_count") or 0),
            "failed_count": int(artifacts.get("failed_count") or 0),
        },
        "published_public_jobs": public_count,
        "published_domestic_jobs": domestic_count,
    }


def _stage(
    *,
    enabled: bool,
    health: dict[str, Any],
    task: dict[str, Any],
    latest_run: dict[str, Any],
) -> str:
    if not enabled:
        return "disabled"
    if health.get("status") in {"source_blocked", "access_limited", "robots_blocked"}:
        return "access_limited"
    if task.get("status") == "blocked":
        return "access_limited"
    if not latest_run:
        return "not_scanned"
    if latest_run.get("status") == "running":
        return "running"
    if latest_run.get("status") != "finished":
        return "failed"
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
        "failed": 2,
        "scanned_without_current_match": 3,
        "scanned_with_matches": 4,
        "access_limited": 5,
        "disabled": 6,
    }.get(str(row.get("stage")), 9)
    return (rank, int(row.get("published_domestic_jobs") or 0), str(row.get("source_id") or ""))


__all__ = ["build_source_funnel"]
