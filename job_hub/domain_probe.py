"""Public-domain readiness checks for the production deployment.

The probe is intentionally read-only.  It distinguishes DNS ownership/configuration
problems from an application or certificate problem so a failed HTTPS check is not
mistaken for a crawler or database failure.
"""

from __future__ import annotations

import socket
import ssl
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class DomainProbeResult:
    hostname: str
    expected_ip: str | None
    health_url: str
    resolved_addresses: tuple[str, ...]
    dns_status: str
    https_status: str
    http_status_code: int | None
    detail: str | None
    checked_at: str

    @property
    def ready(self) -> bool:
        return self.dns_status == "ok" and self.https_status == "ok"

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["resolved_addresses"] = list(self.resolved_addresses)
        result["ready"] = self.ready
        return result


def _resolve(hostname: str) -> tuple[str, ...]:
    addresses: set[str] = set()
    for item in socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM):
        address = str(item[4][0]).strip()
        if address:
            addresses.add(address)
    return tuple(sorted(addresses))


def probe_public_domain(
    hostname: str,
    *,
    expected_ip: str | None = None,
    health_url: str | None = None,
    timeout: float = 10.0,
    resolver: Callable[[str], tuple[str, ...]] | None = None,
    opener: Callable[..., Any] | None = None,
) -> DomainProbeResult:
    """Check DNS and the public HTTPS health endpoint without changing state."""

    normalized_hostname = str(hostname or "").strip().lower().rstrip(".")
    if not normalized_hostname:
        raise ValueError("hostname is required")
    target_url = str(health_url or f"https://{normalized_hostname}/healthz").strip()
    if not target_url.startswith("https://"):
        raise ValueError("health_url must use HTTPS")
    checked_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    resolve = resolver or _resolve
    try:
        addresses = tuple(resolve(normalized_hostname))
    except socket.gaierror as error:
        return DomainProbeResult(
            normalized_hostname,
            expected_ip,
            target_url,
            (),
            "nxdomain" if getattr(error, "errno", None) in {socket.EAI_NONAME, socket.EAI_NODATA} else "error",
            "not_checked",
            None,
            f"DNS resolution failed: {error}",
            checked_at,
        )
    except OSError as error:
        return DomainProbeResult(
            normalized_hostname,
            expected_ip,
            target_url,
            (),
            "error",
            "not_checked",
            None,
            f"DNS resolution failed: {error}",
            checked_at,
        )

    if not addresses:
        return DomainProbeResult(
            normalized_hostname,
            expected_ip,
            target_url,
            (),
            "nxdomain",
            "not_checked",
            None,
            "DNS returned no address records",
            checked_at,
        )
    if expected_ip and expected_ip not in addresses:
        return DomainProbeResult(
            normalized_hostname,
            expected_ip,
            target_url,
            addresses,
            "wrong_target",
            "not_checked",
            None,
            f"Expected {expected_ip}, resolved {', '.join(addresses)}",
            checked_at,
        )

    request = Request(target_url, headers={"User-Agent": "cup-geosci-domain-probe/1.0"})
    fetch = opener or urlopen
    try:
        with fetch(request, timeout=max(0.5, float(timeout))) as response:
            status_code = int(getattr(response, "status", None) or response.getcode())
        https_status = "ok" if 200 <= status_code < 400 else "http_error"
        return DomainProbeResult(
            normalized_hostname,
            expected_ip,
            target_url,
            addresses,
            "ok",
            https_status,
            status_code,
            None if https_status == "ok" else f"HTTPS returned HTTP {status_code}",
            checked_at,
        )
    except HTTPError as error:
        return DomainProbeResult(
            normalized_hostname,
            expected_ip,
            target_url,
            addresses,
            "ok",
            "http_error",
            int(error.code),
            f"HTTPS returned HTTP {error.code}",
            checked_at,
        )
    except ssl.SSLError as error:
        return DomainProbeResult(
            normalized_hostname,
            expected_ip,
            target_url,
            addresses,
            "ok",
            "tls_error",
            None,
            f"TLS handshake failed: {error}",
            checked_at,
        )
    except (URLError, TimeoutError, OSError) as error:
        return DomainProbeResult(
            normalized_hostname,
            expected_ip,
            target_url,
            addresses,
            "ok",
            "unavailable",
            None,
            f"HTTPS request failed: {error}",
            checked_at,
        )

