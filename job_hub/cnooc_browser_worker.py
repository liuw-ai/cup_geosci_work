"""Long-running worker for the CNOOC official detail capture."""

from __future__ import annotations

import logging
import os
import signal
from contextlib import contextmanager
from threading import Event

from job_hub.cnooc_browser_capture import run_cnooc_browser_capture, write_cnooc_capture_failure
from job_hub.config import Settings
from job_hub.db import Database
from job_hub.pipeline import JobPipeline


LOGGER = logging.getLogger("job_hub.cnooc_browser_worker")


@contextmanager
def _capture_deadline(seconds: int):
    """Bound one CNOOC capture so a stuck CDP/detail page cannot run forever."""

    bounded = max(0, int(seconds))
    if bounded <= 0 or not hasattr(signal, "SIGALRM"):
        yield
        return
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, 0)

    def raise_timeout(_signum: int, _frame: object) -> None:
        raise TimeoutError(f"CNOOC browser capture exceeded {bounded}s deadline")

    signal.signal(signal.SIGALRM, raise_timeout)
    signal.setitimer(signal.ITIMER_REAL, float(bounded))
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer[0] > 0:
            signal.setitimer(signal.ITIMER_REAL, *previous_timer)


class CnoocBrowserWorker:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.database = Database(settings.database_path)
        self.database.initialize()
        JobPipeline(settings, self.database).bootstrap_sources()
        self.stop_event = Event()
        self.source_id = os.getenv("CNOOC_BROWSER_SOURCE_ID", "cnooc-career-browser")
        self.interval_seconds = max(900, int(os.getenv("CNOOC_BROWSER_INTERVAL_MINUTES", "180")) * 60)
        self.capture_timeout_seconds = max(
            0, int(os.getenv("CNOOC_BROWSER_CAPTURE_TIMEOUT_SECONDS", "900"))
        )
        self.run_once = os.getenv("CNOOC_BROWSER_ONCE", "").strip().lower() in {"1", "true", "yes", "on"}

    def run_forever(self) -> None:
        self._heartbeat("starting", "CNOOC browser worker boot completed")
        while not self.stop_event.is_set():
            self._run_once()
            if self.run_once:
                self._heartbeat("stopped", "CNOOC browser worker completed one-shot capture")
                return
            self.stop_event.wait(self.interval_seconds)
        self._heartbeat("stopped", "CNOOC browser worker received a stop signal")

    def stop(self, *_: object) -> None:
        self.stop_event.set()

    def _run_once(self) -> None:
        source = self.database.get_source(self.source_id)
        if source is None:
            self._heartbeat("degraded", f"source is not registered: {self.source_id}")
            return
        config = dict(source["config"])
        cdp_url = os.getenv("CNOOC_BROWSER_CDP_URL", "").strip()
        if cdp_url:
            config["cdp_url"] = cdp_url
        output = self.settings.data_dir / str(config["capture_path"])
        self._heartbeat("capturing", f"capturing {self.source_id}")
        try:
            with _capture_deadline(self.capture_timeout_seconds):
                payload = run_cnooc_browser_capture(
                    listing_url=str(source["homepage_url"] if not config.get("application_url") else config["application_url"]),
                    output=output,
                    config=config,
                    allowed_hosts=list(config["allowed_hosts"]),
                    user_agent="CUPB-Geoscience-Employment-Information-Service/1.0",
                )
            if payload.get("status") != "success":
                self._heartbeat("degraded", f"partial detail capture: {payload.get('scan')}")
                return
            self._heartbeat("running", f"captured {len(payload.get('rows', []))} CNOOC detail rows")
            LOGGER.info("CNOOC capture completed: %s", payload.get("scan"))
        except Exception as error:
            try:
                write_cnooc_capture_failure(
                    output=output,
                    platform_url=str(config.get("application_url") or source["homepage_url"]),
                    status="access_limited" if "HTTP 4" in str(error) or "robots" in str(error).lower() else "parse_failed",
                    reason=str(error),
                )
            except Exception:
                LOGGER.exception("Could not persist CNOOC failure capture")
            self._heartbeat("degraded", str(error)[:500])
            LOGGER.exception("CNOOC browser capture failed")

    def _heartbeat(self, status: str, detail: str = "") -> None:
        try:
            self.database.record_service_heartbeat("cnooc-browser", status, detail)
        except Exception:
            LOGGER.exception("Could not record CNOOC browser heartbeat")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    worker = CnoocBrowserWorker(Settings.from_env())
    signal.signal(signal.SIGINT, worker.stop)
    signal.signal(signal.SIGTERM, worker.stop)
    worker.run_forever()


if __name__ == "__main__":
    main()
