from __future__ import annotations

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
