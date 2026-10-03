"""Long-running optional worker for the public CNPC browser capture."""

from __future__ import annotations

import logging
import os
import signal
import time
from datetime import datetime, time as clock_time
from pathlib import Path
from threading import Event
from zoneinfo import ZoneInfo

from job_hub.cnpc_browser_runner import run_cnpc_browser_capture, write_cnpc_capture_failure
from job_hub.config import Settings
from job_hub.db import Database
from job_hub.pipeline import JobPipeline


LOGGER = logging.getLogger("job_hub.cnpc_browser_worker")


def outside_maintenance_window(now: datetime) -> bool:
    """CNPC announces a daily 00:00-06:00 Asia/Shanghai maintenance window."""

    return now.time() >= clock_time(6, 5) and now.time() < clock_time(23, 50)


class CnpcBrowserWorker:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.database = Database(settings.database_path)
        self.database.initialize()
        JobPipeline(settings, self.database).bootstrap_sources()
        self.stop_event = Event()
        self.timezone = ZoneInfo(settings.timezone)
        self.source_id = os.getenv("CNPC_BROWSER_SOURCE_ID", "cnpc-career-browser")
        self.interval_seconds = max(
            900, int(os.getenv("CNPC_BROWSER_INTERVAL_MINUTES", "180")) * 60
        )

    def run_forever(self) -> None:
        self._heartbeat("starting", "CNPC browser worker boot completed")
        while not self.stop_event.is_set():
            now = datetime.now(self.timezone)
            if outside_maintenance_window(now):
                self._run_once()
                self.stop_event.wait(self.interval_seconds)
            else:
                self._heartbeat("waiting", "CNPC maintenance window 00:00-06:00")
                self.stop_event.wait(300)
        self._heartbeat("stopped", "CNPC browser worker received a stop signal")

    def stop(self, *_: object) -> None:
        self.stop_event.set()

    def _run_once(self) -> None:
        source = self.database.get_source(self.source_id)
        if source is None:
            self._heartbeat("degraded", f"source is not registered: {self.source_id}")
            LOGGER.error("CNPC browser source is not registered: %s", self.source_id)
            self.stop_event.wait(self.interval_seconds)
            return
        config = dict(source["config"])
        cdp_url = os.getenv("CNPC_BROWSER_CDP_URL", "").strip()
        if cdp_url:
            config["cdp_url"] = cdp_url
        output = self.settings.data_dir / str(config["capture_path"])
        self._heartbeat("capturing", f"capturing {self.source_id}")
        try:
            payload = run_cnpc_browser_capture(
                url=str(config.get("browser_url") or source["homepage_url"]),
                output=output,
                config=config,
                allowed_hosts=list(config["allowed_hosts"]),
                user_agent="CUPB-Geoscience-Employment-Information-Service/1.0",
            )
            if payload.get("status") != "success":
                self._heartbeat("degraded", "CNPC capture is partial; publication remains blocked")
                LOGGER.warning("CNPC capture is partial: %s", payload.get("scan"))
            else:
                self._heartbeat("running", f"CNPC capture wrote {len(payload.get('jobs', []))} job rows")
                LOGGER.info("CNPC capture completed: %s", payload.get("scan"))
        except Exception as error:
            try:
                write_cnpc_capture_failure(
                    output=output,
                    platform_url=str(config.get("browser_url") or source["homepage_url"]),
                    status="access_limited" if "HTTP 4" in str(error) or "access-limited" in str(error) else "parse_failed",
                    reason=str(error),
                    source_id=str(config.get("source_id") or self.source_id),
                    adapter_version=str(config.get("adapter_version") or "cnpc-browser-v1"),
                )
            except Exception:
                LOGGER.exception("Could not persist CNPC failure capture")
            self._heartbeat("degraded", str(error)[:500])
            LOGGER.exception("CNPC browser capture failed")

    def _heartbeat(self, status: str, detail: str = "") -> None:
        try:
            self.database.record_service_heartbeat("cnpc-browser", status, detail)
        except Exception:
            LOGGER.exception("Could not record CNPC browser heartbeat")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    worker = CnpcBrowserWorker(Settings.from_env())
    signal.signal(signal.SIGINT, worker.stop)
    signal.signal(signal.SIGTERM, worker.stop)
    worker.run_forever()


if __name__ == "__main__":
    main()
