from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from job_hub.link_health import (
    LinkHealthResult,
    OfficialLinkHealthProbe,
    build_link_health_report,
)

from conftest import make_settings


@dataclass
class FakeResponse:
    status_code: int
    text: str = ""
    url: str = ""
    content_type: str = "text/html; charset=utf-8"

    @property
    def headers(self) -> dict[str, str]:
        return {"Content-Type": self.content_type}

    @property
    def content(self) -> bytes:
        return self.text.encode("utf-8")


class FakeSession:
    def __init__(self, responses: dict[str, FakeResponse]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def get(self, url: str, **_kwargs):
        self.calls.append(url)
        response = self.responses[url]
        if not response.url:
            response.url = url
        return response


def _source() -> dict[str, object]:
    return {
        "id": "official-test-source",
        "homepage_url": "https://careers.example.test/",
        "config": {"allowed_hosts": ["careers.example.test"]},
    }


def _job(url: str = "https://careers.example.test/jobs/7") -> dict[str, object]:
    return {
        "id": 7,
        "source_id": "official-test-source",
        "official_evidence_url": url,
        "publication_status": "student_eligible",
    }


def test_link_health_honors_robots_without_fetching_detail(tmp_path: Path) -> None:
    session = FakeSession(
        {
            "https://careers.example.test/robots.txt": FakeResponse(
                200, "User-agent: *\nDisallow: /\n", content_type="text/plain"
            ),
        }
    )
    result = OfficialLinkHealthProbe(
        make_settings(tmp_path), session=session, retries=0
    ).probe(_job(), _source())

    assert result.status == "robots_blocked"
    assert session.calls == ["https://careers.example.test/robots.txt"]


def test_link_health_distinguishes_dynamic_shell_from_missing_page(tmp_path: Path) -> None:
    session = FakeSession(
        {
            "https://careers.example.test/robots.txt": FakeResponse(404),
            "https://careers.example.test/jobs/7": FakeResponse(
                200, "<html><title>招聘平台</title><script src='app.js'></script></html>"
            ),
        }
    )
    result = OfficialLinkHealthProbe(
        make_settings(tmp_path), session=session, retries=0
    ).probe(_job(), _source())

    assert result.status == "reachable_dynamic"
    assert result.http_status == 200
    assert result.detail.endswith("dynamic HTML shell")


def test_link_health_keeps_access_policy_failure_out_of_withdrawal_logic(tmp_path: Path) -> None:
    session = FakeSession(
        {
            "https://careers.example.test/robots.txt": FakeResponse(404),
            "https://careers.example.test/jobs/7": FakeResponse(412, "challenge"),
        }
    )
    result = OfficialLinkHealthProbe(
        make_settings(tmp_path), session=session, retries=0
    ).probe(_job(), _source())

    assert result.status == "access_limited"
    assert result.http_status == 412
    assert "not withdrawn" not in result.detail


def test_link_health_filters_source_before_applying_limit(monkeypatch, tmp_path: Path) -> None:
    class FakeDatabase:
        def list_sources(self):
            return [_source(), {"id": "other-source", "homepage_url": "https://other.test/", "config": {}}]

        def list_jobs(self, **_kwargs):
            return [
                {
                    "id": 1,
                    "source_id": "other-source",
                    "official_evidence_url": "https://other.test/jobs/1",
                    "publication_status": "student_eligible",
                },
                _job(),
            ], 2

    class FakeProbe:
        def __init__(self, _settings):
            pass

        def probe(self, job, source):
            return LinkHealthResult(
                int(job["id"]), str(source["id"]), str(job["official_evidence_url"]), "reachable"
            )

    monkeypatch.setattr("job_hub.link_health.OfficialLinkHealthProbe", FakeProbe)
    result = build_link_health_report(
        FakeDatabase(), make_settings(tmp_path), source_id="official-test-source", limit=1
    )

    assert result["checked"] == 1
    assert result["items"][0]["job_id"] == 7


def test_link_health_evaluates_each_path_against_cached_robots_rules(tmp_path: Path) -> None:
    session = FakeSession(
        {
            "https://careers.example.test/robots.txt": FakeResponse(
                200, "User-agent: *\nDisallow: /private/\n", content_type="text/plain"
            ),
            "https://careers.example.test/jobs/7": FakeResponse(
                200, "<html><title>岗位详情</title><p>公开岗位详情内容足够长。</p></html>"
            ),
        }
    )
    probe = OfficialLinkHealthProbe(make_settings(tmp_path), session=session, retries=0)

    public_result = probe.probe(_job(), _source())
    private_result = probe.probe(
        _job("https://careers.example.test/private/job-7"), _source()
    )

    assert public_result.status == "reachable"
    assert private_result.status == "robots_blocked"
    assert "https://careers.example.test/private/job-7" not in session.calls


def test_link_health_round_robins_sources_for_global_audit(monkeypatch, tmp_path: Path) -> None:
    other_source = {
        "id": "other-source",
        "homepage_url": "https://other.test/",
        "config": {"allowed_hosts": ["other.test"]},
    }

    class FakeDatabase:
        def list_sources(self):
            return [_source(), other_source]

        def list_jobs(self, **_kwargs):
            return [
                _job(),
                {**_job("https://careers.example.test/jobs/8"), "id": 8},
                {**_job("https://other.test/jobs/9"), "id": 9, "source_id": "other-source"},
            ], 3

    class FakeProbe:
        def __init__(self, _settings):
            pass

        def probe(self, job, source):
            return LinkHealthResult(
                int(job["id"]), str(source["id"]), str(job["official_evidence_url"]), "reachable"
            )

    monkeypatch.setattr("job_hub.link_health.OfficialLinkHealthProbe", FakeProbe)
    result = build_link_health_report(FakeDatabase(), make_settings(tmp_path), limit=2)

    assert result["checked"] == 2
    assert {item["source_id"] for item in result["items"]} == {
        "official-test-source",
        "other-source",
    }
