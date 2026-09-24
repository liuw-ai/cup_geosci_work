from __future__ import annotations

import logging
import signal
import time
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
            attachment_summary = self._process_registered_attachments()
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
