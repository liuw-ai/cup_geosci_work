from __future__ import annotations

import logging
import json
import signal
import time
from dataclasses import replace
from datetime import datetime, time as clock_time
from threading import Event
from zoneinfo import ZoneInfo

from job_hub.audit import audit_database
from job_hub.attachments import (
    OfficialAttachmentProcessor,
    process_pending_attachments,
)
from job_hub.backups import BackupError, DatabaseBackupManager
from job_hub.config import Settings, load_project_env
from job_hub.coverage import build_coverage_report
from job_hub.db import Database
from job_hub.emailer import DeliveryError, Mailer
from job_hub.government_artifacts import (
    GovernmentArtifactContractError,
    government_artifact_refresh_summary,
    load_government_artifact_manifest,
    project_government_artifact_manifest,
    register_government_artifacts,
)
from job_hub.government_discovery import discover_configured_government_artifacts
from job_hub.government_artifact_verification import verify_government_artifacts
from job_hub.artifact_publication import auto_publish_verified_artifact_candidates
from job_hub.government_positions import (
    consecutive_qualified_source_refreshes,
    current_publishable_position_records,
    load_position_registry,
    position_record_to_posting,
)
from job_hub.government_revalidation import (
    AUTOMATIC_AFTER_OPENING_MODE,
    requires_manual_government_evidence_confirmation,
    revalidate_government_sources,
)
from job_hub.domestic_entry_health import run_identity_only_entry_health
from job_hub.domestic_expansion import load_domestic_expansion_queue
from job_hub.discovery_scan import run_discovery_scan
from job_hub.link_health import build_link_health_report
from job_hub.pipeline import JobPipeline
from job_hub.reports import publish_daily_report


LOGGER = logging.getLogger("job_hub.worker")


class DailyWorker:
    def __init__(self, settings: Settings) -> None:
        settings.ensure_runtime_paths()
        self.settings = settings
        self.database = Database(settings.database_path)
        self.database.initialize()
        self.pipeline = JobPipeline(settings, self.database)
        self.attachment_processor = OfficialAttachmentProcessor(settings, self.database)
        self.backups = DatabaseBackupManager(settings)
        self.mailer = Mailer(settings)
        self.stop_event = Event()
        self.timezone = ZoneInfo(settings.timezone)
        self.publish_time = self._parse_publish_time(settings.daily_publish_time)
        self.last_sync_monotonic = 0.0
        self.last_government_revalidation_monotonic = 0.0
        self.last_domestic_entry_health_monotonic = 0.0
        self.last_discovery_scan_monotonic = 0.0
        self.link_health_enabled = settings.link_health_enabled
        self.link_health_limit = settings.link_health_sample_size
        self._last_link_health_date: str | None = None
        self._last_error_detail: str | None = None

    def run_forever(self) -> None:
        self.pipeline.bootstrap_sources()
        recovered = self.database.recover_stale_crawl_runs(
            self.settings.crawl_run_stale_seconds
        )
        if recovered:
            LOGGER.warning("Recovered %s stale crawl run(s).", len(recovered))
        self._heartbeat("starting", "worker boot completed")
        self._sync_with_alert()
        self._run_domestic_entry_health()
        self._run_discovery_scan_with_alert()
        while not self.stop_event.is_set():
            if self._last_error_detail:
                self._heartbeat("degraded", self._last_error_detail)
            else:
                self._heartbeat("running")
            now = datetime.now(self.timezone)
            if (
                time.monotonic() - self.last_sync_monotonic
                >= self.settings.source_sync_interval_minutes * 60
            ):
                self._sync_with_alert()
            elif (
                time.monotonic() - self.last_government_revalidation_monotonic
                >= self.settings.government_revalidation_interval_minutes * 60
            ):
                self._refresh_government_evidence_with_alert()
            elif (
                time.monotonic() - self.last_domestic_entry_health_monotonic
                >= self.settings.domestic_entry_health_interval_minutes * 60
            ):
                self._run_domestic_entry_health()
            elif (
                time.monotonic() - self.last_discovery_scan_monotonic
                >= self.settings.discovery_scan_interval_minutes * 60
            ):
                self._run_discovery_scan_with_alert()
            if now.time() >= self.publish_time:
                existing = self.database.get_daily_report(now.date().isoformat())
                if existing is None:
                    self._publish_with_alert(now.date().isoformat())
                elif existing.get("delivery_status") in {"pending", "failed"}:
                    self._deliver_existing_report(existing)
            self.stop_event.wait(30)
        self._heartbeat("stopped", "worker received a stop signal")

    def stop(self, *_: object) -> None:
        LOGGER.info("Stopping employment information worker.")
        self.stop_event.set()

    def _sync_with_alert(self) -> None:
        LOGGER.info("Starting source synchronization.")
        self._heartbeat("syncing")
        try:
            backup = self.backups.backup_if_due()
        except BackupError as error:
            # A mutating source synchronization must not run past an overdue
            # failed backup.  Otherwise a source/parser mistake could replace
            # the only recoverable production state.
            self._schedule_sync_retry()
            self._last_error_detail = "database backup failed before source synchronization"
            LOGGER.exception("Required database backup failed before synchronization")
            self._heartbeat("degraded", "database backup failed before source synchronization")
            self._send_failure_safely("数据库备份失败，已暂停本轮同步", str(error))
            return
        if backup is not None:
            LOGGER.info(
                "Verified pre-sync database backup created: %s (%s bytes)",
                backup.path,
                backup.bytes,
            )
        try:
            manifest_summary = self._register_government_artifacts()
            summary = self.pipeline.sync_all(progress_callback=self._sync_progress)
            stale_source_withdrawals = self.pipeline.expire_stale_source_jobs()
            # Matching and taxonomy rules are versioned code, while persisted
            # publication fields are derived data.  Rebuild them after each
            # source cycle so a rule fix repairs existing official rows even
            # when their source has not changed since the previous scan.
            reindex_summary = self.pipeline.reindex_jobs()
            government_verification, government_jobs = self._refresh_government_evidence()
            government_discovery = self._discover_configured_government_artifacts()
            attachment_summary = self._process_registered_attachments()
            # Discovery and extraction must precede verification in the same
            # cycle.  Otherwise a newly found official position table waits
            # for the next scheduled sync before it can be published.
            government_auto_verification = self._verify_government_artifacts(
                manifest_summary.get("manifest")
            )
            government_auto_publication = auto_publish_verified_artifact_candidates(
                self.database,
                self.pipeline,
                government_auto_verification,
            )
            # Candidate publication happens after the normal reindex above.
            # Reindex once more so the government ledger/opening-date boundary
            # is applied before the coverage snapshot is written.
            if government_auto_publication.get("published") or government_auto_publication.get(
                "unchanged"
            ):
                government_auto_publication["reindex"] = self.pipeline.reindex_jobs()
            self._record_attachment_run_metrics()
            expired_candidates = self.database.expire_stale_artifact_candidates(
                as_of=datetime.now(self.timezone).date().isoformat()
            )
            attachment_summary["expired_candidates"] = expired_candidates
            government_summary = government_artifact_refresh_summary(
                self.database,
                manifest=manifest_summary.get("manifest"),
                today=datetime.now(self.timezone).date().isoformat(),
                manifest_error=(
                    str(manifest_summary.get("error"))
                    if manifest_summary.get("status") == "failed"
                    else None
                ),
            )
            manifest_log = {
                key: value for key, value in manifest_summary.items() if key != "manifest"
            }
            snapshot_date = datetime.now(self.timezone).date().isoformat()
            self.database.save_coverage_snapshot(
                snapshot_date,
                build_coverage_report(self.database, snapshot_date=snapshot_date),
            )
            link_health = self._run_link_health_if_due(snapshot_date)
            self.last_sync_monotonic = time.monotonic()
            LOGGER.info(
                "Source synchronization complete: %s; coverage snapshot recorded for %s.",
                {
                    "sources": summary.as_dict(),
                    "stale_source_withdrawals": stale_source_withdrawals,
                    "reindex": reindex_summary,
                    "government_positions": government_jobs,
                    "government_evidence_recheck": government_verification,
                    "government_attachment_discovery": government_discovery,
                    "government_manifest": manifest_log,
                    "attachments": attachment_summary,
                    "government_auto_verification": government_auto_verification,
                    "government_auto_publication": government_auto_publication,
                    "government_quality": government_summary,
                    "link_health": link_health,
                },
                snapshot_date,
            )
            if manifest_summary.get("status") == "failed":
                self._send_failure_safely(
                    "政府职位表清单加载失败",
                    str(manifest_summary.get("error") or "unknown manifest error"),
                )
            if int(government_discovery.get("failed", 0)):
                self._send_failure_safely(
                    "部分政府官方公告附件发现失败",
                    str(government_discovery),
                )
            if summary.failed:
                self._send_failure_safely(
                    "部分官方来源采集失败",
                    f"本次同步有 {summary.failed} 个来源失败。\n{summary.as_dict()}",
                )
            if link_health.get("status") == "failed":
                self._send_failure_safely(
                    "岗位官方链接健康检查失败",
                    str(link_health.get("error") or link_health),
                )
            self._heartbeat("running", "source synchronization completed")
            self._last_error_detail = None
        except Exception as error:
            self._schedule_sync_retry()
            self._last_error_detail = "source synchronization failed"
            LOGGER.exception("Source synchronization failed")
            self._heartbeat("degraded", "source synchronization failed")
            self._send_failure_safely("官方来源同步失败", str(error))

    def _refresh_government_evidence(self) -> tuple[dict[str, int], dict[str, int]]:
        """Recheck governed evidence and reconcile only the reviewed ledger."""
        verification = self._revalidate_government_position_sources()
        jobs = self._sync_verified_government_positions()
        self.last_government_revalidation_monotonic = time.monotonic()
        return verification, jobs

    def _refresh_government_evidence_with_alert(self) -> None:
        """Run the low-impact government evidence cadence between full scans."""
        LOGGER.info("Starting government position evidence revalidation.")
        self._heartbeat("government_evidence_recheck")
        try:
            backup = self.backups.backup_if_due()
        except BackupError as error:
            self._schedule_government_retry()
            self._last_error_detail = "database backup failed before government revalidation"
            LOGGER.exception("Required database backup failed before government revalidation")
            self._heartbeat("degraded", "database backup failed before government revalidation")
            self._send_failure_safely("数据库备份失败，已暂停政府职位表复核", str(error))
            return
        if backup is not None:
            LOGGER.info(
                "Verified pre-revalidation database backup created: %s (%s bytes)",
                backup.path,
                backup.bytes,
            )
        try:
            verification, jobs = self._refresh_government_evidence()
            self._heartbeat(
                "running",
                "government evidence recheck complete: "
                f"verified={verification.get('verified', 0)}, "
                f"activated={verification.get('activated', 0)}, "
                f"published_or_updated={jobs.get('created', 0) + jobs.get('updated', 0)}",
            )
            self._last_error_detail = None
        except Exception as error:  # noqa: BLE001 - keep the worker alive
            self._schedule_government_retry()
            self._last_error_detail = "government position evidence revalidation failed"
            LOGGER.exception("Government position evidence revalidation failed")
            self._heartbeat("degraded", "government position evidence revalidation failed")
            self._send_failure_safely("政府职位表复核失败", str(error))

    def _schedule_sync_retry(self) -> None:
        """Retry a failed full cycle after a short bounded delay."""
        interval = max(1, int(self.settings.source_sync_interval_minutes)) * 60
        retry = max(5, int(self.settings.worker_failure_retry_seconds))
        self.last_sync_monotonic = time.monotonic() - max(0, interval - retry)

    def _schedule_government_retry(self) -> None:
        """Retry a failed government recheck without waiting a full cadence."""
        interval = max(1, int(self.settings.government_revalidation_interval_minutes)) * 60
        retry = max(5, int(self.settings.worker_failure_retry_seconds))
        self.last_government_revalidation_monotonic = time.monotonic() - max(
            0, interval - retry
        )

    def _run_domestic_entry_health(self) -> None:
        """Persist private reachability evidence for unregistered candidates.

        The expansion queue is intentionally outside the public-source
        registry.  This check therefore cannot publish jobs or alter source
        health.  Its report tells an operator or later adapter work when an
        official entry becomes reachable from the current deployment network.
        """
        try:
            self._heartbeat("expansion_entry_health")
            report = run_identity_only_entry_health(
                self.settings,
                load_domestic_expansion_queue(),
            )
            report["generated_at"] = datetime.now(self.timezone).isoformat()
            output_dir = self.settings.data_dir / "domestic-entry-health"
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = output_dir / "latest.json"
            temporary = output_path.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps(report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(output_path)
            self.last_domestic_entry_health_monotonic = time.monotonic()
            LOGGER.info(
                "Domestic expansion entry health complete: %s/%s reachable.",
                report["reachable_count"],
                report["candidate_count"],
            )
        except Exception as error:  # noqa: BLE001 - discovery must not halt vacancies
            interval = max(
                1, int(self.settings.domestic_entry_health_interval_minutes)
            ) * 60
            retry = max(5, int(self.settings.worker_failure_retry_seconds))
            self.last_domestic_entry_health_monotonic = time.monotonic() - max(
                0, interval - retry
            )
            LOGGER.exception("Domestic expansion entry health failed")
            self._send_failure_safely("扩源官方入口健康检查失败", str(error))

    def _run_discovery_scan_with_alert(self) -> None:
        """Scan explicitly enabled discovery pages into the private lead pool.

        Discovery is deliberately isolated from the official source cycle.  A
        third-party outage, robots denial, or parser error produces a durable
        private report and a bounded retry, but cannot withdraw or publish an
        official vacancy.
        """
        self._heartbeat("discovery_scanning")
        try:
            result = run_discovery_scan(self.settings, self.database)
            result["generated_at"] = datetime.now(self.timezone).isoformat()
            output_dir = self.settings.data_dir / "discovery-scan"
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = output_dir / "latest.json"
            temporary = output_path.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps(result, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(output_path)
            self.last_discovery_scan_monotonic = time.monotonic()
            failed = int(result.get("failed_source_count") or 0)
            if failed:
                self._last_error_detail = (
                    f"discovery scan has {failed} unavailable or blocked source(s)"
                )
                self._send_failure_safely("第三方发现线索扫描受限", json.dumps(result, ensure_ascii=False))
            else:
                self._last_error_detail = None
            self._heartbeat(
                "running",
                "discovery scan completed: "
                f"sources={result.get('source_count', 0)}, "
                f"created_leads={result.get('created_lead_count', 0)}",
            )
        except Exception as error:  # noqa: BLE001 - discovery must not halt vacancies
            interval = max(1, int(self.settings.discovery_scan_interval_minutes)) * 60
            retry = max(5, int(self.settings.worker_failure_retry_seconds))
            self.last_discovery_scan_monotonic = time.monotonic() - max(
                0, interval - retry
            )
            self._last_error_detail = "discovery scan failed"
            report = {
                "scope": "private_discovery_only",
                "status": "failed",
                "generated_at": datetime.now(self.timezone).isoformat(),
                "error": str(error),
                "publication_policy": "第三方页面只产生私有线索；不会生成官方岗位、不会填写官方链接、不会进入学生端。",
            }
            try:
                output_dir = self.settings.data_dir / "discovery-scan"
                output_dir.mkdir(parents=True, exist_ok=True)
                output_path = output_dir / "latest.json"
                temporary = output_path.with_suffix(".json.tmp")
                temporary.write_text(
                    json.dumps(report, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                temporary.replace(output_path)
            except OSError:
                LOGGER.exception("Could not persist discovery scan failure report")
            LOGGER.exception("Private discovery scan failed")
            self._heartbeat("degraded", "discovery scan failed")
            self._send_failure_safely("第三方发现线索扫描失败", str(error))

    def _run_link_health_if_due(self, report_date: str) -> dict[str, object]:
        """Persist one bounded, diverse official-link audit per local day.

        This is deliberately diagnostic: a 403/412, robots denial, dynamic
        shell, or 5xx is recorded in the JSON report but never changes a job's
        publication state. The source sync remains the authoritative mutation
        path for vacancies.
        """

        if not self.link_health_enabled:
            return {"status": "disabled"}
        if self._last_link_health_date == report_date:
            return {"status": "already_recorded", "report_date": report_date}
        try:
            report = build_link_health_report(
                self.database,
                self.settings,
                limit=self.link_health_limit,
            )
            report = {
                "observed_on": report_date,
                "generated_at": datetime.now(self.timezone).isoformat(),
                "sample_size": self.link_health_limit,
                **report,
            }
            output_dir = self.settings.data_dir / "link-health"
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = output_dir / f"{report_date}.json"
            temporary = output_path.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps(report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(output_path)
            self._last_link_health_date = report_date
            return {
                "status": "ok",
                "report_date": report_date,
                "path": str(output_path),
                "checked": int(report.get("checked") or 0),
                "status_counts": report.get("status_counts", {}),
            }
        except Exception as error:  # noqa: BLE001 - diagnostics cannot block sync
            LOGGER.exception("Official link health audit failed")
            return {"status": "failed", "report_date": report_date, "error": str(error)}

    def _discover_configured_government_artifacts(self) -> dict[str, object]:
        """Discover new official position-table files before the parse queue runs."""
        try:
            return discover_configured_government_artifacts(
                self.settings,
                self.database,
                collector=self.pipeline.collector,
                processor=self.attachment_processor,
            )
        except Exception as error:  # noqa: BLE001 - preserve the rest of daily sync
            LOGGER.exception("Government attachment discovery failed")
            return {
                "configured_sources": 0,
                "notices_found": 0,
                "notices_scanned": 0,
                "attachments_registered": 0,
                "position_table_attachments": 0,
                "blocked": 0,
                "failed": 1,
                "error": str(error),
                "publication_policy": "发现失败不代表当前无岗位，未复核数据不会进入学生端。",
            }

    def _revalidate_government_position_sources(
        self,
        *,
        only_source_ids: set[str] | None = None,
        allow_activation: bool = True,
    ) -> dict[str, int]:
        """Refresh explicit official evidence without discovering new sources.

        Position-table rows remain a reviewed ledger. This recheck only keeps
        the ledger's already-approved official notice/attachment URLs current
        enough for student-facing publication. ``only_source_ids`` is an
        operator-facing isolation boundary for controlled single-source
        rechecks; it scopes both the reviewed ledger and the configured source
        map before any robots or official URL request can be made.
        """
        path = self.settings.government_position_registry_path
        try:
            registry = load_position_registry(path)
        except Exception as error:  # noqa: BLE001 - keep a broken ledger visible
            LOGGER.error("Government position registry unavailable for recheck: %s", error)
            return {"verified": 0, "scheduled": 0, "source_unavailable": 0, "withdrawn": 0, "not_configured": 0, "manual_confirmation_required": 0, "activated": 0, "error": 1}
        requested_source_ids = (
            None
            if only_source_ids is None
            else {
                str(source_id).strip()
                for source_id in only_source_ids
                if str(source_id).strip()
            }
        )
        if requested_source_ids is not None:
            # The lower-level revalidator also probes configured automatic
            # sources that have no open ledger rows. Filter the registry and
            # source map together so a controlled run cannot cross that
            # boundary accidentally.
            registry = dict(registry)
            registry["records"] = [
                record
                for record in registry.get("records", [])
                if str(record.get("source_id") or "") in requested_source_ids
            ]
            for key in ("source_opening_dates", "source_opening_at"):
                values = registry.get(key)
                if isinstance(values, dict):
                    registry[key] = {
                        str(source_id): value
                        for source_id, value in values.items()
                        if str(source_id) in requested_source_ids
                    }
        configured_sources = self.database.list_sources()
        if requested_source_ids is not None:
            configured_sources = [
                source
                for source in configured_sources
                if str(source.get("id") or "") in requested_source_ids
            ]
        sources_by_id = {
            str(source["id"]): source for source in configured_sources
        }
        try:
            results = revalidate_government_sources(
                registry,
                sources_by_id,
                self.settings,
                now=datetime.now(self.timezone),
            )
        except Exception as error:  # noqa: BLE001 - source recheck must not stop all sources
            LOGGER.exception("Government position evidence recheck failed")
            return {"verified": 0, "scheduled": 0, "source_unavailable": 0, "withdrawn": 0, "not_configured": 0, "manual_confirmation_required": 0, "activated": 0, "error": 1}
        counts = {"verified": 0, "scheduled": 0, "source_unavailable": 0, "withdrawn": 0, "not_configured": 0, "manual_confirmation_required": 0, "activated": 0, "error": 0}
        for result in results:
            status = str(result["status"])
            if status == "scheduled":
                # The opening window has not arrived yet.  Keep this as a
                # report-only state; persisting it as the latest verification
                # would overwrite a prior successful evidence timestamp with
                # a non-terminal status that the publication table does not
                # accept.
                counts[status] += 1
                continue
            if status == "manual_confirmation_required":
                # Preserve a prior verified timestamp. The ordinary freshness
                # window still retires it until an administrator renews it.
                counts[status] += 1
                continue
            source = sources_by_id.get(str(result["source_id"])) or {}
            config = source.get("config") if isinstance(source.get("config"), dict) else {}
            self.database.record_government_source_verification(
                str(result["source_id"]),
                status=status,
                checked_at=str(result["checked_at"]),
                detail=str(result.get("detail") or ""),
                evidence_fingerprint=str(result.get("evidence_fingerprint") or ""),
                availability_confirmed_at=(
                    str(result["availability_confirmed_at"])
                    if result.get("availability_confirmed_at")
                    else None
                ),
                availability_confirmation_detail=str(
                    result.get("availability_confirmation_detail") or ""
                ),
            )
            # Government-ledger sources are manual collectors, so the
            # generic student read gate cannot infer freshness from a crawl
            # run. Mirror a successful official revalidation into the common
            # capture projection when the source declares a bounded window;
            # this keeps the public feed fail-closed if the worker later stops.
            if status == "verified" and config.get("max_age_hours") is not None:
                try:
                    self.database.record_source_capture_freshness(
                        str(result["source_id"]),
                        captured_at=str(result["checked_at"]),
                        evidence_kind="government_evidence_recheck",
                    )
                except ValueError:
                    LOGGER.warning(
                        "Government source %s completed with invalid capture timestamp",
                        result.get("source_id"),
                    )
            counts[status] += 1
            if (
                status == "verified"
                and allow_activation
                and not bool(source.get("enabled"))
                and bool(config.get("runtime_enabled_control"))
                and str(config.get("government_evidence_recheck_mode") or "").strip()
                == AUTOMATIC_AFTER_OPENING_MODE
            ):
                required = max(1, int(config.get("activation_required_successes") or 2))
                source_id = str(result["source_id"])
                consecutive = consecutive_qualified_source_refreshes(
                    self.database.list_government_source_verification_events(
                        source_id, limit=max(10, required + 3)
                    ),
                    opening_at=(registry.get("source_opening_at") or {}).get(source_id),
                    require_opening_at=True,
                )
                if consecutive >= required:
                    self.database.set_source_enabled(source_id, True)
                    counts["activated"] += 1
        return counts

    def _verify_government_artifacts(
        self, manifest: dict[str, object] | None
    ) -> dict[str, object]:
        """Run the fail-closed row verifier after controlled extraction.

        Verification is deliberately separate from publication.  A downloaded
        table can prove its row fields while a rolling recruitment still needs
        a fresh current-availability confirmation before a student-facing row
        is allowed.
        """
        verification_manifest = manifest or {"artifacts": []}
        registry: dict[str, object] = {"records": []}
        try:
            registry = load_position_registry(self.settings.government_position_registry_path)
        except Exception as error:  # noqa: BLE001 - dynamic discovery remains independently gated
            LOGGER.error("Government position registry unavailable for artifact verification: %s", error)
        try:
            result = verify_government_artifacts(
                self.database,
                verification_manifest,
                registry,
                now=datetime.now(self.timezone),
            )
            result["status"] = "ok"
            return result
        except Exception as error:  # noqa: BLE001 - keep source cycle alive
            LOGGER.exception("Government artifact automatic verification failed")
            return {
                "version": "government-artifact-verifier-v1",
                "checked": 0,
                "row_evidence_verified": 0,
                "publication_ready": 0,
                "blocked": 1,
                "candidates_promoted": 0,
                "items": [],
                "status": "failed",
                "error": str(error),
            }

    def _sync_verified_government_positions(self) -> dict[str, int]:
        """Publish current explicit-match rows from the official position ledger."""
        path = self.settings.government_position_registry_path
        try:
            registry = load_position_registry(path)
        except Exception as error:  # noqa: BLE001 - keep registry failures visible
            LOGGER.error("Government position registry unavailable: %s", error)
            return {
                "created": 0,
                "updated": 0,
                "unchanged": 0,
                "skipped": 0,
                "withdrawn": 0,
                "review_pending": 0,
                "error": 1,
            }
        now = datetime.now(self.timezone)
        today = now.date().isoformat()
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
            except (OSError, ValueError, GovernmentArtifactContractError) as error:
                # A broken lifecycle manifest must not be replaced by an old
                # static position ledger. Direct official-detail rows retain
                # their ordinary evidence gate; attachment-backed rows fail
                # closed in ``current_publishable_position_records``.
                LOGGER.error("Government artifact manifest unavailable for position sync: %s", error)
        counts = {
            "created": 0,
            "updated": 0,
            "unchanged": 0,
            "skipped": 0,
            "withdrawn": 0,
            "review_pending": 0,
            "error": 0,
        }
        existing_jobs, _ = self.database.list_jobs(
            page_size=None, only_open=False, student_visible=False
        )
        used_existing_ids: set[int] = set()
        current_records = current_publishable_position_records(
            registry,
            today=today,
            max_age_hours=self.settings.government_position_max_age_hours,
            source_verifications={
                str(item["source_id"]): item
                for item in self.database.list_government_source_verifications()
            },
            manual_confirmation_source_ids={
                str(source["id"])
                for source in self.database.list_sources()
                if requires_manual_government_evidence_confirmation(source)
            },
            artifact_manifest=artifact_manifest,
            artifact_manifest_available=artifact_manifest_available,
            now=now,
        )
        enabled_source_ids = {
            str(source["id"])
            for source in self.database.list_sources()
            if bool(source.get("enabled")) and str(source.get("id") or "").strip()
        }
        # A disabled source must fail closed even when an old verification
        # timestamp is still present.  This prevents a manual/blocked source
        # from being resurrected by the reviewed ledger during reindexing.
        current_records = [
            record
            for record in current_records
            if str(record.get("source_id") or "") in enabled_source_ids
        ]
        current_ids_by_source: dict[str, set[str]] = {}
        reconcile_source_ids: set[str] = set()
        for record in current_records:
            source_id = str(record["source_id"])
            source = self.database.get_source(source_id)
            if source is None:
                counts["skipped"] += 1
                LOGGER.error("Government position source is not registered: %s", source_id)
                continue
            reconcile_source_ids.add(source_id)
            posting = position_record_to_posting(record)
            equivalent = self._find_equivalent_government_job(
                existing_jobs,
                record,
                posting.official_evidence_url,
                excluded_ids=used_existing_ids,
            )
            if equivalent is not None:
                # Preserve the original attachment-candidate fingerprint so a
                # later registry refresh updates one row instead of inserting a
                # second copy of the same official table row.
                posting = replace(posting, external_id=equivalent["external_id"])
                # ``existing_jobs`` is extended with the normalized posting
                # produced during this same batch.  Those transient records do
                # not have a database id yet, so only reserve persisted rows.
                equivalent_id = equivalent.get("id") or equivalent.get("job_id")
                if equivalent_id is not None:
                    used_existing_ids.add(int(equivalent_id))
            # An equivalent row can retain its earlier attachment-candidate
            # identifier. Track the identifier actually saved so the operator
            # review report can distinguish a missing row from a new row.
            current_ids_by_source.setdefault(source_id, set()).add(
                str(posting.external_id)
            )
            normalized = self.pipeline.normalize_posting(posting, source)
            _, outcome = self.database.save_job(normalized)
            if outcome in counts:
                counts[outcome] += 1
            if equivalent is None:
                existing_jobs.append(normalized)
        # Government rows are fed by a reviewed registry rather than a normal
        # network collector. A row missing from one review snapshot is not
        # closure evidence: the snapshot may be stale, partial, blocked, or
        # structurally changed. Keep the old job open and expose the count as
        # an operator review signal. Only an explicit official closure,
        # replacement, or deadline expiry may change lifecycle state.
        registry_source_ids = {
            str(item.get("source_id") or "")
            for item in registry.get("records", [])
            if str(item.get("source_id") or "").strip()
        }
        # A missing source registration is an operator/configuration failure,
        # not a complete scan. Do not clear its existing jobs in that case.
        reconcile_source_ids.update(
            source_id
            for source_id in registry_source_ids
            if self.database.get_source(source_id) is not None
        )
        for source_id in reconcile_source_ids:
            current_ids = current_ids_by_source.get(source_id, set())
            missing_open_rows = sum(
                1
                for job in existing_jobs
                if str(job.get("source_id") or "") == source_id
                and str(job.get("status") or "") == "open"
                and str(job.get("external_id") or "")
                and str(job.get("external_id")) not in current_ids
            )
            if missing_open_rows:
                counts["review_pending"] += missing_open_rows
                LOGGER.warning(
                    "Government rows absent from current review snapshot; "
                    "keeping %s open for source=%s",
                    missing_open_rows,
                    source_id,
                )
        return counts

    @staticmethod
    def _find_equivalent_government_job(
        jobs: list[dict[str, object]],
        record: dict[str, object],
        attachment_url: str | None,
        *,
        excluded_ids: set[int] | None = None,
    ) -> dict[str, object] | None:
        """Match a registry row to an earlier attachment publication."""
        major = str(record.get("major_requirement") or "").strip()
        position_code = str(record.get("position_code") or "").strip()
        source_id = str(record.get("source_id") or "").strip()
        employer = str(record.get("employer") or "").strip()
        deadline = str(record.get("deadline_date") or "").strip()
        candidates: list[tuple[bool, bool, dict[str, object]]] = []
        for job in jobs:
            job_id = job.get("id") or job.get("job_id")
            # A normalized posting appended during the current sync has no
            # database identity and must not be selected as an existing row.
            if job_id is None:
                continue
            if int(job_id) in (excluded_ids or set()):
                continue
            if str(job.get("source_id") or "") != source_id:
                continue
            if str(job.get("employer") or "") != employer:
                continue
            if str(job.get("deadline_date") or "") != deadline:
                continue
            evidence = job.get("field_evidence")
            if not isinstance(evidence, dict):
                continue
            evidence_attachment = str(evidence.get("artifact_url") or "").strip()
            if attachment_url and attachment_url not in {
                str(job.get("official_evidence_url") or "").strip(),
                evidence_attachment,
            }:
                continue
            existing_major = str(
                evidence.get("专业范围") or evidence.get("专业要求") or ""
            ).strip()
            if existing_major and (existing_major == major or major in existing_major or existing_major in major):
                candidates.append(
                    (str(evidence.get("职位代码") or "").strip() == position_code,
                     existing_major == major,
                     job)
                )
        # Prefer a row already produced from the controlled attachment queue;
        # it carries the original OCR/table evidence and keeps its event history.
        candidates.sort(
            key=lambda item: (
                0 if item[0] else 1,
                0 if item[1] else 1,
                0 if str(item[2].get("external_id") or "").startswith("artifact-candidate:") else 1,
                int(item[2].get("id") or 0),
            )
        )
        return candidates[0][2] if candidates else None

    def _register_government_artifacts(self) -> dict[str, object]:
        """Idempotently load the versioned government-artifact manifest.

        This runs on every source cycle so a newly committed official PDF/XLS
        becomes eligible for controlled processing without an operator shell
        command. Registration never downloads a file or publishes a candidate.
        """
        path = self.settings.government_artifact_manifest_path
        if path is None:
            return {"status": "skipped", "registered": 0, "manifest": None}
        try:
            manifest = load_government_artifact_manifest(path)
            registered = register_government_artifacts(self.database, manifest)
            return {
                "status": "ok",
                "path": str(path),
                "as_of": manifest.get("as_of"),
                "registered": len(registered),
                "manifest": manifest,
            }
        except (OSError, ValueError, GovernmentArtifactContractError) as error:
            LOGGER.error("Government artifact manifest could not be registered: %s", error)
            return {"status": "failed", "path": str(path), "error": str(error), "manifest": None}

    def _process_registered_attachments(self) -> dict[str, int]:
        """Advance discovered official files without publishing unreviewed rows."""
        summary = process_pending_attachments(
            self.database,
            self.attachment_processor,
            limit=self.settings.attachment_process_batch_limit,
            include_stale_extracted=True,
            retry_recovered_skipped=True,
        )
        return {
            key: int(summary.get(key, 0))
            for key in (
                "selected",
                "processed",
                "extracted",
                "skipped",
                "failed",
                "rows_extracted",
                "candidates_created",
                "candidates_rejected",
                "reparsed",
                "retry_recovered_skipped",
            )
        }

    def _record_attachment_run_metrics(self) -> None:
        """Attach current controlled-file counts to each source's latest run."""

        latest_runs = {
            str(item["source_id"]): item
            for item in self.database.list_latest_crawl_runs()
            if item.get("source_id") is not None
        }
        for item in self.database.list_source_artifact_status_counts():
            source_id = str(item.get("source_id") or "")
            run = latest_runs.get(source_id)
            if not run:
                continue
            self.database.update_crawl_run_metrics(
                int(run["id"]),
                attachment_success_count=int(item.get("extracted_count") or 0),
                metadata_updates={
                    "attachment_failed_count": int(item.get("failed_count") or 0),
                    "attachment_total_count": int(item.get("total_count") or 0),
                },
            )

    def _publish_with_alert(self, report_date: str) -> None:
        LOGGER.info("Publishing daily report for %s.", report_date)
        self._heartbeat("publishing", f"publishing {report_date}")
        try:
            self._sync_with_alert()
            # A source process can be interrupted after opening a crawl run
            # and before recording its result.  Startup recovery handles the
            # normal restart path, but a long-lived worker may reach the
            # publish window with that abandoned row still marked ``running``.
            # Close only runs older than the configured timeout immediately
            # before the final audit; an active recent run remains a blocker.
            recovered = self.database.recover_stale_crawl_runs(
                self.settings.crawl_run_stale_seconds
            )
            if recovered:
                LOGGER.warning(
                    "Recovered %s stale crawl run(s) before daily publication.",
                    len(recovered),
                )
            audit = audit_database(self.database, self.settings)
            if not audit["ok"]:
                raise RuntimeError(
                    "日报发布前的数据审计未通过："
                    + "; ".join(
                        str(issue.get("message", "未知问题"))
                        for issue in audit["issues"][:5]
                    )
                )
            report = publish_daily_report(
                self.database,
                self.settings,
                datetime.fromisoformat(report_date).date(),
            )
            delivery_status = self.mailer.send_daily_report(report)
            self.database.update_delivery_status(report_date, delivery_status)
            LOGGER.info("Daily report published with delivery status: %s", delivery_status)
            self._heartbeat("running", f"published {report_date}")
        except Exception as error:
            LOGGER.exception("Daily report publishing failed")
            try:
                self.database.update_delivery_status(report_date, "failed", str(error))
            except Exception:
                LOGGER.exception("Could not record daily report failure status")
            self._heartbeat("degraded", f"publish failed for {report_date}")
            self._send_failure_safely("就业日报发布或邮件发送失败", str(error))

    def _deliver_existing_report(self, report: dict[str, object]) -> None:
        """Retry notification delivery without changing an already frozen report."""
        report_date = str(report["report_date"])
        self._heartbeat("delivering", f"retrying delivery for {report_date}")
        try:
            delivery_status = self.mailer.send_daily_report(report)
            self.database.update_delivery_status(report_date, delivery_status)
            self._heartbeat("running", f"delivery completed for {report_date}")
        except Exception as error:
            LOGGER.exception("Daily report email retry failed")
            self.database.update_delivery_status(report_date, "failed", str(error))
            self._heartbeat("degraded", f"delivery failed for {report_date}")
            self._send_failure_safely("就业日报邮件重试失败", str(error))

    def _heartbeat(self, status: str, detail: str = "") -> None:
        try:
            self.database.record_service_heartbeat("worker", status, detail)
        except Exception:
            LOGGER.exception("Could not record worker heartbeat")

    def _sync_progress(self, completed: int, total: int, source_id: str) -> None:
        """Keep Docker health and admin monitoring alive during long syncs."""

        self._heartbeat("syncing", f"{completed}/{total}: {source_id}")

    def _send_failure_safely(self, subject: str, details: str) -> None:
        try:
            self.mailer.send_failure_alert(subject, details)
        except DeliveryError:
            LOGGER.exception("Failure alert could not be delivered")

    @staticmethod
    def _parse_publish_time(value: str) -> clock_time:
        try:
            hour, minute = [int(part) for part in value.split(":", 1)]
            return clock_time(hour=hour, minute=minute)
        except (TypeError, ValueError) as error:
            raise ValueError("DAILY_PUBLISH_TIME must use HH:MM format") from error


def main() -> None:
    load_project_env()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    worker = DailyWorker(Settings.from_env())
    signal.signal(signal.SIGINT, worker.stop)
    signal.signal(signal.SIGTERM, worker.stop)
    worker.run_forever()


if __name__ == "__main__":
    main()
