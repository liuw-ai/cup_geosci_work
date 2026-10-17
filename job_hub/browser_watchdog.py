"""Exit a wedged browser worker so Docker can restart it.

Docker healthchecks expose an unhealthy state but do not restart a running
container by themselves.  The browser workers therefore run this small
in-process watchdog.  It only treats active capture states as liveness
critical; a disabled source (``idle``), a scheduled maintenance wait, or a
robots/access limitation (``degraded``) is allowed to remain visible without
creating a restart loop.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Callable

from job_hub.config import Settings
from job_hub.db import Database


LOGGER = logging.getLogger("job_hub.browser_watchdog")
ACTIVE_STATUSES = frozenset({"starting", "capturing", "running", "retrying"})


def heartbeat_age_seconds(heartbeat: dict[str, str] | None) -> int | None:
    """Return a heartbeat age, or ``None`` for a missing/invalid timestamp."""

    if not heartbeat:
        return None
    try:
        updated_at = datetime.fromisoformat(
            str(heartbeat["updated_at"]).replace("Z", "+00:00")
        )
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        return max(
            0,
            int((datetime.now(timezone.utc) - updated_at).total_seconds()),
        )
    except (KeyError, TypeError, ValueError):
        return None


def watchdog_expired(
    heartbeat: dict[str, str] | None,
    *,
    max_age_seconds: int,
) -> bool:
    """Whether an active worker heartbeat is old enough to restart the process."""

    if not heartbeat:
        return False
    status = str(heartbeat.get("status") or "").strip().lower()
    age = heartbeat_age_seconds(heartbeat)
    return status in ACTIVE_STATUSES and age is not None and age > max(1, int(max_age_seconds))


def start_watchdog(
    settings: Settings,
    service_name: str,
    *,
    max_age_seconds: int = 14_400,
    poll_seconds: int = 60,
    exit_process: Callable[[int], None] = os._exit,
) -> threading.Thread:
    """Start a daemon watchdog for one browser worker service.

    The worker writes its initial ``starting`` heartbeat before the first
    capture. Missing heartbeats are therefore not treated as a failure here;
    Docker's healthcheck reports that startup problem and the worker's own
    process exit/restart policy handles hard crashes. Once an active heartbeat
    exists, a wedged process exits with code 75 and Docker restarts it.
    """

    database = Database(settings.database_path)
    database.initialize()
    name = str(service_name or "").strip()
    maximum = max(1, int(max_age_seconds))
    interval = max(5, int(poll_seconds))

    def monitor() -> None:
        while True:
            try:
                heartbeat = database.get_service_heartbeat(name)
                if watchdog_expired(heartbeat, max_age_seconds=maximum):
                    age = heartbeat_age_seconds(heartbeat)
                    LOGGER.error(
                        "Browser worker heartbeat is stale; exiting for Docker restart: service=%s age=%ss",
                        name,
                        age,
                    )
                    exit_process(75)
            except Exception:  # noqa: BLE001 - watchdog must keep polling
                LOGGER.exception("Browser worker watchdog poll failed")
            time.sleep(interval)

    thread = threading.Thread(
        target=monitor,
        name=f"browser-watchdog-{name or 'unknown'}",
        daemon=True,
    )
    thread.start()
    return thread


__all__ = ["ACTIVE_STATUSES", "heartbeat_age_seconds", "start_watchdog", "watchdog_expired"]
