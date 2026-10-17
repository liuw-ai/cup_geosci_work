from __future__ import annotations

import time
import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from job_hub.browser_timeout import run_capture_with_timeout


def _sleep_capture(*, seconds: float) -> dict[str, str]:
    time.sleep(seconds)
    return {"status": "success"}


def _instant_capture(*, value: str) -> dict[str, str]:
    return {"value": value}


def _large_capture(*, size: int) -> dict[str, str]:
    return {"payload": "x" * size}


def _swallow_interrupt_capture(*, seconds: float) -> dict[str, str]:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            time.sleep(0.1)
        except Exception:
            continue
    return {"status": "late"}


def test_capture_timeout_terminates_a_stalled_child() -> None:
    with pytest.raises(TimeoutError, match="exceeded 1s deadline"):
        run_capture_with_timeout(
            _sleep_capture,
            kwargs={"seconds": 2},
            timeout_seconds=1,
        )


def test_capture_timeout_survives_page_level_exception_handling() -> None:
    with pytest.raises(TimeoutError, match="exceeded 1s deadline"):
        run_capture_with_timeout(
            _swallow_interrupt_capture,
            kwargs={"seconds": 2},
            timeout_seconds=1,
        )


def test_capture_timeout_returns_successful_child_result() -> None:
    assert run_capture_with_timeout(
        _instant_capture,
        kwargs={"value": "ok"},
        timeout_seconds=5,
    ) == {"value": "ok"}


def test_capture_timeout_drains_large_success_result_without_pipe_deadlock() -> None:
    result = run_capture_with_timeout(
        _large_capture,
        kwargs={"size": 2_000_000},
        timeout_seconds=10,
    )
    assert len(result["payload"]) == 2_000_000


def test_capture_timeout_zero_keeps_direct_execution() -> None:
    assert run_capture_with_timeout(
        _instant_capture,
        kwargs={"value": "direct"},
        timeout_seconds=0,
    ) == {"value": "direct"}


@pytest.mark.skipif(os.name != "posix", reason="production slot lock uses POSIX flock")
def test_capture_waits_for_shared_slot_and_updates_heartbeat(tmp_path, monkeypatch) -> None:
    import fcntl

    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("BROWSER_CAPTURE_MAX_CONCURRENCY", "1")
    slot = os.open(tmp_path / "browser-capture-slot-0.lock", os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(slot, fcntl.LOCK_EX)
    statuses: list[str] = []
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                run_capture_with_timeout,
                _instant_capture,
                kwargs={"value": "ok"},
                on_wait=lambda status, _detail: statuses.append(status),
            )
            time.sleep(0.1)
            assert not future.done()
            assert "waiting" in statuses
            fcntl.flock(slot, fcntl.LOCK_UN)
            assert future.result(timeout=12) == {"value": "ok"}
    finally:
        os.close(slot)
    assert statuses[-1] == "capturing"
