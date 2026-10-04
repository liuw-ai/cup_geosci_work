"""Controlled activation of a manually verified official snapshot.

Snapshots are useful when an official dynamic portal is temporarily blocked,
but they must not masquerade as a live source.  This module gives operators a
repeatable, auditable way to publish only rows that pass the normal pipeline
and keeps the dynamic source separate for later atomic replacement.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from job_hub.config import Settings
from job_hub.db import Database
from job_hub.pipeline import JobPipeline
from job_hub.profiles import PUBLIC_STUDENT_PUBLICATION_STATUSES
from job_hub.sources import OfficialSourceCollector


def activate_sinopec_snapshot(
    settings: Settings,
    database: Database,
    *,
    source_id: str = "sinopec-2027-geoscience-snapshot",
    snapshot_path: Path | str | None = None,
) -> dict[str, Any]:
    """Import a verified Sinopec snapshot without enabling live collection.

    The registered source remains ``manual`` and disabled, which is allowed by
    the public-read contract but excluded from scheduled collection.  The
    collector is invoked with a temporary ``sinopec_spa_rows`` view solely to
    reuse its schema, URL allowlist and field-evidence construction.
    """

    source = database.get_source(source_id)
    if source is None:
        raise ValueError(f"snapshot source is not registered: {source_id}")
    if str(source.get("source_type") or "") != "manual":
        raise ValueError("snapshot activation requires a registered manual source")
    config = dict(source.get("config") or {})
    configured_path = str(snapshot_path or config.get("snapshot_path") or "").strip()
    if not configured_path:
        raise ValueError("snapshot source is missing config.snapshot_path")

    # Force the capture reader onto the versioned file.  max_age is deliberately
    # disabled here: freshness is reported in the result and the source remains
    # a manual transition until a live browser refresh replaces it.
    reader_source = {
        **source,
        "source_type": "sinopec_spa_rows",
        "enabled": True,
        "config": {
            **config,
            "snapshot_path": configured_path,
            "capture_path": "",
            # A verified snapshot is already a bounded, administrator-reviewed
            # artifact.  Do not inherit the conservative live-source cap
            # (MAX_SOURCE_ITEMS, 80 in production), or a 363-row capture would
            # be silently truncated during activation.
            "max_items": min(
                max(int(config.get("captured_job_rows") or 1_000), 1),
                1_000,
            ),
            "max_age_hours": None,
            "require_complete_manifest": True,
        },
    }
    collector = OfficialSourceCollector(settings)
    postings = collector._collect_sinopec_spa_rows(reader_source)
    pipeline = JobPipeline(settings, database, collector=collector)
    outcomes = Counter()
    publication = Counter()
    for posting in postings:
        normalized = pipeline.normalize_posting(posting, reader_source)
        # Preserve the manual source identity in the database while retaining
        # the dynamic replacement source in the registry metadata.
        normalized["source_id"] = source_id
        normalized["source_name"] = source["name"]
        normalized["fingerprint"] = pipeline.normalize_posting(
            posting, {**reader_source, "id": source_id}
        )["fingerprint"]
        _, outcome = database.save_job(normalized)
        outcomes[outcome] += 1
        publication[str(normalized.get("publication_status") or "unknown")] += 1

    # Public reads enforce ``max_age_hours`` from the source registry through
    # ``source_capture_freshness``.  Manual snapshot activation is a complete
    # capture just like a browser worker run, so it must register the evidence
    # timestamp or every otherwise eligible row will be hidden forever.  The
    # timestamp comes from the versioned capture metadata, never from the
    # activation time; re-running an old snapshot must not renew its freshness.
    captured_at = str(config.get("snapshot_captured_at") or "").strip()
    if captured_at:
        database.record_source_capture_freshness(
            source_id,
            captured_at=captured_at,
            evidence_kind="manual_verified_snapshot",
        )

    public_rows, public_count = database.list_jobs(page_size=None)
    visible_rows = [row for row in public_rows if row.get("source_id") == source_id]
    return {
        "source_id": source_id,
        "snapshot_path": configured_path,
        "captured_at": config.get("snapshot_captured_at"),
        "captured_rows": len(postings),
        "capture_freshness_recorded": bool(captured_at),
        "publication_status": dict(publication),
        "visible_rows": len(visible_rows),
        "public_total": public_count,
        "outcomes": dict(outcomes),
        "student_publication_statuses": sorted(PUBLIC_STUDENT_PUBLICATION_STATUSES),
        "live_replacement_source_id": config.get("dynamic_replacement_source_id"),
        "freshness_note": "人工核验快照，不代表动态源已恢复每日实时采集。",
    }


__all__ = ["activate_sinopec_snapshot"]
