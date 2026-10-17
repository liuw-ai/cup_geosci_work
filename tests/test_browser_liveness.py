from __future__ import annotations

from job_hub.browser_liveness import wait_with_heartbeat


class _Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def monotonic(self) -> float:
        return self.value


class _Event:
    def __init__(self, clock: _Clock) -> None:
        self.clock = clock
        self.stopped = False
        self.waits: list[float] = []

    def is_set(self) -> bool:
        return self.stopped

    def wait(self, seconds: float) -> bool:
        self.waits.append(seconds)
        self.clock.value += seconds
        return self.stopped


def test_wait_with_heartbeat_pulses_during_the_full_idle_interval() -> None:
    clock = _Clock()
    event = _Event(clock)
    heartbeats: list[tuple[str, str]] = []

    wait_with_heartbeat(
        event,
        125,
        lambda status, detail: heartbeats.append((status, detail)),
        detail="waiting for next capture",
        pulse_seconds=60,
        monotonic=clock.monotonic,
    )

    assert event.waits == [60, 60, 5]
    assert heartbeats == [("running", "waiting for next capture")] * 3


def test_wait_with_heartbeat_stops_immediately_when_shutdown_is_requested() -> None:
    clock = _Clock()
    event = _Event(clock)
    event.stopped = True
    heartbeats: list[tuple[str, str]] = []

    wait_with_heartbeat(
        event,
        180,
        lambda status, detail: heartbeats.append((status, detail)),
        monotonic=clock.monotonic,
    )

    assert event.waits == []
    assert heartbeats == []
