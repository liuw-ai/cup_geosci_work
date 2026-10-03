"""Long-running optional worker for the CMGB/国聘 browser capture."""

from __future__ import annotations

import logging
import os
import signal
from contextlib import contextmanager
from threading import Event
from zoneinfo import ZoneInfo

from job_hub.cmgb_browser_capture import (
    CmgbBrowserCaptureError,
    run_cmgb_browser_capture,
    run_cmgb_detail_retry,
    write_cmgb_capture_failure,
)
from job_hub.config import Settings
from job_hub.db import Database
from job_hub.pipeline import JobPipeline


LOGGER = logging.getLogger("job_hub.cmgb_browser_worker")


@contextmanager
def _capture_deadline(seconds: int):
    """Bound one browser capture so a stalled detail page cannot block forever."""
    bounded = max(0, int(seconds))
    if bounded <= 0 or not hasattr(signal, "SIGALRM"):
        yield
        return
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, 0)

    def raise_timeout(_signum: int, _frame: object) -> None:
        raise TimeoutError(f"CMGB browser capture exceeded {bounded}s deadline")

    signal.signal(signal.SIGALRM, raise_timeout)
    signal.setitimer(signal.ITIMER_REAL, float(bounded))
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer[0] > 0:
            signal.setitimer(signal.ITIMER_REAL, *previous_timer)


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
        self.run_once = os.getenv("CMGB_BROWSER_ONCE", "").strip().lower() in {
            "1",
            "true",
            "yes",
        }
        self.interval_seconds = max(
            900, int(os.getenv("CMGB_BROWSER_INTERVAL_MINUTES", "180")) * 60
        )
        self.capture_timeout_seconds = max(
            0, int(os.getenv("CMGB_BROWSER_CAPTURE_TIMEOUT_SECONDS", "1800"))
        )

    def run_forever(self) -> None:
        self._heartbeat("starting", "CMGB browser worker boot completed")
        while not self.stop_event.is_set():
            self._run_once()
            if self.run_once:
                self._heartbeat("stopped", "CMGB browser worker completed one-shot capture")
                return
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
        if self._retry_recent_partial_capture(source, output, config):
            return
        self._heartbeat("capturing", f"capturing {self.source_id}")
        try:
            with _capture_deadline(self.capture_timeout_seconds):
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
                    source_id=str(config.get("source_id") or self.source_id),
                    adapter_version=str(config.get("adapter_version") or "cmgb-browser-v1"),
                )
            except Exception:
                LOGGER.exception("Could not persist CMGB failure capture")
            self._heartbeat("degraded", str(error)[:500])
            LOGGER.exception("CMGB browser capture failed")

    def _retry_recent_partial_capture(
        self,
        source: dict[str, object],
        output: object,
        config: dict[str, object],
    ) -> bool:
        """Retry a fresh frozen partial scan before touching its list pages.

        The retry input is the newest non-publishable diagnostic, never the
        success-only capture path. A stale or malformed diagnostic is not a
        new observation, so the ordinary full scan is allowed to replace it.
        A browser failure during an otherwise valid targeted retry is recorded
        and ends this cycle instead of immediately re-scanning every card.
        """

        if not bool(config.get("retry_partial_first", True)):
            return False
        capture_path = self.settings.data_dir / str(config["capture_path"])
        failure_capture = capture_path.with_suffix(".failure.json")
        if not failure_capture.is_file():
            return False
        try:
            payload = run_cmgb_detail_retry(
                retry_capture_path=failure_capture,
                output=output,
                config=config,
                allowed_hosts=list(config["allowed_hosts"]),
                user_agent="CUPB-Geoscience-Employment-Information-Service/1.0",
                max_age_hours=float(config.get("detail_retry_max_age_hours", 12)),
                require_capture_manifest=bool(config.get("require_capture_manifest", False)),
            )
        except CmgbBrowserCaptureError as error:
            message = str(error)
            retry_preconditions = (
                "requires a partial capture",
                "stale",
                "requires a completed pagination pass",
                "has no failed detail rows",
                "failure_records do not match",
                "capture_evidence manifest is required",
                "detail_url must be non-empty",
                "must be a concrete CMGB job detail URL",
            )
            if any(marker in message for marker in retry_preconditions):
                LOGGER.info("CMGB partial retry is not applicable: %s", message)
                return False
            self._heartbeat("degraded", f"CMGB retry input is invalid: {message}"[:500])
            LOGGER.exception("CMGB detail retry input is invalid")
            return True
        except Exception as error:
            try:
                write_cmgb_capture_failure(
                    output=output,
                    platform_url=str(config.get("browser_url") or source["homepage_url"]),
                    status="parse_failed",
                    reason=f"CMGB targeted detail retry failed: {error}",
                    source_id=str(config.get("source_id") or self.source_id),
                    adapter_version=str(config.get("adapter_version") or "cmgb-browser-v1"),
                )
            except Exception:
                LOGGER.exception("Could not archive CMGB targeted retry failure")
            self._heartbeat("degraded", f"CMGB targeted detail retry failed: {error}"[:500])
            LOGGER.exception("CMGB targeted detail retry failed")
            return True

        status = "running" if payload.get("status") == "success" else "degraded"
        self._heartbeat(status, f"CMGB targeted detail retry: {payload.get('scan')}")
        LOGGER.info("CMGB targeted detail retry completed: %s", payload.get("scan"))
        return True

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
