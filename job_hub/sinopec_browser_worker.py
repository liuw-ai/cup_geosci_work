"""Long-running worker for the official Sinopec campus SPA capture."""

from __future__ import annotations

import logging
import os
import signal
from threading import Event

from job_hub.config import Settings
from job_hub.db import Database
from job_hub.pipeline import JobPipeline
from job_hub.sinopec_browser_capture import (
    run_sinopec_browser_capture,
    write_sinopec_capture_failure,
)


LOGGER = logging.getLogger("job_hub.sinopec_browser_worker")


class SinopecBrowserWorker:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.database = Database(settings.database_path)
        self.database.initialize()
        JobPipeline(settings, self.database).bootstrap_sources()
        self.stop_event = Event()
        self.source_id = os.getenv("SINOPEC_BROWSER_SOURCE_ID", "sinopec-career")
        self.interval_seconds = max(
            900, int(os.getenv("SINOPEC_BROWSER_INTERVAL_MINUTES", "180")) * 60
        )
        self.run_once = os.getenv("SINOPEC_BROWSER_ONCE", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

    def run_forever(self) -> None:
        self._heartbeat("starting", "Sinopec browser worker boot completed")
        while not self.stop_event.is_set():
            self._run_once()
            if self.run_once:
                self._heartbeat("stopped", "Sinopec browser worker completed one-shot capture")
                return
            self.stop_event.wait(self.interval_seconds)
        self._heartbeat("stopped", "Sinopec browser worker received a stop signal")

    def stop(self, *_: object) -> None:
        self.stop_event.set()

    def _run_once(self) -> None:
        source = self.database.get_source(self.source_id)
        if source is None:
            self._heartbeat("degraded", f"source is not registered: {self.source_id}")
            return
        config = dict(source["config"])
        cdp_url = os.getenv("SINOPEC_BROWSER_CDP_URL", "").strip()
        if cdp_url:
            config["cdp_url"] = cdp_url
        capture_path = str(config.get("capture_path") or "verified/sinopec-geoscience-current.json")
        output = self.settings.data_dir / capture_path
        self._heartbeat("capturing", f"capturing {self.source_id}")
        try:
            payload = run_sinopec_browser_capture(
                listing_url=str(config.get("browser_url") or source["homepage_url"]),
                output=output,
                config=config,
                allowed_hosts=list(config.get("allowed_hosts", [])),
                user_agent="CUPB-Geoscience-Employment-Information-Service/1.0",
            )
            if payload.get("status") != "success":
                self._heartbeat("degraded", f"partial Sinopec capture: {payload.get('scan')}")
                return
            self._heartbeat(
                "running",
                f"captured {payload.get('enterprise_total', 0)} units and {len(payload.get('jobs', []))} rows",
            )
            LOGGER.info("Sinopec capture completed: %s", payload.get("scan"))
        except Exception as error:
            try:
                write_sinopec_capture_failure(
                    output=output,
                    platform_url=str(source["homepage_url"]),
                    status="access_limited" if "HTTP 4" in str(error) or "robots" in str(error).lower() else "parse_failed",
                    reason=str(error),
                )
            except Exception:
                LOGGER.exception("Could not persist Sinopec failure capture")
            self._heartbeat("degraded", str(error)[:500])
            LOGGER.exception("Sinopec browser capture failed")

    def _heartbeat(self, status: str, detail: str = "") -> None:
        try:
            self.database.record_service_heartbeat("sinopec-browser", status, detail)
        except Exception:
            LOGGER.exception("Could not record Sinopec browser heartbeat")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    worker = SinopecBrowserWorker(Settings.from_env())
    signal.signal(signal.SIGINT, worker.stop)
    signal.signal(signal.SIGTERM, worker.stop)
    worker.run_forever()


if __name__ == "__main__":
    main()
