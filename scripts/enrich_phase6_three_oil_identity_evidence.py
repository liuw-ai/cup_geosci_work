"""Backfill unit-owned official identity pages into the Phase 6 entry ledger.

The reviewed organization registry already contains a small subset of
officially confirmed unit homepages.  This migration copies only a unit's own
homepage, never a shared group homepage assigned to a regional branch.  It
does not probe the network, register collectors, or upgrade recruitment-entry
status.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from job_hub.organizations import load_organization_registry
from job_hub.three_oil_catalog import load_three_oil_catalog
from job_hub.three_oil_entry_evidence import (
    ENTRY_EVIDENCE_PATH,
    load_three_oil_entry_evidence,
    validate_three_oil_entry_evidence,
)


def _host(url: str | None) -> str:
    return (urlparse(str(url or "")).hostname or "").lower().rstrip(".")


def build_enriched_payload() -> dict[str, object]:
    ledger = load_three_oil_entry_evidence()
    registry = {
        str(item.get("id") or ""): item
        for item in load_organization_registry().get("organizations", [])
        if isinstance(item, dict)
    }
    catalog = load_three_oil_catalog()
    group_hosts = {
        str(group["id"]): _host(str(group["official_identity_url"]))
        for group in catalog["groups"]
    }
    units: list[dict[str, object]] = []
    for original in ledger["units"]:
        item = dict(original)
        organization = registry.get(str(item["catalog_id"]))
        if item.get("official_identity_url") or not isinstance(organization, dict):
            units.append(item)
            continue
        homepage = next(
            (
                channel
                for channel in organization.get("channels", [])
                if isinstance(channel, dict)
                and channel.get("channel_type") == "official_homepage"
                and channel.get("verification_status") == "official_confirmed"
                and str(channel.get("official_url") or "").strip()
            ),
            None,
        )
        if not isinstance(homepage, dict):
            units.append(item)
            continue
        candidate_url = str(homepage["official_url"]).strip()
        candidate_host = _host(candidate_url)
        # A group record can naturally use its group homepage. For every
        # subordinate unit, the same group host is a shared platform, not a
        # unit-specific identity page.
        if (
            item["catalog_id"] != item["group_id"]
            and candidate_host == group_hosts.get(str(item["group_id"]))
        ):
            units.append(item)
            continue
        item["official_identity_url"] = candidate_url
        item["official_identity_channel_id"] = str(homepage.get("id") or "") or None
        item["official_identity_evidence_note"] = str(
            homepage.get("evidence_note") or ""
        ).strip() or None
        previous_note = str(item.get("notes") or "").strip()
        suffix = (
            "官方身份页已从 organization_registry 的官方主页频道回填；"
            "本次未进行网络复测，且未提升招聘入口状态。"
        )
        item["notes"] = f"{previous_note} {suffix}".strip()
        units.append(item)
    result = dict(ledger)
    result["units"] = units
    result["identity_backfill"] = {
        "policy": (
            "Only official_confirmed unit-owned homepage channels are copied. "
            "A group homepage cannot become a subsidiary identity page."
        ),
        "source": "data/organization_registry.json",
        "network_access": False,
    }
    return validate_three_oil_entry_evidence(result)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ENTRY_EVIDENCE_PATH)
    parser.add_argument("--check", action="store_true", help="render without writing")
    args = parser.parse_args()
    payload = build_enriched_payload()
    rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.check:
        print(rendered)
        return
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
