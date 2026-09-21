from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from job_hub.source_validation import (
    load_source_validation_registry,
    source_validation_matrix_rows,
    source_validation_summary,
)
from job_hub.sources import OfficialSourceCollector, load_source_registries

from conftest import make_settings


PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class FixtureResponse:
    text: str
    url: str


def _runtime_sources() -> dict[str, dict[str, object]]:
    sources = load_source_registries(
        [
            str(PROJECT_ROOT / "data" / "sources.json"),
            str(PROJECT_ROOT / "data" / "provincial_sources.json"),
        ]
    )
    return {str(source["id"]): source for source in sources}


def test_source_validation_registry_loads_and_reports_bounded_evidence() -> None:
    registry = load_source_validation_registry()
    summary = source_validation_summary(registry)

    assert summary["record_count"] == 9
    assert summary["adapter_fixture_verified_records"] == 6
    assert summary["records_by_validation_stage"] == {
        "adapter_fixture_verified": 6,
        "entry_checked_no_recruitment_sample": 3,
    }
    assert summary["adapter_fixture_verified_targets"] == 5
    assert summary["candidate_targets_with_adapter_fixture"] == 1
    assert summary["fixture_verified_enabled_sources"] == 5
    assert summary["records_with_backup"] == 9
    assert len(summary["unvalidated_verified_targets"]) > 0


def test_adapter_fixture_records_collect_current_official_samples(monkeypatch, tmp_path) -> None:
    registry = load_source_validation_registry()
    source_by_id = _runtime_sources()
    settings = make_settings(tmp_path)
    collector = OfficialSourceCollector(settings)
    monkeypatch.setattr(collector, "_wait", lambda _source: None)

    fixture_records = [
        record
        for record in registry["records"]
        if record["validation_stage"] == "adapter_fixture_verified"
    ]
    for record in fixture_records:
        source = source_by_id[record["source_id"]]
        sample = record["sample"]
        listing_url = str(source["config"]["listing_urls"][0])  # type: ignore[index]
        fixture_path = PROJECT_ROOT / str(record["fixture_path"])
        detail_html = fixture_path.read_text(encoding="utf-8")
        requested: list[str] = []

        listing_html = (
            f'<a href="{sample["official_url"]}">{sample["title"]}</a>'
            '<a href="https://outside.example.invalid/jobs/1">公开招聘公告</a>'
            '<a href="/notices/interview.html">进入面试范围人员名单</a>'
        )

        def get(url: str, _source: dict[str, object]) -> FixtureResponse:
            requested.append(url)
            if url == listing_url:
                return FixtureResponse(listing_html, listing_url)
            if url == sample["official_url"]:
                return FixtureResponse(detail_html, str(sample["official_url"]))
            raise AssertionError(f"unexpected URL requested: {url}")

        monkeypatch.setattr(collector, "_get", get)
        postings = collector.collect(source)

        assert len(postings) == 1
        posting = postings[0]
        assert posting.source_url == sample["official_url"]
        assert posting.title == sample["title"]
        assert posting.published_date == sample["published_date"]
        assert posting.deadline_date == sample["deadline_date"]
        assert requested == [listing_url, sample["official_url"]]


def test_source_validation_rows_keep_candidate_source_disabled() -> None:
    registry = load_source_validation_registry()
    rows = source_validation_matrix_rows(registry)
    candidate = next(row for row in rows if row["source_id"] == "shandong-hrss-exam")

    assert candidate["target_state"] == "candidate"
    assert candidate["source_enabled"] is False
    assert candidate["source_registered_in_runtime"] is True
