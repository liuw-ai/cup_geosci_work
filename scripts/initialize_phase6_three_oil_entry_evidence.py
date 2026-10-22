"""Promote the reviewed Phase 4 entry matrix into the current data ledger.

The script performs a deterministic format migration only. It does not probe
the network, create sources, enable collectors, or claim new unit entrances.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from job_hub.three_oil_entry_evidence import (
    ENTRY_EVIDENCE_PATH,
    validate_three_oil_entry_evidence,
)


LEGACY_MATRIX = ROOT / "docs" / "current" / "phase4-three-oil-unit-entry-matrix-2026-10-18.json"


def build_payload(legacy_path: Path = LEGACY_MATRIX) -> dict[str, object]:
    legacy_bytes = legacy_path.read_bytes()
    legacy = json.loads(legacy_bytes.decode("utf-8"))
    raw_units = legacy.get("units")
    if not isinstance(raw_units, list):
        raise ValueError("legacy three-oil entry matrix has no units list")
    units: list[dict[str, object]] = []
    for raw in raw_units:
        if not isinstance(raw, dict):
            raise ValueError("legacy three-oil entry matrix contains invalid unit")
        item = dict(raw)
        # Old group-level rows used a descriptive status not retained in the
        # current six-state contract. A verified group portal is registered,
        # but still not a subordinate-unit-specific entrance.
        if item.get("unit_specific_entry_status") == "group_portal_verified":
            item["unit_specific_entry_status"] = "group_portal_registered"
        evidence_files = [
            str(value).replace("\\", "/")
            for key in ("observed_job_evidence_file", "observed_job_summary_evidence_file")
            if (value := item.get(key))
        ]
        item["evidence_files"] = evidence_files
        item["checked_at"] = item.get("verified_at")
        item["checked_url"] = item.get("unit_specific_entry_url") or item.get("group_entry_url")
        item["official_identity_url"] = None
        item["notes"] = (
            "Migrated from the reviewed Phase 4 unit-entry matrix. "
            "No network revalidation or entry promotion was performed during this migration."
        )
        units.append(item)
    return validate_three_oil_entry_evidence(
        {
            "version": 1,
            "generated_at": "2026-10-23T00:00:00+08:00",
            "policy_version": "phase6-three-oil-entry-evidence-v1",
            "source_matrix": {
                "path": legacy_path.relative_to(ROOT).as_posix(),
                "sha256": hashlib.sha256(legacy_bytes).hexdigest(),
                "migration": "group_portal_verified -> group_portal_registered",
            },
            "units": units,
        }
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ENTRY_EVIDENCE_PATH)
    parser.add_argument("--check", action="store_true", help="validate without writing")
    args = parser.parse_args()
    payload = build_payload()
    rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.check:
        print(rendered)
        return
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
