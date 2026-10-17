"""Build a read-only reconciliation report for the three-oil organization scope.

The report deliberately does not promote registry display names into the formal
three-oil catalog.  Every non-catalog record remains an evidence task until an
official group page, legal-registration record, or official recruitment notice
binds the name and role.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = ROOT / "data" / "three_oil_organization_catalog.json"
REGISTRY_PATH = ROOT / "data" / "organization_registry.json"
OUTPUT_PATH = ROOT / "docs" / "current" / "phase4-organization-reconciliation-2026-10-17.json"
OIL_AFFILIATIONS = {"中国石油体系", "中国石化体系", "中国海油体系"}
OIL_PREFIXES = ("cnpc", "sinopec", "cnooc")


def main() -> None:
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    catalog_units = [unit for group in catalog["groups"] for unit in group["units"]]
    catalog_ids = {str(unit["id"]) for unit in catalog_units}
    catalog_names = {str(unit["name"]).strip() for unit in catalog_units}

    records: list[dict[str, object]] = []
    for organization in registry.get("organizations", []):
        organization_id = str(organization.get("id") or "").strip()
        affiliation = str(organization.get("affiliation") or "").strip()
        if affiliation not in OIL_AFFILIATIONS and not organization_id.startswith(OIL_PREFIXES):
            continue
        channels = organization.get("channels") if isinstance(organization.get("channels"), list) else []
        if organization_id in catalog_ids or str(organization.get("canonical_name") or "").strip() in catalog_names:
            scope_status = "formal_catalog"
        elif "-display-" in organization_id:
            scope_status = "display_candidate_unverified"
        else:
            scope_status = "registry_only_requires_official_evidence"
        records.append(
            {
                "id": organization_id,
                "canonical_name": str(organization.get("canonical_name") or "").strip(),
                "parent_id": organization.get("parent_id"),
                "parent_name": organization.get("parent_name"),
                "affiliation": affiliation,
                "organization_role_legacy": organization.get("organization_role"),
                "scope_status": scope_status,
                "channel_count": len(channels),
                "channel_verification_statuses": dict(
                    Counter(str(channel.get("verification_status") or "unknown") for channel in channels)
                ),
                "channel_ids": [str(channel.get("id") or "") for channel in channels if channel.get("id")],
                "official_urls": [
                    str(channel.get("official_url") or "")
                    for channel in channels
                    if str(channel.get("official_url") or "").strip()
                ],
                "evidence_required": scope_status != "formal_catalog",
            }
        )

    records.sort(key=lambda item: (str(item["affiliation"]), str(item["id"])))
    summary = {
        "catalog_group_count": len(catalog.get("groups", [])),
        "catalog_unit_count": len(catalog_units),
        "registry_related_organization_count": len(records),
        "scope_status_counts": dict(Counter(str(item["scope_status"]) for item in records)),
        "catalog_units_by_group": {
            str(group["id"]): len(group.get("units", [])) for group in catalog.get("groups", [])
        },
        "registry_channel_verification_counts": dict(
            Counter(
                status
                for item in records
                for status, count in (item["channel_verification_statuses"] or {}).items()
                for _ in range(int(count))
            )
        ),
    }
    payload = {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "purpose": "Phase 4 read-only reconciliation; non-catalog records are not production inputs.",
        "external_evidence_policy": "Do not promote a display or registry name without an official group page, legal-registration record, or official recruitment evidence.",
        "summary": summary,
        "records": records,
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
