"""Build the non-formal three-oil display-roster report.

The organization registry contains names observed on official recruitment
portals that have not yet been independently bound to a legal entity.  This
report keeps those observations searchable without promoting them into the
formal three-oil catalog or treating a shared portal as a unit-specific entry.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "data" / "organization_registry.json"
CATALOG = ROOT / "data" / "three_oil_organization_catalog.json"
OUTPUT = ROOT / "docs" / "current" / "phase4-three-oil-observed-roster-2026-10-20.json"


def main() -> None:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    formal_ids = {
        str(unit.get("id") or "")
        for group in catalog.get("groups", [])
        for unit in group.get("units", [])
        if isinstance(unit, dict)
    }
    observed: list[dict[str, object]] = []
    for organization in registry.get("organizations", []):
        if not isinstance(organization, dict):
            continue
        organization_id = str(organization.get("id") or "").strip()
        identity_status = str(organization.get("identity_status") or "").strip()
        if not organization_id or organization_id in formal_ids:
            continue
        if not organization_id.startswith(("cnpc-display-", "sinopec-display-")):
            continue
        channels = [
            {
                "channel_id": str(channel.get("id") or ""),
                "url": str(channel.get("official_url") or ""),
                "verification_status": str(channel.get("verification_status") or ""),
            }
            for channel in organization.get("channels", [])
            if isinstance(channel, dict)
        ]
        observed.append(
            {
                "id": organization_id,
                "group_id": str(organization.get("parent_id") or "").strip(),
                "display_name": str(
                    organization.get("official_display_name")
                    or organization.get("canonical_name")
                    or ""
                ).strip(),
                "aliases": organization.get("aliases") or [],
                "observed_role": str(
                    organization.get("organization_role") or ""
                ).strip(),
                "identity_status": identity_status or "display_candidate_unverified",
                "official_domains": organization.get("official_domains") or [],
                "channels": channels,
                "evidence_boundary": {
                    "displayed_on_official_recruitment_portal": True,
                    "legal_name_verified": False,
                    "unit_specific_recruitment_entry_verified": False,
                    "eligible_for_formal_catalog": False,
                },
                "next_required_evidence": [
                    "official unit identity page or recruitment notice",
                    "legal-registration or equivalent official identity evidence",
                    "unit-specific recruitment entry or an official notice authorizing the shared portal",
                ],
            }
        )
    observed.sort(key=lambda item: (str(item["group_id"]), str(item["id"])))
    by_group: dict[str, int] = {}
    for item in observed:
        group_id = str(item["group_id"])
        by_group[group_id] = by_group.get(group_id, 0) + 1
    payload = {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "phase": "phase4",
        "status": "observed_roster_not_formal_catalog",
        "source": {
            "registry": "data/organization_registry.json",
            "catalog": "data/three_oil_organization_catalog.json",
            "scope": "official recruitment portal display candidates with pending legal normalization",
        },
        "summary": {
            "observed_candidate_count": len(observed),
            "observed_candidate_count_by_group": by_group,
            "formal_catalog_unit_count_excluded": len(formal_ids),
            "legal_identity_verified_count": 0,
            "unit_specific_entry_verified_count": 0,
            "formal_catalog_additions": 0,
        },
        "policy": {
            "display_name_is_not_legal_name": True,
            "shared_group_portal_is_not_unit_entry": True,
            "observations_are_not_student_application_links": True,
            "no_jobs_created": True,
        },
        "units": observed,
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(OUTPUT)


if __name__ == "__main__":
    main()
