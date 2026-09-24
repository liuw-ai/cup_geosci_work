from __future__ import annotations

import copy
from pathlib import Path

import pytest

from job_hub.sinopec import load_sinopec_capture
from job_hub.sinopec_scan import (
    SinopecScanContractError,
    build_sinopec_scan_plan,
    validate_sinopec_scan_result,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = PROJECT_ROOT / "data" / "verified" / "sinopec-geoscience-20260924.json"


def capture() -> dict[str, object]:
    return load_sinopec_capture(SNAPSHOT)


def test_scan_plan_contains_all_units_and_marks_candidate_detail() -> None:
    payload = capture()
    plan = build_sinopec_scan_plan(payload)

    assert len(plan) == 132
    assert sum(1 for item in plan if item["required_detail"]) == 35
    assert plan[0]["sequence"] == 1
    assert plan[-1]["sequence"] == 132
    assert all(item["detail_url"].startswith("https://job.sinopec.com/") for item in plan)


def test_snapshot_reports_detail_rows_but_not_pagination_completion() -> None:
    result = validate_sinopec_scan_result(capture())

    assert result["enterprise_count"] == 132
    assert result["candidate_count"] == 35
    assert result["job_rows"] == 348
    assert result["rows_bound_to_known_units"] == 348
    assert result["candidate_incomplete_count"] == 35
    assert result["promotion_ready"] is False
    assert result["candidate_status_counts"] == {"success": 35}


def test_strict_candidate_gate_rejects_legacy_snapshot() -> None:
    with pytest.raises(SinopecScanContractError, match="not fully scanned"):
        validate_sinopec_scan_result(capture(), require_candidate_completion=True)


def test_scan_rejects_job_bound_to_unknown_unit() -> None:
    payload = capture()
    mutated = copy.deepcopy(payload)
    mutated["jobs"][0]["detail_url"] = (
        "https://job.sinopec.com/#/school/recruitEnterpriseDetail?deptId=unknown"
    )

    with pytest.raises(SinopecScanContractError, match="unknown Sinopec units"):
        validate_sinopec_scan_result(mutated)


def test_scan_rejects_mismatched_detail_url_and_external_id() -> None:
    payload = capture()
    mutated = copy.deepcopy(payload)
    mutated["jobs"][0]["detail_url"] = (
        "https://job.sinopec.com/#/school/recruitEnterpriseDetail?deptId="
        "EF878D0D-34EC-4A72-A854-1657FB3024F7"
    )

    with pytest.raises(SinopecScanContractError, match="unknown Sinopec units"):
        validate_sinopec_scan_result(mutated)


def test_complete_candidate_metrics_pass_strict_gate() -> None:
    payload = capture()
    mutated = copy.deepcopy(payload)
    for enterprise in mutated["enterprises"]:
        if enterprise.get("candidate_by_keyword"):
            enterprise["scan_metrics"] = {
                "pages_scanned": 1,
                "pages_expected": 1,
                "jobs_discovered": 1,
                "jobs_exported": 1,
                "failed_jobs": 0,
                "pagination_complete": True,
            }
    result = validate_sinopec_scan_result(
        mutated,
        require_candidate_completion=True,
    )
    assert result["promotion_ready"] is True


def test_access_limited_is_terminal_diagnosis_not_completed_scan() -> None:
    payload = capture()
    mutated = copy.deepcopy(payload)
    for enterprise in mutated["enterprises"]:
        if enterprise.get("candidate_by_keyword"):
            enterprise["scan_status"] = "access_limited"
            enterprise["scan_metrics"] = {
                "pages_scanned": 0,
                "pages_expected": None,
                "jobs_discovered": 0,
                "jobs_exported": 0,
                "failed_jobs": 0,
                "pagination_complete": True,
            }

    result = validate_sinopec_scan_result(mutated)
    assert result["candidate_incomplete_count"] == 35
    assert result["promotion_ready"] is False
    with pytest.raises(SinopecScanContractError, match="not fully scanned"):
        validate_sinopec_scan_result(mutated, require_candidate_completion=True)
