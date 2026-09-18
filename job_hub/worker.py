from __future__ import annotations

import logging
import signal
import time
from datetime import datetime, time as clock_time
from threading import Event
from zoneinfo import ZoneInfo

from job_hub.audit import audit_database
from job_hub.config import Settings
from job_hub.db import Database
from job_hub.emailer import DeliveryError, Mailer
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
            summary = self.pipeline.sync_all()
            self.last_sync_monotonic = time.monotonic()
            LOGGER.info(
                "Source synchronization complete: %s",
                summary.as_dict(),
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
