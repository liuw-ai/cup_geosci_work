from __future__ import annotations

import json
from pathlib import Path

from scripts.build_phase4_manifest import local_validation_facts
from scripts.verify_phase4_manifest import verify_manifest


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
    assert facts["formal_candidate_converted_job_count"] == 8
    assert facts["formal_discovery_attributed_job_count"] == 7
    assert facts["formal_third_party_job_count"] == 7


def test_phase4_manifest_recomputes_current_hashes_and_database_facts() -> None:
    result = verify_manifest()

    assert result["ok"] is True, result["errors"]
    assert result["production_deployment"] == "not_performed"
    assert result["automation_status"] == "paused"


def test_phase4_manifest_rejects_changed_bound_evidence_report(tmp_path: Path) -> None:
    manifest = json.loads(
        Path("docs/current/phase4-release-manifest.json").read_text(encoding="utf-8")
    )
    manifest["config_sha256"]["phase4_third_party_funnel"] = "tampered"
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    result = verify_manifest(path)

    assert result["ok"] is False
    assert any("phase4_third_party_funnel" in item for item in result["errors"])


def test_phase4_manifest_binds_live_cnooc_portal_recheck() -> None:
    manifest = json.loads(
        Path("docs/current/phase4-release-manifest.json").read_text(encoding="utf-8")
    )

    assert manifest["config_sha256"]["phase4_cnooc_portal_live_recheck"]
    assert manifest["three_oil_entry_evidence"]["latest_cnooc_portal_live_recheck"] == (
        "docs/current/phase4-cnooc-portal-live-recheck-2026-10-20.json"
    )
    assert manifest["three_oil_entry_evidence"]["latest_official_homepage_entry_recheck"] == (
        "docs/current/phase4-three-oil-official-homepage-entry-recheck-2026-10-20.json"
    )
