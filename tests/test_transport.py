from __future__ import annotations

import pytest

from job_hub.transport import (
    configure_session,
    create_session,
    proxy_environment_present,
    transport_metadata,
    validate_transport_mode,
)


def test_direct_transport_ignores_proxy_environment(monkeypatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7897")
    direct = create_session("direct")
    environment = create_session("environment")

    assert direct.trust_env is False
    assert environment.trust_env is True
    assert proxy_environment_present() is True
    assert transport_metadata("direct")["transport_mode"] == "direct"
    assert transport_metadata("direct")["proxy_environment_present"] is True


def test_transport_mode_rejects_unknown_value() -> None:
    with pytest.raises(ValueError, match="transport mode"):
        validate_transport_mode("browser-stealth")


def test_configure_session_applies_direct_policy_to_existing_session() -> None:
    session = create_session("environment")
    configured = configure_session(session, "direct")

    assert configured is session
    assert session.trust_env is False


def test_proxy_detection_handles_lowercase_environment_names(monkeypatch) -> None:
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("ALL_PROXY", raising=False)
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:7897")

    assert proxy_environment_present() is True
