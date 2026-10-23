from __future__ import annotations

import logging
import math
import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from job_hub.config import Settings
from job_hub.capture_evidence import source_requires_declared_capture
from job_hub.cnooc_browser_capture import (
    repair_cnooc_detail_requirement_evidence,
)
from job_hub.db import Database, utc_now
from job_hub.government_artifacts import (
    load_government_artifact_manifest,
    project_government_artifact_manifest,
)
from job_hub.employers import resolve_employer
from job_hub.government_positions import (
    current_publishable_position_records,
    load_position_registry,
)
from job_hub.government_revalidation import requires_manual_government_evidence_confirmation
from job_hub.locations import (
    extract_location_hint,
    extract_location_provinces,
    extract_structured_location_candidates,
    normalize_location,
)
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
    PUBLIC_STUDENT_PUBLICATION_STATUSES,
    PublicationDecision,
    _has_job_level_evidence,
    classify_major_match,
    evaluate_student_publication,
)
from job_hub.official_identity import official_detail_key
from job_hub.sources import (
    OfficialSourceCollector,
    RawPosting,
    SourceCollectionError,
    SourceSkipped,
    load_source_registries,
)
from job_hub.run_ledger import classify_run_outcome


LOGGER = logging.getLogger("job_hub.pipeline")


def _resolve_location_evidence(
    primary_text: str | None,
    *,
    field_evidence: object,
    fallback_text: str | None,
    official_province: str | None,
    allow_unclassified_domestic_address: bool,
) -> tuple[str, dict[str, Any]]:
    """Normalize a row location, using only same-detail evidence as fallback.

    Older browser captures sometimes persisted an unclassified town/address as
    the primary location even though the same official detail contained a
    labelled province/city. Unknown primary locations remain unknown unless a
    candidate from the bound evidence normalizes to an explicit domestic or
    foreign region.
    """

    primary = str(primary_text or "").strip()
    candidates: list[str] = []
    if primary:
        candidates.append(primary)
    for candidate in extract_structured_location_candidates(field_evidence):
        if candidate not in candidates:
            candidates.append(candidate)
    hinted = extract_location_hint(fallback_text or "")
    if hinted and hinted not in candidates:
        candidates.append(hinted)

    first_location = normalize_location(
        primary or None,
        official_province=official_province,
        allow_unclassified_domestic_address=allow_unclassified_domestic_address,
    )

    # A historical row can retain the original multi-location string in its
    # primary column even though the collector recorded the exact location
    # variant in the same official detail evidence. Prefer that bound variant
    # before normalizing the combined string; otherwise the first domestic
    # city would incorrectly classify every variant (including overseas ones)
    # as the same mainland opportunity. Restrict this repair to official
    # evidence scopes so arbitrary imported text cannot rewrite a location.
    if isinstance(field_evidence, dict):
        evidence_scope = str(field_evidence.get("evidence_scope") or "").strip()
        variant = str(field_evidence.get("官方工作地点") or "").strip()
        if (
            variant
            and variant != primary
            and field_evidence.get("地点拆分基准岗位")
            and evidence_scope.startswith("official_")
        ):
            variant_location = normalize_location(
                variant,
                official_province=official_province,
                allow_unclassified_domestic_address=allow_unclassified_domestic_address,
            )
            if variant_location.get("country_or_region") is not None:
                return variant, variant_location

    if first_location.get("country_or_region") is not None:
        return primary, first_location

    # A collector-created location variant is an independent row. Falling
    # back to the original multi-location string would assign its first
    # province to every unrecognized variant and inflate the public count.
    if isinstance(field_evidence, dict) and field_evidence.get("地点拆分基准岗位"):
        if str(field_evidence.get("官方工作地点") or "").strip() == primary:
            return primary, first_location

    for candidate in candidates[1:]:
        location = normalize_location(
            candidate,
            official_province=official_province,
            allow_unclassified_domestic_address=allow_unclassified_domestic_address,
        )
        if location.get("country_or_region") is not None:
            return candidate, location
    return primary, first_location


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
        sources = [
            self._apply_default_freshness_contract(source)
            for source in load_source_registries(registry_paths)
        ]
        for source in sources:
            self.database.upsert_source(source)
        # A database can retain enabled sources from an older registry or a
        # guarded transition. Materialize the same bounded contract there as
        # well; otherwise those rows would bypass the read-side freshness SQL
        # until the next manual registry cleanup.
        for source in self.database.list_sources(enabled_only=True):
            normalized = self._apply_default_freshness_contract(source)
            if normalized.get("config") != source.get("config"):
                self.database.upsert_source(normalized)
        self._backfill_capture_freshness_from_manifests(sources)
        # Older databases recorded successful live syncs only in
        # ``sources.last_synced_at``. Convert that durable history once after
        # materializing the new contract; artifact-backed sources remain
        # fail-closed unless they provide their own capture evidence.
        self._backfill_capture_freshness_from_manifests(
            self.database.list_sources(enabled_only=True)
        )
        self.database.ensure_source_tasks(
            [source for source in sources if source.get("enabled", True)]
        )
        return len(sources)

    def _apply_default_freshness_contract(self, source: dict[str, Any]) -> dict[str, Any]:
        """Materialize the production freshness contract for live sources.

        A source-specific value remains authoritative. When the registry omits
        one, production supplies a bounded default so a stopped worker cannot
        leave an undated live vacancy visible forever. Manual government
        ledgers and explicitly disabled sources have their own evidence
        lifecycle and are intentionally left unchanged.
        """

        default_age = self.settings.default_source_max_age_hours
        if (
            default_age is None
            or not source.get("enabled", True)
            or str(source.get("source_type") or "") == "manual"
        ):
            return source
        config = source.get("config") if isinstance(source.get("config"), dict) else {}
        if "max_age_hours" in config:
            return source
        updated = dict(source)
        materialized = {**config, "max_age_hours": int(default_age)}
        if str(source.get("source_type") or "") == "structured_opening_page":
            # This adapter fetches a live opening page, unlike the static
            # browser/snapshot sources that require an external capture date.
            materialized.setdefault("freshness_mode", "live_sync")
        updated["config"] = materialized
        return updated

    def _backfill_capture_freshness_from_manifests(
        self,
        sources: list[dict[str, Any]],
    ) -> None:
        """Register existing complete capture evidence after a restart.

        This is intentionally a one-way bootstrap of the read-side projection:
        a missing or partial file contributes nothing, and an existing newer
        runtime record is never replaced by an older registry declaration.
        The resulting timestamp is source-health evidence only; publication
        remains governed by official deadline/90-day rules and job evidence.
        """

        for source in sources:
            config = source.get("config") if isinstance(source.get("config"), dict) else {}
            if config.get("max_age_hours") is None:
                continue
            source_id = str(source.get("id") or "").strip()
            if not source_id:
                continue
            existing = self.database.get_source_capture_freshness(source_id)
            # Phase 364 tightened static artifact sources after older
            # deployments had already written a legacy live-sync projection.
            # Invalidate that projection during bootstrap so a restart cannot
            # resurrect freshness evidence that the source type no longer
            # permits.  Declared/artifact timestamps are rebuilt below.
            if (
                existing
                and existing.get("evidence_kind") == "legacy_live_sync"
                and self._source_requires_declared_capture(source)
            ):
                self.database.clear_source_capture_freshness(source_id)
                existing = None
            if existing:
                continue
            captured_at = self._source_capture_timestamp(source)
            evidence_kind = (
                "capture_manifest"
                if str(config.get("capture_path") or "").strip()
                else "declared_snapshot"
            )
            if not captured_at and not self._source_requires_declared_capture(source):
                captured_at = str(source.get("last_synced_at") or "").strip()
                evidence_kind = "legacy_live_sync"
            if not captured_at:
                continue
            try:
                self.database.record_source_capture_freshness(
                    source_id,
                    captured_at=captured_at,
                    evidence_kind=evidence_kind,
                )
            except ValueError:
                LOGGER.warning(
                    "Ignoring invalid bootstrap capture timestamp for source %s",
                    source_id,
                )

    def sync_all(
        self,
        *,
        progress_callback: SyncProgressCallback | None = None,
    ) -> SyncSummary:
        recovered = self.database.recover_stale_crawl_runs(
            self.settings.crawl_run_stale_seconds
        )
        recovered_tasks = self.database.recover_expired_source_tasks()
        sources = [
            source
            for source in self.database.list_sources(True)
            if not self._is_revalidation_managed_government_ledger_source(source)
            and not self._is_browser_worker_managed_source(source)
        ]
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

    @staticmethod
    def _is_revalidation_managed_government_ledger_source(source: dict[str, Any]) -> bool:
        """Keep reviewed government ledgers out of the generic empty collector.

        ``manual`` collectors intentionally return no postings. A government
        table that is revalidated and published through the reviewed ledger is
        therefore not a normal zero-result scan, and must never be scheduled
        as one after its runtime publication gate enables the source.
        """
        config = source.get("config") if isinstance(source.get("config"), dict) else {}
        return (
            str(source.get("source_type") or "") == "manual"
            and bool(config.get("government_evidence_recheck"))
        )

    @staticmethod
    def _is_browser_worker_managed_source(source: dict[str, Any]) -> bool:
        """Keep browser-only captures out of the generic HTTP sync queue.

        A browser worker owns capture, completeness validation and the
        subsequent one-source synchronization.  Letting ``sync_all`` also
        read its artifact would create duplicate scans and, after a failed
        browser refresh, could report a stale artifact as an ordinary source
        collection failure.
        """

        config = source.get("config") if isinstance(source.get("config"), dict) else {}
        return str(config.get("runtime_mode") or "").strip() == "browser_worker_only"

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
        growth_counts = {"official_url_count": 0, "field_complete_count": 0,
                         "precise_major_pass_count": 0,
                         "similar_major_pass_count": 0,
                         "broad_major_pass_count": 0,
                         "public_broad_pass_count": 0,
                         "unrestricted_major_pass_count": 0}
        try:
            collect = getattr(self.collector, "collect_with_fallback", None)
            postings = (
                collect(source)
                if callable(collect)
                else self.collector.collect(source)
            )
            result.discovered = len(postings)
            current_external_ids: set[str] = set()
            staged_jobs: list[dict[str, Any]] = []
            for posting in postings:
                normalized = self.normalize_posting(posting, source)
                self._count_growth_row(growth_counts, normalized, source)
                minimum_score = int(source["config"].get("minimum_relevance", 0))
                if normalized["relevance_score"] < minimum_score:
                    result.skipped += 1
                    continue
                if self._skip_expired_before_persist(normalized, source):
                    # A dynamic listing can retain years of closed announcements.
                    # Keep the scan auditable, but do not turn known-expired rows
                    # into private-review work.  Preserve its ID for complete-list
                    # reconciliation should this source opt into that behavior.
                    if posting.external_id:
                        current_external_ids.add(str(posting.external_id))
                    result.skipped += 1
                    continue
                if (
                    normalized["status"] == "open"
                    and normalized["publication_status"]
                    in PUBLIC_STUDENT_PUBLICATION_STATUSES
                ):
                    result.open_matches += 1
                    if normalized.get("official_evidence_url") and normalized.get(
                        "field_evidence"
                    ):
                        evidence_complete_count += 1
                if normalized.get("publication_status") == PUBLICATION_PENDING_EVIDENCE:
                    manual_review_count += 1
                staged_jobs.append(normalized)
                if posting.external_id:
                    current_external_ids.add(str(posting.external_id))
            # Stage and then commit all generated rows as one unit. A parser
            # failure after the first row must never leave a partial scan in
            # the public database or trigger missing-ID withdrawals.
            with self.database.transaction():
                for normalized in staged_jobs:
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
                self.database.mark_source_synced(source["id"])
                self._record_successful_source_capture(source, run_id=run_id)
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
                        growth_counts=growth_counts,
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
        growth_counts: dict[str, int] | None = None,
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
                **({"growth_counts": {"version": 1, **growth_counts}}
                   if growth_counts is not None else {}),
            },
        }

    @staticmethod
    def _count_growth_row(
        counts: dict[str, int],
        job: dict[str, Any],
        source: dict[str, Any],
    ) -> None:
        """Count one parsed official row without treating a lead as a vacancy."""
        evidence_url = str(job.get("official_evidence_url") or "").strip()
        evidence_host = (urlparse(evidence_url).hostname or "").lower().rstrip(".")
        config = source.get("config") or {}
        allowed_hosts = {
            str(host).lower().rstrip(".")
            for host in config.get("allowed_hosts", [])
            if str(host).strip()
        }
        homepage_host = (urlparse(str(source.get("homepage_url") or "")).hostname or "").lower()
        if homepage_host:
            allowed_hosts.add(homepage_host.rstrip("."))
        if not evidence_host or not any(
            evidence_host == host or evidence_host.endswith("." + host)
            for host in allowed_hosts
        ):
            return
        counts["official_url_count"] += 1
        evidence = job.get("field_evidence")
        if not (
            job.get("title")
            and job.get("employer")
            and job.get("location")
            and job.get("deadline_date")
            and isinstance(evidence, dict)
            and _has_job_level_evidence(job)
            and qualification_evidence_text(evidence)
            and extract_degree_levels(structured_evidence_text(evidence))
        ):
            return
        counts["field_complete_count"] += 1
        for metric_name in ("similar_major_pass_count", "broad_major_pass_count"):
            counts.setdefault(metric_name, 0)
        match_classification = classify_major_match(job)
        match_level = str(match_classification.get("level") or "")
        if match_level == "exact":
            counts["precise_major_pass_count"] += 1
        elif match_level == "similar":
            counts["similar_major_pass_count"] += 1
        elif match_level == "broad":
            counts["broad_major_pass_count"] += 1
        # Re-run the same row-level publication decision used by normalize and
        # Database.save_job.  A collector-supplied publication marker is only
        # a claim; counting it here would let a stale/forged ``exact_four_names``
        # value inflate the expansion funnel before the database write gate.
        decision = evaluate_student_publication(job)
        if decision.status == "student_eligible" and decision.major_match_mode == "public_named_or_code":
            counts["public_broad_pass_count"] += 1
        elif (
            decision.status == "unrestricted_eligible"
            and decision.major_match_mode == "public_unrestricted_rule"
        ):
            counts["unrestricted_major_pass_count"] += 1

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
        growth_counts = {"official_url_count": 0, "field_complete_count": 0,
                         "precise_major_pass_count": 0,
                         "similar_major_pass_count": 0,
                         "broad_major_pass_count": 0,
                         "public_broad_pass_count": 0,
                         "unrestricted_major_pass_count": 0}
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
                self._count_growth_row(growth_counts, normalized, source)
                if normalized["relevance_score"] < minimum_score:
                    result.skipped += 1
                    continue
                if self._skip_expired_before_persist(normalized, source):
                    if posting.external_id:
                        current_external_ids.add(str(posting.external_id))
                    result.skipped += 1
                    continue
                if (
                    normalized["status"] == "open"
                    and normalized["publication_status"]
                    in PUBLIC_STUDENT_PUBLICATION_STATUSES
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
                self._record_successful_source_capture(source, run_id=run_id)
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
                        growth_counts=growth_counts,
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

    @staticmethod
    def _official_government_province(field_evidence: Any) -> str | None:
        """Return only row-level government province evidence for location fallback."""

        if not isinstance(field_evidence, dict):
            return None
        if str(field_evidence.get("政府岗位类型") or "").strip() not in {
            "public_institution",
            "civil_service",
            "postdoctoral",
        }:
            return None
        province = str(field_evidence.get("官方职位表省份") or "").strip()
        return province or None

    @staticmethod
    def _official_detail_precedence(source: dict[str, Any]) -> int:
        """Return a bounded display preference for duplicate official details.

        Lower values win only when two source records point to the exact same
        official detail.  Employer-specific adapters use the neutral default;
        a broad discovery source can be assigned a larger value so it remains
        an auditable fallback instead of replacing richer specialist evidence.
        """

        raw = (source.get("config") or {}).get("official_detail_precedence", 100)
        if isinstance(raw, bool):
            return 100
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return 100
        return value if 0 <= value <= 10_000 else 100

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
        location_text, location = _resolve_location_evidence(
            posting.location or extract_location_hint(posting.text),
            field_evidence=field_evidence,
            fallback_text=posting.text,
            official_province=self._official_government_province(field_evidence),
            allow_unclassified_domestic_address=bool(
                source.get("config", {}).get("allow_unclassified_domestic_address", False)
            ),
        )
        location["provinces"] = extract_location_provinces(
            location_text,
            official_province=self._official_government_province(field_evidence),
        )
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
        detail_key = official_detail_key(
            source_url=posting.source_url,
            application_url=posting.application_url,
            official_evidence_url=posting.official_evidence_url,
            field_evidence=field_evidence,
        )
        # One official announcement may explicitly enumerate independent work
        # locations.  Location variants retain the same detail URL but must
        # remain separate student-facing opportunities under the site's count
        # contract; the collector marks them in the stable external ID.
        if detail_key and "--location-" in str(posting.external_id or ""):
            detail_key = f"{detail_key}::location:{str(posting.external_id).rsplit('--location-', 1)[1]}"
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
            "official_detail_key": detail_key,
            "official_detail_precedence": self._official_detail_precedence(source),
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
        publication = evaluate_student_publication(
            normalized,
            allow_non_domestic=bool(
                (source.get("config") or {}).get("allow_international_public", False)
            ),
        )
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
    def _skip_expired_before_persist(
        normalized: dict[str, Any],
        source: dict[str, Any],
    ) -> bool:
        """Keep source-specific historical archives out of the review queue.

        This is opt-in because some official sources are intentionally imported
        as historical parser fixtures.  Dynamic notice feeds that continuously
        expose old pages can enable it after their date semantics have been
        verified.  It never changes a source failure into a no-vacancy result.
        """
        return bool(
            source.get("config", {}).get("skip_expired_before_persist", False)
            and normalized.get("status") == "expired"
        )

    @staticmethod
    def _job_status(
        deadline_date: str | None,
        *,
        published_date: str | None,
        source: dict[str, Any],
        today: date,
    ) -> str:
        """Apply the explicit deadline or the Phase 3 90-day undated rule."""
        if is_expired(deadline_date, today):
            return "expired"
        if deadline_date:
            return "open"
        # The source registry's historical undated window is intentionally
        # ignored.  Phase 3 uses publication date + 90 calendar days in
        # Asia/Shanghai; rows with no publication date are kept until the
        # first official evidence timestamp is persisted by Database.save_job.
        if not published_date:
            return "open"
        try:
            published = date.fromisoformat(published_date)
            return "expired" if (today - published).days > 90 else "open"
        except (TypeError, ValueError):
            return "open"

    def reindex_jobs(self) -> dict[str, int]:
        """Recompute taxonomy and matching fields for existing official records.

        Reindexing is deliberately separate from source synchronization.  It
        preserves fingerprints, original URLs, descriptions, dates and ordinary
        update events while recording a dedicated internal ``reclassified`` event
        only when a derived value actually changes.
        """
        # The individual database helpers reuse this outer transaction.  A
        # reindex touches both derived fields and evidence repairs, so committing
        # one row at a time turns a small maintenance operation into thousands
        # of SQLite fsyncs and can leave a partially reindexed production view.
        # One transaction is faster and gives the operation an all-or-nothing
        # publication boundary.
        with self.database.transaction():
            return self._reindex_jobs_in_transaction()

    def _reindex_jobs_in_transaction(self) -> dict[str, int]:
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
            "evidence_repaired": 0,
            "evidence_repair_failed": 0,
            "unchanged": 0,
        }
        government_context = self._government_reindex_context()
        for job in jobs:
            source = sources.get(job.get("source_id"))
            cnooc_repair_error: str | None = None
            # The first CNOOC browser snapshot stored an entire job description
            # in the major field.  Reindexing used to bypass the snapshot
            # loader, so that legacy text could be evaluated as a major
            # condition a second time.  Use the same strict parser here and
            # keep rows private when the job-level condition cannot be rebuilt.
            if (
                source is not None
                and source.get("source_type") == "cnooc_browser_rows"
                and isinstance(job.get("field_evidence"), dict)
                and str(job["field_evidence"].get("evidence_scope") or "")
                == "official_zhaopin_detail_initial_data"
            ):
                repaired_job = dict(job)
                repaired_evidence = dict(job["field_evidence"])
                repaired_job["field_evidence"] = repaired_evidence
                try:
                    repair_cnooc_detail_requirement_evidence(
                        repaired_job,
                        repaired_evidence,
                        index=int(job["id"]),
                    )
                except ValueError as error:
                    cnooc_repair_error = str(error)
                    result["evidence_repair_failed"] += 1
                else:
                    if repaired_evidence != job["field_evidence"]:
                        self.database.update_job_field_evidence(
                            int(job["id"]), repaired_evidence
                        )
                        result["evidence_repaired"] += 1
                    job = repaired_job
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
            existing_location = str(job.get("location") or "").strip() or None
            fallback_text = f"{job.get('summary', '')} {job.get('description', '')}"
            source_config = (
                source.get("config")
                if isinstance(source, dict)
                and isinstance(source.get("config"), dict)
                else {}
            )
            location_text, location = _resolve_location_evidence(
                existing_location or extract_location_hint(fallback_text),
                field_evidence=job.get("field_evidence"),
                fallback_text=fallback_text,
                official_province=self._official_government_province(
                    job.get("field_evidence")
                ),
                allow_unclassified_domestic_address=bool(
                    source_config.get("allow_unclassified_domestic_address", False)
                ),
            )
            location["provinces"] = extract_location_provinces(
                location_text,
                official_province=self._official_government_province(
                    job.get("field_evidence")
                ),
            )
            normalized = {
                **job,
                "category": category,
                "degree_levels": degree_levels,
                "major_tags": major_tags,
                "relevance_score": relevance_score,
                "relevance_band": relevance_band,
                "status": status,
                "location": location_text,
                **location,
            }
            publication = evaluate_student_publication(
                normalized,
                allow_non_domestic=bool(
                    source_config.get("allow_international_public", False)
                ),
            )
            if cnooc_repair_error:
                publication = PublicationDecision(
                    status=PUBLICATION_PENDING_EVIDENCE,
                    label="待补岗位级证据",
                    reason=(
                        "海油历史岗位详情无法恢复明确的专业/学历条件，"
                        "不作为学生端岗位发布。"
                    ),
                    matched_profile_ids=(),
                )
            publication_layer: str | None = None
            government_publication = self._government_reindex_publication(
                job,
                government_context,
            )
            if government_publication is not None:
                publication = government_publication
                # A current government-table gate is authoritative.  Its
                # pending decision must also remove a previously public layer;
                # otherwise the public read path can expose a row whose
                # government lifecycle is no longer confirmed.  Disabled
                # sources retain their separate source-unavailable layer.
                if source is not None and bool(source.get("enabled")):
                    publication_layer = "not_public"
            # Reindexing must never reopen a row from a retired/disabled
            # source.  Source lifecycle transitions own visibility; taxonomy
            # maintenance may refresh derived fields but cannot bypass that
            # publication boundary.
            if source is not None and not bool(source.get("enabled")):
                publication = PublicationDecision(
                    status=PUBLICATION_PENDING_EVIDENCE,
                    label="来源已停用",
                    reason="官方来源当前已停用，历史岗位仅保留在审计库，不进入学生端。",
                    matched_profile_ids=(),
                )
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
                publication_layer=publication_layer,
            )
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

    def expire_stale_source_jobs(
        self,
        *,
        source_ids: set[str] | None = None,
        now: datetime | None = None,
    ) -> dict[str, int]:
        """Report no withdrawals: capture freshness is not job lifecycle."""

        # Phase 3 policy: the 30-hour window describes evidence health only.
        # Keep this method as a stable worker call-site compatibility seam;
        # source health and alerts are handled by capture telemetry instead.
        return {}

    def _source_capture_timestamp(self, source: dict[str, Any]) -> str:
        """Return evidence capture time, never the later sync time.

        Snapshot/browser sources can successfully read an already-old file.
        Their declared capture timestamp is therefore the only safe freshness
        boundary for source-health reporting. A missing or non-success capture
        is unavailable evidence, not job closure.
        """

        config = source.get("config") if isinstance(source.get("config"), dict) else {}
        declared = str(config.get("snapshot_captured_at") or "").strip()
        # Browser-owned snapshots refresh their artifact independently from the
        # static source registry. After a process restart, use the durable
        # successful-capture projection for health reporting; ordinary manual
        # snapshots intentionally continue to honor their declared timestamp.
        if str(config.get("runtime_mode") or "").strip() == "browser_worker_only":
            freshness = self.database.get_source_capture_freshness(str(source.get("id") or ""))
            recorded = str((freshness or {}).get("captured_at") or "").strip()
            if recorded:
                if not declared:
                    declared = recorded
                else:
                    try:
                        declared_dt = datetime.fromisoformat(declared.replace("Z", "+00:00"))
                        recorded_dt = datetime.fromisoformat(recorded.replace("Z", "+00:00"))
                    except ValueError:
                        declared = recorded
                    else:
                        if recorded_dt > declared_dt:
                            declared = recorded
        elif not declared and not str(config.get("capture_path") or "").strip():
            # Live adapters use the successful crawl itself as the capture
            # event. Reuse that durable timestamp after restart for health
            # reporting; failed scans never withdraw existing jobs.
            freshness = self.database.get_source_capture_freshness(
                str(source.get("id") or "")
            )
            declared = str((freshness or {}).get("captured_at") or "").strip()
        # Browser workers persist their successful capture timestamp in the
        # database because their writable runtime capture is intentionally a
        # plain row list (rather than the manifest-shaped payload used by the
        # generic snapshot adapters). Keep using that durable projection for
        # health checks, including after a failed attempt: a failed refresh
        # must not change the lifecycle of a prior official job.
        if str(config.get("runtime_mode") or "").strip() == "browser_worker_only":
            return declared
        capture_path = str(config.get("capture_path") or "").strip()
        if capture_path:
            path = Path(self.settings.data_dir) / capture_path
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return ""
            if not isinstance(payload, dict) or str(payload.get("status") or "") != "success":
                return ""
            declared = str(payload.get("captured_at") or "").strip()
        return declared

    def _record_successful_source_capture(
        self,
        source: dict[str, Any],
        *,
        run_id: int | None,
    ) -> None:
        """Persist capture evidence after, and only after, a successful sync."""

        config = source.get("config") if isinstance(source.get("config"), dict) else {}
        if config.get("max_age_hours") is None:
            return
        requires_declared_capture = self._source_requires_declared_capture(source)
        capture_path = str(config.get("capture_path") or "").strip()
        if not requires_declared_capture and not capture_path:
            # A successful live HTML/API collection is the capture event. Do
            # not reuse the previous projection here: doing so would leave a
            # healthy source permanently stale after its first run. Static
            # snapshots, browser captures, and XLSX artifacts still use their
            # independently declared or manifest-backed timestamp below.
            captured_at = utc_now()
            evidence_kind = "live_sync"
        else:
            captured_at = self._source_capture_timestamp(source)
            evidence_kind = "capture_file" if capture_path else "declared_snapshot"
        if not captured_at:
            return
        try:
            self.database.record_source_capture_freshness(
                str(source["id"]),
                captured_at=captured_at,
                crawl_run_id=run_id,
                evidence_kind=evidence_kind,
            )
        except ValueError:
            LOGGER.warning(
                "Source %s completed without a valid capture timestamp",
                source.get("id"),
            )

    @staticmethod
    def _source_requires_declared_capture(source: dict[str, Any]) -> bool:
        """Return whether freshness must come from an external artifact."""
        return source_requires_declared_capture(source)

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
        registered_sources = self.database.list_sources()
        enabled_source_ids = {
            str(source["id"])
            for source in registered_sources
            if bool(source.get("enabled")) and str(source.get("id") or "").strip()
        }
        artifact_manifest = None
        artifact_manifest_available = self.settings.government_artifact_manifest_path is None
        if self.settings.government_artifact_manifest_path is not None:
            try:
                artifact_manifest = load_government_artifact_manifest(
                    self.settings.government_artifact_manifest_path
                )
                artifact_manifest = project_government_artifact_manifest(
                    self.database, artifact_manifest, registry=registry
                )
                artifact_manifest_available = True
            except (OSError, ValueError):
                # Reindexing is a publication operation. Table-backed rows
                # are held private when their lifecycle contract is missing.
                artifact_manifest_available = False
        current_records = current_publishable_position_records(
            registry,
            today=now.date().isoformat(),
            max_age_hours=self.settings.government_position_max_age_hours,
            source_verifications=source_verifications,
            manual_confirmation_source_ids={
                str(source["id"])
                for source in self.database.list_sources()
                if requires_manual_government_evidence_confirmation(source)
            },
            artifact_manifest=artifact_manifest,
            artifact_manifest_available=artifact_manifest_available,
            now=now,
        )
        # A verified ledger row is not enough to keep a source alive.  Source
        # disablement is an explicit operational decision (for example when
        # robots or a broken adapter blocks automated refresh), so reindexing
        # must not resurrect rows from that source via a stale verification.
        current_records = [
            record
            for record in current_records
            if str(record.get("source_id") or "") in enabled_source_ids
        ]
        records = list(registry.get("records", []))
        return {
            "today": now.date(),
            "now": now,
            "records": records,
            "current_record_ids": {
                str(record["id"]) for record in current_records
            },
            "source_opening_dates": dict(registry.get("source_opening_dates") or {}),
            "source_opening_at": dict(registry.get("source_opening_at") or {}),
            "source_ids": {
                str(record.get("source_id") or "")
                for record in records
                if str(record.get("source_id") or "").strip()
            },
            "enabled_source_ids": enabled_source_ids,
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

        if source_id not in context.get("enabled_source_ids", set()):
            return PublicationDecision(
                status=PUBLICATION_PENDING_EVIDENCE,
                label="官方来源已停用",
                reason="对应官方来源当前已停用，不能使用旧台账或旧复核记录继续发布。",
                matched_profile_ids=(),
            )

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
        current_time = context.get("now")
        opening_dates = context["source_opening_dates"]
        opening_datetimes = context.get("source_opening_at") or {}
        for record in matching_records:
            raw_opening = str(
                record.get("opening_date")
                or opening_dates.get(source_id)
                or ""
            ).strip()
            raw_opening_at = str(
                record.get("opening_at")
                or opening_datetimes.get(source_id)
                or ""
            ).strip()
            if raw_opening_at:
                try:
                    opening_at = datetime.fromisoformat(
                        raw_opening_at.replace("Z", "+00:00")
                    )
                    if opening_at.tzinfo is not None and current_time is not None:
                        if current_time.astimezone(opening_at.tzinfo) < opening_at:
                            reason = f"官方报名尚未开始（{raw_opening_at}），岗位暂不对学生端发布。"
                            break
                    elif opening_at.date() > today:
                        reason = f"官方报名尚未开始（{raw_opening_at}），岗位暂不对学生端发布。"
                        break
                except ValueError:
                    # Registry validation normally prevents this; retain the
                    # generic private decision if an in-memory record is bad.
                    pass
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
