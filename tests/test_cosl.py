from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from dataclasses import replace
from pathlib import Path

from job_hub.pipeline import JobPipeline
from job_hub.sources import OfficialSourceCollector

from conftest import make_settings


PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class FakeResponse:
    payload: dict

    def json(self) -> dict:
        return self.payload


def _cosl_source() -> dict:
    records = json.loads(
        (PROJECT_ROOT / "data" / "sources.json").read_text(encoding="utf-8")
    )
    return next(item for item in records if item["id"] == "cosl-career")


def test_cosl_public_api_splits_official_role_blocks_and_preserves_evidence(
    tmp_path, monkeypatch
) -> None:
    collector = OfficialSourceCollector(
        replace(make_settings(tmp_path), max_source_items=120)
    )
    source = copy.deepcopy(_cosl_source())
    # The captured live advertisement is labelled as interview information;
    # remove the production exclusion here to test the row parser itself.
    source["config"]["excluded_title_patterns"] = []
    fixture = json.loads(
        (PROJECT_ROOT / "tests" / "fixtures" / "domestic" / "cosl_job_page_2026.json")
        .read_text(encoding="utf-8")
    )
    monkeypatch.setattr(collector, "_robots_allowed", lambda _url: True)
    monkeypatch.setattr(
        collector,
        "_post_json",
        lambda _url, _payload, headers=None: FakeResponse(fixture),
    )
    monkeypatch.setattr(collector, "_wait", lambda _source: None)

    postings = collector.collect(source)

    assert len(postings) >= 30
    geology = next(item for item in postings if "\u7269\u63a2\u65b9\u6cd5\u7814\u53d1\u5de5\u7a0b\u5e08" in item.title)
    assert geology.employer == "\u4e2d\u6d77\u6cb9\u7530\u670d\u52a1\u80a1\u4efd\u6709\u9650\u516c\u53f8"
    assert geology.source_url.endswith("/campus/detail?jobAdId=390785184")
    assert geology.official_evidence_url == geology.source_url
    assert geology.external_id.startswith("cosl-390785184-role-")
    assert geology.field_evidence is not None
    assert geology.field_evidence["evidence_scope"] == "official_role_section"
    assert geology.field_evidence["\u5b66\u5386\u8981\u6c42"].startswith("\u7855\u58eb\u3001\u535a\u58eb")
    assert "\u5730\u8d28\u5b66" in geology.field_evidence["\u4e13\u4e1a\u8981\u6c42"]
    assert "\u5730\u8d28\u8d44\u6e90\u4e0e\u5730\u8d28\u5de5\u7a0b" in geology.field_evidence["\u4e13\u4e1a\u8981\u6c42"]
    undergraduate = next(item for item in postings if "\u5730\u9707\u91c7\u96c6\u5de5\u7a0b\u5e08" in item.title)
    assert "\u8d44\u6e90\u52d8\u67e5\u5de5\u7a0b" in undergraduate.field_evidence["\u4e13\u4e1a\u8981\u6c42"]
    assert "\ufffd" not in geology.field_evidence["\u4e13\u4e1a\u8981\u6c42"]
    assert "\u5929\u6d25" in undergraduate.location
    assert "\u5de5\u4f5c\u5730\u70b9\u53ca\u7279\u6b8a\u8981\u6c42" not in undergraduate.location
    assert geology.location


def test_cosl_production_filter_rejects_interview_collection_notice(
    tmp_path, monkeypatch
) -> None:
    collector = OfficialSourceCollector(make_settings(tmp_path))
    source = _cosl_source()
    fixture = json.loads(
        (PROJECT_ROOT / "tests" / "fixtures" / "domestic" / "cosl_job_page_2026.json")
        .read_text(encoding="utf-8")
    )
    monkeypatch.setattr(collector, "_robots_allowed", lambda _url: True)
    monkeypatch.setattr(
        collector,
        "_post_json",
        lambda _url, _payload, headers=None: FakeResponse(fixture),
    )
    monkeypatch.setattr(collector, "_wait", lambda _source: None)

    assert collector.collect(source) == []


def test_cosl_student_gate_rejects_non_geoscience_roles(
    tmp_path, monkeypatch
) -> None:
    collector = OfficialSourceCollector(
        replace(make_settings(tmp_path), max_source_items=120)
    )
    source = copy.deepcopy(_cosl_source())
    source["config"]["excluded_title_patterns"] = []
    fixture = json.loads(
        (PROJECT_ROOT / "tests" / "fixtures" / "domestic" / "cosl_job_page_2026.json")
        .read_text(encoding="utf-8")
    )
    monkeypatch.setattr(collector, "_robots_allowed", lambda _url: True)
    monkeypatch.setattr(
        collector,
        "_post_json",
        lambda _url, _payload, headers=None: FakeResponse(fixture),
    )
    monkeypatch.setattr(collector, "_wait", lambda _source: None)

    postings = collector.collect(source)
    pipeline = JobPipeline(make_settings(tmp_path), database=None)
    geology = next(item for item in postings if "\u5de5\u7a0b\u5730\u8d28\u3001\u5de5\u7a0b\u7269\u63a2\u5de5\u7a0b\u5e08" in item.title)
    mechanical = next(item for item in postings if "\u673a\u68b0\u8bbe\u8ba1\u5de5\u7a0b\u5e08" in item.title)

    geoscience_job = pipeline.normalize_posting(geology, source)
    mechanical_job = pipeline.normalize_posting(mechanical, source)
    assert geoscience_job["publication_status"] == "student_eligible"
    assert mechanical_job["publication_status"] not in {
        "student_eligible",
        "unrestricted_eligible",
    }
    assert mechanical_job["official_evidence_url"].endswith(
        "/campus/detail?jobAdId=390785184"
    )
