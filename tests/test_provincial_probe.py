from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

from job_hub.cli import main as cli_main
from job_hub.provincial_probe import provincial_probe_targets, run_provincial_entry_probe
from job_hub.sources import SourceHealthResult

from conftest import make_settings


def _matrix() -> dict[str, object]:
    return {
        "required_roles": [
            "human_resources_or_exam",
            "natural_resources",
            "geology_bureau_or_institute",
            "public_institution_recruitment",
            "civil_service",
        ],
        "province_targets": {
            "测试省": {
                "human_resources_or_exam": {"state": "unlocated"},
                "natural_resources": {
                    "state": "candidate",
                    "official_entry_url": "https://natural.example.test/recruitment/",
                },
                "geology_bureau_or_institute": {
                    "state": "blocked",
                    "official_entry_url": "https://geology.example.test/notices/",
                },
                "public_institution_recruitment": {"state": "unlocated"},
                "civil_service": {"state": "verified", "source_id": "existing-source"},
            }
        },
    }


def test_provincial_probe_targets_only_include_explicit_candidate_entries() -> None:
    targets = provincial_probe_targets(_matrix())

    assert [(item["role"], item["target_state"]) for item in targets] == [
        ("natural_resources", "candidate"),
        ("geology_bureau_or_institute", "blocked"),
    ]
    assert all(item["official_entry_url"].startswith("https://") for item in targets)


def test_provincial_entry_probe_classifies_without_enabling_or_publishing(tmp_path) -> None:
    checked_sources: list[dict[str, object]] = []

    def fake_probe(source: dict[str, object]) -> SourceHealthResult:
        checked_sources.append(source)
        if "natural" in str(source["homepage_url"]):
            return SourceHealthResult(
                "source_active",
                "robots.txt 允许且栏目可访问",
                200,
                successful=True,
                checks={"robots": {"status": "allowed"}},
            )
        return SourceHealthResult(
            "source_blocked",
            "robots.txt 不允许访问",
            403,
            checks={"robots": {"status": "disallowed"}},
        )

    report = run_provincial_entry_probe(
        make_settings(tmp_path),
        matrix=_matrix(),
        probe=fake_probe,
        checked_at=datetime(2026, 9, 29, tzinfo=timezone.utc),
    )

    assert report["target_count"] == 2
    assert report["classification_counts"] == {
        "access_limited": 1,
        "entry_accessible": 1,
    }
    assert report["results"][0]["classification"] == "entry_accessible"
    assert report["results"][1]["classification"] == "access_limited"
    assert all(str(item["id"]).startswith("provincial-probe:") for item in checked_sources)
    assert "不得据此启用来源" in str(report["publication_policy"])


def test_provincial_probe_cli_writes_an_explicitly_requested_private_report(
    tmp_path, monkeypatch, capsys
) -> None:
    output = tmp_path / "provincial-probe.json"
    report = {
        "version": 1,
        "target_count": 1,
        "classification_counts": {"entry_accessible": 1},
        "results": [],
        "publication_policy": "只读，不发布岗位。",
    }
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("APP_DATABASE_PATH", str(tmp_path / "jobs.sqlite3"))
    monkeypatch.setattr("job_hub.cli.run_provincial_entry_probe", lambda *_args, **_kwargs: report)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "job_hub.cli",
            "provincial-entry-probe",
            "--province",
            "北京",
            "--role",
            "natural_resources",
            "--output",
            str(output),
        ],
    )
    monkeypatch.setattr(
        "job_hub.cli.services",
        lambda: (_ for _ in ()).throw(
            AssertionError("read-only probe must not bootstrap services")
        ),
    )

    cli_main()

    assert json.loads(capsys.readouterr().out)["target_count"] == 1
    assert json.loads(output.read_text(encoding="utf-8"))["version"] == 1
