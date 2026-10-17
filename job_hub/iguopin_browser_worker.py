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
import argparse
from contextlib import contextmanager
from threading import Event
from zoneinfo import ZoneInfo

from job_hub.browser_liveness import wait_with_heartbeat
from job_hub.config import Settings, load_project_env
from job_hub.browser_timeout import run_capture_with_timeout
from job_hub.db import Database
from job_hub.iguopin_browser_capture import (
    detail_ids_from_capture_payload,
    _keyword_batches,
    load_iguopin_browser_capture,
    merge_iguopin_general_batch_payloads,
    persist_cmgb_browser_capture,
    run_iguopin_browser_capture,
    run_iguopin_general_browser_capture,
    write_iguopin_capture_failure,
)
from job_hub.pipeline import JobPipeline


LOGGER = logging.getLogger("job_hub.iguopin_browser_worker")


@contextmanager
def _capture_deadline(seconds: int):
    """Bound one 国聘 browser capture so a stalled page cannot block forever."""
    bounded = max(0, int(seconds))
    if bounded <= 0 or not hasattr(signal, "SIGALRM"):
        yield
        return
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, 0)

    def raise_timeout(_signum: int, _frame: object) -> None:
        raise TimeoutError(f"国聘 browser capture exceeded {bounded}s deadline")

    signal.signal(signal.SIGALRM, raise_timeout)
    signal.setitimer(signal.ITIMER_REAL, float(bounded))
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer[0] > 0:
            signal.setitimer(signal.ITIMER_REAL, *previous_timer)


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
        self.capture_timeout_seconds = max(
            0, int(os.getenv("IGUOPIN_BROWSER_CAPTURE_TIMEOUT_SECONDS", "3600"))
        )

    def run_forever(self) -> int:
        self._heartbeat("starting", "国聘 employer browser worker boot completed")
        while not self.stop_event.is_set():
            self._run_once()
            if self.run_once:
                if getattr(self, "_last_heartbeat_status", "") == "degraded":
                    return 1
                self._heartbeat("stopped", "国聘 browser worker completed one-shot capture")
                return 0
            wait_with_heartbeat(
                self.stop_event,
                self.interval_seconds,
                self._heartbeat,
                detail="waiting for next GuoPin browser capture",
            )
        self._heartbeat("stopped", "国聘 browser worker received a stop signal")
        return 0

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
            batches = (
                _keyword_batches(config)
                if source_type == "iguopin_general_browser_rows"
                else []
            )
            if len(batches) <= 1:
                payload = run_capture_with_timeout(
                    capture,
                    kwargs={
                        "url": str(config.get("browser_url") or source["homepage_url"]),
                        "output": output,
                        "config": config,
                        "allowed_hosts": list(config["allowed_hosts"]),
                        "user_agent": "CUPB-Geoscience-Employment-Information-Service/1.0",
                    },
                    timeout_seconds=getattr(self, "capture_timeout_seconds", 0),
                    on_wait=self._heartbeat,
                    stop_event=getattr(self, "stop_event", None),
                )
            else:
                batch_payloads: list[dict[str, object]] = []
                known_detail_ids: set[str] = set()
                external_id_prefix = str(
                    config.get("external_id_prefix") or "iguopin-general"
                ).strip()
                reuse_complete_batches = os.getenv(
                    "IGUOPIN_BROWSER_REUSE_COMPLETE_BATCHES", ""
                ).strip().lower() in {"1", "true", "yes"}
                configured_batch_timeout = os.getenv(
                    "IGUOPIN_BROWSER_BATCH_TIMEOUT_SECONDS",
                    str(
                        config.get(
                            "search_keyword_batch_timeout_seconds",
                            getattr(self, "capture_timeout_seconds", 0) or 900,
                        )
                    ),
                )
                batch_timeout = max(
                    60,
                    int(configured_batch_timeout),
                )
                for batch_index, keywords in enumerate(batches, start=1):
                    batch_config = dict(config)
                    batch_config["search_keywords"] = keywords
                    batch_config["known_detail_ids"] = sorted(known_detail_ids)
                    batch_config["allow_empty_duplicate_batch"] = True
                    batch_output = output.with_name(
                        f"{output.stem}.batch-{batch_index}{output.suffix}"
                    )
                    if reuse_complete_batches and batch_output.exists():
                        try:
                            reused = load_iguopin_browser_capture(
                                batch_output,
                                allowed_hosts=list(config["allowed_hosts"]),
                                max_age_hours=float(config.get("max_age_hours", 30)),
                                require_complete_scan=True,
                                require_capture_manifest=bool(
                                    config.get("require_capture_manifest", False)
                                ),
                                allow_empty_duplicate_batch=True,
                            )
                            reused_keywords = [
                                str(item.get("keyword") or "")
                                for item in reused.get("scan", {}).get(
                                    "keyword_scans", []
                                )
                                if isinstance(item, dict)
                            ]
                            if reused_keywords != keywords:
                                raise ValueError(
                                    "reusable batch keyword set does not match current configuration"
                                )
                            batch_payloads.append(reused)
                            known_detail_ids.update(
                                detail_ids_from_capture_payload(
                                    reused,
                                    external_id_prefix=external_id_prefix,
                                )
                            )
                            self._heartbeat(
                                "capturing",
                                f"复用已验证国聘关键词批次 {batch_index}/{len(batches)}",
                            )
                            continue
                        except Exception as error:
                            LOGGER.info(
                                "Ignoring unusable reusable 国聘 batch %s: %s",
                                batch_output,
                                error,
                            )
                    batch_payload = run_capture_with_timeout(
                        capture,
                        kwargs={
                            "url": str(config.get("browser_url") or source["homepage_url"]),
                            "output": batch_output,
                            "config": batch_config,
                            "allowed_hosts": list(config["allowed_hosts"]),
                            "user_agent": "CUPB-Geoscience-Employment-Information-Service/1.0",
                        },
                        timeout_seconds=batch_timeout,
                        on_wait=self._heartbeat,
                        stop_event=getattr(self, "stop_event", None),
                    )
                    if batch_payload.get("status") != "success":
                        raise RuntimeError(
                            f"国聘关键词批次 {batch_index}/{len(batches)} 未完整成功"
                        )
                    batch_payloads.append(batch_payload)
                    known_detail_ids.update(
                        detail_ids_from_capture_payload(
                            batch_payload,
                            external_id_prefix=external_id_prefix,
                        )
                    )
                    self._heartbeat(
                        "capturing",
                        f"国聘关键词批次完成 {batch_index}/{len(batches)}",
                    )
                merged = merge_iguopin_general_batch_payloads(
                    batch_payloads,
                    source_id=str(config.get("source_id") or self.source_id),
                    adapter_version=str(
                        config.get("adapter_version") or "iguopin-general-browser-v1"
                    ),
                )
                payload = persist_cmgb_browser_capture(
                    output=output,
                    payload=merged,
                    source_id=str(config.get("source_id") or self.source_id),
                    adapter_version=str(
                        config.get("adapter_version") or "iguopin-general-browser-v1"
                    ),
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
        self._last_heartbeat_status = status
        try:
            self.database.record_service_heartbeat(self.service_name, status, detail)
        except Exception:
            LOGGER.exception("Could not record 国聘 browser heartbeat")


def main() -> None:
    argparse.ArgumentParser(
        prog="python -m job_hub.iguopin_browser_worker",
        description="运行国聘授权来源浏览器采集 worker。",
    ).parse_args()
    load_project_env()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    worker = IguopinBrowserWorker(Settings.from_env())
    signal.signal(signal.SIGINT, worker.stop)
    signal.signal(signal.SIGTERM, worker.stop)
    raise SystemExit(worker.run_forever())


if __name__ == "__main__":
    main()
