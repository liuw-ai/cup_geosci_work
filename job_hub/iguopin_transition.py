"""Guarded handover for an employer-owned 国聘 browser source.

An isolated browser capture is not production evidence until the same source
has completed two full scans and the current manifest passes the normal
student-publication gate.  This module keeps that decision explicit and
atomic; a failed preflight never enables the source or mutates its jobs.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from job_hub.cmgb_browser_capture import CmgbBrowserCaptureError
from job_hub.db import Database
from job_hub.iguopin_browser_capture import load_iguopin_browser_capture
from job_hub.pipeline import JobPipeline, SourceSyncResult


@dataclass(frozen=True)
class IguopinTransitionResult:
    status: str
    source_id: str
    current_capture_rows: int = 0
    previous_capture_rows: int = 0
    current_eligible: int = 0
    previous_only: int = 0
    current_only: int = 0
    synchronized: SourceSyncResult | None = None
    message: str = ""

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        if self.synchronized is not None:
            result["synchronized"] = asdict(self.synchronized)
        return result


def _capture_ids(payload: dict[str, Any]) -> set[str]:
    return {
        str(item.get("external_id") or "").strip()
        for item in payload.get("rows", [])
        if isinstance(item, dict) and str(item.get("external_id") or "").strip()
    }


def _preview_student_external_ids(
    pipeline: JobPipeline, source: dict[str, Any]
) -> set[str]:
    collector = pipeline.collector
    collect = getattr(collector, "collect_with_fallback", None)
    postings = collect(source) if callable(collect) else collector.collect(source)
    identifiers: set[str] = set()
    minimum_score = int(source.get("config", {}).get("minimum_relevance", 0))
    for posting in postings:
        normalized = pipeline.normalize_posting(posting, source)
        if normalized["relevance_score"] < minimum_score:
            continue
        if (
            normalized["status"] == "open"
            and normalized["publication_status"]
            in {"student_eligible", "unrestricted_eligible"}
            and posting.external_id
        ):
            identifiers.add(str(posting.external_id))
    return identifiers


def transition_iguopin_source_to_production(
    database: Database,
    pipeline: JobPipeline,
    *,
    source_id: str,
    previous_capture_path: Path | str,
    activate: bool = False,
) -> IguopinTransitionResult:
    """Validate two complete captures, then atomically enable one source.

    ``previous_capture_path`` is deliberately explicit instead of inferred
    from a timestamp: operators must point the audit at the exact earlier
    manifest they reviewed.  The current capture is loaded from the source
    registry's ``capture_path`` and is the only file that can be synchronized.
    """

    source = database.get_source(source_id)
    if source is None:
        raise ValueError(f"国聘来源未注册: {source_id}")
    if source.get("source_type") not in {
        "iguopin_browser_rows",
        "iguopin_general_browser_rows",
    }:
        raise ValueError(
            "国聘生产切换只接受 iguopin_browser_rows 或 "
            "iguopin_general_browser_rows 来源"
        )
    config = dict(source.get("config") or {})
    current_path = Path(pipeline.settings.data_dir) / str(config.get("capture_path") or "")
    previous_path = Path(previous_capture_path)
    if not previous_path.is_absolute():
        previous_path = Path(pipeline.settings.data_dir) / previous_path
    hosts = list(config.get("allowed_hosts") or [])
    max_age_hours = float(config.get("max_age_hours", 30))
    try:
        current = load_iguopin_browser_capture(
            current_path,
            allowed_hosts=hosts,
            max_age_hours=max_age_hours,
            require_complete_scan=True,
            require_capture_manifest=True,
        )
        previous = load_iguopin_browser_capture(
            previous_path,
            allowed_hosts=hosts,
            max_age_hours=max_age_hours,
            require_complete_scan=True,
            require_capture_manifest=True,
        )
    except (CmgbBrowserCaptureError, OSError, ValueError) as error:
        return IguopinTransitionResult(
            status="blocked",
            source_id=source_id,
            message=f"两次完整捕获预检失败，来源保持停用：{error}",
        )

    current_ids = _capture_ids(current)
    previous_ids = _capture_ids(previous)
    if not current_ids or not previous_ids:
        return IguopinTransitionResult(
            status="blocked",
            source_id=source_id,
            current_capture_rows=len(current_ids),
            previous_capture_rows=len(previous_ids),
            message="捕获清单没有可核验的岗位编号，来源保持停用。",
        )
    try:
        eligible_ids = _preview_student_external_ids(pipeline, source)
    except Exception as error:
        return IguopinTransitionResult(
            status="blocked",
            source_id=source_id,
            current_capture_rows=len(current_ids),
            previous_capture_rows=len(previous_ids),
            message=f"当前捕获岗位门禁预检失败，来源保持停用：{error}",
        )
    result = IguopinTransitionResult(
        status="ready" if not activate else "blocked",
        source_id=source_id,
        current_capture_rows=len(current_ids),
        previous_capture_rows=len(previous_ids),
        current_eligible=len(eligible_ids),
        previous_only=len(previous_ids - current_ids),
        current_only=len(current_ids - previous_ids),
        message="两次完整捕获和当前岗位门禁均通过；未写入数据库。"
        if not activate
        else "",
    )
    if not activate:
        return result
    if not eligible_ids:
        return IguopinTransitionResult(
            **{**result.__dict__, "status": "blocked", "message": "当前捕获没有明确匹配岗位，来源保持停用。"}
        )

    sync = pipeline.sync_source_atomically(
        source,
        before_commit=lambda: database.set_source_enabled(source_id, True),
    )
    if sync.status != "finished":
        return IguopinTransitionResult(
            **{**result.__dict__, "status": "blocked", "synchronized": sync, "message": "生产同步失败，来源保持停用。"}
        )
    return IguopinTransitionResult(
        **{
            **result.__dict__,
            "status": "activated",
            "synchronized": sync,
            "message": "国聘来源已通过两次完整捕获门禁并原子启用。",
        }
    )


__all__ = ["IguopinTransitionResult", "transition_iguopin_source_to_production"]
