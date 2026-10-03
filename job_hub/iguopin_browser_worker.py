"""Long-running worker for one registered employer-owned 国聘 portal.

The worker is intentionally source-scoped.  It never discovers arbitrary
国聘 employers and it refuses to run a registry entry still marked disabled.
That lets an adapter be tested in an isolated database before any student-facing
source transition is authorised.
"""

from __future__ import annotations

import logging
import os
import signal
from threading import Event
from zoneinfo import ZoneInfo

from job_hub.config import Settings
from job_hub.db import Database
from job_hub.iguopin_browser_capture import (
    run_iguopin_browser_capture,
    run_iguopin_general_browser_capture,
    write_iguopin_capture_failure,
)
from job_hub.pipeline import JobPipeline


LOGGER = logging.getLogger("job_hub.iguopin_browser_worker")


class IguopinBrowserWorker:
    """Periodically capture one approved public employer portal only."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.database = Database(settings.database_path)
        self.database.initialize()
        self.pipeline = JobPipeline(settings, self.database)
        self.pipeline.bootstrap_sources()
        self.stop_event = Event()
        self.timezone = ZoneInfo(settings.timezone)
        self.source_id = os.getenv(
            "IGUOPIN_BROWSER_SOURCE_ID", "chinalco-iguopin-browser"
        ).strip()
        self.service_name = os.getenv(
            "IGUOPIN_BROWSER_SERVICE_NAME", self.source_id
        ).strip() or self.source_id
        self.run_once = os.getenv("IGUOPIN_BROWSER_ONCE", "").strip().lower() in {
            "1",
            "true",
            "yes",
        }
        self.interval_seconds = max(
            900, int(os.getenv("IGUOPIN_BROWSER_INTERVAL_MINUTES", "180")) * 60
        )

    def run_forever(self) -> None:
        self._heartbeat("starting", "国聘 employer browser worker boot completed")
        while not self.stop_event.is_set():
            self._run_once()
            if self.run_once:
                self._heartbeat("stopped", "国聘 browser worker completed one-shot capture")
                return
            self.stop_event.wait(self.interval_seconds)
        self._heartbeat("stopped", "国聘 browser worker received a stop signal")

    def stop(self, *_: object) -> None:
        self.stop_event.set()

    def _run_once(self) -> None:
        source = self.database.get_source(self.source_id)
        if source is None:
            self._heartbeat("degraded", f"source is not registered: {self.source_id}")
            LOGGER.error("国聘 browser source is not registered: %s", self.source_id)
            return
        source_type = source.get("source_type")
        if source_type not in {
            "iguopin_browser_rows",
            "iguopin_general_browser_rows",
        }:
            self._heartbeat(
                "degraded",
                f"source has incompatible type: {source.get('source_type')}",
            )
            LOGGER.error(
                "国聘 browser source has incompatible type: %s (%s)",
                self.source_id,
                source.get("source_type"),
            )
            return
        if not source.get("enabled", False):
            self._heartbeat(
                "idle",
                f"source remains isolated and disabled: {self.source_id}",
            )
            LOGGER.info("国聘 browser source is disabled: %s", self.source_id)
            return

        config = dict(source["config"])
        cdp_url = os.getenv("IGUOPIN_BROWSER_CDP_URL", "").strip()
        if cdp_url:
            config["cdp_url"] = cdp_url
        output = self.settings.data_dir / str(config["capture_path"])
        self._heartbeat("capturing", f"capturing {self.source_id}")
        try:
            capture = (
                run_iguopin_general_browser_capture
                if source_type == "iguopin_general_browser_rows"
                else run_iguopin_browser_capture
            )
            payload = capture(
                url=str(config.get("browser_url") or source["homepage_url"]),
                output=output,
                config=config,
                allowed_hosts=list(config["allowed_hosts"]),
                user_agent="CUPB-Geoscience-Employment-Information-Service/1.0",
            )
            # The capture and the public database used to be two independent
            # schedules: a successful browser scan could sit on disk for up to
            # one full source-sync interval before students saw it.  Sync this
            # source immediately after a complete capture.  The defensive
            # getattr keeps the small __new__-based unit-test fixtures usable.
            pipeline = getattr(self, "pipeline", None)
            if payload.get("status") == "success" and pipeline is not None:
                sync_result = pipeline.sync_source_manual(source)
                if sync_result.status != "finished":
                    raise RuntimeError(
                        "国聘 capture succeeded but source synchronization did not finish: "
                        f"{sync_result.status}: {sync_result.error or 'unknown error'}"
                    )
            status = "running" if payload.get("status") == "success" else "degraded"
            self._heartbeat(status, f"国聘 capture: {payload.get('scan')}")
            LOGGER.info("国聘 capture completed: %s", payload.get("scan"))
        except Exception as error:
            try:
                write_iguopin_capture_failure(
                    output=output,
                    platform_url=str(
                        config.get("browser_url") or source["homepage_url"]
                    ),
                    status=(
                        "access_limited"
                        if "HTTP 4" in str(error)
                        or "robots" in str(error).lower()
                        else "parse_failed"
                    ),
                    reason=str(error),
                    source_id=str(config.get("source_id") or self.source_id),
                    adapter_version=str(
                        config.get("adapter_version") or "iguopin-browser-v1"
                    ),
                )
            except Exception:
                LOGGER.exception("Could not persist 国聘 failure capture")
            self._heartbeat("degraded", str(error)[:500])
            LOGGER.exception("国聘 browser capture failed")

    def _heartbeat(self, status: str, detail: str = "") -> None:
        try:
            self.database.record_service_heartbeat(self.service_name, status, detail)
        except Exception:
            LOGGER.exception("Could not record 国聘 browser heartbeat")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    worker = IguopinBrowserWorker(Settings.from_env())
    signal.signal(signal.SIGINT, worker.stop)
    signal.signal(signal.SIGTERM, worker.stop)
    worker.run_forever()


if __name__ == "__main__":
    main()
