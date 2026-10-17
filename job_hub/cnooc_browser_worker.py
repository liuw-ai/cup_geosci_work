"""Long-running worker for the CNOOC official detail capture."""

from __future__ import annotations

import logging
import os
import signal
import argparse
from contextlib import contextmanager
from threading import Event

from job_hub.browser_liveness import wait_with_heartbeat
from job_hub.cnooc_browser_capture import (
    run_cnooc_browser_capture,
    run_cnooc_detail_retry,
    write_cnooc_capture_failure,
)
from job_hub.browser_timeout import run_capture_with_timeout
from job_hub.config import Settings, load_project_env
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
        self.pipeline = JobPipeline(settings, self.database)
        self.pipeline.bootstrap_sources()
        self.stop_event = Event()
        self.source_id = os.getenv("CNOOC_BROWSER_SOURCE_ID", "cnooc-career-browser")
        self.interval_seconds = max(900, int(os.getenv("CNOOC_BROWSER_INTERVAL_MINUTES", "180")) * 60)
        self.capture_timeout_seconds = max(
            0, int(os.getenv("CNOOC_BROWSER_CAPTURE_TIMEOUT_SECONDS", "900"))
        )
        self.run_once = os.getenv("CNOOC_BROWSER_ONCE", "").strip().lower() in {"1", "true", "yes", "on"}

    def run_forever(self) -> int:
        self._heartbeat("starting", "CNOOC browser worker boot completed")
        while not self.stop_event.is_set():
            self._run_once()
            if self.run_once:
                if getattr(self, "_last_heartbeat_status", "") == "degraded":
                    return 1
                self._heartbeat("stopped", "CNOOC browser worker completed one-shot capture")
                return 0
            wait_with_heartbeat(
                self.stop_event,
                self.interval_seconds,
                self._heartbeat,
                detail="waiting for next CNOOC browser capture",
            )
        self._heartbeat("stopped", "CNOOC browser worker received a stop signal")
        return 0

    def stop(self, *_: object) -> None:
        self.stop_event.set()

    def _run_once(self) -> None:
        source = self.database.get_source(self.source_id)
        if source is None:
            self._heartbeat("degraded", f"source is not registered: {self.source_id}")
            return
        config = dict(source["config"])
        # Propagate the process-wide transport policy to the API pagination
        # session used by the browser capture.  Without this, raw requests
        # would silently honor a broken local proxy even in direct mode.
        config["transport_mode"] = getattr(
            self.settings, "http_transport_mode", "environment"
        )
        config["http_client"] = getattr(self.settings, "http_client", "requests")
        cdp_url = os.getenv("CNOOC_BROWSER_CDP_URL", "").strip()
        if cdp_url:
            config["cdp_url"] = cdp_url
        output = self.settings.data_dir / str(config["capture_path"])
        self._heartbeat("capturing", f"capturing {self.source_id}")
        try:
            payload = run_capture_with_timeout(
                run_cnooc_browser_capture,
                kwargs={
                    "listing_url": str(source["homepage_url"] if not config.get("application_url") else config["application_url"]),
                    "output": output,
                    "config": config,
                    "allowed_hosts": list(config["allowed_hosts"]),
                    "user_agent": "CUPB-Geoscience-Employment-Information-Service/1.0",
                },
                timeout_seconds=getattr(self, "capture_timeout_seconds", 0),
                on_wait=self._heartbeat,
                stop_event=getattr(self, "stop_event", None),
            )
            if payload.get("status") != "success":
                scan = payload.get("scan") if isinstance(payload.get("scan"), dict) else {}
                # A complete listing with a small number of detail failures is
                # eligible for a targeted retry. The retry merges only rows
                # from this same listing pass and replaces the canonical file
                # only after every failed detail succeeds.
                retry_path = output.with_suffix(".failure.json")
                retry_limit = max(0, min(int(config.get("retry_max_targets", 10)), 50))
                failed = int(scan.get("detail_failed", 0) or 0)
                if (
                    bool(scan.get("pagination_complete"))
                    and failed > 0
                    and failed <= retry_limit
                    and retry_path.is_file()
                ):
                    self._heartbeat("retrying", f"retrying {failed} CNOOC detail rows")
                    retry = run_capture_with_timeout(
                        run_cnooc_detail_retry,
                        kwargs={
                            "partial_capture": retry_path,
                            "output": output,
                            "config": config,
                            "allowed_hosts": list(config["allowed_hosts"]),
                            "user_agent": "CUPB-Geoscience-Employment-Information-Service/1.0",
                        },
                        timeout_seconds=getattr(self, "capture_timeout_seconds", 0),
                        on_wait=self._heartbeat,
                        stop_event=getattr(self, "stop_event", None),
                    )
                    if retry.get("status") == "success":
                        sync_result = self.pipeline.sync_source_manual(source)
                        if sync_result.status != "finished":
                            raise RuntimeError(
                                "CNOOC detail retry succeeded but source synchronization did not finish: "
                                f"{sync_result.status}: {sync_result.error or 'unknown error'}"
                            )
                        self._heartbeat(
                            "running",
                            f"repaired {failed} CNOOC detail rows; captured {len(retry.get('rows', []))} rows",
                        )
                        LOGGER.info("CNOOC detail retry completed: %s", retry.get("scan"))
                        return
                    payload = retry
                self._heartbeat("degraded", f"partial detail capture: {payload.get('scan')}")
                return
            # A successful browser snapshot must become the public source
            # immediately. Otherwise the normal sync cadence can leave the
            # student view stale for a full interval after a complete capture.
            pipeline = getattr(self, "pipeline", None)
            if pipeline is not None:
                sync_result = pipeline.sync_source_manual(source)
                if sync_result.status != "finished":
                    raise RuntimeError(
                        "CNOOC capture succeeded but source synchronization did not finish: "
                        f"{sync_result.status}: {sync_result.error or 'unknown error'}"
                    )
            self._heartbeat("running", f"captured {len(payload.get('rows', []))} CNOOC detail rows")
            LOGGER.info("CNOOC capture completed: %s", payload.get("scan"))
        except Exception as error:
            try:
                write_cnooc_capture_failure(
                    output=output,
                    platform_url=str(config.get("application_url") or source["homepage_url"]),
                    status="access_limited" if "HTTP 4" in str(error) or "robots" in str(error).lower() else "parse_failed",
                    reason=str(error),
                    source_id=str(config.get("source_id") or self.source_id),
                    adapter_version=str(config.get("adapter_version") or "cnooc-browser-v1"),
                )
            except Exception:
                LOGGER.exception("Could not persist CNOOC failure capture")
            self._heartbeat("degraded", str(error)[:500])
            LOGGER.exception("CNOOC browser capture failed")

    def _heartbeat(self, status: str, detail: str = "") -> None:
        self._last_heartbeat_status = status
        try:
            self.database.record_service_heartbeat("cnooc-browser", status, detail)
        except Exception:
            LOGGER.exception("Could not record CNOOC browser heartbeat")


def main() -> None:
    argparse.ArgumentParser(
        prog="python -m job_hub.cnooc_browser_worker",
        description="运行中国海油官方详情浏览器采集 worker。",
    ).parse_args()
    load_project_env()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    worker = CnoocBrowserWorker(Settings.from_env())
    signal.signal(signal.SIGINT, worker.stop)
    signal.signal(signal.SIGTERM, worker.stop)
    raise SystemExit(worker.run_forever())


if __name__ == "__main__":
    main()
