"""Long-running worker for a generic official browser-row source.

The worker is intentionally source-agnostic: source-specific selectors stay
in ``data/sources.json`` while this process owns scheduling, heartbeats and
failure evidence.  A failed or partial capture is written beside the last
successful snapshot and is never published.
"""

from __future__ import annotations

import logging
import os
import signal
from threading import Event

from job_hub.browser_capture import run_browser_capture, write_browser_capture_failure
from job_hub.config import Settings
from job_hub.db import Database


LOGGER = logging.getLogger("job_hub.official_browser_worker")


class OfficialBrowserWorker:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.database = Database(settings.database_path)
        self.database.initialize()
        # This worker is intentionally source-isolated. Loading the complete
        # registry here would let an unrelated, newly added source contract
        # prevent an otherwise healthy browser capture from starting. Source
        # registration remains the web/worker synchronization responsibility;
        # a missing target source is reported through the heartbeat below.
        self.stop_event = Event()
        self.source_id = os.getenv("OFFICIAL_BROWSER_SOURCE_ID", "pipechina-browser-capture")
        self.service_name = os.getenv("OFFICIAL_BROWSER_SERVICE_NAME", "pipechina-browser")
        self.interval_seconds = max(
            900, int(os.getenv("OFFICIAL_BROWSER_INTERVAL_MINUTES", "180")) * 60
        )
        self.run_once = os.getenv("OFFICIAL_BROWSER_ONCE", "").strip().lower() in {
            "1", "true", "yes", "on"
        }

    def run_forever(self) -> None:
        self._heartbeat("starting", f"official browser worker boot completed: {self.source_id}")
        while not self.stop_event.is_set():
            self._run_once()
            if self.run_once:
                self._heartbeat("stopped", "official browser worker completed one-shot capture")
                return
            self.stop_event.wait(self.interval_seconds)
        self._heartbeat("stopped", "official browser worker received a stop signal")

    def stop(self, *_: object) -> None:
        self.stop_event.set()

    def _run_once(self) -> None:
        source = self.database.get_source(self.source_id)
        if source is None:
            self._heartbeat("degraded", f"source is not registered: {self.source_id}")
            return
        config = dict(source["config"])
        output = self.settings.data_dir / str(config["capture_path"])
        browser_url = str(config.get("browser_url") or source["homepage_url"])
        self._heartbeat("capturing", f"capturing {self.source_id}")
        try:
            payload = run_browser_capture(
                url=browser_url,
                output=output,
                config=config,
                allowed_hosts=list(config["allowed_hosts"]),
                user_agent="CUPB-Geoscience-Employment-Information-Service/1.0",
            )
            self._heartbeat(
                "running",
                f"capture completed: {len(payload.get('rows', []))} rows",
            )
            LOGGER.info("Official browser capture completed: %s", payload.get("scan"))
        except Exception as error:
            try:
                write_browser_capture_failure(
                    output=output,
                    platform_url=browser_url,
                    status=(
                        "access_limited"
                        if "HTTP 4" in str(error) or "robots" in str(error).lower()
                        else "parse_failed"
                    ),
                    reason=str(error),
                    source_id=str(config.get("source_id") or self.source_id),
                    adapter_version=str(config.get("adapter_version") or "browser-capture-v1"),
                )
            except Exception:
                LOGGER.exception("Could not persist official browser failure capture")
            self._heartbeat("degraded", str(error)[:500])
            LOGGER.exception("Official browser capture failed")

    def _heartbeat(self, status: str, detail: str = "") -> None:
        try:
            self.database.record_service_heartbeat(self.service_name, status, detail)
        except Exception:
            LOGGER.exception("Could not record official browser heartbeat")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    worker = OfficialBrowserWorker(Settings.from_env())
    signal.signal(signal.SIGINT, worker.stop)
    signal.signal(signal.SIGTERM, worker.stop)
    worker.run_forever()


if __name__ == "__main__":
    main()
