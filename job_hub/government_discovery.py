"""Daily discovery of official government recruitment attachments.

This module bridges registered official announcement feeds and the existing
controlled attachment processor.  A discovered PDF or spreadsheet first
enters the private evidence queue.  A later verifier may promote a fully
self-contained, current, student-eligible row through the normal publication
gate; discovery itself is never evidence that a student is eligible.
"""

from __future__ import annotations

import time
from typing import Any, Iterable

from job_hub.attachments import (
    AttachmentProcessingError,
    AttachmentSkipped,
    OfficialAttachmentProcessor,
)
from job_hub.config import Settings
from job_hub.db import Database
from job_hub.sources import OfficialSourceCollector, SourceCollectionError, SourceSkipped


def discover_configured_government_artifacts(
    settings: Settings,
    database: Database,
    *,
    collector: OfficialSourceCollector | None = None,
    processor: OfficialAttachmentProcessor | None = None,
    source_ids: Iterable[str] | None = None,
) -> dict[str, object]:
    """Discover approved official notice attachments into the private ledger.

    A source needs both to be enabled and to declare
    ``attachment_discovery_enabled: true``.  A source/network failure is
    reported separately from a successful scan that found no notices.  This
    distinction prevents a blocked provincial portal from being misreported as
    having no available positions.
    """
    selected_ids = {str(value).strip() for value in source_ids or () if str(value).strip()}
    collector = collector or OfficialSourceCollector(settings)
    processor = processor or OfficialAttachmentProcessor(settings, database)
    sources = [
        source
        for source in database.list_sources(enabled_only=True)
        if isinstance(source.get("config"), dict)
        and bool(source["config"].get("attachment_discovery_enabled", False))
        and (not selected_ids or str(source["id"]) in selected_ids)
    ]

    summary: dict[str, object] = {
        "configured_sources": len(sources),
        "notices_found": 0,
        "notices_scanned": 0,
        "attachments_observed": 0,
        "attachments_registered": 0,
        "attachments_reencountered": 0,
        "position_table_attachments": 0,
        "blocked": 0,
        "failed": 0,
        "no_notice_sources": 0,
        "source_results": [],
        "publication_policy": (
            "新发现附件先进入私有证据队列；只有通过官方域名、哈希、"
            "岗位行专业、学历、地点、人数、截止日期和学生端门禁的记录，"
            "才允许由自动发布流程展示给学生。"
        ),
    }
    source_results = summary["source_results"]
    assert isinstance(source_results, list)

    for source in sources:
        source_id = str(source["id"])
        result: dict[str, object] = {
            "source_id": source_id,
            "notices_found": 0,
            "notices_scanned": 0,
            "attachments_observed": 0,
            "attachments_registered": 0,
            "attachments_reencountered": 0,
            "position_table_attachments": 0,
            "status": "ok",
            "detail": "",
        }
        # ``discover_from_page`` is intentionally idempotent: its upsert may
        # return an existing artifact after refreshing notice metadata.  Keep
        # that useful observation separate from a new private-ledger entry so
        # a rescan of an expired table is never reported as new capacity.
        known_artifact_urls = {
            str(artifact.get("artifact_url") or "")
            for artifact in database.iter_source_artifacts(source_id=source_id)
            if str(artifact.get("artifact_url") or "")
        }
        try:
            notices = collector.discover_notice_pages(source)
        except SourceSkipped as error:
            summary["blocked"] = int(summary["blocked"]) + 1
            result.update(status="blocked", detail=str(error))
            source_results.append(result)
            continue
        except SourceCollectionError as error:
            summary["failed"] = int(summary["failed"]) + 1
            result.update(status="failed", detail=str(error))
            source_results.append(result)
            continue

        result["notices_found"] = len(notices)
        summary["notices_found"] = int(summary["notices_found"]) + len(notices)
        if not notices:
            summary["no_notice_sources"] = int(summary["no_notice_sources"]) + 1
            result["detail"] = "官方栏目扫描成功，当前未发现符合来源公告过滤规则的招聘通知。"
            source_results.append(result)
            continue

        for notice_index, (title, notice_url) in enumerate(notices, start=1):
            result["notices_scanned"] = int(result["notices_scanned"]) + 1
            summary["notices_scanned"] = int(summary["notices_scanned"]) + 1
            try:
                artifacts = processor.discover_from_page(source_id, notice_url)
            except AttachmentSkipped as error:
                summary["blocked"] = int(summary["blocked"]) + 1
                result["status"] = "partial"
                result["detail"] = _append_detail(
                    str(result["detail"]), f"{title or notice_url}: {error}"
                )
                if notice_index < len(notices):
                    _wait_between_notices(source)
                continue
            except (AttachmentProcessingError, ValueError) as error:
                summary["failed"] = int(summary["failed"]) + 1
                result["status"] = "partial"
                result["detail"] = _append_detail(
                    str(result["detail"]), f"{title or notice_url}: {error}"
                )
                if notice_index < len(notices):
                    _wait_between_notices(source)
                continue

            position_tables = sum(
                1
                for artifact in artifacts
                if str(artifact.get("artifact_kind") or "") == "position_table"
            )
            observed = len(artifacts)
            newly_registered = 0
            for artifact in artifacts:
                artifact_url = str(artifact.get("artifact_url") or "")
                if artifact_url and artifact_url not in known_artifact_urls:
                    newly_registered += 1
                    known_artifact_urls.add(artifact_url)
            reencountered = observed - newly_registered
            result["attachments_observed"] = int(result["attachments_observed"]) + observed
            result["attachments_registered"] = int(
                result["attachments_registered"]
            ) + newly_registered
            result["attachments_reencountered"] = int(
                result["attachments_reencountered"]
            ) + reencountered
            result["position_table_attachments"] = int(
                result["position_table_attachments"]
            ) + position_tables
            summary["attachments_observed"] = int(summary["attachments_observed"]) + observed
            summary["attachments_registered"] = int(
                summary["attachments_registered"]
            ) + newly_registered
            summary["attachments_reencountered"] = int(
                summary["attachments_reencountered"]
            ) + reencountered
            summary["position_table_attachments"] = int(
                summary["position_table_attachments"]
            ) + position_tables
            if notice_index < len(notices):
                _wait_between_notices(source)
        source_results.append(result)

    summary["requested_source_ids"] = sorted(selected_ids) if selected_ids else None
    # A blocked source is not a successful discovery cycle.  Keeping ``ok``
    # true for robots/access failures makes an all-blocked run look healthy
    # and can mislead operators into treating an empty queue as authoritative.
    # A completed scan with no notices remains healthy because it increments
    # ``no_notice_sources`` rather than ``blocked`` or ``failed``.
    summary["ok"] = int(summary["failed"]) == 0 and int(summary["blocked"]) == 0
    return summary


def _append_detail(existing: str, value: str) -> str:
    """Keep bounded diagnostics without exposing an unbounded remote response."""
    combined = "; ".join(part for part in (existing, value) if part)
    return combined[:1_000]


def _wait_between_notices(source: dict[str, Any]) -> None:
    """Apply the source's declared public-page pacing to attachment discovery."""
    config = source.get("config")
    if not isinstance(config, dict):
        return
    try:
        seconds = float(config.get("request_interval_seconds", 0.75))
    except (TypeError, ValueError):
        seconds = 0.75
    if seconds > 0:
        time.sleep(min(seconds, 5.0))
