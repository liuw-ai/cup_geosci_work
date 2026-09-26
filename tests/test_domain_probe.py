from __future__ import annotations

import socket
from urllib.error import URLError

from job_hub.domain_probe import probe_public_domain


class _Response:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def getcode(self):
        return self.status


def test_domain_probe_distinguishes_nxdomain() -> None:
    def resolve(_hostname: str):
        raise socket.gaierror(socket.EAI_NONAME, "not found")

    result = probe_public_domain(
        "jobs.cupdky.cn",
        expected_ip="81.70.62.174",
        resolver=resolve,
    )

    assert result.dns_status == "nxdomain"
    assert result.https_status == "not_checked"
    assert result.ready is False


def test_domain_probe_rejects_wrong_a_record_without_https_request() -> None:
    called = False

    def opener(*_args, **_kwargs):
        nonlocal called
        called = True
        return _Response()

    result = probe_public_domain(
        "jobs.cupdky.cn",
        expected_ip="81.70.62.174",
        resolver=lambda _hostname: ("192.0.2.10",),
        opener=opener,
    )

    assert result.dns_status == "wrong_target"
    assert result.https_status == "not_checked"
    assert called is False


def test_domain_probe_reports_https_success() -> None:
    result = probe_public_domain(
        "jobs.cupdky.cn",
        expected_ip="81.70.62.174",
        resolver=lambda _hostname: ("81.70.62.174",),
        opener=lambda *_args, **_kwargs: _Response(),
    )

    assert result.dns_status == "ok"
    assert result.https_status == "ok"
    assert result.ready is True


def test_domain_probe_reports_https_unavailable() -> None:
    result = probe_public_domain(
        "jobs.cupdky.cn",
        resolver=lambda _hostname: ("81.70.62.174",),
        opener=lambda *_args, **_kwargs: (_ for _ in ()).throw(URLError("connection refused")),
    )

    assert result.dns_status == "ok"
    assert result.https_status == "unavailable"
    assert result.ready is False

