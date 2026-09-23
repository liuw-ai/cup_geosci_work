from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from job_hub.config import Settings
from job_hub.db import Database
from job_hub.employers import resolve_employer
from job_hub.locations import extract_location_hint, normalize_location
from job_hub.matching import (
    classify_category,
    extract_degree_levels,
    has_major_qualification_evidence,
    is_expired,
    qualification_evidence_text,
    score_relevance,
    stable_hash,
    structured_evidence_text,
)
from job_hub.sources import (
    OfficialSourceCollector,
    RawPosting,
    SourceCollectionError,
    SourceSkipped,
    load_source_registries,
)


@dataclass
class SourceSyncResult:
    source_id: str
    status: str
    discovered: int = 0
    open_matches: int = 0
    created: int = 0
    updated: int = 0
    skipped: int = 0
    error: str | None = None
    run_id: int | None = None


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
    def open_matches(self) -> int:
        return sum(item.open_matches for item in self.source_results)

    @property
    def updated(self) -> int:
        return sum(item.updated for item in self.source_results)

    @property
    def failed(self) -> int:
        return sum(1 for item in self.source_results if item.status == "failed")

    @property
    def blocked(self) -> int:
        return sum(
            1
            for item in self.source_results
            if item.status == "skipped" and item.error
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "discovered": self.discovered,
            "open_matches": self.open_matches,
            "created": self.created,
            "updated": self.updated,
            "expired": self.expired,
            "recovered_crawl_runs": self.recovered_crawl_runs,
            "failed": self.failed,
            "blocked": self.blocked,
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
        registry_paths = [str(self.settings.source_registry_path)]
        provincial_registry = self.settings.source_registry_path.with_name(
            "provincial_sources.json"
        )
        if provincial_registry.exists():
            registry_paths.append(str(provincial_registry))
        sources = load_source_registries(registry_paths)
        for source in sources:
            self.database.upsert_source(source)
        self.database.ensure_source_tasks(
            [source for source in sources if source.get("enabled", True)]
        )
        return len(sources)

    def sync_all(self) -> SyncSummary:
        recovered = self.database.recover_stale_crawl_runs(
            self.settings.crawl_run_stale_seconds
        )
        sources = self.database.list_sources(True)
        self.database.ensure_source_tasks(sources)
        results: list[SourceSyncResult] = []
        for source in sources:
            task = self.database.claim_source_task(
                source["id"],
                lease_seconds=self.settings.crawl_run_stale_seconds,
            )
            if task is None:
                results.append(
                    SourceSyncResult(
                        source_id=source["id"],
                        status="deferred",
                        error="source task is not due yet",
                    )
                )
                continue
            result = self.sync_source(source)
            results.append(result)
            if result.status == "finished":
                self.database.complete_source_task(
                    source["id"],
                    next_attempt_seconds=self.settings.source_sync_interval_minutes * 60,
                    run_id=result.run_id,
                )
            else:
                blocked = result.status == "skipped" or self._is_policy_error(result.error)
                retry_after = (
                    max(3600, self.settings.source_sync_interval_minutes * 60)
                    if blocked
                    else max(60, min(1800, self.settings.source_sync_interval_minutes * 60))
                )
                self.database.fail_source_task(
                    source["id"],
                    error=result.error or "source task failed",
                    error_class="access_policy" if blocked else "collector_error",
                    retry_after_seconds=retry_after,
                    blocked=blocked,
                    run_id=result.run_id,
                )
        today = datetime.now(self.timezone).date().isoformat()
        expired = self.database.expire_jobs_before(today)
        return SyncSummary(results, expired, len(recovered))

    def sync_source(self, source: dict[str, Any]) -> SourceSyncResult:
        self.database.recover_stale_crawl_runs(
            self.settings.crawl_run_stale_seconds,
            source["id"],
        )
        run_id = self.database.record_crawl_start(source["id"])
        result = SourceSyncResult(source_id=source["id"], status="finished", run_id=run_id)
        try:
            collect = getattr(self.collector, "collect_with_fallback", None)
            postings = (
                collect(source)
                if callable(collect)
                else self.collector.collect(source)
            )
            result.discovered = len(postings)
            for posting in postings:
                normalized = self.normalize_posting(posting, source)
                minimum_score = int(source["config"].get("minimum_relevance", 0))
                if normalized["relevance_score"] < minimum_score:
                    result.skipped += 1
                    continue
                if normalized["status"] == "open":
                    result.open_matches += 1
                _, outcome = self.database.save_job(normalized)
                if outcome == "created":
                    result.created += 1
                elif outcome == "updated":
                    result.updated += 1
            if source["config"].get("deduplicate_by_title"):
                self.database.supersede_duplicate_jobs(source["id"])
            self.database.mark_source_synced(source["id"])
            self.database.record_source_health(
                source["id"],
                status="source_active",
                detail=(
                    f"公开采集完成：发现 {result.discovered} 条候选，"
                    f"当前在招匹配 {result.open_matches} 条，"
                    f"新增 {result.created} 条，更新 {result.updated} 条，"
                    f"过滤 {result.skipped} 条。"
                ),
                successful=True,
            )
            self.database.record_crawl_finish(
                run_id,
                result.status,
                discovered_count=result.discovered,
                open_matching_count=result.open_matches,
                inserted_count=result.created,
                updated_count=result.updated,
            )
        except SourceSkipped as error:
            result.status = "skipped"
            result.error = str(error)
            self.database.record_source_health(
                source["id"],
                status=self._skipped_source_health_status(result.error),
                detail=result.error,
            )
            self.database.record_crawl_finish(
                run_id,
                result.status,
                error_message=result.error,
            )
        except Exception as error:
            result.status = "failed"
            result.error = str(error)
            self.database.record_source_health(
                source["id"],
                status="source_error",
                detail=result.error,
            )
            self.database.record_crawl_finish(
                run_id,
                result.status,
                error_message=result.error[:1500],
            )
        return result

    @staticmethod
    def _is_policy_error(error: str | None) -> bool:
        normalized = str(error or "").lower()
        return any(
            marker in normalized
            for marker in (
                "robots",
                "http 401",
                "http 403",
                "http 407",
                "http 412",
                "access policy",
                "not permit",
                "captcha",
            )
        )

    def normalize_posting(
        self,
        posting: RawPosting,
        source: dict[str, Any],
    ) -> dict[str, Any]:
        text = f"{posting.title} {posting.employer} {posting.text}"
        evidence_text = qualification_evidence_text(posting.field_evidence)
        matching_text = posting.match_text or " ".join(
            value
            for value in (posting.title, posting.employer, posting.summary, evidence_text)
            if value
        )
        has_qualification_evidence = has_major_qualification_evidence(
            posting.field_evidence
        )
        employer_identity = resolve_employer(posting.employer)
        category = (
            employer_identity["category"]
            if employer_identity
            else classify_category(
                matching_text,
                source.get("category"),
                identity_text=f"{posting.title} {posting.employer}",
            )
        )
        location_text = posting.location or extract_location_hint(posting.text)
        location = normalize_location(location_text)
        relevance_score, relevance_band, major_tags = score_relevance(
            matching_text,
            source["source_tier"],
            category,
            qualification_evidence=has_qualification_evidence,
        )
        qualification_text = (
            posting.qualification_text
            or structured_evidence_text(posting.field_evidence)
            or posting.summary
            or posting.title
        )
        degree_levels = extract_degree_levels(qualification_text)
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
            "location": location_text,
            "canonical_employer_id": (
                employer_identity.get("canonical_employer_id")
                if employer_identity
                else None
            ),
            "canonical_employer_name": (
                employer_identity.get("canonical_employer_name")
                if employer_identity
                else None
            ),
            "parent_employer_name": (
                employer_identity.get("parent_employer_name")
                if employer_identity
                else None
            ),
            **location,
            "verification_status": "published_official",
            "official_evidence_url": posting.official_evidence_url or posting.source_url,
            "published_date": posting.published_date,
            "deadline_date": posting.deadline_date,
            "degree_levels": degree_levels,
            "major_tags": major_tags,
            "field_evidence": posting.field_evidence or {},
            "summary": posting.summary,
            "description": posting.text[:12000],
            "relevance_score": relevance_score,
            "relevance_band": relevance_band,
            "status": status,
        }

    @staticmethod
    def _skipped_source_health_status(detail: str) -> str:
        normalized = detail.lower()
        if "robots" in normalized or "permit" in normalized:
            return "source_blocked"
        return "source_degraded"

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
        result = {
            "checked": len(jobs),
            "reclassified": 0,
            "normalized": 0,
            "unchanged": 0,
        }
        for job in jobs:
            source = sources.get(job.get("source_id"))
            source_tier = (
                source["source_tier"] if source else str(job["source_tier"])
            )
            source_category = source["category"] if source else job.get("category")
            evidence_text = qualification_evidence_text(job.get("field_evidence"))
            matching_text = " ".join(
                str(value)
                for value in (
                    job.get("title"),
                    job.get("employer"),
                    job.get("summary"),
                    evidence_text,
                )
                if value
            )
            has_qualification_evidence = has_major_qualification_evidence(
                job.get("field_evidence")
            )
            employer_identity = resolve_employer(str(job.get("employer") or ""))
            category = (
                employer_identity["category"]
                if employer_identity
                else classify_category(
                    matching_text,
                    source_category,
                    identity_text=f"{job.get('title', '')} {job.get('employer', '')}",
                )
            )
            relevance_score, relevance_band, major_tags = score_relevance(
                matching_text,
                source_tier,
                category,
                qualification_evidence=has_qualification_evidence,
            )
            degree_levels = extract_degree_levels(
                " ".join(
                    value
                    for value in (
                        structured_evidence_text(job.get("field_evidence")),
                        job.get("summary"),
                        job.get("title"),
                    )
                    if value
                )
            )
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
            existing_location = str(job.get("location") or "").strip() or None
            location_text = existing_location or extract_location_hint(
                f"{job.get('summary', '')} {job.get('description', '')}"
            )
            location = normalize_location(location_text)
            normalized = self.database.update_job_normalization(
                int(job["id"]),
                canonical_employer_id=(
                    employer_identity.get("canonical_employer_id")
                    if employer_identity
                    else None
                ),
                canonical_employer_name=(
                    employer_identity.get("canonical_employer_name")
                    if employer_identity
                    else None
                ),
                parent_employer_name=(
                    employer_identity.get("parent_employer_name")
                    if employer_identity
                    else None
                ),
                location=location_text,
                **location,
            )
            if changed:
                result["reclassified"] += 1
            if normalized:
                result["normalized"] += 1
            if not changed and not normalized:
                result["unchanged"] += 1
        return result
