"""Build a read-only evidence report for Huatu table links.

This script intentionally stops at link discovery.  It does not download a
third-party attachment, create a candidate lead, or write the runtime
database.  The subsequent table importer performs a separate robots check and
retains bytes only when the access policy permits it.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from job_hub.third_party_tables import discover_table_links, load_table_adapters


ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "docs" / "current" / "phase4-third-party-huatu-table-discovery-2026-10-19.json"
SOURCE_ID = "huatu-discovery"
USER_AGENT = "job-hub-phase4-table-review/1.0"
# Article pages are explicit evidence targets from the Phase 4 trial/recheck;
# the script does not crawl arbitrary article URLs or follow unregistered hosts.
ARTICLE_URLS = (
    "https://sydw.huatu.com/2026/1016/2875151.html",
    "https://sydw.huatu.com/2026/1016/2875231.html",
    "https://sydw.huatu.com/2026/1016/2875238.html",
    "https://sydw.huatu.com/2026/0717/2868457.html",
)


def _robots(url: str) -> dict[str, object]:
    parsed = urlparse(url)
    root = f"{parsed.scheme}://{parsed.netloc}"
    robots_url = urljoin(root, "/robots.txt")
    checked_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    try:
        response = requests.get(robots_url, headers={"User-Agent": USER_AGENT}, timeout=15)
        if response.status_code == 404:
            return {"robots_url": robots_url, "status": 404, "allowed": True, "checked_at": checked_at}
        if not 200 <= response.status_code < 300:
            return {
                "robots_url": robots_url,
                "status": response.status_code,
                "allowed": False,
                "checked_at": checked_at,
                "reason": "robots_not_verifiable",
            }
        parser = RobotFileParser()
        parser.set_url(robots_url)
        parser.parse(response.text.splitlines())
        allowed = bool(parser.can_fetch(USER_AGENT, url))
        return {
            "robots_url": robots_url,
            "status": response.status_code,
            "allowed": allowed,
            "checked_at": checked_at,
            "reason": "permitted" if allowed else "disallowed",
        }
    except requests.RequestException as error:
        return {
            "robots_url": robots_url,
            "status": None,
            "allowed": False,
            "checked_at": checked_at,
            "reason": f"robots_unavailable:{error.__class__.__name__}",
        }


def main() -> None:
    adapter = load_table_adapters()[SOURCE_ID]
    rows: list[dict[str, object]] = []
    for page_url in (*adapter["scan_urls"], *ARTICLE_URLS):
        page_evidence = _robots(page_url)
        item: dict[str, object] = {
            "source_id": SOURCE_ID,
            "discovery_page_url": page_url,
            "robots": page_evidence,
            "http_status": None,
            "content_type": None,
            "table_links": [],
            "status": "access_limited",
        }
        if not page_evidence.get("allowed"):
            rows.append(item)
            continue
        try:
            response = requests.get(
                page_url,
                headers={"User-Agent": USER_AGENT},
                timeout=30,
                allow_redirects=True,
            )
            item["http_status"] = response.status_code
            item["content_type"] = response.headers.get("Content-Type")
            response.raise_for_status()
            discovered = discover_table_links(
                response.text,
                page_url=page_url,
                source_id=SOURCE_ID,
            )
            for link in discovered:
                link["table_robots"] = _robots(str(link["table_url"]))
                if not link["table_robots"].get("allowed"):
                    link["download_decision"] = "blocked_before_download"
                else:
                    link["download_decision"] = "eligible_for_separate_bounded_download"
            item["table_links"] = discovered
            item["status"] = "scanned"
        except (requests.RequestException, ValueError) as error:
            item["status"] = "source_unavailable"
            item["error"] = f"{error.__class__.__name__}: {error}"
        rows.append(item)

    payload = {
        "phase": "phase4",
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "source_id": SOURCE_ID,
        "policy": "仅发现已登记汇总页和职位表链接；不下载 robots 拒绝的附件，不生成候选或正式岗位。",
        "items": rows,
        "summary": {
            "pages": len(rows),
            "scanned_pages": sum(item["status"] == "scanned" for item in rows),
            "access_limited_pages": sum(item["status"] == "access_limited" for item in rows),
            "table_links": sum(len(item.get("table_links", [])) for item in rows),
        },
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(OUTPUT)


if __name__ == "__main__":
    main()
