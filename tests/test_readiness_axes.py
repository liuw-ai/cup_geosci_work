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
