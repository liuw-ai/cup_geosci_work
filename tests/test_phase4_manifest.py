from __future__ import annotations

import json
from pathlib import Path

from scripts.build_phase4_manifest import local_validation_facts


def test_phase4_manifest_builder_reads_isolated_validation_report(tmp_path: Path) -> None:
    report = tmp_path / "validation.json"
    report.write_text(
        json.dumps(
            {
                "tests": {"passed": 1051, "failed": 0},
                "release_blockers": ["browser_permission_blocked"],
            }
        ),
        encoding="utf-8",
    )

    facts = local_validation_facts(report)

    assert facts == {
        "tests": {"passed": 1051, "failed": 0},
        "release_blockers": ["browser_permission_blocked"],
    }


def test_phase4_manifest_builder_does_not_invent_missing_validation(tmp_path: Path) -> None:
    assert local_validation_facts(tmp_path / "missing.json") is None


def test_phase4_manifest_database_facts_separate_full_layer_counts_from_student_read(
    tmp_path: Path,
) -> None:
    from scripts.build_phase4_manifest import db_facts

    source = Path("runtime/job_hub.sqlite3")
    facts = db_facts(source)

    assert facts["integrity_check"] == "ok"
    assert facts["job_count"] == sum(facts["status_counts"].values())
    assert facts["student_visible_total"] == sum(
        facts["student_visible_layer_counts"].values()
    )
    assert facts["student_visible_total"] <= sum(facts["publication_layer_counts"].values())


def test_phase4_manifest_distinguishes_overall_target_from_third_party_gate() -> None:
    from scripts.build_phase4_manifest import db_facts

    facts = db_facts(Path("runtime/job_hub.sqlite3"))
    assert int(facts["student_visible_total"]) >= 500
