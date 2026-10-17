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
import argparse
from threading import Event

from job_hub.browser_liveness import wait_with_heartbeat
from job_hub.browser_capture import run_browser_capture, write_browser_capture_failure
from job_hub.browser_timeout import run_capture_with_timeout
from job_hub.config import Settings, load_project_env
from job_hub.db import Database
from job_hub.pipeline import JobPipeline


LOGGER = logging.getLogger("job_hub.official_browser_worker")


class OfficialBrowserWorker:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.database = Database(settings.database_path)
        self.database.initialize()
        self.pipeline = JobPipeline(settings, self.database)
        # Refresh the persisted target configuration on restart.  In
        # particular this projects ``background_capture_enabled`` and the
        # runtime activation policy into an existing database; the durable
        # enabled state is preserved by ``runtime_enabled_control``.
        self.pipeline.bootstrap_sources()
        self.stop_event = Event()
        self.source_id = os.getenv("OFFICIAL_BROWSER_SOURCE_ID", "pipechina-browser-capture")
        self.service_name = os.getenv("OFFICIAL_BROWSER_SERVICE_NAME", "pipechina-browser")
        self.interval_seconds = max(
            900, int(os.getenv("OFFICIAL_BROWSER_INTERVAL_MINUTES", "180")) * 60
        )
        self.capture_timeout_seconds = max(
            0, int(os.getenv("OFFICIAL_BROWSER_CAPTURE_TIMEOUT_SECONDS", "1800"))
        )
        self.run_once = os.getenv("OFFICIAL_BROWSER_ONCE", "").strip().lower() in {
            "1", "true", "yes", "on"
        }

    def run_forever(self) -> int:
        self._heartbeat("starting", f"official browser worker boot completed: {self.source_id}")
        while not self.stop_event.is_set():
            self._run_once()
            if self.run_once:
                if getattr(self, "_last_heartbeat_status", "") == "degraded":
                    return 1
                self._heartbeat("stopped", "official browser worker completed one-shot capture")
                return 0
            wait_with_heartbeat(
                self.stop_event,
                self.interval_seconds,
                self._heartbeat,
                detail="waiting for next official browser capture",
            )
        self._heartbeat("stopped", "official browser worker received a stop signal")
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
        was_enabled = bool(source.get("enabled", False))
        if not was_enabled and not background_capture_enabled:
            self._heartbeat("idle", f"source remains disabled: {self.source_id}")
            LOGGER.info("Official browser source is disabled: %s", self.source_id)
            return
        output = self.settings.data_dir / str(config["capture_path"])
        browser_url = str(config.get("browser_url") or source["homepage_url"])
        self._heartbeat("capturing", f"capturing {self.source_id}")
        try:
            payload = run_capture_with_timeout(
                run_browser_capture,
                kwargs={
                    "url": browser_url,
                    "output": output,
                    "config": config,
                    "allowed_hosts": list(config["allowed_hosts"]),
                    "user_agent": "CUPB-Geoscience-Employment-Information-Service/1.0",
                },
                timeout_seconds=getattr(self, "capture_timeout_seconds", 0),
                on_wait=self._heartbeat,
                stop_event=getattr(self, "stop_event", None),
            )
            if str(payload.get("status") or "success") != "success":
                # Keep the failed attempt beside the last successful snapshot.
                # A partial payload is diagnostic only; persisting it through
                # the same failure contract makes the reason and scan boundary
                # auditable without ever turning partial rows into evidence.
                scan = payload.get("scan") if isinstance(payload, dict) else {}
                scan_detail = (
                    scan.get("failure_reason")
                    if isinstance(scan, dict)
                    else None
                ) or (
                    f"capture status={payload.get('status')}"
                    if isinstance(payload, dict)
                    else "capture returned a non-success result"
                )
                try:
                    capture_status = str(payload.get("status") or "")
                    failure_status = (
                        capture_status
                        if capture_status in {"access_limited", "partial", "parse_failed"}
                        else "parse_failed"
                    )
                    write_browser_capture_failure(
                        output=output,
                        platform_url=browser_url,
                        status=failure_status,
                        reason=str(scan_detail),
                        source_id=str(config.get("source_id") or self.source_id),
                        adapter_version=str(
                            config.get("adapter_version") or "browser-capture-v1"
                        ),
                    )
                except Exception:
                    LOGGER.exception("Could not persist partial official browser capture")
                self._record_source_health(
                    status="source_degraded",
                    detail="官方浏览器捕获不完整，学生端来源状态未改变。",
                )
                self._heartbeat("degraded", "official browser capture is partial; publication remains blocked")
                return
            if not was_enabled:
                if not bool(config.get("auto_activate_on_complete_capture")):
                    raise RuntimeError(
                        "official browser complete capture is staged but automatic activation is disabled"
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
                    "official browser capture succeeded but source synchronization did not finish: "
                    f"{sync_result.status}: {sync_result.error or 'unknown error'}"
                )
            self._heartbeat(
                "running",
                f"capture completed: {len(payload.get('rows', []))} rows",
            )
            LOGGER.info("Official browser capture completed: %s", payload.get("scan"))
        except Exception as error:
            if not was_enabled:
                self._restore_disabled_state()
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
            self._record_source_health(
                status=(
                    "source_degraded"
                    if self._is_access_limited(str(error))
                    else "source_error"
                ),
                detail=f"官方浏览器捕获失败，学生端来源状态未改变：{error}",
            )
            self._heartbeat("degraded", str(error)[:500])
            LOGGER.exception("Official browser capture failed")

    @staticmethod
    def _is_access_limited(error: str) -> bool:
        normalized = str(error or "").lower()
        return any(
            marker in normalized
            for marker in (
                "robots",
                "http 401",
                "http 403",
                "http 407",
                "http 412",
                "access policy",
                "access-limited",
                "captcha",
            )
        )

    def _record_source_health(self, *, status: str, detail: str) -> None:
        recorder = getattr(self.database, "record_source_health", None)
        if not callable(recorder):
            return
        try:
            recorder(self.source_id, status=status, detail=detail)
        except Exception:
            LOGGER.exception("Could not record official browser source health")

    def _restore_disabled_state(self) -> None:
        """Keep a staged source disabled when capture or atomic sync fails."""

        try:
            setter = getattr(self.database, "set_source_enabled", None)
            if callable(setter):
                setter(self.source_id, False)
        except Exception:
            LOGGER.exception("Could not restore disabled official browser source state")

    def _heartbeat(self, status: str, detail: str = "") -> None:
        self._last_heartbeat_status = status
        try:
            self.database.record_service_heartbeat(self.service_name, status, detail)
        except Exception:
            LOGGER.exception("Could not record official browser heartbeat")


def main() -> None:
    argparse.ArgumentParser(
        prog="python -m job_hub.official_browser_worker",
        description="运行官方动态来源浏览器采集 worker。",
    ).parse_args()
    load_project_env()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    worker = OfficialBrowserWorker(Settings.from_env())
    signal.signal(signal.SIGINT, worker.stop)
    signal.signal(signal.SIGTERM, worker.stop)
    raise SystemExit(worker.run_forever())


if __name__ == "__main__":
    main()
