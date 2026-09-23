from __future__ import annotations

import pytest
import requests
from pathlib import Path

from job_hub.transport import (
    RequestPolicy,
    ResponseCache,
    configure_session,
    create_session,
    parse_retry_after,
    proxy_environment_present,
    request_exception_types,
    transport_metadata,
    validate_http_client,
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


def test_http_client_validation_and_optional_curl_exception_class() -> None:
    assert validate_http_client("requests") == "requests"
    assert requests.exceptions.RequestException in request_exception_types()
    try:
        import curl_cffi  # noqa: F401
    except ImportError:
        return
    assert validate_http_client("curl_cffi") == "curl_cffi"
    assert len(request_exception_types()) >= 2


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


class _Response:
    def __init__(self, status_code: int, content: bytes = b"ok", headers=None):
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}
        self.encoding = "utf-8"


class _Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def get(self, _url, **_kwargs):
        self.calls += 1
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def test_request_policy_retries_transient_status_with_bounded_backoff() -> None:
    session = _Session([_Response(503), _Response(200, b"fresh")])
    delays = []
    policy = RequestPolicy(
        session,
        retries=2,
        backoff_seconds=1,
        jitter_seconds=0,
        sleep=delays.append,
        random_fn=lambda: 0,
    )

    response = policy.request("GET", "https://example.test/jobs")

    assert response.status_code == 200
    assert session.calls == 2
    assert delays == [1]
    assert policy.last_outcome.attempts == 2
    assert policy.last_outcome.retryable_failures == 1


def test_request_policy_does_not_retry_access_policy_status() -> None:
    session = _Session([_Response(412)])
    delays = []
    policy = RequestPolicy(session, retries=3, sleep=delays.append)

    response = policy.request("GET", "https://example.test/blocked")

    assert response.status_code == 412
    assert session.calls == 1
    assert delays == []


def test_request_policy_retries_connection_error_and_re_raises_original() -> None:
    error = requests.ConnectionError("reset")
    session = _Session([error, _Response(200)])
    delays = []
    policy = RequestPolicy(
        session,
        retries=1,
        backoff_seconds=0,
        jitter_seconds=0,
        sleep=delays.append,
    )

    response = policy.request("GET", "https://example.test/retry")

    assert response.status_code == 200
    assert session.calls == 2
    assert delays == []


def test_retry_after_supports_seconds_and_http_date() -> None:
    assert parse_retry_after("2") == 2
    assert parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT", now=1445412478) == 2
    assert parse_retry_after("not-a-date") is None


def test_response_cache_round_trips_content_and_metadata(tmp_path: Path) -> None:
    cache = ResponseCache(tmp_path / "cache")
    response = _Response(200, b"official body", {"Content-Type": "text/html"})

    key = cache.store("GET", "https://official.example/jobs/1", response)
    replay = cache.read("GET", "https://official.example/jobs/1")

    assert len(key) == 64
    assert replay is not None
    metadata, content = replay
    assert metadata["status_code"] == 200
    assert metadata["content_sha256"]
    assert content == b"official body"


def test_response_cache_separates_post_bodies_and_replays_response(tmp_path: Path) -> None:
    cache = ResponseCache(tmp_path / "cache")
    first = _Response(200, b"first")
    second = _Response(200, b"second")
    cache.store("POST", "https://official.example/api", first, request_body=b"a")
    cache.store("POST", "https://official.example/api", second, request_body=b"b")

    assert cache.read("POST", "https://official.example/api", b"a")[1] == b"first"
    replay = cache.replay("POST", "https://official.example/api", b"b")
    assert replay is not None
    assert replay.text == "second"
