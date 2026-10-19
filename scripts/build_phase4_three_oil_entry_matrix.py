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
REGISTRY = ROOT / "data" / "organization_registry.json"
ENTRY_EVIDENCE = ROOT / "docs" / "current" / "phase4-three-oil-entry-evidence-2026-10-18.json"
CNOOC_UNIT_EVIDENCE = ROOT / "docs" / "current" / "phase4-cnooc-unit-display-evidence-2026-10-19.json"
NEGATIVE_ENTRY_EVIDENCE = ROOT / "docs" / "current" / "phase4-three-oil-cooec-entry-negative-evidence-2026-10-19.json"
BGP_ENTRY_RECHECK = ROOT / "docs" / "current" / "phase4-cnpc-bgp-entry-recheck-2026-10-19.json"
SINOPEC_PAGE_ROSTER = ROOT / "docs" / "current" / "phase4-sinopec-page-roster-2026-10-19.json"
OUTPUT = ROOT / "docs" / "current" / "phase4-three-oil-unit-entry-matrix-2026-10-18.json"

# These aliases are limited to names whose group-portal display text is
# unambiguous against the formal catalog.  Similar-looking "石油局" or
# "石油工程" labels are intentionally left unmatched until legal identity
# evidence binds them to a catalog unit.
SINOPEC_DISPLAY_ALIASES = {
    "sinopec-shengli": {"胜利油田"},
    "sinopec-zhongyuan": {"中原油田"},
    "sinopec-henan": {"河南油田"},
    "sinopec-jianghan": {"江汉油田"},
    "sinopec-jiangsu": {"江苏油田"},
    "sinopec-petroleum-exploration-production-research": {"石油勘探开发研究院"},
    "sinopec-petroleum-engineering-research": {"石油工程技术研究院"},
    "sinopec-petroleum-machinery": {"石油机械股份有限公司"},
    "sinopec-engineering-construction": {"石油工程建设有限公司"},
    "sinopec-international-exploration": {"国际石油勘探公司"},
}


def registry_recruitment_entry(
    registry: dict[str, object],
    *,
    unit_id: str,
    group_entry_url: str | None,
) -> dict[str, str] | None:
    """Return a verified unit-specific recruitment channel, never a homepage.

    The registry may contain official homepages and the shared group portal.
    Neither proves a unit recruitment entry.  Only a verified recruitment
    channel with a URL different from the group portal is eligible here.
    """

    organizations = registry.get("organizations") if isinstance(registry, dict) else None
    if not isinstance(organizations, list):
        return None
    shared = str(group_entry_url or "").strip().rstrip("/")
    for organization in organizations:
        if not isinstance(organization, dict) or str(organization.get("id") or "") != unit_id:
            continue
        channels = organization.get("channels")
        if not isinstance(channels, list):
            continue
        candidates: list[dict[str, str]] = []
        for channel in channels:
            if not isinstance(channel, dict):
                continue
            status = str(channel.get("verification_status") or "").strip()
            channel_type = str(channel.get("channel_type") or "").strip()
            url = str(channel.get("official_url") or "").strip()
            if (
                status in {"official_confirmed", "automation_ready"}
                and channel_type in {"campus_recruitment", "official_announcement"}
                and url.startswith(("http://", "https://"))
                and url.rstrip("/") != shared
            ):
                candidates.append(
                    {
                        "url": url,
                        "channel_id": str(channel.get("id") or ""),
                        "channel_type": channel_type,
                        "verification_status": status,
                    }
                )
        if candidates:
            candidates.sort(key=lambda item: (item["channel_type"], item["url"]))
            return candidates[0]
    return None


def registry_shared_group_entry(
    registry: dict[str, object],
    *,
    unit_id: str,
    group_entry_url: str | None,
) -> dict[str, str] | None:
    """Return an explicitly verified channel that is exactly the group portal."""

    organizations = registry.get("organizations") if isinstance(registry, dict) else None
    if not isinstance(organizations, list):
        return None
    shared = str(group_entry_url or "").strip().rstrip("/")
    if not shared:
        return None
    for organization in organizations:
        if not isinstance(organization, dict) or str(organization.get("id") or "") != unit_id:
            continue
        for channel in organization.get("channels", []):
            if not isinstance(channel, dict):
                continue
            url = str(channel.get("official_url") or "").strip()
            status = str(channel.get("verification_status") or "").strip()
            if (
                status in {"official_confirmed", "automation_ready"}
                and str(channel.get("channel_type") or "").strip() == "campus_recruitment"
                and url.startswith(("http://", "https://"))
                and url.rstrip("/") == shared
            ):
                return {
                    "url": url,
                    "channel_id": str(channel.get("id") or ""),
                    "channel_type": str(channel.get("channel_type") or ""),
                    "verification_status": status,
                }
    return None


def main() -> None:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    evidence = json.loads(ENTRY_EVIDENCE.read_text(encoding="utf-8"))
    cnooc_unit_evidence = json.loads(CNOOC_UNIT_EVIDENCE.read_text(encoding="utf-8"))
    negative_entry_evidence = json.loads(NEGATIVE_ENTRY_EVIDENCE.read_text(encoding="utf-8"))
    bgp_entry_recheck = json.loads(BGP_ENTRY_RECHECK.read_text(encoding="utf-8"))
    sinopec_roster = json.loads(SINOPEC_PAGE_ROSTER.read_text(encoding="utf-8"))
    negative_entry_by_catalog_id = {
        str(negative_entry_evidence.get("catalog_id") or "").strip(): negative_entry_evidence
    }
    rechecked_unit_entry_ids = {str(bgp_entry_recheck.get("catalog_id") or "").strip()} - {""}
    cnooc_observations = {
        str(item.get("canonical_name") or "").strip(): item
        for item in cnooc_unit_evidence.get("units", [])
        if isinstance(item, dict) and str(item.get("canonical_name") or "").strip()
    }
    displayed_cnooc = {
        str(item).strip()
        for item in evidence.get("cnooc", {}).get("displayed_units", [])
        if str(item).strip()
    }
    sinopec_display_records = {
        str(item.get("display_name") or "").strip(): item
        for item in sinopec_roster.get("records", [])
        if isinstance(item, dict) and str(item.get("display_name") or "").strip()
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
            display_aliases = SINOPEC_DISPLAY_ALIASES.get(unit_id, set())
            sinopec_display = next(
                (
                    sinopec_display_records[alias]
                    for alias in display_aliases
                    if alias in sinopec_display_records
                ),
                None,
            ) if group_id == "sinopec" else None
            displayed = unit_name in displayed_cnooc if group_id == "cnooc" else bool(sinopec_display)
            unit_entry = None if is_group else registry_recruitment_entry(
                registry,
                unit_id=unit_id,
                group_entry_url=group_entry_url,
            )
            shared_entry = None if is_group else registry_shared_group_entry(
                registry,
                unit_id=unit_id,
                group_entry_url=group_entry_url,
            )
            if is_group:
                unit_status = "group_portal_verified" if group_status == "official_portal_verified" else "group_portal_not_revalidated"
            elif unit_entry:
                unit_status = "unit_specific_entry_verified"
            elif unit_id in negative_entry_by_catalog_id:
                unit_status = "official_site_checked_no_unit_entry_found"
            elif displayed:
                unit_status = "group_portal_unit_display_observed"
            elif shared_entry:
                unit_status = "group_portal_registered"
            else:
                unit_status = "unit_specific_entry_pending"
            observed = cnooc_observations.get(unit_name) if group_id == "cnooc" else sinopec_display
            rows.append(
                {
                    "catalog_id": unit_id,
                    "group_id": group_id,
                    "canonical_name": unit_name,
                    "catalog_role": unit.get("role"),
                    "group_entry_url": group_entry_url,
                    "group_entry_status": group_status,
                    "unit_specific_entry_url": unit_entry["url"] if unit_entry else None,
                    "unit_specific_entry_channel_id": unit_entry["channel_id"] if unit_entry else None,
                    "unit_specific_entry_channel_type": unit_entry["channel_type"] if unit_entry else None,
                    "unit_specific_entry_verification_status": unit_entry["verification_status"] if unit_entry else None,
                    "shared_group_entry_channel_id": shared_entry["channel_id"] if shared_entry else None,
                    "shared_group_entry_verification_status": shared_entry["verification_status"] if shared_entry else None,
                    "unit_specific_entry_status": unit_status,
                    "official_display_name_observed": displayed,
                    "observed_official_job_count": observed.get("job_count") if observed else None,
                    "observed_official_application_url": observed.get("first_official_application_url") if observed else None,
                    "observed_official_application_url_count": observed.get("unique_official_application_url_count") if observed else 0,
                    "observed_job_evidence_file": (
                        "docs/current/phase4-cnooc-unit-job-evidence-2026-10-19.json"
                        if group_id == "cnooc" and observed
                        else None
                    ),
                    "observed_job_summary_evidence_file": (
                        "docs/current/phase4-cnooc-unit-display-evidence-2026-10-19.json"
                        if group_id == "cnooc" and observed
                        else "docs/current/phase4-sinopec-page-roster-2026-10-19.json"
                        if group_id == "sinopec" and observed
                        else None
                    ),
                    "observed_job_evidence_scope": (
                        "official_group_portal_active_unit_job_cards"
                        if group_id == "cnooc" and observed
                        else "official_group_portal_display_roster"
                        if group_id == "sinopec" and observed
                        else None
                    ),
                    "evidence_scope": (
                        "official_registry_recruitment_channel"
                        if unit_entry
                        else "official_homepage_no_recruitment_entry_found"
                        if unit_id in negative_entry_by_catalog_id
                        else "official_group_portal_display_roster"
                        if displayed
                        else "official_registry_shared_group_entry"
                        if shared_entry
                        else "official_group_portal"
                        if is_group
                        else "no_unit_specific_entry_evidence"
                    ),
                    "verified_at": (
                        "2026-10-19"
                        if observed or unit_id in negative_entry_by_catalog_id or unit_id in rechecked_unit_entry_ids
                        else "2026-10-18"
                    ),
                    "next_evidence_required": (
                        None
                        if is_group
                        else None
                        if unit_entry
                        else negative_entry_by_catalog_id[unit_id].get("evidence_boundary", {}).get("next_evidence_required")
                        if unit_id in negative_entry_by_catalog_id
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
            "cnooc_group_portal_units_with_active_job_evidence": sum(
                1 for row in rows if row["group_id"] == "cnooc" and row["observed_official_job_count"]
            ),
            "cnooc_observed_job_cards": sum(
                int(row["observed_official_job_count"] or 0)
                for row in rows
                if row["group_id"] == "cnooc"
            ),
        },
        "units": rows,
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
