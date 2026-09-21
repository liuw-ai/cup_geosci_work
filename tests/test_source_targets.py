from __future__ import annotations

import copy
import json

import pytest

from job_hub.source_targets import load_source_targets, role_label, target_matrix_summary


def test_source_target_matrix_keeps_unverified_entries_out_of_verified_count() -> None:
    matrix = load_source_targets()
    summary = target_matrix_summary(
        matrix,
        source_ids={"beijing-hrss", "beijing-natural-resources"},
    )

    assert summary["province_count"] == 31
    assert summary["totals"]["targets"] == 155
    beijing = next(item for item in summary["provinces"] if item["province"] == "北京")
    assert beijing["verified_targets"] == 2
    assert "civil_service" in beijing["missing_target_roles"]


def test_role_labels_keep_internal_ids_out_of_student_facing_views() -> None:
    assert role_label("human_resources_or_exam") == "人社/考试"
    assert role_label("geology_bureau_or_institute") == "地质局/地质院"


def test_candidate_source_id_is_only_valid_for_candidate_targets(tmp_path) -> None:
    payload = copy.deepcopy(load_source_targets())
    payload["province_targets"]["山东"]["human_resources_or_exam"] = {
        "state": "verified",
        "source_id": "some-source",
        "candidate_source_id": "shandong-hrss-exam",
    }
    path = tmp_path / "source_targets.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="candidate_source_id is only valid for candidate"):
        load_source_targets(path)
