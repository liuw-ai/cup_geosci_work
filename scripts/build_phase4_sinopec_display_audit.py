"""Record the Sinopec portal display roster without promoting it to the catalog."""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUTPUT = ROOT / "docs" / "current" / "phase4-sinopec-display-units-2026-10-18.json"
REGISTRY = ROOT / "data" / "organization_registry.json"
CATALOG = ROOT / "data" / "three_oil_organization_catalog.json"


def normalize(value: str) -> str:
    return re.sub(r"[\s（）()·、,，./\\-]", "", value or "").lower()


def main() -> None:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    records = [
        item
        for item in registry.get("organizations", [])
        if str(item.get("id", "")).startswith("sinopec-display-")
    ]
    catalog_units = [
        unit
        for group in catalog.get("groups", [])
        if group.get("id") == "sinopec"
        for unit in group.get("units", [])
    ]
    catalog_names = {
        normalize(str(unit.get("name", ""))): str(unit.get("id", ""))
        for unit in catalog_units
    }
    rows = []
    for item in sorted(records, key=lambda value: str(value.get("id", ""))):
        display_name = str(item.get("official_display_name") or item.get("canonical_name") or "").strip()
        rows.append(
            {
                "registry_id": item.get("id"),
                "display_name": display_name,
                "identity_status": item.get("identity_status"),
                "formal_catalog_match": catalog_names.get(normalize(display_name)),
                "portal_url": "https://job.sinopec.com/",
                "evidence_scope": "official_portal_display_roster",
                "promotion_status": "display_candidate_only",
            }
        )
    report = {
        "phase": "phase4",
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "source": {
            "portal_url": "https://job.sinopec.com/",
            "observed_display_unit_total": 132,
            "retained_registry_record_count": len(rows),
            "missing_from_registry_count": max(132 - len(rows), 0),
            "observation_note": "132 is the portal's observed display count. Only retained records are listed below; no missing names are invented.",
        },
        "catalog_scope": {
            "formal_sinopec_catalog_count": len(catalog_units),
            "matched_display_records": sum(1 for row in rows if row["formal_catalog_match"]),
            "unmatched_display_records": sum(1 for row in rows if not row["formal_catalog_match"]),
            "promotion_rule": "A display name is not a formal unit or independent recruitment entry without legal/official identity evidence.",
        },
        "records": rows,
    }
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(OUTPUT)


if __name__ == "__main__":
    main()
