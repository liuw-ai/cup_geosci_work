from __future__ import annotations

import json
import sys
from types import SimpleNamespace

from job_hub.expansion_targets import (
    build_expansion_target_report,
    load_effective_job_target_plan,
)


def test_target_plan_totals_five_hundred_with_three_oil_priority() -> None:
    plan = load_effective_job_target_plan()
    assert plan["target_total"] == 500
    assert sum(item["target_jobs"] for item in plan["segments"]) == 500
    three_oil_ids = {
        "sinopec-subsidiaries",
        "cnpc-subsidiaries",
        "cnooc-subsidiaries",
    }
    assert sum(
        item["target_jobs"]
        for item in plan["segments"]
        if item["id"] in three_oil_ids
    ) == 360
    central_geology = next(
        item for item in plan["segments"] if item["id"] == "central-geology-and-mining"
    )
    assert "cmgb-iguopin-2027-geoscience-snapshot" in central_geology["source_ids"]
    assert "chinalco-iguopin-browser" in central_geology["source_ids"]


def test_three_oil_targets_follow_verified_employer_across_public_sources() -> None:
    jobs = [
        {
            "source_id": "cupb-career",
            "employer": "中国石油勘探开发研究院西北分院",
            "category": "油气上游业主与研究机构",
            "province": "甘肃",
        },
        {
            "source_id": "cupb-career",
            "employer": "中国石化胜利油田分公司",
            "category": "油气上游业主与研究机构",
            "province": "山东",
        },
        {
            "source_id": "cupb-career",
            "employer": "中国海油天津分公司",
            "category": "油气上游业主与研究机构",
            "province": "天津",
        },
        {
            "source_id": "cnpc-career",
            "employer": "国家石油天然气管网集团有限公司",
            "category": "管网、炼化与综合能源",
            "province": "北京",
        },
        {
            "source_id": "cnpc-career",
            "employer": "中国石化胜利油田分公司",
            "category": "油气上游业主与研究机构",
            "province": "山东",
        },
    ]
    for job in jobs:
        job.update(
            status="open",
            publication_status="student_eligible",
            country_or_region="中国大陆",
        )

    report = build_expansion_target_report(jobs)
    segments = {item["id"]: item for item in report["segments"]}
    assert report["current_effective_jobs"] == 5
    assert segments["cnpc-subsidiaries"]["current_jobs"] == 1
    assert segments["cnpc-subsidiaries"]["source_count"] == 1
    assert segments["cnpc-subsidiaries"]["planned_source_count"] == 0
    assert segments["sinopec-subsidiaries"]["current_jobs"] == 2
    assert segments["sinopec-subsidiaries"]["planned_source_count"] == 0
    assert segments["cnooc-subsidiaries"]["current_jobs"] == 1
    assert segments["cnooc-subsidiaries"]["planned_source_count"] == 0
    assert sum(item["current_jobs"] for item in report["segments"]) == 4


def test_three_oil_target_keeps_dedicated_source_fallback_for_short_labels() -> None:
    report = build_expansion_target_report(
        [
            {
                "source_id": "cnpc-career",
                "employer": "未登记简称单位",
                "category": "油气上游业主与研究机构",
                "province": "新疆",
                "country_or_region": "中国大陆",
                "status": "open",
                "publication_status": "student_eligible",
            }
        ]
    )
    cnpc = next(item for item in report["segments"] if item["id"] == "cnpc-subsidiaries")
    assert cnpc["current_jobs"] == 1
    assert cnpc["planned_source_count"] == 1


def test_target_report_assigns_a_job_to_only_one_segment() -> None:
    plan = {
        "version": 1,
        "as_of": "2026-10-02",
        "target_total": 2,
        "segments": [
            {
                "id": "a",
                "target_jobs": 1,
                "source_ids": ["source-a"],
                "categories": ["油气上游业主与研究机构"],
            },
            {
                "id": "b",
                "target_jobs": 1,
                "source_ids": ["source-b"],
                "categories": ["油气上游业主与研究机构"],
            },
        ],
    }
    jobs = [
        {
            "source_id": "source-a",
            "category": "油气上游业主与研究机构",
            "status": "open",
            "publication_status": "student_eligible",
            "employer": "A",
            "province": "山东",
        },
        {
            "source_id": "source-b",
            "category": "油气上游业主与研究机构",
            "status": "open",
            "publication_status": "student_eligible",
            "employer": "B",
            "province": "天津",
        },
        {
            "source_id": "ignored",
            "category": "油气上游业主与研究机构",
            "status": "open",
            "publication_status": "pending_evidence",
            "employer": "C",
            "province": "北京",
        },
    ]
    report = build_expansion_target_report(jobs, plan=plan)
    assert report["current_effective_jobs"] == 2
    assert [item["current_jobs"] for item in report["segments"]] == [1, 1]
    assert report["gap_jobs"] == 0
    assert report["unique_sources"] == 2
    assert report["unique_employers"] == 2
    assert report["unique_provinces"] == 2
    assert report["employer_top_share"] == 0.5


def test_target_report_excludes_overseas_and_unclassified_rows_by_default() -> None:
    plan = {
        "version": 1,
        "as_of": "2026-10-03",
        "target_total": 2,
        "segments": [
            {
                "id": "geology",
                "target_jobs": 2,
                "source_ids": ["official-source"],
                "categories": ["自然资源、地调与地勘"],
            }
        ],
    }
    jobs = [
        {
            "source_id": "official-source",
            "category": "自然资源、地调与地勘",
            "status": "open",
            "publication_status": "student_eligible",
            "employer": "国内单位",
            "province": "北京",
            "country_or_region": "中国大陆",
        },
        {
            "source_id": "official-source",
            "category": "自然资源、地调与地勘",
            "status": "open",
            "publication_status": "student_eligible",
            "employer": "海外单位",
            "province": "",
            "country_or_region": "哈萨克斯坦",
        },
        {
            "source_id": "official-source",
            "category": "自然资源、地调与地勘",
            "status": "open",
            "publication_status": "student_eligible",
            "employer": "地点未明单位",
            "province": "",
            "country_or_region": "",
        },
    ]

    report = build_expansion_target_report(jobs, plan=plan)
    assert report["scope"] == "domestic_mainland"
    assert report["current_effective_jobs"] == 1
    assert report["excluded_non_domestic_public_jobs"] == 2
    assert report["gap_jobs"] == 1

    diagnostic = build_expansion_target_report(
        jobs,
        plan=plan,
        include_non_domestic=True,
    )
    assert diagnostic["scope"] == "all_public"
    assert diagnostic["current_effective_jobs"] == 3


def test_target_report_surfaces_planned_sources_missing_from_registry() -> None:
    plan = {
        "version": 1,
        "as_of": "2026-10-03",
        "target_total": 1,
        "segments": [
            {
                "id": "cnpc",
                "target_jobs": 1,
                "source_ids": ["cnpc-career", "cnpc-logging-campus"],
                "categories": ["油气上游业主与研究机构"],
            }
        ],
    }
    report = build_expansion_target_report(
        [], plan=plan, registered_source_ids={"cnpc-career"}
    )
    assert report["registered_source_ids_checked"] is True
    assert report["unregistered_planned_source_ids"] == ["cnpc-logging-campus"]


def test_category_match_cannot_fake_a_missing_registered_source() -> None:
    plan = {
        "version": 1,
        "as_of": "2026-10-03",
        "target_total": 1,
        "segments": [
            {
                "id": "cnpc",
                "target_jobs": 1,
                "source_ids": ["cnpc-career"],
                "categories": ["油气上游业主与研究机构"],
            }
        ],
    }
    report = build_expansion_target_report(
        [
            {
                "source_id": "cnooc-career-browser",
                "category": "油气上游业主与研究机构",
                "status": "open",
                "publication_status": "student_eligible",
                "province": "北京",
                "country_or_region": "中国大陆",
            }
        ],
        plan=plan,
    )
    assert report["segments"][0]["current_jobs"] == 0
    assert report["segments"][0]["category_candidates_not_counted"] == 1


def test_target_report_excludes_manual_sources_from_unattended_total() -> None:
    plan = {
        "version": 1,
        "as_of": "2026-10-06",
        "target_total": 1,
        "segments": [
            {
                "id": "official",
                "target_jobs": 1,
                "source_ids": ["official-source"],
                "categories": ["自然资源、地调与地勘"],
            }
        ],
    }
    jobs = [
        {
            "source_id": "manual-source",
            "category": "自然资源、地调与地勘",
            "status": "open",
            "publication_status": "student_eligible",
            "employer": "人工核验单位",
            "province": "北京",
            "country_or_region": "中国大陆",
        },
        {
            "source_id": "official-source",
            "category": "自然资源、地调与地勘",
            "status": "open",
            "publication_status": "student_eligible",
            "employer": "自动来源单位",
            "province": "北京",
            "country_or_region": "中国大陆",
        },
    ]
    report = build_expansion_target_report(
        jobs,
        plan=plan,
        excluded_source_ids={"manual-source"},
    )
    assert report["current_effective_jobs"] == 1
    assert report["excluded_non_automated_public_jobs"] == 1


def test_expansion_target_cli_uses_student_currentness_date(monkeypatch, capsys) -> None:
    """The CLI must pass its as-of date through the same public-job gate."""

    from job_hub import cli

    calls: list[dict[str, object]] = []

    class FakeDatabase:
        def list_jobs(self, **kwargs):
            calls.append(kwargs)
            return (
                [
                    {
                        "source_id": "official-source",
                        "category": "自然资源、地调与地勘",
                        "status": "open",
                        "publication_status": "student_eligible",
                        "employer": "国内单位",
                        "province": "北京",
                        "country_or_region": "中国大陆",
                    }
                ],
                1,
            )

        def list_sources(self):
            return [{"id": "official-source"}]

    fake_settings = SimpleNamespace(timezone="Asia/Shanghai")
    monkeypatch.setattr(
        cli,
        "services",
        lambda: (fake_settings, FakeDatabase(), None),
    )
    monkeypatch.setattr(
        cli,
        "load_effective_job_target_plan",
        lambda: {
            "version": 1,
            "as_of": "2026-10-06",
            "target_total": 1,
            "segments": [
                {
                    "id": "official",
                    "target_jobs": 1,
                    "source_ids": ["official-source"],
                    "categories": ["自然资源、地调与地勘"],
                }
            ],
        },
    )
    monkeypatch.setattr(cli, "coverage_snapshot_date", lambda _settings: "2026-10-06")

    monkeypatch.setattr(sys, "argv", ["job_hub.cli", "expansion-target-audit"])
    cli.main()
    default_output = json.loads(capsys.readouterr().out)

    monkeypatch.setattr(
        sys,
        "argv",
        ["job_hub.cli", "expansion-target-audit", "--as-of", "2026-10-05"],
    )
    cli.main()
    explicit_output = json.loads(capsys.readouterr().out)

    assert [call["as_of_date"] for call in calls] == ["2026-10-06", "2026-10-05"]
    assert default_output["as_of_date"] == "2026-10-06"
    assert explicit_output["as_of_date"] == "2026-10-05"
    assert "同一日期/来源新鲜度门禁" in default_output["scope_note"]
