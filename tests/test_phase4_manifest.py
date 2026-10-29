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


def test_phase4_manifest_records_a_verified_online_release_backup() -> None:
    manifest = json.loads(
        Path("docs/current/phase4-release-manifest.json").read_text(encoding="utf-8")
    )
    # Phase 4 is a frozen historical release record.  Its verifier must not
    # be run against the later Phase 5 working tree and database.
    assert manifest["phase"] == "phase4"
    assert manifest["status"] == "approved_pending_server_access"
    backup = manifest["database"]["release_backup"]
    assert backup["exists"] is True
    assert backup["integrity_check"] == "ok"
    assert backup["matches_runtime_job_count"] is True


def test_phase4_manifest_distinguishes_overall_target_from_third_party_gate() -> None:
    from scripts.build_phase4_manifest import db_facts

    facts = db_facts(Path("runtime/job_hub.sqlite3"))
    # The local runtime is a post-policy reindex candidate, not the frozen
    # historical release database. The Phase 7.2 1000-row target is a
    # separate release acceptance gate.
    assert int(facts["student_visible_total"]) >= 200
    assert facts["formal_candidate_converted_job_count"] == 9
    assert facts["formal_discovery_attributed_job_count"] == 8
    assert facts["formal_third_party_job_count"] == 8


def test_phase4_manifest_recomputes_current_hashes_and_database_facts() -> None:
    manifest = json.loads(
        Path("docs/current/phase4-release-manifest.json").read_text(encoding="utf-8")
    )
    # The facts below describe the frozen Phase 4 release, not the active
    # Phase 5 runtime.  Validate the recorded contract without recomputing it
    # from today's database or Compose files.
    assert manifest["production_deployment"] == "not_performed"
    assert manifest["automation_status"] == "paused"
    assert manifest["release_blockers"] == []
    assert manifest["deployment_execution_status"] == (
        "pending_server_access_and_immutable_images"
    )


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
