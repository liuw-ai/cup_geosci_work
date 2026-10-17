"""Bounded execution for browser captures on POSIX and Windows.

A browser capture runs in a child process when a positive timeout is set.
Page-level exception handlers can swallow SIGALRM on POSIX, so an in-process
timer cannot enforce the deadline. Capture writers publish atomically; killing
the child cannot replace the last complete capture.
"""

from __future__ import annotations

import multiprocessing
import os
import queue
import time
from contextlib import contextmanager
from collections.abc import Callable, Mapping
from pathlib import Path
from threading import Event
from typing import Any


def _capture_process_entry(
    result_queue: Any,
    capture: Callable[..., Any],
    kwargs: dict[str, Any],
) -> None:
    try:
        result_queue.put(("success", capture(**kwargs)));
    except BaseException as error:  # pragma: no cover - exercised in child
        result_queue.put(("error", type(error).__name__, str(error)))


@contextmanager
def _capture_slot(
    *,
    on_wait: Callable[[str, str], None] | None,
    stop_event: Event | None,
):
    slots = max(0, int(os.getenv("BROWSER_CAPTURE_MAX_CONCURRENCY", "0")))
    if not slots:
        yield
        return
    if os.name != "posix":
        raise RuntimeError("browser capture slots require POSIX file locks")
    import fcntl

    data_dir = Path(os.getenv("APP_DATA_DIR", "data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    handles = [
        os.open(data_dir / f"browser-capture-slot-{index}.lock", os.O_CREAT | os.O_RDWR, 0o600)
        for index in range(slots)
    ]
    selected = None
    try:
        while selected is None:
            if stop_event is not None and stop_event.is_set():
                raise InterruptedError("browser capture stopped while waiting for a slot")
            for handle in handles:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    selected = handle
                    break
                except BlockingIOError:
                    continue
            if selected is None:
                if on_wait is not None:
                    on_wait("waiting", "waiting for browser capture capacity")
                if stop_event is not None:
                    stop_event.wait(10)
                else:
                    time.sleep(10)
        if on_wait is not None:
            on_wait("capturing", "browser capture slot acquired")
        yield
    finally:
        if selected is not None:
            fcntl.flock(selected, fcntl.LOCK_UN)
        for handle in handles:
            os.close(handle)


def run_capture_with_timeout(
    capture: Callable[..., Any],
    *,
    kwargs: Mapping[str, Any] | None = None,
    timeout_seconds: int = 0,
    on_wait: Callable[[str, str], None] | None = None,
    stop_event: Event | None = None,
) -> Any:
    """Run one browser capture with a hard timeout when enabled."""

    capture_kwargs = dict(kwargs or {})
    bounded = max(0, int(timeout_seconds))
    with _capture_slot(on_wait=on_wait, stop_event=stop_event):
        if bounded <= 0:
            return capture(**capture_kwargs)

        context = multiprocessing.get_context("spawn")
        result_queue = context.Queue(maxsize=1)
        process = context.Process(
            target=_capture_process_entry,
            args=(result_queue, capture, capture_kwargs),
            name="job-hub-browser-capture",
        )
        process.start()
        # Drain before joining: a large result can fill the queue pipe.
        message: Any | None = None
        deadline = time.monotonic() + float(bounded)
        while message is None and time.monotonic() < deadline:
            remaining = max(0.0, deadline - time.monotonic())
            try:
                message = result_queue.get(timeout=min(0.2, remaining))
            except queue.Empty:
                if not process.is_alive():
                    break
        if message is None and not process.is_alive():
            try:
                message = result_queue.get(timeout=5)
            except queue.Empty:
                message = None
        if message is None:
            process.terminate()
            process.join(5)
            if process.is_alive() and hasattr(process, "kill"):
                process.kill()
                process.join(5)
            result_queue.close()
            result_queue.join_thread()
            raise TimeoutError(f"browser capture exceeded {bounded}s deadline")
        process.join(5)
        if process.is_alive():
            process.terminate()
            process.join(5)
            if process.is_alive() and hasattr(process, "kill"):
                process.kill()
                process.join(5)
        result_queue.close()
        result_queue.join_thread()
        if message[0] == "error":
            raise RuntimeError(f"{message[1]}: {message[2]}")
        return message[1]


__all__ = ["run_capture_with_timeout"]
