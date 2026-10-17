from __future__ import annotations

from job_hub.readiness_axes import build_dual_axis_readiness


def _job(
    source_id: str,
    employer: str,
    province: str | None,
    country: str | None,
) -> dict[str, object]:
    return {
        "source_id": source_id,
        "employer": employer,
        "province": province,
        "country_or_region": country,
        "status": "open",
        "publication_status": "student_eligible",
    }


def _reliability(*, score: float = 90.0) -> dict[str, object]:
    return {
        "score": score,
        "max_score": 100,
        "components": {
            "official_detail_evidence": {"rate": 0.95},
            "explicit_major_degree_match": {"rate": 0.95},
            "consecutive_refresh": {"rate": 0.9},
            "lifecycle_quality": {"rate": 1.0},
        },
    }


def _targets(*, target: int, all_gates: bool) -> dict[str, object]:
    return {
        "target_total": target,
        "all_segment_source_gates": all_gates,
        "all_segment_unit_gates": all_gates,
        "all_segment_province_gates": all_gates,
    }


def test_coverage_axis_excludes_overseas_rows_from_domestic_target() -> None:
    result = build_dual_axis_readiness(
        [
            _job("mainland", "甲单位", "北京", "中国大陆"),
            _job("overseas", "乙单位", None, "哈萨克斯坦"),
        ],
        reliability_scorecard=_reliability(),
        domestic_expansion_targets=_targets(target=2, all_gates=False),
        quality_gate={"status": "pass"},
    )

    coverage = result["coverage_axis"]
    assert coverage["all_public_effective_jobs"] == 2
    assert coverage["domestic_effective_jobs"] == 1
    assert coverage["overseas_or_unclassified_public_jobs"] == 1
    assert coverage["completion_score"] == 50.0
    assert result["college_data_readiness"]["status"] == "not_ready"
    assert "domestic_effective_job_target" in result["college_data_readiness"]["failed_checks"]


def test_college_gate_requires_coverage_and_balance_in_addition_to_reliability() -> None:
    jobs = [
        _job(f"source-{index}", f"单位-{index}", "北京", "中国大陆")
        for index in range(7)
    ]
    result = build_dual_axis_readiness(
        jobs,
        reliability_scorecard=_reliability(),
        domestic_expansion_targets=_targets(target=7, all_gates=True),
        quality_gate={"status": "pass"},
    )

    assert result["coverage_axis"]["completion_score"] == 100.0
    assert result["coverage_axis"]["top_five_source_share"] == 0.7143
    assert result["college_data_readiness"]["status"] == "ready"


def test_reliability_cannot_compensate_for_a_concentrated_domestic_corpus() -> None:
    jobs = [
        _job("dominant", "同一单位", "北京", "中国大陆") for _ in range(10)
    ]
    result = build_dual_axis_readiness(
        jobs,
        reliability_scorecard=_reliability(score=100.0),
        domestic_expansion_targets=_targets(target=10, all_gates=True),
        quality_gate={"status": "pass"},
    )

    assert result["coverage_axis"]["completion_score"] == 100.0
    assert result["reliability_axis"]["score"] == 100.0
    assert result["college_data_readiness"]["status"] == "not_ready"
    assert "source_share_at_most_25_percent" in result["college_data_readiness"]["failed_checks"]


def test_three_oil_mix_is_reported_from_explicit_employer_identity() -> None:
    jobs = [
        {
            **_job("cnpc-bgp-recruitment", "中国石油集团东方地球物理勘探有限责任公司", "河北", "中国大陆"),
            "category": "油气工程技术服务",
        },
        {
            **_job("sinopec-career", "胜利油田", "山东", "中国大陆"),
            "category": "油气上游业主与研究机构",
        },
        {
            **_job("cnooc-career-browser", "中国海油-中海油研究总院", "北京", "中国大陆"),
            "category": "油气上游业主与研究机构",
        },
        _job("other", "某省地质调查院", "河南", "中国大陆"),
    ]
    result = build_dual_axis_readiness(
        jobs,
        reliability_scorecard=_reliability(),
        domestic_expansion_targets=_targets(target=4, all_gates=False),
        quality_gate={"status": "needs_attention"},
    )
    coverage = result["coverage_axis"]
    assert coverage["three_oil_job_count"] == 3
    assert coverage["three_oil_share"] == 0.75
    assert coverage["three_oil_share_within_target_band"] is True
    assert coverage["other_job_count"] == 1
    assert coverage["three_oil_target_counts"]["additional_to_min"] == 0
    assert coverage["other_target_counts"]["min"] == 1


def test_mix_plan_reports_complete_target_endpoints() -> None:
    result = build_dual_axis_readiness(
        [
            {
                **_job("cnpc-career", f"油企-{index}", "北京", "中国大陆"),
                "category": "油气上游业主与研究机构",
            }
            for index in range(7)
        ]
        + [_job("other", "事业单位", "河南", "中国大陆") for _ in range(3)],
        reliability_scorecard=_reliability(),
        domestic_expansion_targets=_targets(target=10, all_gates=False),
        quality_gate={"status": "needs_attention"},
    )

    plan = result["coverage_axis"]["additional_jobs_to_complete_target"]
    assert plan["at_70_percent_three_oil"]["total"] == 0
    assert plan["at_75_percent_three_oil"]["total"] == 0


def test_unit_coverage_prefers_specific_recruiting_unit_over_group_label() -> None:
    jobs = [
        {
            **_job("cnooc-career-browser", "中国海油-中海油研究总院", "北京", "中国大陆"),
            "canonical_employer_name": "中国海洋石油集团有限公司",
        },
        {
            **_job("cnooc-career-browser", "中国海油-中海油能源发展股份有限公司", "天津", "中国大陆"),
            "canonical_employer_name": "中国海洋石油集团有限公司",
        },
    ]
    result = build_dual_axis_readiness(
        jobs,
        reliability_scorecard=_reliability(),
        domestic_expansion_targets=_targets(target=2, all_gates=False),
        quality_gate={"status": "needs_attention"},
    )

    coverage = result["coverage_axis"]
    assert coverage["employers_or_units"] == 2
    assert coverage["top_employer_share"] == 0.5
