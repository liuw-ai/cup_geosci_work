from __future__ import annotations

from job_hub.expansion_targets import (
    build_expansion_target_report,
    load_effective_job_target_plan,
)


def test_target_plan_totals_one_thousand_without_counting_pending_rows() -> None:
    plan = load_effective_job_target_plan()
    assert plan["target_total"] == 1000
    assert sum(item["target_jobs"] for item in plan["segments"]) == 1000
    central_geology = next(
        item for item in plan["segments"] if item["id"] == "central-geology-and-mining"
    )
    assert "cmgb-iguopin-2027-geoscience-snapshot" in central_geology["source_ids"]
    assert "chinalco-iguopin-browser" in central_geology["source_ids"]


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
