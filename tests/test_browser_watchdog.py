from __future__ import annotations

from datetime import datetime, timedelta, timezone

from job_hub.browser_watchdog import heartbeat_age_seconds, watchdog_expired


def _heartbeat(status: str, *, age_seconds: int = 0) -> dict[str, str]:
    updated_at = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    return {"status": status, "updated_at": updated_at.isoformat()}


def test_watchdog_expires_stale_active_capture() -> None:
    assert watchdog_expired(
        _heartbeat("capturing", age_seconds=14_401),
        max_age_seconds=14_400,
    ) is True


def test_watchdog_does_not_restart_stale_access_limitation() -> None:
    assert watchdog_expired(
        _heartbeat("degraded", age_seconds=100_000),
        max_age_seconds=14_400,
    ) is False


def test_watchdog_does_not_restart_disabled_idle_source() -> None:
    assert watchdog_expired(
        _heartbeat("idle", age_seconds=100_000),
        max_age_seconds=14_400,
    ) is False


def test_invalid_or_missing_heartbeat_is_not_expired() -> None:
    assert heartbeat_age_seconds(None) is None
    assert watchdog_expired(
        {"status": "capturing", "updated_at": "not-a-timestamp"},
        max_age_seconds=14_400,
    ) is False
