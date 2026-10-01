from __future__ import annotations

import signal
import time

import pytest

from job_hub.cnooc_browser_worker import _capture_deadline


def test_cnooc_capture_deadline_interrupts_a_stalled_capture() -> None:
    if not hasattr(signal, "SIGALRM"):
        pytest.skip("capture deadline uses POSIX SIGALRM")
    with pytest.raises(TimeoutError, match="exceeded 1s deadline"):
        with _capture_deadline(1):
            time.sleep(2)


def test_cnooc_capture_deadline_zero_disables_the_alarm() -> None:
    with _capture_deadline(0):
        time.sleep(0.01)
