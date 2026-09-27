"""Guarded production handover from CMGB's reviewed snapshot to browser data.

The browser collector is valuable only if it can replace a reviewed snapshot
without a brief access failure deleting vacancies or a second source creating
student-facing duplicates.  This module performs the handover in a fixed
order: read-only preflight, successful dynamic sync, then one atomic source
switch that retains the old evidence as superseded history.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from job_hub.db import Database
from job_hub.pipeline import JobPipeline, SourceSyncResult


SNAPSHOT_SOURCE_ID = "cmgb-iguopin-2027-geoscience-snapshot"
BROWSER_SOURCE_ID = "cmgb-iguopin-browser"
TRANSITION_NAME = "cmgb-iguopin-browser-production"


@dataclass(frozen=True)
class CmgbTransitionResult:
    status: str
    snapshot_eligible: int
    dynamic_eligible_preview: int
    missing_snapshot_external_ids: tuple[str, ...] = ()
    synchronized: SourceSyncResult | None = None
    superseded_snapshot_jobs: int = 0
    message: str = ""

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        if self.synchronized is not None:
            result["synchronized"] = asdict(self.synchronized)
        return result


def _preview_student_external_ids(
    pipeline: JobPipeline,
    source: dict[str, Any],
) -> set[str]:
    """Collect and normalize without writing any public job records."""

    collect = getattr(pipeline.collector, "collect_with_fallback", None)
    postings = collect(source) if callable(collect) else pipeline.collector.collect(source)
    identifiers: set[str] = set()
    minimum_score = int(source["config"].get("minimum_relevance", 0))
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


def transition_cmgb_browser_to_production(
    database: Database,
    pipeline: JobPipeline,
    *,
    snapshot_source_id: str = SNAPSHOT_SOURCE_ID,
    browser_source_id: str = BROWSER_SOURCE_ID,
    activate: bool = False,
) -> CmgbTransitionResult:
    """Activate a complete CMGB browser capture only when it preserves matches.

    Any failed preview, incomplete capture, or missing existing explicit match
    exits before a database write.  With ``activate=False`` this is a read-only
    readiness check.  With ``activate=True`` the successor sync and the source
    handover share one transaction, so a failed write cannot leave duplicate
    public jobs or hide the snapshot.  Rollback remains possible by
    re-enabling the reviewed snapshot and re-syncing its file.
    """

    snapshot = database.get_source(snapshot_source_id)
    browser = database.get_source(browser_source_id)
    if snapshot is None or browser is None:
        raise ValueError("CMGB snapshot and browser sources must both be registered")
    if snapshot.get("enabled") is False and browser.get("enabled") is True:
        return CmgbTransitionResult(
            status="already_active",
            snapshot_eligible=len(
                database.student_visible_external_ids_for_source(snapshot_source_id)
            ),
            dynamic_eligible_preview=len(
                database.student_visible_external_ids_for_source(browser_source_id)
            ),
            message="动态国聘来源已处于生产状态；未重复切换。",
        )

    snapshot_ids = database.student_visible_external_ids_for_source(snapshot_source_id)
    try:
        preview_ids = _preview_student_external_ids(pipeline, browser)
    except Exception as error:  # The snapshot must remain public on any failure.
        return CmgbTransitionResult(
            status="blocked",
            snapshot_eligible=len(snapshot_ids),
            dynamic_eligible_preview=0,
            message=f"动态来源只读预检失败，旧快照未改动：{error}",
        )
    missing = tuple(sorted(snapshot_ids - preview_ids))
    if missing:
        return CmgbTransitionResult(
            status="blocked",
            snapshot_eligible=len(snapshot_ids),
            dynamic_eligible_preview=len(preview_ids),
            missing_snapshot_external_ids=missing,
            message="动态来源没有覆盖全部既有明确匹配岗位，旧快照未改动。",
        )

    if not activate:
        return CmgbTransitionResult(
            status="ready",
            snapshot_eligible=len(snapshot_ids),
            dynamic_eligible_preview=len(preview_ids),
            message="动态来源预检通过；未写入数据库，等待带确认参数的生产切换。",
        )

    sync = pipeline.sync_source_atomically(
        browser,
        before_commit=lambda: database.complete_source_transition(
            retired_source_id=snapshot_source_id,
            active_source_id=browser_source_id,
            transition_name=TRANSITION_NAME,
        ),
    )
    if sync.status != "finished":
        return CmgbTransitionResult(
            status="blocked",
            snapshot_eligible=len(snapshot_ids),
            dynamic_eligible_preview=len(preview_ids),
            synchronized=sync,
            message="动态来源同步未完成，旧快照未改动。",
        )
    return CmgbTransitionResult(
        status="activated",
        snapshot_eligible=len(snapshot_ids),
        dynamic_eligible_preview=len(preview_ids),
        synchronized=sync,
        superseded_snapshot_jobs=database.count_jobs_for_source(snapshot_source_id),
        message="动态国聘来源已完成核对并接管生产；旧快照已保留为可审计历史。",
    )
