from __future__ import annotations

from dataclasses import dataclass
import json
import sys

import pytest
import requests

from job_hub.app import create_app
from job_hub.cli import main as cli_main
from job_hub.contracts import (
    ContractValidationError,
    validate_national_entry_probe_run,
    validate_national_entry_targets,
)
from job_hub.entry_probes import (
    PublicEntryProbeRunner,
    load_national_entry_targets,
    national_entry_probe_summary,
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

    @property
    def encoding(self) -> str:
        return "utf-8"


class FakeSession:
    def __init__(self, responses: dict[str, object]) -> None:
        self.headers: dict[str, str] = {}
        self.responses = responses
        self.calls: list[str] = []

    def get(self, url: str, **_kwargs):
        self.calls.append(url)
        value = self.responses[url]
        if isinstance(value, list):
            if len(value) > 1:
                return value.pop(0)
            value = value[0]
        if isinstance(value, Exception):
            raise value
        response = value
        if not response.url:
            response.url = url
        return response


def _targets() -> dict[str, object]:
    return {
        "version": 1,
        "as_of": "2026-09-22",
        "systems": [
            {
                "system_id": "demo",
                "affiliation": "测试体系",
                "source_id": "demo-source",
                "name": "测试公开入口",
                "entries": [
                    {
                        "entry_id": "demo-primary",
                        "role": "primary_recruitment",
                        "url": "https://careers.example.test/jobs/",
                        "official_hosts": ["careers.example.test"],
                    },
                    {
                        "entry_id": "demo-backup",
                        "role": "backup_recruitment",
                        "url": "https://www.example.test/",
                        "official_hosts": ["www.example.test"],
                    },
                ],
                "recruitment_keywords": ["招聘", "jobs"],
                "max_links": 10,
            }
        ],
    }


def test_real_national_entry_target_registry_is_valid() -> None:
    targets = load_national_entry_targets(source_ids={
        "cnpc-career",
        "sinopec-career",
        "cnooc-career",
        "pipechina-career",
    })
    assert len(targets["systems"]) == 4
    assert all(len(system["entries"]) >= 2 for system in targets["systems"])


def test_target_contract_requires_one_primary_entry() -> None:
    payload = _targets()
    payload["systems"][0]["entries"][1]["role"] = "primary_recruitment"  # type: ignore[index]
    with pytest.raises(ContractValidationError, match="exactly one primary"):
        validate_national_entry_targets(payload, source_ids={"demo-source"})


def test_probe_contract_rejects_inconsistent_attempt_count() -> None:
    targets = _targets()
    session = FakeSession(
        {
            "https://careers.example.test/robots.txt": FakeResponse(404),
            "https://careers.example.test/jobs/": FakeResponse(200, "<html>招聘</html>"),
            "https://www.example.test/robots.txt": FakeResponse(404),
            "https://www.example.test/": FakeResponse(200, "<html>备用</html>"),
        }
    )
    result = PublicEntryProbeRunner(
        session=session, retries=0, backoff_seconds=0
    ).run(targets, observed_on="2026-09-22")
    result["systems"][0]["attempt_count"] = 1
    with pytest.raises(ContractValidationError, match="attempt_count"):
        validate_national_entry_probe_run(result, source_ids={"demo-source"})


def test_probe_contract_rejects_attempt_source_mismatch() -> None:
    targets = _targets()
    session = FakeSession(
        {
            "https://careers.example.test/robots.txt": FakeResponse(404),
            "https://careers.example.test/jobs/": FakeResponse(200, "<html>招聘</html>"),
            "https://www.example.test/robots.txt": FakeResponse(404),
            "https://www.example.test/": FakeResponse(200, "<html>备用</html>"),
        }
    )
    result = PublicEntryProbeRunner(
        session=session, retries=0, backoff_seconds=0
    ).run(targets, observed_on="2026-09-22")
    result["attempts"][0]["source_id"] = "other-source"
    with pytest.raises(ContractValidationError, match="does not match"):
        validate_national_entry_probe_run(result, source_ids={"demo-source", "other-source"})


def test_probe_discovers_official_recruitment_link() -> None:
    targets = _targets()
    session = FakeSession(
        {
            "https://careers.example.test/robots.txt": FakeResponse(404),
            "https://careers.example.test/jobs/": FakeResponse(
                200,
                '<html><title>招聘</title><a href="/jobs/geology">地质招聘</a></html>',
            ),
            "https://www.example.test/robots.txt": FakeResponse(404),
            "https://www.example.test/": FakeResponse(200, "<html>备用主页</html>"),
        }
    )
    result = PublicEntryProbeRunner(
        session=session, retries=0, backoff_seconds=0
    ).run(targets, observed_on="2026-09-22")

    assert result["systems"][0]["status"] == "accessible_structure_unverified"
    assert result["systems"][0]["scan_conclusion"] == "structure_needs_adapter"
    assert result["systems"][0]["recruitment_links"] == [
        "https://careers.example.test/jobs/geology"
    ]
    assert result["attempts"][0]["classification"] == "accessible_html"


def test_probe_uses_backup_after_transient_primary_failure() -> None:
    targets = _targets()
    session = FakeSession(
        {
            "https://careers.example.test/robots.txt": requests.exceptions.SSLError(
                "TLS EOF"
            ),
            "https://www.example.test/robots.txt": FakeResponse(404),
            "https://www.example.test/": FakeResponse(200, "<html>招聘入口</html>"),
        }
    )
    result = PublicEntryProbeRunner(
        session=session, retries=1, backoff_seconds=0
    ).run(targets, observed_on="2026-09-22")

    assert result["systems"][0]["usable_entry_url"] == "https://www.example.test/"
    assert result["systems"][0]["status"] == "accessible_structure_unverified"
    assert result["attempts"][0]["classification"] == "source_unavailable"
    assert result["attempts"][0]["error_class"] == "tls_or_policy_block"
    assert result["attempts"][0]["retries"] == 1


def test_probe_preserves_robots_block_without_fetching_page() -> None:
    targets = _targets()
    session = FakeSession(
        {
            "https://careers.example.test/robots.txt": FakeResponse(
                200, "User-agent: *\nDisallow: /\n", content_type="text/plain"
            ),
            "https://www.example.test/robots.txt": FakeResponse(
                403, "blocked", content_type="text/plain"
            ),
        }
    )
    result = PublicEntryProbeRunner(
        session=session, retries=0, backoff_seconds=0
    ).run(targets, observed_on="2026-09-22")

    assert result["systems"][0]["status"] == "access_limited"
    assert result["systems"][0]["scan_conclusion"] == "source_unavailable"
    assert result["attempts"][0]["classification"] == "robots_blocked"
    assert "https://careers.example.test/jobs/" not in session.calls
    assert result["attempts"][1]["classification"] == "access_policy_block"


def test_probe_marks_dynamic_shell_as_structure_unverified() -> None:
    targets = _targets()
    session = FakeSession(
        {
            "https://careers.example.test/robots.txt": FakeResponse(404),
            "https://careers.example.test/jobs/": FakeResponse(
                200,
                "<html><head><script src='/app.js'></script></head><body><div id='root'></div></body></html>",
            ),
            "https://www.example.test/robots.txt": FakeResponse(404),
            "https://www.example.test/": FakeResponse(404, "<title>404</title>"),
        }
    )
    result = PublicEntryProbeRunner(
        session=session, retries=0, backoff_seconds=0
    ).run(targets, observed_on="2026-09-22")

    assert result["attempts"][0]["classification"] == "accessible_dynamic_shell"
    assert national_entry_probe_summary(result)["classification_counts"][
        "accessible_dynamic_shell"
    ] == 1


def test_entry_probe_admin_endpoint_is_private(tmp_path) -> None:
    app = create_app(make_settings(tmp_path))
    client = app.test_client()

    assert client.get("/api/admin/national-entry-probes").status_code == 403
    response = client.get(
        "/api/admin/national-entry-probes",
        headers={"X-Admin-Token": "test-admin-token"},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert len(payload["targets"]) == 4
    assert payload["run"]["systems"]
    assert payload["summary"]["system_count"] == 4
    assert payload["run_error"] is None


def test_entry_probe_cli_writes_private_run_output(tmp_path, monkeypatch, capsys) -> None:
    targets = _targets()
    session = FakeSession(
        {
            "https://careers.example.test/robots.txt": FakeResponse(404),
            "https://careers.example.test/jobs/": FakeResponse(
                200, "<html><a href='/jobs/geology'>招聘</a></html>"
            ),
            "https://www.example.test/robots.txt": FakeResponse(404),
            "https://www.example.test/": FakeResponse(404, "<title>404</title>"),
        }
    )

    class StubRunner:
        def __init__(self, **_kwargs) -> None:
            self.runner = PublicEntryProbeRunner(
                session=session, retries=0, backoff_seconds=0
            )

        def run(self, loaded_targets, **kwargs):
            assert loaded_targets == targets
            return self.runner.run(loaded_targets, **kwargs)

    monkeypatch.setattr("job_hub.cli.load_national_entry_targets", lambda: targets)
    monkeypatch.setattr("job_hub.cli.PublicEntryProbeRunner", StubRunner)
    output_path = tmp_path / "probe-run.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "job_hub.cli",
            "national-entry-probe",
            "--output",
            str(output_path),
            "--retries",
            "0",
        ],
    )

    cli_main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["system_count"] == 1
    assert output_path.exists()
    stored = json.loads(output_path.read_text(encoding="utf-8"))
    assert stored["version"] == 1
    assert stored["systems"][0]["system_id"] == "demo"
