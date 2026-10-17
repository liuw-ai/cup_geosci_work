"""Heartbeat support for long-running browser-worker wait intervals."""

from __future__ import annotations

import time
from collections.abc import Callable
from threading import Event


def wait_with_heartbeat(
    stop_event: Event,
    duration_seconds: float,
    heartbeat: Callable[[str, str], None],
    *,
    status: str = "running",
    detail: str = "waiting for next browser capture",
    pulse_seconds: float = 60,
    monotonic: Callable[[], float] = time.monotonic,
) -> None:
    """Wait interruptibly while proving that an idle worker process is alive.

    This is used only between bounded capture attempts. A capture itself still
    owns its ``capturing`` heartbeat, so the watchdog can terminate a stuck
    browser rather than receiving synthetic liveness updates.
    """

    deadline = monotonic() + max(0.0, float(duration_seconds))
    pulse = max(1.0, float(pulse_seconds))
    while not stop_event.is_set():
        remaining = deadline - monotonic()
        if remaining <= 0:
            return
        heartbeat(status, detail)
        if stop_event.wait(min(pulse, remaining)):
            return
