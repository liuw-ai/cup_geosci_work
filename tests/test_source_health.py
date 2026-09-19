from __future__ import annotations

from dataclasses import dataclass

from job_hub.sources import SourceHealthProbe

from conftest import make_settings, source


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

    def get(self, url: str, **_kwargs):
        response = self.responses[url]
        response.url = response.url or url
        return response


def test_source_health_accepts_public_entry_when_robots_is_absent(tmp_path) -> None:
    settings = make_settings(tmp_path)
    entry = source()
    entry["source_type"] = "landing_page"
    session = ProbeSession(
        {
            "https://careers.example.edu.cn/robots.txt": ProbeResponse(404),
            "https://careers.example.edu.cn/": ProbeResponse(200),
        }
    )

    result = SourceHealthProbe(settings, session).check(entry)

    assert result.status == "source_active"
    assert result.successful is True
    assert result.status_code == 200


def test_source_health_stops_when_robots_disallows_access(tmp_path) -> None:
    settings = make_settings(tmp_path)
    entry = source()
    entry["source_type"] = "landing_page"
    session = ProbeSession(
        {
            "https://careers.example.edu.cn/robots.txt": ProbeResponse(
                200,
                "User-agent: *\nDisallow: /\n",
            ),
        }
    )

    result = SourceHealthProbe(settings, session).check(entry)

    assert result.status == "source_blocked"
    assert result.successful is False


def test_source_health_checks_registered_listing_not_generic_homepage(tmp_path) -> None:
    settings = make_settings(tmp_path)
    entry = source()
    entry["source_type"] = "html_notice"
    entry["config"] = {
        "listing_urls": ["https://careers.example.edu.cn/jobs/"],
    }
    session = ProbeSession(
        {
            "https://careers.example.edu.cn/robots.txt": ProbeResponse(404),
            "https://careers.example.edu.cn/jobs/": ProbeResponse(200),
        }
    )

    result = SourceHealthProbe(settings, session).check(entry)

    assert result.status == "source_active"
    assert result.successful is True


def test_source_health_rejects_soft_404_page(tmp_path) -> None:
    settings = make_settings(tmp_path)
    entry = source()
    entry["source_type"] = "landing_page"
    session = ProbeSession(
        {
            "https://careers.example.edu.cn/robots.txt": ProbeResponse(404),
            "https://careers.example.edu.cn/": ProbeResponse(
                200,
                "<title>404 - 页面不存在</title>",
                "https://careers.example.edu.cn/404/40x.html",
            ),
        }
    )

    result = SourceHealthProbe(settings, session).check(entry)

    assert result.status == "source_degraded"
    assert result.successful is False
