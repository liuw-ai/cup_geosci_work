"""Long-running optional worker for the public CNPC browser capture."""

from __future__ import annotations

import argparse
import logging
import os
import signal
import time
from datetime import datetime, time as clock_time
from pathlib import Path
from threading import Event
from zoneinfo import ZoneInfo

from job_hub.browser_liveness import wait_with_heartbeat
from job_hub.browser_timeout import run_capture_with_timeout
from job_hub.cnpc_browser_runner import run_cnpc_browser_capture, write_cnpc_capture_failure
from job_hub.config import Settings, load_project_env
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
        self.pipeline = JobPipeline(settings, self.database)
        self.pipeline.bootstrap_sources()
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
                wait_with_heartbeat(
                    self.stop_event,
                    self.interval_seconds,
                    self._heartbeat,
                    detail="waiting for next CNPC browser capture",
                )
            else:
                wait_with_heartbeat(
                    self.stop_event,
                    300,
                    self._heartbeat,
                    status="waiting",
                    detail="CNPC maintenance window 00:00-06:00",
                )
        self._heartbeat("stopped", "CNPC browser worker received a stop signal")

    def stop(self, *_: object) -> None:
        self.stop_event.set()

    def _run_once(self) -> None:
        source = self.database.get_source(self.source_id)
        if source is None:
            self._heartbeat("degraded", f"source is not registered: {self.source_id}")
            LOGGER.error("CNPC browser source is not registered: %s", self.source_id)
            return
        config = dict(source["config"])
        background_capture_enabled = bool(config.get("background_capture_enabled"))
        if not source.get("enabled", False) and not background_capture_enabled:
            self._heartbeat("idle", f"source remains disabled: {self.source_id}")
            LOGGER.info("CNPC browser source is disabled: %s", self.source_id)
            return
        cdp_url = os.getenv("CNPC_BROWSER_CDP_URL", "").strip()
        if cdp_url:
            config["cdp_url"] = cdp_url
        output = self.settings.data_dir / str(config["capture_path"])
        self._heartbeat("capturing", f"capturing {self.source_id}")
        try:
            payload = run_capture_with_timeout(
                run_cnpc_browser_capture,
                kwargs={
                    "url": str(config.get("browser_url") or source["homepage_url"]),
                    "output": output,
                    "config": config,
                    "allowed_hosts": list(config["allowed_hosts"]),
                    "user_agent": "CUPB-Geoscience-Employment-Information-Service/1.0",
                },
                timeout_seconds=int(os.getenv("CNPC_BROWSER_CAPTURE_TIMEOUT_SECONDS", "0")),
                on_wait=self._heartbeat,
                stop_event=getattr(self, "stop_event", None),
            )
            if payload.get("status") != "success":
                try:
                    self.database.record_source_health(
                        self.source_id,
                        status="source_degraded",
                        detail="中国石油浏览器捕获不完整，学生端来源状态未改变。",
                    )
                except Exception:
                    LOGGER.exception("Could not record CNPC partial-capture health")
                self._heartbeat("degraded", "CNPC capture is partial; publication remains blocked")
                LOGGER.warning("CNPC capture is partial: %s", payload.get("scan"))
            else:
                if not source.get("enabled", False):
                    if not bool(config.get("auto_activate_on_complete_capture")):
                        raise RuntimeError(
                            "CNPC complete capture is staged but automatic activation is disabled"
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
                        "CNPC capture succeeded but source synchronization did not finish: "
                        f"{sync_result.status}: {sync_result.error or 'unknown error'}"
                    )
                self._heartbeat("running", f"CNPC capture wrote {len(payload.get('jobs', []))} job rows")
                LOGGER.info("CNPC capture completed: %s", payload.get("scan"))
        except Exception as error:
            try:
                write_cnpc_capture_failure(
                    output=output,
                    platform_url=str(config.get("browser_url") or source["homepage_url"]),
                    status=(
                        "access_limited"
                        if (
                            "HTTP 4" in str(error)
                            or "access-limited" in str(error)
                            or "robots" in str(error).lower()
                        )
                        else "parse_failed"
                    ),
                    reason=str(error),
                    source_id=str(config.get("source_id") or self.source_id),
                    adapter_version=str(config.get("adapter_version") or "cnpc-browser-v1"),
                )
            except Exception:
                LOGGER.exception("Could not persist CNPC failure capture")
            try:
                self.database.record_source_health(
                    self.source_id,
                    status=(
                        "source_degraded"
                        if "HTTP 4" in str(error) or "robots" in str(error).lower()
                        else "source_error"
                    ),
                    detail=f"中国石油浏览器捕获失败，学生端来源状态未改变：{error}",
                )
            except Exception:
                LOGGER.exception("Could not record CNPC source health")
            self._heartbeat("degraded", str(error)[:500])
            LOGGER.exception("CNPC browser capture failed")

    def _heartbeat(self, status: str, detail: str = "") -> None:
        try:
            self.database.record_service_heartbeat("cnpc-browser", status, detail)
        except Exception:
            LOGGER.exception("Could not record CNPC browser heartbeat")


def main() -> None:
    argparse.ArgumentParser(
        prog="python -m job_hub.cnpc_browser_worker",
        description="运行中国石油官方动态招聘浏览器 worker。",
    ).parse_args()
    load_project_env()
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
