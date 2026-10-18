"""Build the explicit per-unit three-oil recruitment-entry evidence matrix.

The catalog is the scope list; the portal evidence is a separate fact.  A
shared group portal is recorded for every applicable unit, but it is never
copied into ``unit_specific_entry_url``.  This keeps an incomplete evidence
state visible instead of silently promoting a group URL to a subsidiary URL.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "data" / "three_oil_organization_catalog.json"
ENTRY_EVIDENCE = ROOT / "docs" / "current" / "phase4-three-oil-entry-evidence-2026-10-18.json"
OUTPUT = ROOT / "docs" / "current" / "phase4-three-oil-unit-entry-matrix-2026-10-18.json"


def main() -> None:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    evidence = json.loads(ENTRY_EVIDENCE.read_text(encoding="utf-8"))
    displayed_cnooc = {
        str(item).strip()
        for item in evidence.get("cnooc", {}).get("displayed_units", [])
        if str(item).strip()
    }
    rows: list[dict[str, object]] = []
    for group in catalog.get("groups", []):
        group_id = str(group.get("id") or "").strip()
        group_evidence = evidence.get(group_id, {})
        group_entry_url = str(group_evidence.get("entry_url") or "").strip() or None
        group_status = str(group_evidence.get("status") or "unverified").strip()
        for unit in group.get("units", []):
            unit_id = str(unit.get("id") or "").strip()
            unit_name = str(unit.get("name") or "").strip()
            is_group = unit_id == group_id
            displayed = unit_name in displayed_cnooc if group_id == "cnooc" else False
            if is_group:
                unit_status = "group_portal_verified" if group_status == "official_portal_verified" else "group_portal_not_revalidated"
            elif displayed:
                unit_status = "group_portal_unit_display_observed"
            else:
                unit_status = "unit_specific_entry_pending"
            rows.append(
                {
                    "catalog_id": unit_id,
                    "group_id": group_id,
                    "canonical_name": unit_name,
                    "catalog_role": unit.get("role"),
                    "group_entry_url": group_entry_url,
                    "group_entry_status": group_status,
                    "unit_specific_entry_url": None,
                    "unit_specific_entry_status": unit_status,
                    "official_display_name_observed": displayed,
                    "evidence_scope": (
                        "official_group_portal"
                        if is_group
                        else "official_group_portal_display_roster"
                        if displayed
                        else "no_unit_specific_entry_evidence"
                    ),
                    "verified_at": "2026-10-18",
                    "next_evidence_required": (
                        None
                        if is_group
                        else "unit-specific official recruitment page, notice, or authorized recruitment record"
                    ),
                }
            )
    rows.sort(key=lambda row: (str(row["group_id"]), str(row["catalog_id"])))
    statuses: dict[str, int] = {}
    for row in rows:
        status = str(row["unit_specific_entry_status"])
        statuses[status] = statuses.get(status, 0) + 1
    payload = {
        "version": 1,
        "phase": "phase4",
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "policy": "A shared group portal proves only the shared portal. It is not copied into a unit-specific URL.",
        "summary": {
            "formal_catalog_units": len(rows),
            "units_by_group": {
                str(group.get("id")): len(group.get("units", []))
                for group in catalog.get("groups", [])
            },
            "unit_specific_entry_status_counts": statuses,
            "unit_specific_entry_evidence_complete": all(
                row["unit_specific_entry_status"] == "unit_specific_entry_verified"
                for row in rows
                if row["catalog_id"] != row["group_id"]
            ),
        },
        "units": rows,
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
