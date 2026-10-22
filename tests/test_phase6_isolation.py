from __future__ import annotations

import json

from job_hub.db import Database
from scripts.prepare_phase6_isolated_runtime import create_isolated_runtime, sha256


def test_phase6_isolation_copies_and_migrates_only_the_review_database(tmp_path) -> None:
    source = tmp_path / "formal-local.sqlite3"
    database = Database(source)
    database.initialize()
    source_hash = sha256(source)
    workspace = tmp_path / "runtime" / "phase6" / "review"

    manifest = create_isolated_runtime(
        source_database=source,
        workspace=workspace,
        phase6_root=tmp_path / "runtime" / "phase6",
    )

    assert sha256(source) == source_hash
    assert manifest["source_was_unchanged"] is True
    assert manifest["review_database_integrity"].lower() == "ok"
    assert (workspace / "job_hub.sqlite3").is_file()
    assert "third_party_batches" in manifest["schema_tables"]
    on_disk = json.loads((workspace / "isolation-manifest.json").read_text(encoding="utf-8"))
    assert on_disk["counts_before_phase6_trial"]["jobs"] == 0


def test_phase6_isolation_rejects_nonempty_or_nonstandard_workspaces(tmp_path) -> None:
    source = tmp_path / "formal-local.sqlite3"
    Database(source).initialize()
    outside = tmp_path / "outside-phase6"
    try:
        create_isolated_runtime(
            source_database=source,
            workspace=outside,
            phase6_root=tmp_path / "runtime" / "phase6",
        )
    except ValueError as error:
        assert "runtime/phase6" in str(error)
    else:  # pragma: no cover - guards the safety condition directly
        raise AssertionError("nonstandard Phase 6 workspace was accepted")
