from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from job_hub.config import Settings
from job_hub.db import Database
from job_hub.employers import resolve_employer
from job_hub.government_positions import (
    current_publishable_position_records,
    load_position_registry,
)
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
from job_hub.profiles import (
    PUBLICATION_PENDING_EVIDENCE,
    PublicationDecision,
    evaluate_student_publication,
)
from job_hub.sources import (
    OfficialSourceCollector,
    RawPosting,
    SourceCollectionError,
    SourceSkipped,
    load_source_registries,
)
from job_hub.run_ledger import classify_run_outcome


@dataclass
class SourceSyncResult:
    source_id: str
    status: str
    discovered: int = 0
    open_matches: int = 0
    created: int = 0
    updated: int = 0
    withdrawn: int = 0
    skipped: int = 0
    error: str | None = None
    run_id: int | None = None


SyncProgressCallback = Callable[[int, int, str], None]


@dataclass
class SyncSummary:
    source_results: list[SourceSyncResult]
    expired: int
    recovered_crawl_runs: int = 0
    recovered_source_tasks: int = 0

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
    def withdrawn(self) -> int:
        return sum(item.withdrawn for item in self.source_results)

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
            "withdrawn": self.withdrawn,
            "expired": self.expired,
            "recovered_crawl_runs": self.recovered_crawl_runs,
            "recovered_source_tasks": self.recovered_source_tasks,
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

    def sync_all(
        self,
        *,
        progress_callback: SyncProgressCallback | None = None,
    ) -> SyncSummary:
        recovered = self.database.recover_stale_crawl_runs(
            self.settings.crawl_run_stale_seconds
        )
        recovered_tasks = self.database.recover_expired_source_tasks()
        sources = self.database.list_sources(True)
        self.database.ensure_source_tasks(sources)
        results: list[SourceSyncResult] = []
        total_sources = len(sources)
        for index, source in enumerate(sources, start=1):
            if progress_callback is not None:
                try:
                    progress_callback(index - 1, total_sources, str(source["id"]))
                except Exception:
                    # Progress reporting must never change source collection
                    # semantics or turn a healthy sync into a failed one.
                    pass
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
                if progress_callback is not None:
                    try:
                        progress_callback(index, total_sources, str(source["id"]))
                    except Exception:
                        pass
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
                retry_after = self._source_retry_after_seconds(task, blocked=blocked)
                self.database.fail_source_task(
                    source["id"],
                    error=result.error or "source task failed",
                    error_class="access_policy" if blocked else "collector_error",
                    retry_after_seconds=retry_after,
                    blocked=blocked,
                    run_id=result.run_id,
                )
            if progress_callback is not None:
                try:
                    progress_callback(index, total_sources, str(source["id"]))
                except Exception:
                    pass
        today = datetime.now(self.timezone).date().isoformat()
        expired = self.database.expire_jobs_before(today)
        return SyncSummary(
            results,
            expired,
            recovered_crawl_runs=len(recovered),
            recovered_source_tasks=len(recovered_tasks),
        )

    def _source_retry_after_seconds(
        self,
        task: dict[str, Any] | None,
        *,
        blocked: bool,
    ) -> int:
        """Return a bounded exponential delay for one source queue item.

        ``attempts`` is a lifetime counter and is useful for audit reports;
        ``consecutive_failures`` is the operational signal used here.  A
        successful run resets the latter, so one old incident cannot make a
        healthy source wait several hours forever.  Access-policy failures
        deliberately start with a longer delay and never become a tight loop.
        """
        consecutive = max(
            0,
            int((task or {}).get("consecutive_failures") or 0),
        )
        if blocked:
            base = max(
                1,
                int(getattr(self.settings, "source_blocked_retry_base_seconds", 21_600)),
            )
            maximum = max(
                base,
                int(getattr(self.settings, "source_blocked_retry_max_seconds", 86_400)),
            )
        else:
            base = max(
                1,
                int(getattr(self.settings, "source_retry_base_seconds", 300)),
            )
            maximum = max(
                base,
                int(getattr(self.settings, "source_retry_max_seconds", 21_600)),
            )
        # The task passed to this method is the row before fail_source_task
        # increments its consecutive failure counter.
        exponent = min(consecutive, 30)
        return min(maximum, base * (2**exponent))

    def sync_source(self, source: dict[str, Any]) -> SourceSyncResult:
        self.database.recover_stale_crawl_runs(
            self.settings.crawl_run_stale_seconds,
            source["id"],
        )
        request_policy = getattr(self.collector, "request_policy", None)
        if request_policy is not None and hasattr(request_policy, "reset_run_metrics"):
            request_policy.reset_run_metrics()
        transport_mode = str(
            getattr(request_policy, "transport_mode", None)
            or self.settings.http_transport_mode
        )
        run_id = self.database.record_crawl_start(
            source["id"], transport_mode=transport_mode
        )
        result = SourceSyncResult(source_id=source["id"], status="finished", run_id=run_id)
        evidence_complete_count = 0
        manual_review_count = 0
        try:
            collect = getattr(self.collector, "collect_with_fallback", None)
            postings = (
                collect(source)
                if callable(collect)
                else self.collector.collect(source)
            )
            result.discovered = len(postings)
            current_external_ids: set[str] = set()
            for posting in postings:
                normalized = self.normalize_posting(posting, source)
                minimum_score = int(source["config"].get("minimum_relevance", 0))
                if normalized["relevance_score"] < minimum_score:
                    result.skipped += 1
                    continue
                if (
                    normalized["status"] == "open"
                    and normalized["publication_status"]
                    in {"student_eligible", "unrestricted_eligible"}
                ):
                    result.open_matches += 1
                    if normalized.get("official_evidence_url") and normalized.get(
                        "field_evidence"
                    ):
                        evidence_complete_count += 1
                if normalized.get("publication_status") == PUBLICATION_PENDING_EVIDENCE:
                    manual_review_count += 1
                _, outcome = self.database.save_job(normalized)
                if posting.external_id:
                    current_external_ids.add(str(posting.external_id))
                if outcome == "created":
                    result.created += 1
                elif outcome == "updated":
                    result.updated += 1
            if source["config"].get("reconcile_missing_external_ids"):
                result.withdrawn = self.database.withdraw_missing_source_jobs(
                    source["id"],
                    current_external_ids,
                    reason="完整官方动态清单中已不再出现该岗位。",
                )
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
                    f"过滤 {result.skipped} 条，撤回 {result.withdrawn} 条。"
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
                outcome=classify_run_outcome(
                    result.status,
                    open_matching_count=result.open_matches,
                    discovered_count=result.discovered,
                    manual_review_count=manual_review_count,
                ),
                **self._run_metrics(
                    request_policy,
                    transport_mode=transport_mode,
                    evidence_complete_count=evidence_complete_count,
                    manual_review_count=manual_review_count,
                ),
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
                outcome=classify_run_outcome(result.status, error=result.error),
                **self._run_metrics(request_policy, transport_mode=transport_mode),
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
                outcome=classify_run_outcome(result.status, error=result.error),
                **self._run_metrics(request_policy, transport_mode=transport_mode),
            )
        return result

    @staticmethod
    def _run_metrics(
        request_policy: Any,
        *,
        transport_mode: str,
        evidence_complete_count: int = 0,
        manual_review_count: int = 0,
    ) -> dict[str, Any]:
        metrics = {}
        if request_policy is not None and hasattr(request_policy, "run_metrics"):
            metrics.update(request_policy.run_metrics())
        return {
            "attempts": int(metrics.get("attempts", 0)),
            "retryable_failures": int(metrics.get("retryable_failures", 0)),
            "transport_mode": str(metrics.get("transport_mode") or transport_mode),
            "evidence_complete_count": int(evidence_complete_count),
            "manual_review_count": int(manual_review_count),
            "metadata": {
                "request_count": int(metrics.get("request_count", 0)),
                "last_error_class": metrics.get("last_error_class"),
            },
        }

    def sync_source_atomically(
        self,
        source: dict[str, Any],
        *,
        before_commit: Callable[[], None] | None = None,
    ) -> SourceSyncResult:
        """Collect and persist one source as one database transaction.

        This is reserved for a source handover where exposing a half-synced
        successor would create duplicate public vacancies.  Collection and all
        normalization happen before any write.  The optional callback shares
        the same transaction and may switch source states only after every
        successor record has been saved successfully.
        """

        self.database.recover_stale_crawl_runs(
            self.settings.crawl_run_stale_seconds,
            source["id"],
        )
        request_policy = getattr(self.collector, "request_policy", None)
        if request_policy is not None and hasattr(request_policy, "reset_run_metrics"):
            request_policy.reset_run_metrics()
        transport_mode = str(
            getattr(request_policy, "transport_mode", None)
            or self.settings.http_transport_mode
        )
        run_id = self.database.record_crawl_start(
            source["id"], transport_mode=transport_mode
        )
        result = SourceSyncResult(source_id=source["id"], status="finished", run_id=run_id)
        evidence_complete_count = 0
        manual_review_count = 0
        try:
            collect = getattr(self.collector, "collect_with_fallback", None)
            postings = (
                collect(source)
                if callable(collect)
                else self.collector.collect(source)
            )
            result.discovered = len(postings)
            minimum_score = int(source["config"].get("minimum_relevance", 0))
            normalized_postings: list[tuple[dict[str, Any], str | None]] = []
            current_external_ids: set[str] = set()
            for posting in postings:
                normalized = self.normalize_posting(posting, source)
                if normalized["relevance_score"] < minimum_score:
                    result.skipped += 1
                    continue
                if (
                    normalized["status"] == "open"
                    and normalized["publication_status"]
                    in {"student_eligible", "unrestricted_eligible"}
                ):
                    result.open_matches += 1
                    if normalized.get("official_evidence_url") and normalized.get(
                        "field_evidence"
                    ):
                        evidence_complete_count += 1
                if normalized.get("publication_status") == PUBLICATION_PENDING_EVIDENCE:
                    manual_review_count += 1
                normalized_postings.append((normalized, posting.external_id))
                if posting.external_id:
                    current_external_ids.add(str(posting.external_id))

            # ``save_job`` and all database helpers reuse this outer
            # transaction.  An exception anywhere below therefore leaves no
            # staged successor rows and, critically, no source-state switch.
            with self.database.transaction():
                for normalized, _external_id in normalized_postings:
                    _, outcome = self.database.save_job(normalized)
                    if outcome == "created":
                        result.created += 1
                    elif outcome == "updated":
                        result.updated += 1
                if source["config"].get("reconcile_missing_external_ids"):
                    result.withdrawn = self.database.withdraw_missing_source_jobs(
                        source["id"],
                        current_external_ids,
                        reason="完整官方动态清单中已不再出现该岗位。",
                    )
                if source["config"].get("deduplicate_by_title"):
                    self.database.supersede_duplicate_jobs(source["id"])
                if before_commit is not None:
                    before_commit()
                self.database.mark_source_synced(source["id"])
                self.database.record_source_health(
                    source["id"],
                    status="source_active",
                    detail=(
                        f"原子公开采集完成：发现 {result.discovered} 条候选，"
                        f"当前在招匹配 {result.open_matches} 条，"
                        f"新增 {result.created} 条，更新 {result.updated} 条，"
                        f"过滤 {result.skipped} 条，撤回 {result.withdrawn} 条。"
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
                    outcome=classify_run_outcome(
                        result.status,
                        open_matching_count=result.open_matches,
                        discovered_count=result.discovered,
                        manual_review_count=manual_review_count,
                    ),
                    **self._run_metrics(
                        request_policy,
                        transport_mode=transport_mode,
                        evidence_complete_count=evidence_complete_count,
                        manual_review_count=manual_review_count,
                    ),
                )
        except SourceSkipped as error:
            result.status = "skipped"
            result.error = str(error)
            result.created = result.updated = result.withdrawn = 0
            self.database.record_source_health(
                source["id"],
                status=self._skipped_source_health_status(result.error),
                detail=result.error,
            )
            self.database.record_crawl_finish(
                run_id,
                result.status,
                error_message=result.error,
                outcome=classify_run_outcome(result.status, error=result.error),
                **self._run_metrics(request_policy, transport_mode=transport_mode),
            )
        except Exception as error:
            result.status = "failed"
            result.error = str(error)
            result.created = result.updated = result.withdrawn = 0
            self.database.record_source_health(
                source["id"], status="source_error", detail=result.error
            )
            self.database.record_crawl_finish(
                run_id,
                result.status,
                error_message=result.error[:1500],
                outcome=classify_run_outcome(result.status, error=result.error),
                **self._run_metrics(request_policy, transport_mode=transport_mode),
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
        field_evidence = dict(posting.field_evidence or {})
        # Keep the CNPC snapshot contract consistent for both live collector
        # runs and administrator imports. The official page currently renders
        # an empty shell when its public showN response lacks recruitInfoObj;
        # the opaque id and status must remain visible in the audit record.
        if source["id"] == "cnpc-career":
            detail_id = parse_qs(urlparse(posting.source_url).query).get("id", [""])[0]
            if detail_id:
                field_evidence.setdefault("官方详情编号", detail_id)
            field_evidence.setdefault(
                "官方详情状态",
                str(
                    source.get("config", {}).get("detail_access_status")
                    or "official_detail_api_degraded"
                ),
            )
            field_evidence.setdefault(
                "官方招聘入口",
                str(source.get("config", {}).get("listing_url") or posting.source_url),
            )
        text = f"{posting.title} {posting.employer} {posting.text}"
        evidence_text = qualification_evidence_text(field_evidence)
        matching_text = posting.match_text or " ".join(
            value
            for value in (posting.title, posting.employer, posting.summary, evidence_text)
            if value
        )
        has_qualification_evidence = has_major_qualification_evidence(
            field_evidence
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
        configured_category = str(
            source.get("config", {}).get("publication_category") or ""
        ).strip()
        if configured_category:
            category = configured_category
        government_type = str(field_evidence.get("政府岗位类型") or "").strip()
        if government_type in {"public_institution", "civil_service", "postdoctoral"}:
            category = {
                "public_institution": "事业单位与人才引进",
                "civil_service": "公务员与选调",
                "postdoctoral": "博士后与科研助理",
            }[government_type]
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
            or structured_evidence_text(field_evidence)
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
        normalized = {
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
            "field_evidence": field_evidence,
            "summary": posting.summary,
            "description": posting.text[:12000],
            "relevance_score": relevance_score,
            "relevance_band": relevance_band,
            "status": status,
        }
        publication = evaluate_student_publication(normalized)
        normalized["publication_status"] = publication.status
        normalized["publication_basis"] = publication.as_dict()
        return normalized

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
        jobs, _ = self.database.list_jobs(
            page_size=None,
            only_open=False,
            student_visible=False,
        )
        result = {
            "checked": len(jobs),
            "reclassified": 0,
            "normalized": 0,
            "unchanged": 0,
        }
        government_context = self._government_reindex_context()
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
            government_type = str(
                (job.get("field_evidence") or {}).get("政府岗位类型") or ""
            ).strip()
            if government_type in {"public_institution", "civil_service", "postdoctoral"}:
                category = {
                    "public_institution": "事业单位与人才引进",
                    "civil_service": "公务员与选调",
                    "postdoctoral": "博士后与科研助理",
                }[government_type]
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
            # Reindexing only refreshes derived taxonomy fields.  Lifecycle
            # status is controlled by source sync, expiry, and source
            # transitions; recomputing it here could reopen a superseded or
            # withdrawn historical row and expose a retired source again.
            status = str(job.get("status") or "open")
            normalized = {
                **job,
                "category": category,
                "degree_levels": degree_levels,
                "major_tags": major_tags,
                "relevance_score": relevance_score,
                "relevance_band": relevance_band,
                "status": status,
            }
            publication = evaluate_student_publication(normalized)
            government_publication = self._government_reindex_publication(
                job,
                government_context,
            )
            if government_publication is not None:
                publication = government_publication
            changed = self.database.update_derived_job_fields(
                int(job["id"]),
                category=category,
                degree_levels=degree_levels,
                major_tags=major_tags,
                relevance_score=relevance_score,
                relevance_band=relevance_band,
                status=status,
                publication_status=publication.status,
                publication_basis=publication.as_dict(),
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

    def sync_source_manual(self, source: dict[str, Any]) -> SourceSyncResult:
        """Run an operator-triggered sync and reconcile its durable queue state.

        ``sync_all`` owns queue claiming because it processes the scheduled
        batch.  The CLI's one-source command intentionally bypasses that batch,
        but it must still update ``source_tasks``; otherwise a successful
        manual recovery leaves an old ``failed`` row visible to monitoring.
        Crawl history is untouched, so the original failure remains auditable.
        """
        self.database.ensure_source_tasks([source])
        claimed = self.database.claim_source_task(
            source["id"],
            lease_seconds=self.settings.crawl_run_stale_seconds,
            force=True,
        )
        if claimed is None:
            return SourceSyncResult(
                source_id=source["id"],
                status="deferred",
                error="source task is already running",
            )
        result = self.sync_source(source)
        if result.status == "finished":
            self.database.complete_source_task(
                source["id"],
                next_attempt_seconds=self.settings.source_sync_interval_minutes * 60,
                run_id=result.run_id,
            )
        else:
            blocked = result.status == "skipped" or self._is_policy_error(result.error)
            retry_after = self._source_retry_after_seconds(claimed, blocked=blocked)
            self.database.fail_source_task(
                source["id"],
                error=result.error or "source task failed",
                error_class="access_policy" if blocked else "collector_error",
                retry_after_seconds=retry_after,
                blocked=blocked,
                run_id=result.run_id,
            )
        return result

    def _government_reindex_context(self) -> dict[str, Any] | None:
        """Build the current government-table publication boundary.

        Government position rows have a second lifecycle beyond generic major
        and degree matching: their official table can be published before the
        application window opens, become stale, or be superseded by a revised
        row. Reindexing must apply the same reviewed-ledger boundary used by
        the daily worker, otherwise a taxonomy repair can accidentally expose
        a future or stale government vacancy.
        """
        path = self.settings.government_position_registry_path
        if path is None:
            return None
        try:
            registry = load_position_registry(path)
        except Exception:
            # A registry loading failure must not make an unrelated taxonomy
            # reindex destructive. The daily worker and audit surface that
            # operational failure; a valid ledger is required for this gate.
            return None
        now = datetime.now(self.timezone)
        source_verifications = {
            str(item["source_id"]): item
            for item in self.database.list_government_source_verifications()
        }
        current_records = current_publishable_position_records(
            registry,
            today=now.date().isoformat(),
            max_age_hours=self.settings.government_position_max_age_hours,
            source_verifications=source_verifications,
            now=now,
        )
        records = list(registry.get("records", []))
        return {
            "today": now.date(),
            "records": records,
            "current_record_ids": {
                str(record["id"]) for record in current_records
            },
            "source_opening_dates": dict(registry.get("source_opening_dates") or {}),
            "source_ids": {
                str(record.get("source_id") or "")
                for record in records
                if str(record.get("source_id") or "").strip()
            },
        }

    @staticmethod
    def _government_reindex_publication(
        job: dict[str, Any],
        context: dict[str, Any] | None,
    ) -> PublicationDecision | None:
        """Fail closed for reviewed government-table rows outside this window."""
        if context is None:
            return None
        source_id = str(job.get("source_id") or "").strip()
        if source_id not in context["source_ids"]:
            return None
        if not JobPipeline._is_government_position_row(job):
            return None

        matching_records = [
            record
            for record in context["records"]
            if JobPipeline._government_record_matches_job(record, job)
        ]
        if any(
            str(record["id"]) in context["current_record_ids"]
            for record in matching_records
        ):
            return None

        reason = "当前官方职位表复核快照未确认该岗位可发布。"
        today = context["today"]
        opening_dates = context["source_opening_dates"]
        for record in matching_records:
            raw_opening = str(
                record.get("opening_date")
                or opening_dates.get(source_id)
                or ""
            ).strip()
            if raw_opening:
                try:
                    if date.fromisoformat(raw_opening) > today:
                        reason = f"官方报名尚未开始（{raw_opening}），岗位暂不对学生端发布。"
                        break
                except ValueError:
                    # Registry validation prevents this in normal operation;
                    # retain the conservative private outcome if a hand-edited
                    # in-memory record is malformed.
                    pass
        return PublicationDecision(
            status=PUBLICATION_PENDING_EVIDENCE,
            label="政府职位表待当前复核",
            reason=reason,
            matched_profile_ids=(),
        )

    @staticmethod
    def _is_government_position_row(job: dict[str, Any]) -> bool:
        external_id = str(job.get("external_id") or "")
        evidence = job.get("field_evidence")
        if not isinstance(evidence, dict):
            evidence = {}
        return (
            external_id.startswith("government-position:")
            or external_id.startswith("artifact-candidate:")
            or str(evidence.get("政府岗位类型") or "").strip()
            in {"public_institution", "civil_service", "postdoctoral"}
            or str(evidence.get("evidence_scope") or "").strip()
            == "official_attachment_row"
        )

    @staticmethod
    def _government_record_matches_job(
        record: dict[str, Any],
        job: dict[str, Any],
    ) -> bool:
        """Recognize both ledger ids and pre-ledger attachment candidates."""
        if str(record.get("source_id") or "") != str(job.get("source_id") or ""):
            return False
        expected_external_id = (
            f"government-position:{record.get('id')}:{record.get('position_code')}"
        )
        if str(job.get("external_id") or "") == expected_external_id:
            return True

        evidence = job.get("field_evidence")
        if not isinstance(evidence, dict):
            return False
        position_code = str(record.get("position_code") or "").strip()
        if str(evidence.get("职位代码") or "").strip() != position_code:
            return False
        if str(job.get("employer") or "").strip() != str(record.get("employer") or "").strip():
            return False
        if str(job.get("deadline_date") or "").strip() != str(record.get("deadline_date") or "").strip():
            return False

        attachment_url = str(record.get("official_attachment_url") or "").strip()
        evidence_urls = {
            str(job.get("official_evidence_url") or "").strip(),
            str(evidence.get("artifact_url") or "").strip(),
            str(evidence.get("官方附件链接") or "").strip(),
        }
        if attachment_url not in evidence_urls:
            return False
        record_major = str(record.get("major_requirement") or "").strip()
        job_major = str(
            evidence.get("专业要求") or evidence.get("专业范围") or ""
        ).strip()
        return bool(
            record_major
            and job_major
            and (
                record_major == job_major
                or record_major in job_major
                or job_major in record_major
            )
        )
