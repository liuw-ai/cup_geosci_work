"""Explicit outbound HTTP transport profiles.

The service may run behind a desktop proxy during local development and on a
direct-egress server in production. Making that choice explicit prevents a
proxy handshake failure from being mistaken for a target-site vacancy state.
This module does not disable TLS verification or implement access-control
evasion.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

import requests


TRANSPORT_MODES = frozenset({"environment", "direct"})
# Requests accepts both spellings.  Windows normally exposes the upper-case
# names, while Linux service managers may provide lower-case variables.
PROXY_ENVIRONMENT_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)


def validate_transport_mode(value: str | None) -> str:
    mode = str(value or "environment").strip().lower()
    if mode not in TRANSPORT_MODES:
        choices = ", ".join(sorted(TRANSPORT_MODES))
        raise ValueError(f"HTTP transport mode must be one of: {choices}")
    return mode


def create_session(
    mode: str = "environment",
    *,
    headers: Mapping[str, str] | None = None,
) -> requests.Session:
    """Create a requests session with an explicit proxy policy.

    ``environment`` preserves requests' normal proxy handling. ``direct``
    ignores HTTP(S)_PROXY/ALL_PROXY for this session only, which is useful on a
    server with direct egress or when diagnosing a broken local proxy.
    """
    normalized = validate_transport_mode(mode)
    return configure_session(
        requests.Session(),
        normalized,
        headers=headers,
    )


def configure_session(
    session: requests.Session,
    mode: str = "environment",
    *,
    headers: Mapping[str, str] | None = None,
) -> requests.Session:
    """Apply the transport policy to an existing requests-compatible session.

    Collectors accept injected sessions for offline fixtures and tests.  The
    policy still needs to be explicit for a real ``requests.Session`` passed
    by an application, so this helper keeps the two construction paths alike.
    Lightweight fakes without ``trust_env`` remain supported.
    """
    normalized = validate_transport_mode(mode)
    if hasattr(session, "trust_env"):
        session.trust_env = normalized == "environment"
    if headers:
        session.headers.update(dict(headers))
    return session


def proxy_environment_present(environ: Mapping[str, str] | None = None) -> bool:
    values = environ if environ is not None else os.environ
    return any(str(values.get(key, "")).strip() for key in PROXY_ENVIRONMENT_KEYS)


def transport_metadata(
    mode: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Return non-secret transport evidence for an auditable probe run."""
    normalized = validate_transport_mode(mode)
    return {
        "transport_mode": normalized,
        "proxy_environment_present": proxy_environment_present(environ),
    }
