"""Long-running optional worker for the CMGB/国聘 browser capture."""

from __future__ import annotations

import logging
import os
import signal
from threading import Event
from zoneinfo import ZoneInfo

from job_hub.cmgb_browser_capture import run_cmgb_browser_capture, write_cmgb_capture_failure
from job_hub.config import Settings
from job_hub.db import Database
from job_hub.pipeline import JobPipeline


LOGGER = logging.getLogger("job_hub.cmgb_browser_worker")


class CmgbBrowserWorker:
    """Capture a public CMGB page periodically without publishing partial data."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.database = Database(settings.database_path)
        self.database.initialize()
        JobPipeline(settings, self.database).bootstrap_sources()
        self.stop_event = Event()
        self.timezone = ZoneInfo(settings.timezone)
        self.source_id = os.getenv("CMGB_BROWSER_SOURCE_ID", "cmgb-iguopin-browser")
        self.interval_seconds = max(
            900, int(os.getenv("CMGB_BROWSER_INTERVAL_MINUTES", "180")) * 60
        )

    def run_forever(self) -> None:
        self._heartbeat("starting", "CMGB browser worker boot completed")
        while not self.stop_event.is_set():
            self._run_once()
            self.stop_event.wait(self.interval_seconds)
        self._heartbeat("stopped", "CMGB browser worker received a stop signal")

    def stop(self, *_: object) -> None:
        self.stop_event.set()

    def _run_once(self) -> None:
        source = self.database.get_source(self.source_id)
        if source is None:
            self._heartbeat("degraded", f"source is not registered: {self.source_id}")
            LOGGER.error("CMGB browser source is not registered: %s", self.source_id)
            return
        config = dict(source["config"])
        cdp_url = os.getenv("CMGB_BROWSER_CDP_URL", "").strip()
        if cdp_url:
            config["cdp_url"] = cdp_url
        output = self.settings.data_dir / str(config["capture_path"])
        self._heartbeat("capturing", f"capturing {self.source_id}")
        try:
            payload = run_cmgb_browser_capture(
                url=str(config.get("browser_url") or source["homepage_url"]),
                output=output,
                config=config,
                allowed_hosts=list(config["allowed_hosts"]),
                user_agent="CUPB-Geoscience-Employment-Information-Service/1.0",
            )
            status = "running" if payload.get("status") == "success" else "degraded"
            self._heartbeat(status, f"CMGB capture: {payload.get('scan')}")
            LOGGER.info("CMGB capture completed: %s", payload.get("scan"))
        except Exception as error:
            try:
                write_cmgb_capture_failure(
                    output=output,
                    platform_url=str(config.get("browser_url") or source["homepage_url"]),
                    status="access_limited" if "HTTP 4" in str(error) or "robots" in str(error).lower() else "parse_failed",
                    reason=str(error),
                )
            except Exception:
                LOGGER.exception("Could not persist CMGB failure capture")
            self._heartbeat("degraded", str(error)[:500])
            LOGGER.exception("CMGB browser capture failed")

    def _heartbeat(self, status: str, detail: str = "") -> None:
        try:
            self.database.record_service_heartbeat("cmgb-browser", status, detail)
        except Exception:
            LOGGER.exception("Could not record CMGB browser heartbeat")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    worker = CmgbBrowserWorker(Settings.from_env())
    signal.signal(signal.SIGINT, worker.stop)
    signal.signal(signal.SIGTERM, worker.stop)
    worker.run_forever()


if __name__ == "__main__":
    main()
