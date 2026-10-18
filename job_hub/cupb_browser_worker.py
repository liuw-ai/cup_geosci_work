"""Unattended CUPB official browser snapshot worker."""

from __future__ import annotations

import logging
import os
import signal
import shutil
import argparse
from threading import Event
from pathlib import Path

from job_hub.browser_liveness import wait_with_heartbeat
from job_hub.browser_timeout import run_capture_with_timeout
from job_hub.config import Settings, load_project_env
from job_hub.cupb_browser_capture import run_cupb_browser_capture, write_cupb_capture_failure
from job_hub.db import Database
from job_hub.pipeline import JobPipeline

LOGGER = logging.getLogger("job_hub.cupb_browser_worker")


class CupbBrowserWorker:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.database = Database(settings.database_path)
        self.database.initialize()
        self.pipeline = JobPipeline(settings, self.database)
        self.pipeline.bootstrap_sources()
        self.source_id = os.getenv("CUPB_BROWSER_SOURCE_ID", "cupb-career-browser-20261007")
        self.interval_seconds = max(900, int(os.getenv("CUPB_BROWSER_INTERVAL_MINUTES", "180")) * 60)
        self.executable_path = os.getenv(
            "CUPB_BROWSER_EXECUTABLE_PATH",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        )
        self.stop_event = Event()
        self.service_name = os.getenv("CUPB_BROWSER_SERVICE_NAME", "cupb-browser")

    def _heartbeat(self, status: str, detail: str = "") -> None:
        try:
            self.database.record_service_heartbeat(self.service_name, status, detail)
        except Exception:
            LOGGER.exception("Could not record CUPB browser heartbeat")

    def run_once(self) -> dict[str, object]:
        source = self.database.get_source(self.source_id)
        if source is None:
            raise RuntimeError(f"CUPB browser source is not registered: {self.source_id}")
        config = dict(source.get("config") or {})
        cdp_url = os.getenv("CUPB_BROWSER_CDP_URL", "").strip()
        if cdp_url:
            config["cdp_url"] = cdp_url
        if not config.get("direct_notice_urls"):
            primary = self.database.get_source("cupb-career")
            primary_config = dict((primary or {}).get("config") or {})
            config["direct_notice_urls"] = list(primary_config.get("direct_notice_urls") or [])
            config["listing_urls"] = list(primary_config.get("listing_urls") or [])
            config["detail_path_patterns"] = list(primary_config.get("detail_path_patterns") or [])
            config["listing_exclude_patterns"] = list(
                primary_config.get("listing_exclude_patterns")
                or primary_config.get("exclude_patterns")
                or []
            )
            config["listing_page_limit"] = primary_config.get("listing_page_limit", 6)
            config["candidate_limit"] = primary_config.get("candidate_limit", 400)
            config["browser_discover_listings"] = True
            source = {**source, "config": config}
        # Browser containers mount the repository data read-only. Keep the
        # success-only capture and failure evidence in the writable runtime
        # volume, then pass that exact file to the normal snapshot adapter.
        configured_capture = str(config.get("capture_path") or "").strip()
        configured_snapshot = str(config.get("snapshot_path") or "").strip()
        capture_path = configured_capture or (
            configured_snapshot
            if Path(configured_snapshot).is_absolute()
            else f"captures/{self.source_id}.json"
        )
        output = Path(capture_path)
        if not output.is_absolute():
            output = self.settings.data_dir / output
        config["snapshot_path"] = str(output)
        source = {**source, "config": config}
        self._heartbeat("capturing", f"capturing {self.source_id}")
        try:
            result = run_capture_with_timeout(
                run_cupb_browser_capture,
                kwargs={
                    "settings": self.settings,
                    "source": source,
                    "output": output,
                    "executable_path": self.executable_path,
                },
                timeout_seconds=int(os.getenv("CUPB_BROWSER_CAPTURE_TIMEOUT_SECONDS", "0")),
                on_wait=self._heartbeat,
                stop_event=getattr(self, "stop_event", None),
            )
            captured_at = str(result.get("captured_at") or "").strip()
            sync_source = {
                **source,
                "config": {
                    **config,
                    "snapshot_captured_at": captured_at,
                },
            }
            sync = self.pipeline.sync_source_manual(sync_source)
            if sync.status != "finished":
                raise RuntimeError(f"CUPB snapshot sync failed: {sync.error or sync.status}")
            # CUPB's capture artifact is intentionally a plain list of
            # publishable rows rather than the manifest-shaped payload used by
            # the other browser adapters.  The generic pipeline therefore
            # cannot derive its capture timestamp from ``capture_path``.  Make
            # the successful browser result durable explicitly; without this
            # projection the source-health report would retain the old
            # declared snapshot timestamp. Freshness is an evidence-health
            # signal only and never withdraws or hides a job.
            self.database.record_source_capture_freshness(
                self.source_id,
                captured_at=captured_at,
                crawl_run_id=getattr(sync, "run_id", None),
                evidence_kind="browser_capture_result",
            )
            rollback_path = str(result.get("rollback_path") or "").strip()
            if rollback_path:
                Path(rollback_path).unlink(missing_ok=True)
            self.database.record_source_health(self.source_id, status="source_active", detail="CUPB浏览器完整公告捕获并同步成功。")
            self._heartbeat("running", f"captured {result.get('rows_exported', 0)} CUPB rows")
            return {**result, "sync_status": sync.status}
        except Exception as error:
            rollback_path = str(locals().get("result", {}).get("rollback_path") or "").strip()
            if rollback_path:
                rollback = Path(rollback_path)
                if rollback.exists():
                    output.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(rollback, output)
                    rollback.unlink(missing_ok=True)
            write_cupb_capture_failure(output=output, reason=str(error), status="access_limited" if "robots" in str(error).lower() else "parse_failed")
            self.database.record_source_health(self.source_id, status="source_degraded", detail=f"CUPB浏览器捕获失败，旧快照保持不变：{error}")
            self._heartbeat("degraded", str(error)[:500])
            raise

    def run_forever(self) -> None:
        self._heartbeat("starting", "CUPB browser worker boot completed")
        while not self.stop_event.is_set():
            try:
                self.run_once()
            except Exception:
                LOGGER.exception("CUPB browser capture failed")
            wait_with_heartbeat(
                self.stop_event,
                self.interval_seconds,
                self._heartbeat,
                detail="waiting for next CUPB browser capture",
            )
        self._heartbeat("stopped", "CUPB browser worker received a stop signal")

    def stop(self, *_: object) -> None:
        self.stop_event.set()


def main() -> None:
    argparse.ArgumentParser(
        prog="python -m job_hub.cupb_browser_worker",
        description="运行中国石油大学（北京）官方就业网浏览器采集 worker。",
    ).parse_args()
    load_project_env()
    logging.basicConfig(level=logging.INFO)
    worker = CupbBrowserWorker(Settings.from_env())
    signal.signal(signal.SIGINT, worker.stop)
    signal.signal(signal.SIGTERM, worker.stop)
    worker.run_forever()


if __name__ == "__main__":
    main()
