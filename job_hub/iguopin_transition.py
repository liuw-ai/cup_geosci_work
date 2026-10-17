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
from job_hub.profiles import PUBLIC_STUDENT_PUBLICATION_STATUSES


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


def _capture_detail_ids(
    payload: dict[str, Any],
    *,
    external_id_prefix: str,
) -> set[str]:
    """Return every discovered detail ID, including deterministic rejects."""

    ids = _capture_ids(payload)
    prefix = str(external_id_prefix or "").strip()
    if not prefix:
        return ids
    scan = payload.get("scan")
    rejected = scan.get("rejected_records") if isinstance(scan, dict) else None
    if isinstance(rejected, list):
        for item in rejected:
            if not isinstance(item, dict):
                continue
            detail_id = str(item.get("detail_id") or "").strip()
            if detail_id:
                ids.add(f"{prefix}-{detail_id}")
    return ids


def _source_with_capture_path(
    source: dict[str, Any],
    *,
    data_dir: Path,
    capture_path: Path,
) -> dict[str, Any] | None:
    """Point a source copy at an in-data-dir historical capture."""

    try:
        relative = capture_path.resolve().relative_to(data_dir.resolve())
    except ValueError:
        return None
    copied = dict(source)
    copied["config"] = {
        **dict(source.get("config") or {}),
        "capture_path": relative.as_posix(),
    }
    return copied


def _validate_scan_vocabulary(
    payload: dict[str, Any],
    *,
    source_type: str,
    config: dict[str, Any],
) -> str | None:
    """Require the configured full scan before a source can be promoted.

    Employer-owned portals have a bounded visible programme filter and do not
    carry ``keyword_scans``.  The national board is different: a successful
    pagination pass for one keyword is only a partial discovery.  Promotion
    therefore requires one complete sub-scan for every configured keyword.
    """

    if source_type != "iguopin_general_browser_rows":
        return None
    expected = [
        " ".join(str(item).split()).casefold()
        for item in (config.get("search_keywords") or [])
        if str(item).strip()
    ]
    if not expected:
        return "国聘主站来源缺少 search_keywords 配置"
    scan = payload.get("scan")
    if not isinstance(scan, dict):
        return "国聘主站捕获缺少 scan 审计"
    keyword_scans = scan.get("keyword_scans")
    if not isinstance(keyword_scans, list):
        return "国聘主站捕获缺少 keyword_scans，不能证明完成全关键词扫描"
    observed: list[str] = []
    for item in keyword_scans:
        if not isinstance(item, dict):
            return "国聘主站 keyword_scans 含无效记录"
        keyword = " ".join(str(item.get("keyword") or "").split()).casefold()
        if not keyword:
            return "国聘主站 keyword_scans 含空关键词"
        if not bool(item.get("pagination_complete")):
            return f"国聘主站关键词“{keyword}”分页未完成"
        try:
            if int(item.get("pages_scanned") or 0) <= 0:
                return f"国聘主站关键词“{keyword}”没有有效分页"
        except (TypeError, ValueError):
            return f"国聘主站关键词“{keyword}”分页数量无效"
        observed.append(keyword)
    if sorted(observed) != sorted(expected) or len(observed) != len(set(observed)):
        return (
            "国聘主站关键词扫描不完整："
            f"期望 {expected}，实际 {observed}"
        )
    return None


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
            in PUBLIC_STUDENT_PUBLICATION_STATUSES
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
    source_type = str(source.get("source_type") or "")
    if source_type not in {
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

    for label, payload in (("当前", current), ("上一次", previous)):
        vocabulary_error = _validate_scan_vocabulary(
            payload,
            source_type=source_type,
            config=config,
        )
        if vocabulary_error:
            return IguopinTransitionResult(
                status="blocked",
                source_id=source_id,
                current_capture_rows=len(_capture_ids(current)),
                previous_capture_rows=len(_capture_ids(previous)),
                message=f"{label}捕获未通过全关键词扫描门禁，来源保持停用：{vocabulary_error}",
            )

    # The national board deliberately searches a broad vocabulary. Its raw
    # result set contains unrelated jobs and is expected to drift as the
    # portal reorders or refreshes those results. Stability is therefore
    # measured on the same strict student-publication gate used for release;
    # employer-owned portals retain the stricter complete-detail ID check.
    try:
        eligible_ids = _preview_student_external_ids(pipeline, source)
    except Exception as error:
        return IguopinTransitionResult(
            status="blocked",
            source_id=source_id,
            current_capture_rows=len(_capture_ids(current)),
            previous_capture_rows=len(_capture_ids(previous)),
            message=f"当前捕获岗位门禁预检失败，来源保持停用：{error}",
        )
    if source_type == "iguopin_general_browser_rows":
        previous_source = _source_with_capture_path(
            source,
            data_dir=Path(pipeline.settings.data_dir),
            capture_path=previous_path,
        )
        if previous_source is None:
            return IguopinTransitionResult(
                status="blocked",
                source_id=source_id,
                current_capture_rows=len(_capture_ids(current)),
                previous_capture_rows=len(_capture_ids(previous)),
                current_eligible=len(eligible_ids),
                message="上一次国聘主站捕获不在数据目录内，无法进行学生岗位稳定性复核。",
            )
        try:
            previous_eligible_ids = _preview_student_external_ids(
                pipeline,
                previous_source,
            )
        except Exception as error:
            return IguopinTransitionResult(
                status="blocked",
                source_id=source_id,
                current_capture_rows=len(_capture_ids(current)),
                previous_capture_rows=len(_capture_ids(previous)),
                current_eligible=len(eligible_ids),
                message=f"上一次捕获岗位门禁预检失败，来源保持停用：{error}",
            )
        current_stability_ids = eligible_ids
        previous_stability_ids = previous_eligible_ids
    else:
        current_stability_ids = _capture_detail_ids(
            current,
            external_id_prefix=str(config.get("external_id_prefix") or ""),
        )
        previous_stability_ids = _capture_detail_ids(
            previous,
            external_id_prefix=str(config.get("external_id_prefix") or ""),
        )
    union = current_stability_ids | previous_stability_ids
    jaccard = (
        len(current_stability_ids & previous_stability_ids) / len(union)
        if union
        else 0.0
    )
    try:
        minimum_jaccard = float(config.get("minimum_capture_id_jaccard", 0.98))
    except (TypeError, ValueError):
        return IguopinTransitionResult(
            status="blocked",
            source_id=source_id,
            current_capture_rows=len(_capture_ids(current)),
            previous_capture_rows=len(_capture_ids(previous)),
            message="岗位编号集合稳定性阈值配置无效，来源保持停用。",
        )
    if not 0.0 <= minimum_jaccard <= 1.0 or jaccard < minimum_jaccard:
        return IguopinTransitionResult(
            status="blocked",
            source_id=source_id,
            current_capture_rows=len(_capture_ids(current)),
            previous_capture_rows=len(_capture_ids(previous)),
            current_only=len(current_stability_ids - previous_stability_ids),
            previous_only=len(previous_stability_ids - current_stability_ids),
            message=(
                "两次完整捕获的岗位编号集合不稳定，来源保持停用："
                f"Jaccard={jaccard:.4f} < {minimum_jaccard:.4f}。"
            ),
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
