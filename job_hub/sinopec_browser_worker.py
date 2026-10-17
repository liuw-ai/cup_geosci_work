"""Long-running worker for the official Sinopec campus SPA capture."""

from __future__ import annotations

import argparse
import logging
import os
import signal
from threading import Event

from job_hub.browser_liveness import wait_with_heartbeat
from job_hub.config import Settings, load_project_env
from job_hub.browser_timeout import run_capture_with_timeout
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
        self.pipeline = JobPipeline(settings, self.database)
        self.pipeline.bootstrap_sources()
        self.stop_event = Event()
        self.source_id = os.getenv("SINOPEC_BROWSER_SOURCE_ID", "sinopec-career")
        self.interval_seconds = max(
            900, int(os.getenv("SINOPEC_BROWSER_INTERVAL_MINUTES", "180")) * 60
        )
        self.capture_timeout_seconds = max(
            0, int(os.getenv("SINOPEC_BROWSER_CAPTURE_TIMEOUT_SECONDS", "1800"))
        )
        self.run_once = os.getenv("SINOPEC_BROWSER_ONCE", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

    def run_forever(self) -> int:
        self._heartbeat("starting", "Sinopec browser worker boot completed")
        while not self.stop_event.is_set():
            self._run_once()
            if self.run_once:
                if getattr(self, "_last_heartbeat_status", "") == "degraded":
                    return 1
                self._heartbeat("stopped", "Sinopec browser worker completed one-shot capture")
                return 0
            wait_with_heartbeat(
                self.stop_event,
                self.interval_seconds,
                self._heartbeat,
                detail="waiting for next Sinopec browser capture",
            )
        self._heartbeat("stopped", "Sinopec browser worker received a stop signal")
        return 0

    def stop(self, *_: object) -> None:
        self.stop_event.set()

    def _run_once(self) -> None:
        source = self.database.get_source(self.source_id)
        if source is None:
            self._heartbeat("degraded", f"source is not registered: {self.source_id}")
            return
        config = dict(source["config"])
        background_capture_enabled = bool(config.get("background_capture_enabled"))
        if not source.get("enabled", False) and not background_capture_enabled:
            self._heartbeat("idle", f"source remains disabled: {self.source_id}")
            LOGGER.info("Sinopec browser source is disabled: %s", self.source_id)
            return
        cdp_url = os.getenv("SINOPEC_BROWSER_CDP_URL", "").strip()
        if cdp_url:
            config["cdp_url"] = cdp_url
        capture_path = str(config.get("capture_path") or "verified/sinopec-geoscience-current.json")
        output = self.settings.data_dir / capture_path
        self._heartbeat("capturing", f"capturing {self.source_id}")
        try:
            payload = run_capture_with_timeout(
                run_sinopec_browser_capture,
                kwargs={
                    "listing_url": str(config.get("browser_url") or source["homepage_url"]),
                    "output": output,
                    "config": config,
                    "allowed_hosts": list(config.get("allowed_hosts", [])),
                    "user_agent": "CUPB-Geoscience-Employment-Information-Service/1.0",
                },
                timeout_seconds=getattr(self, "capture_timeout_seconds", 0),
                on_wait=self._heartbeat,
                stop_event=getattr(self, "stop_event", None),
            )
            if payload.get("status") != "success":
                self.database.record_source_health(
                    self.source_id,
                    status="source_degraded",
                    detail="中国石化浏览器捕获不完整，学生端来源状态未改变。",
                )
                self._heartbeat("degraded", f"partial Sinopec capture: {payload.get('scan')}")
                return
            if not source.get("enabled", False):
                if not bool(config.get("auto_activate_on_complete_capture")):
                    raise RuntimeError(
                        "Sinopec complete capture is staged but automatic activation is disabled"
                    )
                sync_result = self.pipeline.sync_source_atomically(
                    source,
                    before_commit=lambda: self.database.set_source_enabled(
                        self.source_id, True
                    ),
                )
            else:
                sync_result = self.pipeline.sync_source_manual(source)
            if sync_result.status != "finished":
                raise RuntimeError(
                    "Sinopec capture succeeded but source synchronization did not finish: "
                    f"{sync_result.status}: {sync_result.error or 'unknown error'}"
                )
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
                    source_id=str(config.get("source_id") or self.source_id),
                    adapter_version=str(config.get("adapter_version") or "sinopec-browser-v1"),
                )
            except Exception:
                LOGGER.exception("Could not persist Sinopec failure capture")
            status = (
                "source_degraded"
                if JobPipeline._is_policy_error(str(error))
                else "source_error"
            )
            try:
                self.database.record_source_health(
                    self.source_id,
                    status=status,
                    detail=f"中国石化浏览器捕获失败，学生端来源状态未改变：{error}",
                )
            except Exception:
                LOGGER.exception("Could not record Sinopec source health")
            self._heartbeat("degraded", str(error)[:500])
            LOGGER.exception("Sinopec browser capture failed")

    def _heartbeat(self, status: str, detail: str = "") -> None:
        self._last_heartbeat_status = status
        try:
            self.database.record_service_heartbeat("sinopec-browser", status, detail)
        except Exception:
            LOGGER.exception("Could not record Sinopec browser heartbeat")


def main() -> None:
    argparse.ArgumentParser(
        prog="python -m job_hub.sinopec_browser_worker",
        description="运行中国石化官方动态招聘浏览器 worker。",
    ).parse_args()
    load_project_env()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    worker = SinopecBrowserWorker(Settings.from_env())
    signal.signal(signal.SIGINT, worker.stop)
    signal.signal(signal.SIGTERM, worker.stop)
    raise SystemExit(worker.run_forever())


if __name__ == "__main__":
    main()
