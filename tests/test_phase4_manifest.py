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
