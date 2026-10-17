from __future__ import annotations

from dataclasses import dataclass

import pytest

from job_hub.domestic_entry_health import build_identity_only_entry_health_report
from job_hub.sources import SourceHealthProbe

from conftest import make_settings


@dataclass
class ProbeResponse:
    status_code: int
    text: str = ""
    url: str = ""

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 400


class ProbeSession:
    def __init__(self, responses: dict[str, ProbeResponse]) -> None:
        self.headers: dict[str, str] = {}
        self.responses = responses
        self.calls: list[str] = []

    def get(self, url: str, **_kwargs):
        self.calls.append(url)
        response = self.responses[url]
        response.url = response.url or url
        return response


def _queue(*records: dict[str, object]) -> dict[str, object]:
    return {"version": 1, "records": list(records)}


def _candidate(record_id: str = "candidate-a") -> dict[str, object]:
    return {
        "id": record_id,
        "system": "中国石油",
        "organization": "测试勘探开发研究院",
        "official_url": "https://careers.example.edu.cn/recruitment/",
        "status": "official_identity_only",
    }


def test_identity_only_entry_health_is_read_only_and_requires_job_sample_next(
    tmp_path,
) -> None:
    settings = make_settings(tmp_path)
    session = ProbeSession(
        {
            "https://careers.example.edu.cn/robots.txt": ProbeResponse(404),
            "https://careers.example.edu.cn/recruitment/": ProbeResponse(200),
        }
    )
    report = build_identity_only_entry_health_report(
        _queue(_candidate()),
        check=SourceHealthProbe(settings, session).check,
        observed_at="2026-10-10T09:00:00+08:00",
    )

    assert report["scope"] == "official_identity_only_entry_health"
    assert report["candidate_count"] == 1
    assert report["reachable_count"] == 1
    row = report["items"][0]
    assert row["candidate_id"] == "candidate-a"
    assert row["entry_reachable"] is True
    assert row["publication_status"] == "not_registered"
    assert row["next_gate"] == "locate_current_official_job_sample"
    assert session.calls == [
        "https://careers.example.edu.cn/robots.txt",
        "https://careers.example.edu.cn/recruitment/",
    ]


def test_identity_only_entry_health_stops_at_disallowed_robots(tmp_path) -> None:
    settings = make_settings(tmp_path)
    session = ProbeSession(
        {
            "https://careers.example.edu.cn/robots.txt": ProbeResponse(
                200,
                "User-agent: *\nDisallow: /\n",
            ),
        }
    )
    report = build_identity_only_entry_health_report(
        _queue(_candidate()),
        check=SourceHealthProbe(settings, session).check,
    )

    row = report["items"][0]
    assert row["entry_reachable"] is False
    assert row["entry_status"] == "source_blocked"
    assert row["next_gate"] == "retry_from_approved_environment"
    assert session.calls == ["https://careers.example.edu.cn/robots.txt"]


def test_identity_only_entry_health_rejects_non_candidate_filter() -> None:
    verified = {
        **_candidate("verified-record"),
        "status": "official_job_sample_verified",
    }
    with pytest.raises(ValueError, match="official_identity_only"):
        build_identity_only_entry_health_report(
            _queue(_candidate(), verified),
            check=lambda _source: None,  # type: ignore[arg-type]
            record_ids=["verified-record"],
        )
