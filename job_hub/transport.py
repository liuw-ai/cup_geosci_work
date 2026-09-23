"""Explicit outbound transport, retry and private response replay helpers.

The service may run behind a desktop proxy during local development and on a
direct-egress server in production. Making that choice explicit prevents a
proxy handshake failure from being mistaken for a target-site vacancy state.
Retries are deliberately conservative: transient transport failures and
server-side ``5xx`` responses may be retried, while ``403``/``412`` and robots
denials are returned to the caller immediately. Nothing in this module
disables TLS verification or implements access-control evasion.
"""

from __future__ import annotations

import os
import hashlib
import json
import random
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import requests

try:  # Optional browser-compatible TLS client; requests remains the default.
    from curl_cffi import requests as curl_requests
except ImportError:  # pragma: no cover - optional deployment dependency
    curl_requests = None  # type: ignore[assignment]


TRANSPORT_MODES = frozenset({"environment", "direct"})
HTTP_CLIENTS = frozenset({"requests", "curl_cffi"})
TRANSIENT_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})
POLICY_STATUS_CODES = frozenset({401, 403, 407, 410, 412, 451})


def request_exception_types() -> tuple[type[BaseException], ...]:
    """Return request exception classes for the enabled HTTP clients.

    ``curl_cffi`` has its own exception hierarchy and does not inherit from
    ``requests``.  A shared tuple lets collectors classify both clients as
    transport failures instead of parser failures.
    """
    classes: list[type[BaseException]] = [requests.exceptions.RequestException]
    if curl_requests is not None:
        candidate = getattr(
            getattr(curl_requests, "exceptions", None),
            "RequestException",
            None,
        )
        if isinstance(candidate, type) and candidate not in classes:
            classes.append(candidate)
    return tuple(classes)
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


def validate_http_client(value: str | None) -> str:
    client = str(value or "requests").strip().lower()
    if client not in HTTP_CLIENTS:
        choices = ", ".join(sorted(HTTP_CLIENTS))
        raise ValueError(f"HTTP client must be one of: {choices}")
    if client == "curl_cffi" and curl_requests is None:
        raise ValueError(
            "HTTP_CLIENT=curl_cffi requires the optional curl-cffi package"
        )
    return client


def create_session(
    mode: str = "environment",
    *,
    headers: Mapping[str, str] | None = None,
    client: str = "requests",
) -> requests.Session:
    """Create a requests session with an explicit proxy policy.

    ``environment`` preserves requests' normal proxy handling. ``direct``
    ignores HTTP(S)_PROXY/ALL_PROXY for this session only, which is useful on a
    server with direct egress or when diagnosing a broken local proxy.
    """
    normalized = validate_transport_mode(mode)
    normalized_client = validate_http_client(client)
    if normalized_client == "curl_cffi":
        # ``curl_cffi`` exposes a requests-compatible session while allowing a
        # deployment to opt into a browser TLS profile. It is not a bypass for
        # robots, authentication, CAPTCHAs or other access controls.
        session = curl_requests.Session(impersonate="chrome")
    else:
        session = requests.Session()
    return configure_session(
        session,
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


def parse_retry_after(value: str | None, *, now: float | None = None) -> float | None:
    """Return a bounded, non-negative delay from an HTTP ``Retry-After`` value."""
    if not value:
        return None
    text = str(value).strip()
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    try:
        target = parsedate_to_datetime(text)
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)
        current = time.time() if now is None else now
        return max(0.0, target.timestamp() - current)
    except (TypeError, ValueError, OverflowError):
        return None


@dataclass(frozen=True)
class RequestOutcome:
    """Non-secret request evidence useful in crawl-run diagnostics."""

    attempts: int
    retryable_failures: int
    last_error_class: str | None = None
    transport_mode: str = "environment"


class CachedResponse:
    """Minimal response object used only for explicit private replay."""

    def __init__(
        self,
        *,
        content: bytes,
        status_code: int,
        headers: Mapping[str, Any],
        url: str,
        encoding: str | None = None,
    ) -> None:
        self.content = bytes(content)
        self.status_code = int(status_code)
        self.headers = dict(headers)
        self.url = url
        self.encoding = encoding or "utf-8"

    @property
    def text(self) -> str:
        return self.content.decode(self.encoding or "utf-8", errors="replace")

    @property
    def ok(self) -> bool:
        return self.status_code < 400

    def raise_for_status(self) -> None:
        if not self.ok:
            raise requests.HTTPError(f"cached response returned HTTP {self.status_code}")

    def close(self) -> None:
        return None


class ResponseCache:
    """Small private HTTP response cache for parser development and replay.

    Cache entries are content-addressed by method, URL and request body. They
    are never exposed through the public site and are not used as a substitute
    for a fresh source scan unless a caller explicitly asks for replay.
    """

    def __init__(self, directory: Path, *, max_entries: int = 2_000) -> None:
        self.directory = Path(directory)
        self.max_entries = max(1, int(max_entries))

    def _key(self, method: str, url: str, body: bytes | None = None) -> str:
        payload = f"{method.upper()}\n{url}\n".encode("utf-8") + (body or b"")
        return hashlib.sha256(payload).hexdigest()

    def _paths(self, key: str) -> tuple[Path, Path]:
        return self.directory / f"{key}.body", self.directory / f"{key}.json"

    def store(
        self,
        method: str,
        url: str,
        response: Any,
        request_body: bytes | None = None,
    ) -> str:
        """Persist a response, using the request body only in the cache key."""
        content = bytes(getattr(response, "content", b""))
        key = self._key(method, url, request_body)
        body_path, metadata_path = self._paths(key)
        self.directory.mkdir(parents=True, exist_ok=True)
        body_tmp = body_path.with_suffix(".body.tmp")
        meta_tmp = metadata_path.with_suffix(".json.tmp")
        body_tmp.write_bytes(content)
        metadata = {
            "method": method.upper(),
            "url": url,
            "status_code": int(getattr(response, "status_code", 0) or 0),
            "headers": dict(getattr(response, "headers", {}) or {}),
            "encoding": getattr(response, "encoding", None),
            "content_sha256": hashlib.sha256(content).hexdigest(),
            "stored_at": time.time(),
        }
        meta_tmp.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
        body_tmp.replace(body_path)
        meta_tmp.replace(metadata_path)
        self._prune()
        return key

    def metadata(self, method: str, url: str, body: bytes | None = None) -> dict[str, Any] | None:
        key = self._key(method, url, body)
        _, metadata_path = self._paths(key)
        if not metadata_path.exists():
            return None
        try:
            return json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def read(self, method: str, url: str, body: bytes | None = None) -> tuple[dict[str, Any], bytes] | None:
        metadata = self.metadata(method, url, body)
        if metadata is None:
            return None
        key = self._key(method, url, body)
        body_path, _ = self._paths(key)
        try:
            content = body_path.read_bytes()
        except OSError:
            return None
        if hashlib.sha256(content).hexdigest() != metadata.get("content_sha256"):
            return None
        return metadata, content

    def replay(
        self,
        method: str,
        url: str,
        body: bytes | None = None,
    ) -> CachedResponse | None:
        """Build a requests-compatible response for explicit offline replay."""
        item = self.read(method, url, body)
        if item is None:
            return None
        metadata, content = item
        return CachedResponse(
            content=content,
            status_code=int(metadata.get("status_code") or 200),
            headers=dict(metadata.get("headers") or {}),
            url=str(metadata.get("url") or url),
            encoding=metadata.get("encoding"),
        )

    def _prune(self) -> None:
        entries = sorted(
            self.directory.glob("*.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for metadata_path in entries[self.max_entries :]:
            key = metadata_path.stem
            body_path, _ = self._paths(key)
            for path in (metadata_path, body_path):
                try:
                    path.unlink()
                except OSError:
                    pass


class RequestPolicy:
    """Execute compliant HTTP requests with bounded retries and evidence."""

    def __init__(
        self,
        session: Any,
        *,
        timeout: float = 20.0,
        retries: int = 2,
        backoff_seconds: float = 0.75,
        max_backoff_seconds: float = 30.0,
        jitter_seconds: float = 0.15,
        cache: ResponseCache | None = None,
        sleep: Any = time.sleep,
        random_fn: Any = random.random,
        transport_mode: str = "environment",
    ) -> None:
        self.session = session
        self.timeout = max(0.1, float(timeout))
        self.retries = max(0, int(retries))
        self.backoff_seconds = max(0.0, float(backoff_seconds))
        self.max_backoff_seconds = max(self.backoff_seconds, float(max_backoff_seconds))
        self.jitter_seconds = max(0.0, float(jitter_seconds))
        self.cache = cache
        self.sleep = sleep
        self.random_fn = random_fn
        self.transport_mode = validate_transport_mode(transport_mode)
        self.last_outcome = RequestOutcome(0, 0, None, self.transport_mode)

    def request(self, method: str, url: str, **kwargs: Any) -> Any:
        normalized_method = method.upper()
        attempts = 0
        retryable_failures = 0
        last_error: str | None = None
        last_exception: Exception | None = None
        for attempt in range(self.retries + 1):
            attempts += 1
            try:
                request_kwargs = dict(kwargs)
                request_kwargs.setdefault("timeout", self.timeout)
                request_kwargs.setdefault("allow_redirects", True)
                response = self._call(normalized_method, url, request_kwargs)
                status = int(getattr(response, "status_code", 0) or 0)
                if status in TRANSIENT_STATUS_CODES and attempt < self.retries:
                    retryable_failures += 1
                    delay = parse_retry_after(
                        getattr(response, "headers", {}).get("Retry-After")
                    )
                    self._sleep(delay if delay is not None else self._backoff(attempt))
                    continue
                if self.cache is not None and 200 <= status < 400:
                    self.cache.store(
                        normalized_method,
                        url,
                        response,
                        request_body=_request_body_bytes(request_kwargs),
                    )
                self.last_outcome = RequestOutcome(
                    attempts, retryable_failures, None, self.transport_mode
                )
                return response
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as error:
                last_error = error.__class__.__name__
                last_exception = error
                if attempt >= self.retries:
                    break
                retryable_failures += 1
                self._sleep(self._backoff(attempt))
            except Exception as error:
                # curl_cffi errors are not requests exceptions.  Retry only
                # the optional client's transport hierarchy; application bugs
                # and parser errors must surface immediately.
                if not _is_transport_exception(error):
                    raise
                last_error = error.__class__.__name__
                last_exception = error
                if attempt >= self.retries:
                    break
                retryable_failures += 1
                self._sleep(self._backoff(attempt))
        self.last_outcome = RequestOutcome(
            attempts, retryable_failures, last_error, self.transport_mode
        )
        # Re-raise the original transport exception so callers retain the
        # existing error classification and audit trail.
        if last_exception is not None:
            raise last_exception
        raise requests.RequestException(f"request failed after {attempts} attempt(s)")

    def _call(self, method: str, url: str, kwargs: dict[str, Any]) -> Any:
        request_method = getattr(self.session, method.lower(), None)
        if request_method is not None:
            return request_method(url, **kwargs)
        return self.session.request(method, url, **kwargs)

    def _backoff(self, attempt: int) -> float:
        base = min(self.max_backoff_seconds, self.backoff_seconds * (2**attempt))
        return min(self.max_backoff_seconds, base + self.random_fn() * self.jitter_seconds)

    def _sleep(self, delay: float) -> None:
        if delay > 0:
            self.sleep(min(self.max_backoff_seconds, delay))


def _request_body_bytes(kwargs: Mapping[str, Any]) -> bytes | None:
    """Create a deterministic private cache key for POST payloads."""
    if kwargs.get("json") is not None:
        try:
            return json.dumps(
                kwargs["json"], ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        except (TypeError, ValueError):
            return repr(kwargs["json"]).encode("utf-8", errors="replace")
    data = kwargs.get("data")
    if data is None:
        return None
    if isinstance(data, bytes):
        return data
    if isinstance(data, str):
        return data.encode("utf-8")
    try:
        return json.dumps(data, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    except (TypeError, ValueError):
        return repr(data).encode("utf-8", errors="replace")


def _is_transport_exception(error: BaseException) -> bool:
    return isinstance(error, request_exception_types())


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
