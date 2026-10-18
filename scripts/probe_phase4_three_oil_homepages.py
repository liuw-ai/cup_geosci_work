"""Run a bounded, read-only probe of registered three-oil official homepages.

This is an identity/entry-health report only.  It does not create sources,
jobs, candidate leads, or recruitment channels.  A recruitment-looking link
found on a homepage remains evidence for manual review until its unit scope
and official recruitment semantics are separately verified.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from job_hub.entry_probes import PublicEntryProbeRunner

CATALOG = ROOT / "data" / "three_oil_organization_catalog.json"
REGISTRY = ROOT / "data" / "organization_registry.json"
OUTPUT = ROOT / "docs" / "current" / "phase4-three-oil-homepage-probe-2026-10-19.json"


def load_homepage_rows() -> list[dict[str, str]]:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    catalog_ids = {
        str(unit["id"])
        for group in catalog.get("groups", [])
        for unit in group.get("units", [])
    }
    rows: list[dict[str, str]] = []
    for organization in registry.get("organizations", []):
        organization_id = str(organization.get("id") or "")
        if organization_id not in catalog_ids:
            continue
        for channel in organization.get("channels", []):
            if str(channel.get("channel_type") or "") != "official_homepage":
                continue
            url = str(channel.get("official_url") or "").strip()
            if not url:
                continue
            rows.append(
                {
                    "catalog_id": organization_id,
                    "canonical_name": str(organization.get("canonical_name") or ""),
                    "url": url,
                    "channel_id": str(channel.get("id") or ""),
                }
            )
    return rows


def main() -> None:
    rows = load_homepage_rows()
    unique_urls = sorted({row["url"] for row in rows})
    entries = [
        {
            "entry_id": f"homepage-{index:03d}",
            "url": url,
            "official_hosts": [(urlparse(url).hostname or "").lower()],
        }
        for index, url in enumerate(unique_urls, start=1)
    ]
    target = {
        "systems": [
            {
                "system_id": "three-oil-homepage-probe",
                "source_id": "three-oil-homepage-probe",
                "entries": entries,
                "recruitment_keywords": [
                    "招聘",
                    "人才",
                    "校招",
                    "校园",
                    "职位",
                    "career",
                    "recruit",
                ],
                "max_links": 40,
            }
        ]
    }
    runner = PublicEntryProbeRunner(
        timeout_seconds=5,
        retries=0,
        transport_mode="environment",
    )
    observed_on = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    run = runner.run(target, observed_on=observed_on, environment="local-read-only")
    by_url = {str(item["url"]): item for item in run["attempts"]}
    unit_rows = []
    for row in rows:
        observation = by_url[row["url"]]
        unit_rows.append(
            {
                **row,
                "classification": observation["classification"],
                "robots_url": observation["robots_url"],
                "robots_http_status": observation["robots_http_status"],
                "robots_allowed": observation["robots_allowed"],
                "http_status": observation["http_status"],
                "final_url": observation["final_url"],
                "recruitment_links": observation["recruitment_links"],
                "error_class": observation["error_class"],
                "error_detail": observation["error_detail"],
                "entry_evidence_status": (
                    "homepage_reachable_recruitment_link_found"
                    if observation["recruitment_links"]
                    else "homepage_reachable_without_recruitment_link"
                    if observation["classification"] == "accessible_html"
                    else "homepage_not_verified"
                ),
            }
        )
    classifications: dict[str, int] = {}
    for item in unit_rows:
        key = item["classification"]
        classifications[key] = classifications.get(key, 0) + 1
    payload = {
        "version": 1,
        "phase": "phase4",
        "generated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "observed_on": observed_on,
        "scope": "registered_three_oil_official_homepages_only",
        "policy": (
            "只检查已登记官方主页的 robots、可达性和公开链接；不采集岗位、不下载附件、"
            "不创建来源或招聘入口，不把主页或主页中的招聘关键词自动升级为单位招聘入口。"
        ),
        "summary": {
            "registered_homepage_records": len(rows),
            "unique_homepage_urls": len(unique_urls),
            "classification_counts": dict(sorted(classifications.items())),
            "units_with_recruitment_links": sum(
                bool(item["recruitment_links"]) for item in unit_rows
            ),
            "jobs_collected": 0,
            "formal_catalog_changed": False,
        },
        "probe_run": run,
        "units": unit_rows,
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
