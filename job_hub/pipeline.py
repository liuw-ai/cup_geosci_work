from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from job_hub.config import Settings
from job_hub.db import Database
from job_hub.matching import (
    classify_category,
    extract_degree_levels,
    is_expired,
    score_relevance,
    stable_hash,
)
from job_hub.sources import (
    OfficialSourceCollector,
    RawPosting,
    SourceCollectionError,
    SourceSkipped,
    load_source_registry,
)


@dataclass
class SourceSyncResult:
    source_id: str
    status: str
    discovered: int = 0
    created: int = 0
    updated: int = 0
    skipped: int = 0
    error: str | None = None


@dataclass
class SyncSummary:
    source_results: list[SourceSyncResult]
    expired: int
    recovered_crawl_runs: int = 0

    @property
    def discovered(self) -> int:
        return sum(item.discovered for item in self.source_results)

    @property
    def created(self) -> int:
        return sum(item.created for item in self.source_results)

    @property
    def updated(self) -> int:
        return sum(item.updated for item in self.source_results)

    @property
    def failed(self) -> int:
        return sum(1 for item in self.source_results if item.status == "failed")

    def as_dict(self) -> dict[str, Any]:
        return {
            "discovered": self.discovered,
            "created": self.created,
            "updated": self.updated,
            "expired": self.expired,
            "recovered_crawl_runs": self.recovered_crawl_runs,
            "failed": self.failed,
            "sources": [asdict(item) for item in self.source_results],
        }


class JobPipeline:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        collector: OfficialSourceCollector | None = None,
    ) -> None:
        self.settings = settings
        self.database = database
        self.collector = collector or OfficialSourceCollector(settings)
        self.timezone = ZoneInfo(settings.timezone)

    def bootstrap_sources(self) -> int:
        sources = load_source_registry(str(self.settings.source_registry_path))
        for source in sources:
            self.database.upsert_source(source)
        return len(sources)

    def sync_all(self) -> SyncSummary:
        recovered = self.database.recover_stale_crawl_runs(
            self.settings.crawl_run_stale_seconds
        )
        results = [self.sync_source(source) for source in self.database.list_sources(True)]
        today = datetime.now(self.timezone).date().isoformat()
        expired = self.database.expire_jobs_before(today)
        return SyncSummary(results, expired, len(recovered))

    def sync_source(self, source: dict[str, Any]) -> SourceSyncResult:
        self.database.recover_stale_crawl_runs(
            self.settings.crawl_run_stale_seconds,
            source["id"],
        )
        run_id = self.database.record_crawl_start(source["id"])
        result = SourceSyncResult(source_id=source["id"], status="finished")
        try:
            postings = self.collector.collect(source)
            result.discovered = len(postings)
            for posting in postings:
                normalized = self.normalize_posting(posting, source)
                minimum_score = int(source["config"].get("minimum_relevance", 0))
                if normalized["relevance_score"] < minimum_score:
                    result.skipped += 1
                    continue
                _, outcome = self.database.save_job(normalized)
                if outcome == "created":
                    result.created += 1
                elif outcome == "updated":
                    result.updated += 1
            self.database.mark_source_synced(source["id"])
            self.database.record_crawl_finish(
                run_id,
                result.status,
                result.discovered,
                result.created,
                result.updated,
            )
        except SourceSkipped as error:
            result.status = "skipped"
            result.error = str(error)
            self.database.record_crawl_finish(
                run_id,
                result.status,
                error_message=result.error,
            )
        except Exception as error:
            result.status = "failed"
            result.error = str(error)
            self.database.record_crawl_finish(
                run_id,
                result.status,
                error_message=result.error[:1500],
            )
        return result

    def normalize_posting(
        self,
        posting: RawPosting,
        source: dict[str, Any],
    ) -> dict[str, Any]:
        text = f"{posting.title} {posting.employer} {posting.text}"
        matching_text = posting.match_text or text
        category = classify_category(
            matching_text,
            source.get("category"),
            identity_text=f"{posting.title} {posting.employer}",
        )
        relevance_score, relevance_band, major_tags = score_relevance(
            matching_text,
            source["source_tier"],
            category,
        )
        degree_levels = extract_degree_levels(text)
        today = datetime.now(self.timezone).date()
        status = self._job_status(
            posting.deadline_date,
            published_date=posting.published_date,
            source=source,
            today=today,
        )
        fingerprint = stable_hash(
            source["id"],
            posting.external_id or posting.source_url,
        )
        content_hash = stable_hash(
            posting.title,
            posting.employer,
            posting.source_url,
            posting.application_url or "",
            posting.text,
            posting.deadline_date or "",
        )
        return {
            "source_id": source["id"],
            "external_id": posting.external_id,
            "fingerprint": fingerprint,
            "content_hash": content_hash,
            "title": posting.title,
            "employer": posting.employer,
            "group_name": source["publisher"],
            "category": category,
            "source_tier": source["source_tier"],
            "source_name": source["name"],
            "source_url": posting.source_url,
            "application_url": posting.application_url,
            "location": posting.location,
            "published_date": posting.published_date,
            "deadline_date": posting.deadline_date,
            "degree_levels": degree_levels,
            "major_tags": major_tags,
            "summary": posting.summary,
            "description": posting.text[:12000],
            "relevance_score": relevance_score,
            "relevance_band": relevance_band,
            "status": status,
        }

    @staticmethod
    def _job_status(
        deadline_date: str | None,
        *,
        published_date: str | None,
        source: dict[str, Any],
        today: date,
    ) -> str:
        """Avoid treating old undated announcements as currently accepting applications."""
        if is_expired(deadline_date, today):
            return "expired"
        if deadline_date:
            return "open"
        max_age_days = source.get("config", {}).get("undated_open_window_days")
        if not published_date or max_age_days is None:
            return "open"
        try:
            published = date.fromisoformat(published_date)
            return "expired" if (today - published).days > int(max_age_days) else "open"
        except (TypeError, ValueError):
            return "open"

    def reindex_jobs(self) -> dict[str, int]:
        """Recompute taxonomy and matching fields for existing official records.

        Reindexing is deliberately separate from source synchronization.  It
        preserves fingerprints, original URLs, descriptions, dates and ordinary
        update events while recording a dedicated internal ``reclassified`` event
        only when a derived value actually changes.
        """
        sources = {source["id"]: source for source in self.database.list_sources()}
        jobs, _ = self.database.list_jobs(page_size=None, only_open=False)
        result = {"checked": len(jobs), "reclassified": 0, "unchanged": 0}
        for job in jobs:
            source = sources.get(job.get("source_id"))
            source_tier = (
                source["source_tier"] if source else str(job["source_tier"])
            )
            source_category = source["category"] if source else job.get("category")
            matching_text = " ".join(
                str(value)
                for value in (
                    job.get("title"),
                    job.get("employer"),
                    job.get("description"),
                )
                if value
            )
            category = classify_category(
                matching_text,
                source_category,
                identity_text=f"{job.get('title', '')} {job.get('employer', '')}",
            )
            relevance_score, relevance_band, major_tags = score_relevance(
                matching_text,
                source_tier,
                category,
            )
            degree_levels = extract_degree_levels(matching_text)
            status = self._job_status(
                str(job.get("deadline_date") or "") or None,
                published_date=str(job.get("published_date") or "") or None,
                source=source or {"config": {}},
                today=datetime.now(self.timezone).date(),
            )
            changed = self.database.update_derived_job_fields(
                int(job["id"]),
                category=category,
                degree_levels=degree_levels,
                major_tags=major_tags,
                relevance_score=relevance_score,
                relevance_band=relevance_band,
                status=status,
            )
            result["reclassified" if changed else "unchanged"] += 1
        return result
