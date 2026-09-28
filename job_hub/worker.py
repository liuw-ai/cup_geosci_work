from __future__ import annotations

import logging
import signal
import time
from dataclasses import replace
from datetime import datetime, time as clock_time
from threading import Event
from zoneinfo import ZoneInfo

from job_hub.audit import audit_database
from job_hub.attachments import AttachmentProcessingError, OfficialAttachmentProcessor
from job_hub.config import Settings
from job_hub.coverage import build_coverage_report
from job_hub.db import Database
from job_hub.emailer import DeliveryError, Mailer
from job_hub.government_artifacts import (
    GovernmentArtifactContractError,
    government_artifact_refresh_summary,
    load_government_artifact_manifest,
    register_government_artifacts,
)
from job_hub.government_discovery import discover_configured_government_artifacts
from job_hub.government_positions import (
    current_publishable_position_records,
    load_position_registry,
    position_record_to_posting,
)
from job_hub.government_revalidation import revalidate_government_sources
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
        self.mailer = Mailer(settings)
        self.stop_event = Event()
        self.timezone = ZoneInfo(settings.timezone)
        self.publish_time = self._parse_publish_time(settings.daily_publish_time)
        self.last_sync_monotonic = 0.0

    def run_forever(self) -> None:
        self.pipeline.bootstrap_sources()
        recovered = self.database.recover_stale_crawl_runs(
            self.settings.crawl_run_stale_seconds
        )
        if recovered:
            LOGGER.warning("Recovered %s stale crawl run(s).", len(recovered))
        self._heartbeat("starting", "worker boot completed")
        self._sync_with_alert()
        while not self.stop_event.is_set():
            self._heartbeat("running")
            now = datetime.now(self.timezone)
            if (
                time.monotonic() - self.last_sync_monotonic
                >= self.settings.source_sync_interval_minutes * 60
            ):
                self._sync_with_alert()
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
            manifest_summary = self._register_government_artifacts()
            summary = self.pipeline.sync_all(progress_callback=self._sync_progress)
            government_verification = self._revalidate_government_position_sources()
            government_jobs = self._sync_verified_government_positions()
            government_discovery = self._discover_configured_government_artifacts()
            attachment_summary = self._process_registered_attachments()
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
            self.last_sync_monotonic = time.monotonic()
            LOGGER.info(
                "Source synchronization complete: %s; coverage snapshot recorded for %s.",
                {
                    "sources": summary.as_dict(),
                    "government_positions": government_jobs,
                    "government_evidence_recheck": government_verification,
                    "government_attachment_discovery": government_discovery,
                    "government_manifest": manifest_log,
                    "attachments": attachment_summary,
                    "government_quality": government_summary,
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
            self._heartbeat("running", "source synchronization completed")
        except Exception as error:
            self.last_sync_monotonic = time.monotonic()
            LOGGER.exception("Source synchronization failed")
            self._heartbeat("degraded", "source synchronization failed")
            self._send_failure_safely("官方来源同步失败", str(error))

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

    def _revalidate_government_position_sources(self) -> dict[str, int]:
        """Refresh explicit official evidence without discovering new sources.

        Position-table rows remain a reviewed ledger. This recheck only keeps
        the ledger's already-approved official notice/attachment URLs current
        enough for student-facing publication.
        """
        path = self.settings.government_position_registry_path
        try:
            registry = load_position_registry(path)
        except Exception as error:  # noqa: BLE001 - keep a broken ledger visible
            LOGGER.error("Government position registry unavailable for recheck: %s", error)
            return {"verified": 0, "source_unavailable": 0, "withdrawn": 0, "not_configured": 0, "error": 1}
        try:
            results = revalidate_government_sources(
                registry,
                {source["id"]: source for source in self.database.list_sources()},
                self.settings,
                now=datetime.now(self.timezone),
            )
        except Exception as error:  # noqa: BLE001 - source recheck must not stop all sources
            LOGGER.exception("Government position evidence recheck failed")
            return {"verified": 0, "source_unavailable": 0, "withdrawn": 0, "not_configured": 0, "error": 1}
        counts = {"verified": 0, "source_unavailable": 0, "withdrawn": 0, "not_configured": 0, "error": 0}
        for result in results:
            status = str(result["status"])
            self.database.record_government_source_verification(
                str(result["source_id"]),
                status=status,
                checked_at=str(result["checked_at"]),
                detail=str(result.get("detail") or ""),
                evidence_fingerprint=str(result.get("evidence_fingerprint") or ""),
            )
            counts[status] += 1
        return counts

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
                "error": 1,
            }
        now = datetime.now(self.timezone)
        today = now.date().isoformat()
        counts = {
            "created": 0,
            "updated": 0,
            "unchanged": 0,
            "skipped": 0,
            "withdrawn": 0,
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
            now=now,
        )
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
            current_ids_by_source.setdefault(source_id, set()).add(
                str(posting.external_id)
            )
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
            normalized = self.pipeline.normalize_posting(posting, source)
            _, outcome = self.database.save_job(normalized)
            if outcome in counts:
                counts[outcome] += 1
            if equivalent is None:
                existing_jobs.append(normalized)
        # Government rows are fed by a reviewed registry rather than a normal
        # network collector. Reconcile each registered source explicitly so a
        # closed deadline, removed table row, or stale registry cannot leave an
        # old vacancy open forever. An empty set is intentional when the
        # registry freshness gate fails; it withdraws the old rows and the
        # quality report records the reason separately.
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
            counts["withdrawn"] += self.database.withdraw_missing_source_jobs(
                source_id,
                current_ids_by_source.get(source_id, set()),
                reason=(
                    "政府职位表复核快照中已不再出现该岗位，或快照超过新鲜度门禁。"
                ),
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
        processed = 0
        extracted = 0
        failed = 0
        for artifact in self.database.list_source_artifacts():
            if str(artifact.get("extraction_status")) != "registered":
                continue
            processed += 1
            try:
                result = self.attachment_processor.process(int(artifact["id"]))
                if result.status == "extracted":
                    extracted += 1
                elif result.status in {"failed", "skipped"}:
                    failed += 1
            except (AttachmentProcessingError, ValueError):
                failed += 1
                LOGGER.exception(
                    "Official attachment processing failed for artifact %s.",
                    artifact.get("id"),
                )
        return {"processed": processed, "extracted": extracted, "failed": failed}

    def _publish_with_alert(self, report_date: str) -> None:
        LOGGER.info("Publishing daily report for %s.", report_date)
        self._heartbeat("publishing", f"publishing {report_date}")
        try:
            self._sync_with_alert()
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
