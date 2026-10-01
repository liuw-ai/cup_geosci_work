"""Daily discovery of official government recruitment attachments.

This module bridges registered official announcement feeds and the existing
controlled attachment processor.  It intentionally ends at the private review
queue: a discovered PDF or spreadsheet is neither an active job nor evidence
that a student is eligible.  Publication remains an explicit row-level review
operation in the administrator interface.
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
        "attachments_registered": 0,
        "position_table_attachments": 0,
        "blocked": 0,
        "failed": 0,
        "no_notice_sources": 0,
        "source_results": [],
        "publication_policy": (
            "新发现附件只进入私有待复核队列；必须逐岗位核验官方原文、"
            "专业、学历、地点、人数和截止日期后，才允许展示给学生。"
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
            "attachments_registered": 0,
            "position_table_attachments": 0,
            "status": "ok",
            "detail": "",
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
            result["attachments_registered"] = int(
                result["attachments_registered"]
            ) + len(artifacts)
            result["position_table_attachments"] = int(
                result["position_table_attachments"]
            ) + position_tables
            summary["attachments_registered"] = int(
                summary["attachments_registered"]
            ) + len(artifacts)
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
